"""Layer 1 — chunk walker for the SOLIDWORKS 2015+ "modern" file format.

PURPOSE
-------
Turn a flat byte buffer (the contents of a .sldprt/.sldasm/.slddrw saved
by SW 2015 or later) into an ordered list of :class:`~swformat.types.Chunk`
and :class:`~swformat.types.Gap` records that account for **every byte**.

This is a productionised port of the proven algorithm in
``_imported_swx_reader_v0.py`` (itself ported from schwitters/openswx, MIT).
The imported module is preserved verbatim as the
historical reference; this module is the maintained implementation and
adds the **gap model** (no-orphan-bytes) that the imported reader lacked.

ALGORITHM (two passes)
----------------------
1. **Scan.** Linearly search for the 6-byte marker ``14 00 06 00 08 00``.
   For each hit, parse the fixed header at ``si = marker - 4``, apply the
   openswx sanity caps (``nsz <= 512``, ``csz <= 64 MiB``), ROL-decode the
   stream name, and require it to be printable ASCII. A hit that passes
   all checks *and* declares an inline payload (``f1 >= 65536`` and
   ``csz > 0``) that fits within the file is accepted as a
   :class:`Chunk`. After accepting one, the scan cursor jumps to the end
   of its compressed payload — so markers that happen to appear *inside*
   compressed data are never mistaken for chunks (DEFLATE output is
   high-entropy; spurious marker hits do occur).

   Non-inline markers (TOC/table-of-contents entries with ``f1 < 65536``
   or ``csz == 0``) are **not** emitted as chunks; they fall into the
   surrounding gap and are preserved verbatim. We do not interpret the
   TOC at M0 — risk of TOC offset rewriting is an explicit M1 risk in
   ``docs/ROADMAP.md``.

2. **Gap fill.** Walk the accepted chunks in offset order. Any byte not
   owned by a chunk — the leading file header (which holds the ROL key at
   byte 7), inter-chunk padding, and the trailing TOC region — becomes a
   :class:`Gap`. The result is a contiguous record list with no holes and
   no overlaps: ``sum(len(r) for r in records) == len(data)``.

WHY ONLY INLINE CHUNKS BECOME CHUNKS
------------------------------------
The data streams we care about (XML props, CMgrHdr2, ResolvedFeatures,
DisplayLists, …) are all inline, payload-bearing chunks. Folding TOC
markers into gaps keeps the round-trip exact (gaps are verbatim) while
making the chunk set equal to the set of real, inflatable streams — which
is exactly what ``Document.streams()`` and the diff tool operate on. This
matches ``read_file``'s long-standing "inline only" semantics.

FAILURE MODES — see ``docs/REVERSE_ENGINEERING.md`` §9 for the catalog.
"""
from __future__ import annotations

import struct
from collections.abc import Iterator

from swformat.types import Chunk, FileFormat, Gap, Record

# The 6-byte chunk marker. Preceded by 4 file-specific "val_a" bytes, so a
# chunk header starts at (marker_position - 4).
MARKER = b"\x14\x00\x06\x00\x08\x00"

# Container signatures for format detection.
_OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OPC_ZIP_SIGNATURE = b"\x50\x4b\x03\x04"  # "PK\x03\x04" — ZIP local file header

# openswx sanity caps — reject marker hits whose declared sizes exceed these,
# which would indicate a false-positive marker inside high-entropy data.
_MAX_NAME_LEN = 512
_MAX_CSZ = 64 * 1024 * 1024

# Header field offsets relative to the chunk start (si).
_OFF_SECTION_TYPE = 0x0A
_OFF_F1 = 0x0E
_OFF_CSZ = 0x12
_OFF_USZ = 0x16
_OFF_NSZ = 0x1A
_OFF_NAME = 0x1E

# An inline (payload-bearing) chunk is flagged by f1 >= this threshold.
_INLINE_THRESHOLD = 65536


def detect_format(data: bytes) -> FileFormat:
    """Classify a file by its leading bytes / header probe.

    Returns one of ``"modern"``, ``"ole2"``, ``"opc"``, ``"unknown"``:

    - ``ole2``    : OLE2 compound document (pre-2015 SW files). Detected by
      the 8-byte OLE2 signature.
    - ``opc``     : OPC/ZIP container (``PK\\x03\\x04``). Rare for native SW
      files but possible for some SW-adjacent formats.
    - ``modern``  : the 2015+ chunk format — detected by the presence of the
      6-byte marker within the first 64 bytes (the document header chunk
      appears almost immediately).
    - ``unknown`` : none of the above matched.

    Order matters: OLE2/ZIP signatures are checked first because a modern
    file never starts with them.
    """
    if data[:8] == _OLE2_SIGNATURE:
        return "ole2"
    if data[:4] == _OPC_ZIP_SIGNATURE:
        return "opc"
    if MARKER in data[:64]:
        return "modern"
    return "unknown"


def _rol_byte(b: int, k: int) -> int:
    """Rotate-left a single byte by ``k`` bits (the SW name obfuscation)."""
    k &= 7
    if k == 0:
        return b
    return ((b << k) | (b >> (8 - k))) & 0xFF


