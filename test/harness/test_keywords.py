"""Tests for ``swformat.api.keywords`` — full keyword/entity index."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from swformat.api.keywords import (  # noqa: E402
    keyword_index_counts,
    parse_keyword_index,
)

_KW = (b"\x86<?xml version='1.0'?><Keywords>"
       b'<Dimension Name="D4">38"</Dimension>'
       b'<Note>BREAK ALL SHARP EDGES</Note>'
       b'<View Name="Drawing View1">part.sldprt</View>'
       b'<Feature Name="Plane1"/>'              # name only
       b'<Empty/>'                              # no name/text -> skipped
       b"</Keywords>")


def test_parse_keyword_index() -> None:
    idx = parse_keyword_index(_KW)
    assert idx["Dimension"] == [("D4", '38"')]
    assert idx["Note"] == [("", "BREAK ALL SHARP EDGES")]
    assert idx["View"] == [("Drawing View1", "part.sldprt")]
    assert idx["Feature"] == [("Plane1", "")]
    assert "Empty" not in idx                   # element with neither name nor text dropped
    assert "Keywords" not in idx                # root wrapper not catalogued


def test_counts_and_empty() -> None:
    assert keyword_index_counts.__doc__                      # exists
    assert parse_keyword_index(b"") == {}
    assert parse_keyword_index(b"\x86<?xml?><broken") == {}


def test_read_keyword_index(monkeypatch) -> None:
    import swformat.api.keywords as kw

    class _Doc:
        def streams(self):
            return {"swXmlContents/KeyWords": _KW}

    monkeypatch.setattr(kw, "read_document", lambda _p: _Doc())
    assert kw.keyword_index_counts("x") == {"Dimension": 1, "Note": 1, "View": 1, "Feature": 1}


def test_real_drawing_index_if_present() -> None:
    path = (ROOT / "research" / "empirical_findings" / "twin_save_baseline" /
            "samples" / "drw_10" / "example-drawing.SLDDRW")
    if not path.exists():
        pytest.skip("real drawing fixture not present")
    counts = keyword_index_counts(path)
    assert counts.get("Dimension", 0) >= 50 and counts.get("View", 0) >= 10
