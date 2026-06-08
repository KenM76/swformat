"""Test for ``swformat.api.metadata`` — one-call drawing metadata bundle."""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from swformat.api.metadata import read_drawing_metadata  # noqa: E402
from swformat.carchive.cstring import encode_cstring  # noqa: E402


def _doc(monkeypatch, streams: dict):
    import swformat.api.annotations as an
    import swformat.api.dimensions as dm
    import swformat.api.keywords as kw
    import swformat.api.metadata as m
    import swformat.api.references as rf
    import swformat.api.sheets as sh
    import swformat.api.tables as tb
    import swformat.api.views as vw

    class _Doc:
        def streams(self):
            return streams

    for mod in (m, sh, rf, tb, an, vw, dm, kw):
        monkeypatch.setattr(mod, "read_document", lambda _p, _s=streams: _Doc())


def test_read_drawing_metadata_bundle(monkeypatch) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"x"
    streams = {
        "SheetPreviews/SheetNames": struct.pack("<H", 2) + encode_cstring("S1") + encode_cstring("S2"),
        "Images/Sheet_0": png,
        "Images/Sheet_1": png,
        "Contents/Definition": encode_cstring(r"C:\m\Part1.SLDPRT"),
        "swXmlContents/Tables": (b'<Tables><Table Table_Type="BOM" Display_Name="B" Num_Rows="1" Num_Cols="1">'
                                 b'<Row><Column><Cell><Text_Element Text="ITEM"/></Cell></Column></Row></Table></Tables>'),
        "swXmlContents/CustomProperties": b'<DisplayItems><DisplayItem Text="NOTE"/></DisplayItems>',
        "docProps/ISolidWorksInformation.xml": (
            b'<Properties xmlns="x"><customProperty name="SW-Config-Model-View-Sheet-List">'
            b'<lpwstr>1:@@Default@0@' rb'sub\Part1.SLDPRT' b'@Drawing View1@S1</lpwstr>'
            b'</customProperty></Properties>'),
        "swXmlContents/KeyWords": b'\x86<?xml version="1.0"?><Keywords><Dimension Name="D1">38"</Dimension></Keywords>',
    }
    _doc(monkeypatch, streams)
    meta = read_drawing_metadata("drawing.SLDDRW")
    assert meta["file"] == "drawing.SLDDRW"
    assert meta["sheet_count"] == 2
    assert meta["sheet_names"] == ["S1", "S2"]
    assert meta["sheet_preview_count"] == 2
    assert meta["referenced_models"] == [r"C:\m\Part1.SLDPRT"]
    assert meta["bom_count"] == 1
    assert meta["tables"][0]["type"] == "BOM" and meta["tables"][0]["rows"] == [["ITEM"]]
    assert meta["annotation_text"] == ["NOTE"]
    assert meta["views"] == [{"view": "Drawing View1", "model": r"sub\Part1.SLDPRT", "sheet": "S1"}]
    assert meta["dimensions"] == [{"name": "D1", "value": '38"'}]
    assert meta["keyword_index_counts"] == {"Dimension": 1}
    json.dumps(meta)                                  # must be JSON-serialisable


def test_no_rows_option(monkeypatch) -> None:
    streams = {"swXmlContents/Tables": (b'<Tables><Table Table_Type="BOM" Display_Name="B">'
                                        b'<Row><Column><Cell><Text_Element Text="X"/></Cell></Column></Row></Table></Tables>')}
    _doc(monkeypatch, streams)
    meta = read_drawing_metadata("d", include_table_rows=False)
    assert "rows" not in meta["tables"][0]
    assert meta["tables"][0]["num_rows"] == 1


def test_empty_document(monkeypatch) -> None:
    _doc(monkeypatch, {})
    meta = read_drawing_metadata("d")
    assert meta["sheet_count"] == 0 and meta["referenced_models"] == [] and meta["tables"] == []
    json.dumps(meta)


# --- part/assembly metadata + unified dispatcher ----------------------------

def test_detect_doc_type_by_extension() -> None:
    from swformat.api.metadata import detect_doc_type
    assert detect_doc_type("a.SLDPRT") == "part"
    assert detect_doc_type("a.sldasm") == "assembly"
    assert detect_doc_type("a.SLDDRW") == "drawing"
    assert detect_doc_type("a.txt", streams={}) == "unknown"
    assert detect_doc_type("x", streams={"Contents/Definition": b""}) == "drawing"
    assert detect_doc_type("x", streams={"swXmlContents/MATERIALTREE": b""}) == "part"


def test_read_model_metadata_real_part_if_present() -> None:
    import glob
    import os

    from swformat.api.metadata import read_model_metadata
    # NB: filter on the BASENAME only — the fixture dir is ``twin_save_baseline``,
    # so a substring check on the full path would wrongly exclude every real part.
    parts = [p for p in glob.glob(str(ROOT / "research" / "empirical_findings" /
             "twin_save_baseline" / "samples" / "*" / "*.SLDPRT"))
             if "twin" not in os.path.basename(p).lower()]
    if not parts:
        pytest.skip("no real part fixture present")
    m = read_model_metadata(parts[0])
    assert m["doc_type"] == "part"
    assert isinstance(m["properties"], dict) and isinstance(m["configurations"], list)
    assert "dimensions" in m and "material" in m
    json.dumps(m)                                       # JSON-serialisable


def test_read_metadata_dispatches_if_present() -> None:
    import glob
    import os

    from swformat.api.metadata import read_metadata
    drw = ROOT / "research/empirical_findings/twin_save_baseline/samples/drw_10/example-drawing.SLDDRW"
    parts = [p for p in glob.glob(str(ROOT / "research/empirical_findings/twin_save_baseline/samples/*/*.SLDPRT"))
             if "twin" not in os.path.basename(p).lower()]
    if not drw.exists() or not parts:
        pytest.skip("real fixtures not present")
    assert read_metadata(drw)["doc_type"] == "drawing" and "sheet_count" in read_metadata(drw)
    assert read_metadata(parts[0])["doc_type"] == "part" and "properties" in read_metadata(parts[0])
