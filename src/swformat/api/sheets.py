"""High-level drawing-sheet API (M4, read side) — list sheet names.

A SOLIDWORKS drawing stores its sheet-name list in the ``SheetPreviews/SheetNames``
stream as ``[u16 count][count × CString]`` (the same MFC ``CString`` form —
``FF FE FF <len:u8> <utf16le>`` — used throughout the format), followed by a
preview/metadata tail (carried verbatim; tail-bytes invariant). This module
reads those names WITHOUT SOLIDWORKS. The count equals the number of
``Images/Sheet_N`` streams. SW-verified against ``IDrawingDoc.GetSheetNames``
is the Layer-3 gate (manual, via combridge).

Public functions:
- :func:`read_sheet_names_from_stream` — parse the raw stream bytes.
- :func:`read_sheets` — sheet names for a drawing file path.
- :func:`sheet_count` — number of sheets.
- :func:`extract_sheet_previews` — write each sheet's rendered PNG preview
  (``Images/Sheet_N``) to a directory, named by sheet index + name. Real-file
  capable; high-value labelled visual data for training on a drawing corpus.

Modifying sheets (rename / reorder / add) is later M4 work; rename will reuse
the proven "locate the name CString → edit → write → SW-verify" pattern (as
shipped for configuration rename and cut-list value edit).
"""
from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from swformat.carchive.cstring import encode_cstring, read_cstring
from swformat.io.reader import read_document
from swformat.io.writer import set_stream_payload, write_with_toc

_SHEETNAMES_STREAM = "SheetPreviews/SheetNames"
# The sheet name SW reports is authoritative in Contents/Definition (the drawing
# CArchive); Header2 + SheetPreviews/SheetNames carry mirror copies. SW-verified:
# editing Contents/Definition alone changes GetSheetNames; editing only the
# mirrors does not. We edit all three for a consistent file (so a later SW
# re-save / the preview don't show a stale name).
_SHEET_NAME_STREAMS = ("Contents/Definition", "Header2", _SHEETNAMES_STREAM)


class SheetError(Exception):
    """Raised when a drawing-sheet edit cannot be applied."""


def read_sheet_names_from_stream(data: bytes) -> list[str]:
    """Parse ``SheetPreviews/SheetNames`` bytes → list of sheet names (in order).

    Layout: ``[u16 count]`` then ``count`` CStrings. Stops early (returning what
    was parsed) if a CString fails to decode. ``[]`` for empty/too-short input.
    """
    if len(data) < 2:
        return []
    count = struct.unpack_from("<H", data, 0)[0]
    names: list[str] = []
    o = 2
    for _ in range(count):
        cs = read_cstring(data, o)
        if cs is None:
            break
        names.append(cs[0])
        o = cs[1]
    return names


def read_sheets(path: str | Path) -> list[str]:
    """Return a drawing's sheet names (file order); ``[]`` if not a drawing /
    no ``SheetPreviews/SheetNames`` stream. No SOLIDWORKS required."""
    stream = read_document(path).streams().get(_SHEETNAMES_STREAM)
    return read_sheet_names_from_stream(stream) if stream else []


def sheet_count(path: str | Path) -> int:
    """Return the number of sheets, or ``0`` if unavailable. No SW."""
    stream = read_document(path).streams().get(_SHEETNAMES_STREAM)
    if not stream or len(stream) < 2:
        return 0
    return struct.unpack_from("<H", stream, 0)[0]


# Per-sheet rendered preview images are stored, one PNG per sheet, in
# ``Images/Sheet_<N>`` streams (N is 0-based, in sheet order). Verified on a real
# 28-sheet production drawing: every such stream begins with the PNG signature.
_SHEET_IMAGE_PREFIX = "Images/Sheet_"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _sheet_image_streams(streams: dict) -> list[tuple[int, str]]:
    """Return ``(sheet_index, stream_name)`` for every ``Images/Sheet_<N>`` stream,
    sorted by the numeric index (so ``Sheet_2`` precedes ``Sheet_10``)."""
    out: list[tuple[int, str]] = []
    for name in streams:
        if name.startswith(_SHEET_IMAGE_PREFIX):
            tail = name[len(_SHEET_IMAGE_PREFIX):]
            if tail.isdigit():
                out.append((int(tail), name))
    out.sort()
    return out


