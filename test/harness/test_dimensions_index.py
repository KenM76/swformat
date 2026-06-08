"""Tests for ``swformat.api.dimensions`` — displayed dimension values (KeyWords)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from swformat.api.dimensions import (  # noqa: E402
    DrawingDimension,
    parse_dimensions_xml,
    read_dimension_values,
)

# A 1-byte prefix (0x86) precedes <?xml, as real KeyWords streams have.
_KW = (b"\x86<?xml version='1.0'?><Keywords>"
       b'<View Name="V1"><Dimension Name="D4">38"</Dimension>'
       b'<Dimension Name="D3">23 1/2"</Dimension></View>'
       b'<Dimension Name="D1"></Dimension>'          # no value -> dropped
       b"</Keywords>")


def test_parse_dimensions_xml() -> None:
    dims = parse_dimensions_xml(_KW)
    assert dims == [DrawingDimension("D4", '38"'), DrawingDimension("D3", '23 1/2"')]


def test_skips_prefix_and_handles_namespace() -> None:
    kw = (b"\x00\x00<?xml version='1.0'?><Keywords xmlns='x'>"
          b'<Dimension Name="D1">R0.5</Dimension></Keywords>')
    assert parse_dimensions_xml(kw) == [DrawingDimension("D1", "R0.5")]


def test_empty_and_malformed() -> None:
    assert parse_dimensions_xml(b"") == []
    assert parse_dimensions_xml(b"no xml here") == []
    assert parse_dimensions_xml(b"\x86<?xml><broken") == []


def test_read_dimension_values(monkeypatch) -> None:
    import swformat.api.dimensions as dm

    class _Doc:
        def streams(self):
            return {"swXmlContents/KeyWords": _KW}

    monkeypatch.setattr(dm, "read_document", lambda _p: _Doc())
    assert [d.value for d in read_dimension_values("x")] == ['38"', '23 1/2"']


def test_real_drawing_dimensions_if_present() -> None:
    path = (ROOT / "research" / "empirical_findings" / "twin_save_baseline" /
            "samples" / "drw_10" / "example-drawing.SLDDRW")
    if not path.exists():
        pytest.skip("real drawing fixture not present")
    dims = read_dimension_values(path)
    assert len(dims) >= 50
    assert all(d.value for d in dims)              # every emitted dim has a value
