"""``swformat.api.dimensions`` — a drawing's DISPLAYED dimension values (real-file).

A SOLIDWORKS drawing maintains a search/keyword index in the
``swXmlContents/KeyWords`` stream (an XML document, after a 1-byte prefix) that
lists every entity — including each **Dimension** with its **displayed value text**
(e.g. ``38"``, ``23 1/2"``). This module extracts those name+value pairs **without
SOLIDWORKS**.

Why this matters: it is the RELIABLE real-file path to drawing dimensions — the one
the byte-signature reader (:func:`swformat.api.sketches.read_sketch_dimensions`)
could not provide (it over/under-matches on real files; the geometry-computed value
lives in the binary object map behind the parked "keystone" walk). The KeyWords
index gives the value SOLIDWORKS actually RENDERED on the sheet, which is exactly
what a training/labelling pipeline wants. So for real drawings, prefer this reader
for dimension VALUES; the sketches reader remains the (synthetic-validated) geometry
path.

Format (verified on a real 28-sheet / 125-dimension production drawing): the
``KeyWords`` XML has ``<Dimension Name="D4">38"</Dimension>`` elements (the value is
the element TEXT; ``Name`` is the per-view dimension id like ``D1``…``Dn`` — NOT
globally unique, so values are returned in document order, not keyed by name). A
1-byte non-XML prefix precedes ``<?xml`` and is skipped.

No SOLIDWORKS required; real-file capable. Returns ``[]`` for a file with no
``KeyWords`` stream (e.g. a part, or an older drawing).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from swformat.io.reader import read_document

_KEYWORDS_STREAM = "swXmlContents/KeyWords"
_XML_DECL = b"<?xml"


@dataclass(frozen=True)
class DrawingDimension:
    """One displayed dimension from a drawing's keyword index.

    Attributes:
        name:  the dimension id (``"D4"``) — per-view, NOT globally unique.
        value: the DISPLAYED value text exactly as rendered (``'38"'``,
               ``'23 1/2"'``, ``'R0.5'``, ``'Ø12'``, …) — units/format as shown.
    """

    name: str
    value: str


def _localname(tag: str) -> str:
    return tag.split("}")[-1]


def parse_dimensions_xml(xml_bytes: bytes) -> list[DrawingDimension]:
    """Parse the ``KeyWords`` stream → list of :class:`DrawingDimension` (document
    order). Skips the 1-byte non-XML prefix; robust to absent/malformed XML (``[]``)."""
    if not xml_bytes:
        return []
    i = xml_bytes.find(_XML_DECL)
    if i < 0:
        return []
    try:
        root = ET.fromstring(xml_bytes[i:].decode("utf-8", "ignore"))
    except ET.ParseError:
        return []
    out: list[DrawingDimension] = []
    for el in root.iter():
        if _localname(el.tag) == "Dimension":
            name = el.get("Name") or el.get("name") or ""
            value = (el.text or "").strip()
            if value:                          # only emit dimensions that carry a value
                out.append(DrawingDimension(name=name, value=value))
    return out


def read_dimension_values(path: str | Path) -> list[DrawingDimension]:
    """Return a drawing's DISPLAYED dimension values (name + value text), document
    order. No SOLIDWORKS required; real-file capable. ``[]`` if the file has no
    ``swXmlContents/KeyWords`` stream.

    This is the reliable real-file dimension reader (vs the synthetic-only,
    keystone-blocked byte-signature ``read_sketch_dimensions``) — it returns the
    values SOLIDWORKS rendered on the sheets.
    """
    stream = read_document(path).streams().get(_KEYWORDS_STREAM)
    return parse_dimensions_xml(stream) if stream else []