def extract_sheet_previews(path: str | Path, out_dir: str | Path) -> list[Path]:
    """Write each sheet's rendered PNG preview to ``out_dir`` and return the paths.

    SOLIDWORKS stores one PNG per sheet in the ``Images/Sheet_<N>`` streams; this
    copies them out verbatim (no re-encode), pairing each with its sheet NAME (from
    :func:`read_sheets`) when available so the files are human/agent-meaningful.
    High-value for visual training/labelling: a rendered image of every sheet, with
    no SOLIDWORKS required. Real-file capable (verified: a 28-sheet production
    drawing yields 28 valid PNGs).

    Output file names: ``<NN>_<sanitised sheet name>.png`` (zero-padded index +
    sheet name); falls back to ``sheet_<NN>.png`` if the name is unavailable. Only
    streams whose bytes actually start with the PNG signature are written (a guard
    against a renamed/empty stream). Creates ``out_dir`` if needed.

    Returns the list of written file paths (sheet order); ``[]`` for a non-drawing /
    no sheet-image streams.
    """
    streams = read_document(path).streams()
    images = _sheet_image_streams(streams)
    if not images:
        return []
    names = read_sheets(path)                       # may be [] if no name list
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    width = max(2, len(str(len(images) - 1)))
    written: list[Path] = []
    for idx, stream_name in images:
        data = streams[stream_name]
        if not data.startswith(_PNG_SIGNATURE):     # not a PNG — skip defensively
            continue
        label = names[idx] if idx < len(names) else ""
        safe = _sanitise(label)
        fname = f"{idx:0{width}d}_{safe}.png" if safe else f"sheet_{idx:0{width}d}.png"
        dest = out_dir / fname
        dest.write_bytes(data)
        written.append(dest)
    return written


def _sanitise(name: str) -> str:
    """Make a sheet name safe for a file name (drop path/illegal chars, trim)."""
    cleaned = "".join(c if (c.isalnum() or c in " -_.") else "_" for c in name).strip()
    return cleaned[:60]


# Per-sheet paper size + scale live in the ``SW-All Sheet Format Data`` property of
# ``docProps/ISolidWorksInformation.xml`` as an ``@``-delimited string, one 11-field
# record per sheet: ``name@type@width@height@scaleNum@scaleDenom@flag@<.slddrt
# template>@…``. The TEMPLATE PATH (``.slddrt``) is a reliable per-record anchor
# (verified: 28 anchors, 11 tokens apart, on a 28-sheet drawing) — robust against
# numeric sheet names ("18", "160") that would otherwise collide with numeric fields.
_INFO_STREAM = "docProps/ISolidWorksInformation.xml"
_SHEET_FORMAT_PROP = "SW-All Sheet Format Data"


@dataclass(frozen=True)
class SheetFormat:
    """One sheet's paper size + drawing scale.

    Attributes:
        name:        the sheet name.
        width:       paper width in METERS (SW SI; e.g. 0.4318 = 17 in / B-landscape).
        height:      paper height in METERS.
        scale_num:   scale numerator (model : drawing); e.g. 1.0.
        scale_denom: scale denominator; e.g. 32.0 for a 1:32 sheet.
    """

    name: str
    width: float
    height: float
    scale_num: float
    scale_denom: float

    @property
    def scale(self) -> str:
        """Human scale ratio, e.g. ``"1:32"`` (``"?"`` if non-finite)."""
        try:
            return f"{self.scale_num:g}:{self.scale_denom:g}"
        except (ValueError, TypeError):
            return "?"


def _info_property(streams: dict, name: str) -> str:
    """Return a named property's value from ``docProps/ISolidWorksInformation.xml``,
    or ``""`` (robust to absent/malformed XML). The value may be the property
    element's text or a child element's text."""
    data = streams.get(_INFO_STREAM)
    if not data:
        return ""
    try:
        root = ET.fromstring(data.decode("utf-8", "ignore"))
    except ET.ParseError:
        return ""
    for prop in root.iter():
        if (prop.get("name") or prop.get("Name")) == name:
            txt = (prop.text or "").strip()
            if txt:
                return txt
            for child in prop:
                if (child.text or "").strip():
                    return child.text.strip()
    return ""


