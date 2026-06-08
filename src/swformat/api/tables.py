"""``swformat.api.tables`` — extract a drawing's tables (BOM, Revision, …).

A SOLIDWORKS drawing stores its annotation tables — Bill of Materials, Revision
tables, hole tables, general tables — as XML in the ``swXmlContents/Tables``
stream. This module parses that XML into structured rows **without SOLIDWORKS**,
so a consumer gets each table's type, name, and cell grid directly.

Why this exists (the consumer): an agent training on a drawing corpus wants the
BILL OF MATERIALS (item no / part no / description / quantity) and the REVISION
history per drawing — high-value structured labels joining a drawing to its parts
and change history. The data is literal cell text in the XML (not `$PRP` tokens),
so it is directly usable.

The ``swXmlContents/Tables`` XML layout (verified on a real 33-table production
drawing — 5 BOM + 28 Revision):

    <Tables>
      <Table Table_Type="BOM" Display_Name="Bill of Materials12"
             Num_Rows="24" Num_Cols="4">
        <Row RowNum="0">
          <Column> <Cell> … <Text_Element Text="ITEM"/> … </Cell> </Column>
          <Column> … <Text_Element Text="PARTNO"/> … </Column>
          …
        </Row>
        …
      </Table>
      <Table Table_Type="Revision" …> … </Table>
    </Tables>

i.e. ``Table → Row → Column → Cell → Text_Element[@Text]``. A cell's text is the
concatenation of its ``Text_Element`` ``Text`` attributes (usually one).

No SOLIDWORKS required; real-file capable (it is just XML). Returns ``[]`` for a
file with no ``swXmlContents/Tables`` (e.g. a part, or a drawing with no tables).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from swformat.io.reader import read_document

_TABLES_STREAM = "swXmlContents/Tables"


@dataclass
class DrawingTable:
    """One annotation table decoded from a drawing.

    Attributes:
        table_type: the ``Table_Type`` (e.g. ``"BOM"``, ``"Revision"``,
                    ``"GeneralTable"``, ``"HoleTable"``).
        name:       the ``Display_Name`` (e.g. ``"Bill of Materials12"``).
        rows:       the cell grid as a list of rows, each a list of cell-text
                    strings in column order. ``rows[0]`` is typically the header
                    row (``["ITEM", "PARTNO", "DESCRIPTION", "QTY"]`` for a BOM).
    """

    table_type: str
    name: str
    rows: list[list[str]] = field(default_factory=list)

    @property
    def num_rows(self) -> int:
        return len(self.rows)

    @property
    def num_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    @property
    def header(self) -> list[str]:
        """The first row (column titles), or ``[]`` if the table is empty."""
        return self.rows[0] if self.rows else []

    def as_dicts(self) -> list[dict[str, str]]:
        """The data rows (everything after the header) as dicts keyed by the
        header titles — convenient for a BOM (``{"PARTNO": "TS-...", "QTY": "1"}``).
        Header titles are used verbatim; extra/short cells are tolerated."""
        if not self.rows:
            return []
        head = self.rows[0]
        out: list[dict[str, str]] = []
        for row in self.rows[1:]:
            out.append({head[i] if i < len(head) else f"col{i}": v
                        for i, v in enumerate(row)})
        return out


def _cell_text(column: ET.Element) -> str:
    """Concatenate a column's cell text (its ``Text_Element`` ``Text`` attrs)."""
    return "".join(te.get("Text", "") for te in column.iter("Text_Element"))


def parse_tables_xml(xml_bytes: bytes) -> list[DrawingTable]:
    """Parse the raw ``swXmlContents/Tables`` XML into :class:`DrawingTable` list.

    Robust to the stream being absent/empty (returns ``[]``) and to malformed XML
    (returns ``[]`` rather than raising — a corrupt table stream should not crash a
    corpus scan). Decodes as UTF-8 (the XML declares it).
    """
    if not xml_bytes:
        return []
    try:
        root = ET.fromstring(xml_bytes.decode("utf-8", "ignore"))
    except ET.ParseError:
        return []
    out: list[DrawingTable] = []
    for t in root.findall(".//Table"):
        rows: list[list[str]] = []
        for row in t.findall("Row"):
            rows.append([_cell_text(col) for col in row.findall("Column")])
        out.append(DrawingTable(table_type=t.get("Table_Type", ""),
                                name=t.get("Display_Name", ""), rows=rows))
    return out


def read_tables(path: str | Path, table_type: str | None = None) -> list[DrawingTable]:
    """Return a drawing's tables (BOM / Revision / …) as structured rows.

    Args:
        path:        the drawing file.
        table_type:  if given, return only tables of this ``Table_Type``
                     (case-insensitive, e.g. ``"BOM"``).

    Returns:
        ``list[DrawingTable]`` in document order; ``[]`` if the file has no
        ``swXmlContents/Tables`` stream. No SOLIDWORKS required; real-file capable.
    """
    stream = read_document(path).streams().get(_TABLES_STREAM)
    tables = parse_tables_xml(stream) if stream else []
    if table_type is not None:
        tt = table_type.lower()
        tables = [t for t in tables if t.table_type.lower() == tt]
    return tables


def read_boms(path: str | Path) -> list[DrawingTable]:
    """Convenience: the drawing's Bill-of-Materials tables only."""
    return read_tables(path, table_type="BOM")