def rol_decode(name_bytes: bytes, key: int) -> str:
    """Decode a ROL-obfuscated stream name to UTF-8.

    SOLIDWORKS rotate-left-encodes each byte of a stream name by a
    per-file key. For modern files the key is ``data[7]`` (the 8th byte of
    the file header). Undecodable bytes are replaced rather than raising,
    so a misidentified chunk degrades to a gibberish name (rejected by the
    printable-ASCII check) instead of crashing the walk.
    """
    return bytes(_rol_byte(b, key) for b in name_bytes).decode("utf-8", errors="replace")


def _is_printable_ascii(name: str) -> bool:
    """True if every char is in the printable ASCII range (0x20..0x7E)."""
    return bool(name) and all(" " <= c <= "~" for c in name)


def _scan_chunks(data: bytes) -> list[Chunk]:
    """Pass 1: return accepted inline chunks in ascending offset order.

    A chunk is accepted only if its header validates, its name decodes to
    printable ASCII, and its declared inline payload fits within the file.
    The cursor jumps past each accepted payload so markers embedded in
    compressed data are skipped.
    """
    n = len(data)
    if n < 8:
        return []
    key = data[7]
    accepted: list[Chunk] = []
    pos = 0
    while True:
        m = data.find(MARKER, pos)
        if m < 0:
            break  # no more markers anywhere after pos
        if m < 4:
            # A marker in the first 4 bytes can't be a real chunk (its header
            # would start at si = m-4 < 0). This is only ever a coincidental
            # byte pattern; SKIP past it and keep scanning — breaking here would
            # abort the whole walk and silently drop every real chunk (which
            # all begin at m >= 4), turning the file into one opaque gap.
            pos = m + 1
            continue
        si = m - 4
        # Need the full fixed header (through the name-length field) present.
        if si + _OFF_NAME > n:
            pos = m + 1
            continue
        section_type = data[si + _OFF_SECTION_TYPE]
        f1 = struct.unpack_from("<I", data, si + _OFF_F1)[0]
        csz = struct.unpack_from("<I", data, si + _OFF_CSZ)[0]
        usz = struct.unpack_from("<I", data, si + _OFF_USZ)[0]
        nsz = struct.unpack_from("<I", data, si + _OFF_NSZ)[0]
        if nsz > _MAX_NAME_LEN or csz > _MAX_CSZ:
            pos = m + 1
            continue
        name_start = si + _OFF_NAME
        name_end = name_start + nsz
        if name_end > n:
            pos = m + 1
            continue
        name = rol_decode(data[name_start:name_end], key)
        if not _is_printable_ascii(name):
            pos = m + 1
            continue

        is_inline = f1 >= _INLINE_THRESHOLD and csz > 0
        if not is_inline:
            # TOC / marker chunk: not emitted; folded into the gap. Advance
            # just past this marker so we keep scanning the TOC region.
            pos = m + len(MARKER)
            continue

        data_end = name_end + csz
        if data_end > n:
            # Claims an inline payload that overruns EOF => false positive.
            pos = m + 1
            continue

        accepted.append(
            Chunk(
                offset=si,
                section_type=section_type,
                f1=f1,
                csz=csz,
                usz=usz,
                nsz=nsz,
                name=name,
                header_bytes=data[si:name_end],
                original_compressed=data[name_end:data_end],
            )
        )
        pos = data_end
    return accepted


def iter_records(data: bytes) -> Iterator[Record]:
    """Yield every :class:`Chunk` / :class:`Gap`, covering all bytes once.

    The returned records, concatenated in order, reproduce ``data``
    exactly (the no-orphan-bytes invariant). For non-modern files there
    are no chunks, so this yields a single Gap spanning the whole file.

    A defensive guard drops any accepted chunk whose start falls *before*
    the running cursor (a pathological overlap from a marker landing in
    the trailing bytes of a previous chunk); dropping it preserves the
    invariant — those bytes simply remain part of the surrounding gap.
    """
    n = len(data)
    cursor = 0
    for ch in _scan_chunks(data):
        if ch.offset < cursor:
            # Overlap with already-emitted bytes — skip to keep accounting exact.
            continue
        if ch.offset > cursor:
            yield Gap(offset=cursor, raw_bytes=data[cursor : ch.offset])
        yield ch
        cursor = ch.end
    if cursor < n:
        yield Gap(offset=cursor, raw_bytes=data[cursor:n])


def iter_chunks(data: bytes) -> Iterator[Chunk]:
    """Yield only the accepted :class:`Chunk` records (no gaps).

    Convenience for callers that don't need byte-exact accounting (e.g.
    stream enumeration). Equivalent to filtering :func:`iter_records`.
    """
    for rec in iter_records(data):
        if isinstance(rec, Chunk):
            yield rec


def doc_version(streams: dict[str, bytes]) -> int | None:
    """Return the document's effective format version, or ``None``.

    Per openswx, the version is encoded in stream *names* of the form
    ``_MO_VERSION_NNNNN/...``; the document's effective version is the
    largest ``NNNNN`` present. ``None`` if no such stream exists.
    """
    best = -1
    for name in streams:
        if not name.startswith("_MO_VERSION_"):
            continue
        head = name[len("_MO_VERSION_") :].split("/", 1)[0]
        try:
            v = int(head)
        except ValueError:
            continue
        best = max(best, v)
    return best if best >= 0 else None