def _f(token: str) -> float:
    """Parse a float field, returning NaN on failure (defensive)."""
    try:
        return float(token)
    except (ValueError, TypeError):
        return float("nan")


def read_sheet_formats(path: str | Path) -> list[SheetFormat]:
    """Return per-sheet paper size + scale (file order). ``[]`` if unavailable.

    Parses ``SW-All Sheet Format Data`` anchored on each record's ``.slddrt``
    template token (robust against numeric sheet names). No SOLIDWORKS required;
    real-file capable (verified: a 28-sheet drawing yields 28 records with
    per-sheet scales like 1:32 / 1:2 / 1:1 / 1:8 and 0.4318×0.2794 m paper).
    """
    value = _info_property(read_document(path).streams(), _SHEET_FORMAT_PROP)
    if not value:
        return []
    toks = value.split("@")
    out: list[SheetFormat] = []
    for p, tok in enumerate(toks):
        # record = [name][type][w][h][sN][sD][flag][template.slddrt][…]; template @+7
        if tok.lower().endswith(".slddrt") and p >= 7:
            out.append(SheetFormat(
                name=toks[p - 7], width=_f(toks[p - 5]), height=_f(toks[p - 4]),
                scale_num=_f(toks[p - 3]), scale_denom=_f(toks[p - 2])))
    return out


def rename_sheet(
    path: str | Path,
    old_name: str,
    new_name: str,
    out_path: str | Path,
) -> list[str]:
    """Rename drawing sheet ``old_name`` → ``new_name``, writing to ``out_path``.

    Edits the sheet-name CString across the authoritative ``Contents/Definition``
    and the ``Header2`` + ``SheetPreviews/SheetNames`` mirrors, then re-deflates
    and fixes the central directory via :func:`write_with_toc`. This is
    span-preserving when the new name fits the original compressed span (the
    SW-verified path). A LONGER name that overflows the span raises
    :class:`~swformat.io.writer.SpanPreserveError`: drawings cannot use the
    parts-only relocate-to-EOF grow (they validate file size — see
    :func:`swformat.io.writer.serialize_with_toc`), so sheet rename-to-longer
    beyond span is not yet supported. Returns the new sheet-name list.

    SW-verified (SW 2026) for the span-preserving case: SOLIDWORKS reopens the
    result (errors=0, warnings=0) and ``IDrawingDoc.GetSheetNames`` reports the
    new name.

    Implementation note / caveat: the edit replaces exact-length CString
    occurrences of ``old_name`` in those streams. This is safe for typical unique
    sheet names (a differently-named sheet has a different CString length prefix,
    so it cannot collide). It would be unsafe only if the exact ``old_name``
    string also appears as a standalone CString of unrelated drawing data — rare;
    guarded below against the empty name and a name that duplicates another sheet.

    :raises SheetError: if the file has no sheet list, ``old_name`` is absent,
        ``new_name`` already names a sheet, or the name CString is not found.
    """
    names = read_sheets(path)
    if not names:
        raise SheetError(f"{path}: no drawing sheet list (not a drawing?)")
    if old_name not in names:
        raise SheetError(f"sheet {old_name!r} not found (have: {names})")
    if not new_name:
        raise SheetError("new sheet name must be non-empty")
    if new_name in names:
        raise SheetError(f"a sheet named {new_name!r} already exists")

    doc = read_document(path)
    streams = doc.streams()
    old_cs = encode_cstring(old_name)
    new_cs = encode_cstring(new_name)
    edited = 0
    for sname in _SHEET_NAME_STREAMS:
        s = streams.get(sname)
        if s and old_cs in s:
            set_stream_payload(doc, sname, s.replace(old_cs, new_cs))
            edited += 1
    if edited == 0:
        raise SheetError(f"sheet name CString for {old_name!r} not found in any stream")
    write_with_toc(doc, out_path)
    return read_sheets(out_path)
