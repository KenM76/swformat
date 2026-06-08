"""M4 (read side) tests — drawing sheet-name listing from SheetPreviews/SheetNames.

The synthetic parser test is committable + deterministic. The staged-drawing
test cross-checks the parsed count against the number of Images/Sheet_N streams
(skips if no staged drawing is present — real CAD files are local-only and never
committed). No third-party data is asserted. Marked layer2 (pure Python, no SW).
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

import swformat  # noqa: E402
from swformat.api.sheets import (  # noqa: E402
    read_sheet_names_from_stream,
    read_sheets,
    sheet_count,
)
from swformat.carchive.cstring import encode_cstring  # noqa: E402


def test_sheetnames_parser_synthetic() -> None:
    """[u16 count][count CStrings][tail] parses to the names, ignoring the tail."""
    blob = (
        struct.pack("<H", 3)
        + encode_cstring("Sheet1")
        + encode_cstring("Detail A")
        + encode_cstring("160 PARTS P1")
        + b"\x99\x88preview-tail-bytes"
    )
    assert read_sheet_names_from_stream(blob) == ["Sheet1", "Detail A", "160 PARTS P1"]


def test_sheetnames_parser_empty() -> None:
    assert read_sheet_names_from_stream(b"") == []
    assert read_sheet_names_from_stream(b"\x00\x00") == []  # count 0


def _staged_drawing() -> Path | None:
    samples = ROOT / "research" / "empirical_findings" / "twin_save_baseline" / "samples"
    if not samples.exists():
        return None
    for p in sorted(samples.rglob("*.SLDDRW")):
        if "SheetPreviews/SheetNames" in swformat.read_document(p).streams():
            return p
    return None


# Prefer the synthetic, deterministic fixture (a generated empty drawing whose
# single sheet is named "ALPHA" — gen_drawing_min.csx; no client geometry).
# Fall back to any staged drawing.
_SYNTH_DRAWING = ROOT / "research" / "empirical_findings" / "m4_drawing_sheets" / "drawing_min.SLDDRW"
_DRAWING = _SYNTH_DRAWING if _SYNTH_DRAWING.exists() else _staged_drawing()


@pytest.mark.layer2
@pytest.mark.skipif(_DRAWING is None, reason="no staged drawing with SheetNames")
def test_read_sheets_count_matches_image_sheets() -> None:
    """Parsed sheet count == number of sheets == count of Images/Sheet_N streams,
    and every sheet name is a non-empty printable string."""
    streams = swformat.read_document(_DRAWING).streams()
    names = read_sheets(_DRAWING)
    img = sum(1 for n in streams if n.startswith("Images/Sheet_"))
    assert len(names) == sheet_count(_DRAWING) == img
    assert all(isinstance(n, str) and n for n in names)


@pytest.mark.layer2
@pytest.mark.skipif(_DRAWING is None, reason="no staged drawing with SheetNames")
def test_rename_sheet_roundtrip(tmp_path: Path) -> None:
    """rename_sheet writes a drawing the reader reads back with the new sheet
    name (others unchanged); guards reject unknown/duplicate names.
    (SW-verified separately via combridge — see the M4 hypothesis log.)

    NOTE: uses a SAME-LENGTH replacement name so the edit stays within the
    original compressed span (span-preservation — the only SW-verified write
    path). Renaming to a LONGER name grows the tiny ``SheetPreviews/SheetNames``
    stream beyond its span; that grow now correctly raises ``SpanPreserveError``
    (the offset-shift grow path is SW-invalid — re-falsified 2026-06-11, see the
    m1_writer_roundtrip log), so it is no longer exercised here."""
    from swformat.api.sheets import SheetError, read_sheets, rename_sheet

    names = read_sheets(_DRAWING)
    target = names[-1]
    # same-length, distinct, non-colliding replacement → guaranteed span-preserve
    new = target[:-1] + ("X" if target[-1] != "X" else "Q")
    while new in names:
        new = new[:-1] + ("Y" if new[-1] != "Y" else "Z")
    out = tmp_path / f"renamed{_DRAWING.suffix}"
    result = rename_sheet(_DRAWING, target, new, out)
    expected = [new if n == target else n for n in names]
    assert result == expected
    assert read_sheets(out) == expected
    with pytest.raises(SheetError):
        rename_sheet(_DRAWING, "NoSuchSheetXYZ", "x", tmp_path / "x.SLDDRW")
    if len(names) >= 2:
        with pytest.raises(SheetError):
            rename_sheet(_DRAWING, names[0], names[1], tmp_path / "y.SLDDRW")  # dup
    else:
        # single-sheet fixture: renaming to the SAME existing name is the dup case
        with pytest.raises(SheetError):
            rename_sheet(_DRAWING, names[0], names[0], tmp_path / "y.SLDDRW")


# --- sheet preview PNG extraction (real-file metadata for training) ----------

def test_extract_sheet_previews_synthetic(tmp_path, monkeypatch) -> None:
    """Extract two sheet PNGs from a fake document, named by sheet index + name;
    a non-PNG Images/Sheet stream is skipped defensively."""
    import struct

    import swformat.api.sheets as sheets
    from swformat.carchive.cstring import encode_cstring

    png = b"\x89PNG\r\n\x1a\n" + b"fakebody"
    names_stream = struct.pack("<H", 2) + encode_cstring("Front") + encode_cstring("Detail/A")

    class _Doc:
        def streams(self):
            return {
                "SheetPreviews/SheetNames": names_stream,
                "Images/Sheet_0": png,
                "Images/Sheet_1": png,
                "Images/Sheet_2": b"not-a-png",   # defensively skipped
            }

    monkeypatch.setattr(sheets, "read_document", lambda _p: _Doc())
    written = sheets.extract_sheet_previews("anything", tmp_path)
    got = sorted(p.name for p in written)
    assert got == ["00_Front.png", "01_Detail_A.png"]      # slash sanitised; non-PNG skipped
    assert (tmp_path / "00_Front.png").read_bytes() == png


def test_extract_sheet_previews_none(monkeypatch, tmp_path) -> None:
    import swformat.api.sheets as sheets

    class _Doc:
        def streams(self):
            return {}                                      # no Images/Sheet_N

    monkeypatch.setattr(sheets, "read_document", lambda _p: _Doc())
    assert sheets.extract_sheet_previews("anything", tmp_path) == []


# --- per-sheet paper size + scale (read_sheet_formats) -----------------------

def test_read_sheet_formats_synthetic(monkeypatch) -> None:
    """Template-anchored per-sheet format parse; robust to numeric sheet names."""
    import swformat.api.sheets as sheets
    # two records, 11 fields each: name,type,w,h,sN,sD,flag,template,x,y,(empty)
    val = ("SH-1@2@0.4318@0.2794@1@32@0@f:/tpl/a.slddrt@13@1@"
           "@18@2@0.4318@0.2794@1@2@0@f:/tpl/a.slddrt@13@1@")
    xml = ('<Properties xmlns="x"><customProperty name="SW-All Sheet Format Data">'
           f'<lpwstr>{val}</lpwstr></customProperty></Properties>').encode()

    class _Doc:
        def streams(self):
            return {"docProps/ISolidWorksInformation.xml": xml}

    monkeypatch.setattr(sheets, "read_document", lambda _p: _Doc())
    fmts = sheets.read_sheet_formats("d")
    assert [f.name for f in fmts] == ["SH-1", "18"]      # numeric name "18" kept
    assert fmts[0].scale == "1:32" and fmts[1].scale == "1:2"
    assert abs(fmts[0].width - 0.4318) < 1e-9 and abs(fmts[0].height - 0.2794) < 1e-9


def test_read_sheet_formats_none(monkeypatch) -> None:
    import swformat.api.sheets as sheets
    monkeypatch.setattr(sheets, "read_document",
                        lambda _p: type("D", (), {"streams": lambda s: {}})())
    assert sheets.read_sheet_formats("d") == []
