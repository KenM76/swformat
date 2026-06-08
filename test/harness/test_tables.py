"""Tests for ``swformat.api.tables`` — drawing table (BOM/Revision) extraction.

Synthetic XML tests (always run) pin the parse against the documented
``Table → Row → Column → Cell → Text_Element[@Text]`` layout; a fixture
cross-check (skip if absent) validates on a real drawing. Pure Python, no SW.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from swformat.api.tables import (  # noqa: E402
    parse_tables_xml,
    read_boms,
    read_tables,
)

_BOM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Tables>
  <Table Table_Type="BOM" Display_Name="Bill of Materials1" Num_Rows="3" Num_Cols="3">
    <Row RowNum="0">
      <Column><Cell><Text_Element Text="ITEM"/></Cell></Column>
      <Column><Cell><Text_Element Text="PARTNO"/></Cell></Column>
      <Column><Cell><Text_Element Text="QTY"/></Cell></Column>
    </Row>
    <Row RowNum="1">
      <Column><Cell><Text_Element Text="1"/></Cell></Column>
      <Column><Cell><Text_Element Text="PART-018"/></Cell></Column>
      <Column><Cell><Text_Element Text="2"/></Cell></Column>
    </Row>
  </Table>
  <Table Table_Type="Revision" Display_Name="Rev1" Num_Rows="1" Num_Cols="2">
    <Row RowNum="0">
      <Column><Cell><Text_Element Text="REV"/></Cell></Column>
      <Column><Cell><Text_Element Text="A"/></Cell></Column>
    </Row>
  </Table>
</Tables>"""


def test_parse_tables_xml_structure() -> None:
    tables = parse_tables_xml(_BOM_XML)
    assert [t.table_type for t in tables] == ["BOM", "Revision"]
    bom = tables[0]
    assert bom.name == "Bill of Materials1"
    assert bom.num_rows == 2 and bom.num_cols == 3
    assert bom.header == ["ITEM", "PARTNO", "QTY"]
    assert bom.rows[1] == ["1", "PART-018", "2"]


def test_as_dicts() -> None:
    bom = parse_tables_xml(_BOM_XML)[0]
    assert bom.as_dicts() == [{"ITEM": "1", "PARTNO": "PART-018", "QTY": "2"}]


def test_multi_text_element_cell_concatenates() -> None:
    xml = (b'<Tables><Table Table_Type="GeneralTable" Display_Name="G">'
           b'<Row><Column><Cell><Text_Element Text="AB"/><Text_Element Text="CD"/>'
           b'</Cell></Column></Row></Table></Tables>')
    assert parse_tables_xml(xml)[0].rows == [["ABCD"]]


def test_empty_and_malformed() -> None:
    assert parse_tables_xml(b"") == []
    assert parse_tables_xml(b"<not closed") == []          # malformed -> [] not raise


def test_read_tables_filter(monkeypatch) -> None:
    import swformat.api.tables as t

    class _Doc:
        def streams(self):
            return {"swXmlContents/Tables": _BOM_XML}

    monkeypatch.setattr(t, "read_document", lambda _p: _Doc())
    assert [x.table_type for x in read_tables("x")] == ["BOM", "Revision"]
    assert [x.table_type for x in read_tables("x", table_type="bom")] == ["BOM"]
    assert [x.table_type for x in read_boms("x")] == ["BOM"]


def test_no_tables_stream(monkeypatch) -> None:
    import swformat.api.tables as t
    monkeypatch.setattr(t, "read_document", lambda _p: type("D", (), {"streams": lambda s: {}})())
    assert read_tables("x") == []


def test_real_drawing_bom_if_present() -> None:
    """Cross-check on a real drawing if staged locally (client file, not tracked)."""
    path = (ROOT / "research" / "empirical_findings" / "twin_save_baseline" /
            "samples" / "drw_10" / "example-drawing.SLDDRW")
    if not path.exists():
        pytest.skip("real drawing fixture not present")
    boms = read_boms(path)
    assert boms, "expected at least one BOM"
    # a BOM header should contain part/qty-ish columns; just assert non-empty grid
    assert boms[0].num_rows >= 2 and boms[0].num_cols >= 2
