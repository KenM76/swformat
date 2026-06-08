"""``swformat.api.keywords`` — the full searchable entity index of a drawing.

The ``swXmlContents/KeyWords`` stream is SOLIDWORKS' own search index for a
drawing: an XML catalogue of every entity — dimensions, notes, views, features,
sketches, references, attributes, table cells — each as a ``<Type Name="…">text
</Type>`` element. This module exposes that whole index, grouped by entity type,
**without SOLIDWORKS**.

Why this exists (the consumer): an agent training on a drawing corpus may want the
COMPLETE textual/entity content of each drawing (full-text indexing, search,
content embedding) — not just the high-value structured slices the dedicated
readers give. This is the catch-all complement to:

- :func:`swformat.api.dimensions.read_dimension_values` — the ``Dimension`` slice,
  structured (the recommended reader for dimension values).
- :func:`swformat.api.annotations.read_annotation_text` — resolved title-block/notes
  text (cleaner than the raw ``Note`` index here, which is unresolved + may include
  index noise).
- :func:`swformat.api.views.read_views` — view→model→SHEET (this index's ``View``
  entries give view→model but not the sheet).

So prefer the dedicated readers for those three; use this for COMPLETENESS or for
the entity types without a dedicated reader (Feature / Reference / Sketch / Cell /
Attribute). Returns the index grouped by type, each a list of ``(name, text)``.

A 1-byte non-XML prefix precedes ``<?xml`` and is skipped. No SOLIDWORKS required;
real-file capable. Returns ``{}`` for a file with no ``KeyWords`` stream.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

from swformat.io.reader import read_document

_KEYWORDS_STREAM = "swXmlContents/KeyWords"
_XML_DECL = b"<?xml"

# The index root's direct structural wrappers — not entity types to collect.
_SKIP_TAGS = {"Keywords"}


def _localname(tag: str) -> str:
    return tag.split("}")[-1]


def parse_keyword_index(xml_bytes: bytes) -> dict[str, list[tuple[str, str]]]:
    """Parse the ``KeyWords`` stream → ``{entity_type: [(name, text), …]}``.

    Skips the 1-byte non-XML prefix; robust to absent/malformed XML (``{}``). Every
    element under the root is catalogued by its (namespace-stripped) tag name; the
    ``name``/``Name`` attribute and stripped element text are recorded.
    """
    if not xml_bytes:
        return {}
    i = xml_bytes.find(_XML_DECL)
    if i < 0:
        return {}
    try:
        root = ET.fromstring(xml_bytes[i:].decode("utf-8", "ignore"))
    except ET.ParseError:
        return {}
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for el in root.iter():
        tag = _localname(el.tag)
        if tag in _SKIP_TAGS or el is root:
            continue
        name = el.get("Name") or el.get("name") or ""
        text = (el.text or "").strip()
        if name or text:
            out[tag].append((name, text))
    return dict(out)


def read_keyword_index(path: str | Path) -> dict[str, list[tuple[str, str]]]:
    """Return a drawing's full keyword/entity index grouped by entity type.

    ``{ "Dimension": [("D4", '38\"'), …], "Note": [("", "BREAK ALL SHARP EDGES"),
    …], "View": [("Drawing View227", "example-part.sldprt"), …], "Feature":
    […], "Reference": […], … }``. No SOLIDWORKS required; real-file capable. ``{}``
    if the file has no ``swXmlContents/KeyWords`` stream.
    """
    stream = read_document(path).streams().get(_KEYWORDS_STREAM)
    return parse_keyword_index(stream) if stream else {}


def keyword_index_counts(path: str | Path) -> dict[str, int]:
    """Return ``{entity_type: count}`` — a compact summary of the keyword index."""
    return {k: len(v) for k, v in read_keyword_index(path).items()}
