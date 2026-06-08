"""``swformat.api.annotations`` — extract a drawing's visible TEXT annotations.

A SOLIDWORKS drawing's notes, title-block fields, and other text annotations are
stored as display items in the ``swXmlContents/CustomProperties`` stream (an XML
``<DisplayItems>`` document — despite the stream name). Each ``DisplayItem`` may
carry a ``Text`` attribute holding the annotation's text. This module extracts
that text **without SOLIDWORKS**, resolving SOLIDWORKS property tokens to their
cached resolved values, so a consumer gets the drawing's readable text content.

Why this exists (the consumer): an agent training on a drawing corpus wants the
TEXTUAL content of each drawing — title-block values (company, drawing title),
general notes, and callouts — as clean strings, alongside the BOM
(:mod:`swformat.api.tables`), referenced models
(:mod:`swformat.api.references`), and sheet previews (:mod:`swformat.api.sheets`).

Property-token resolution: a title-block field is stored as
``$-PROPERTY NAME-$<cache_string>RESOLVED VALUE</cache_string>`` inside the
``Text`` attribute — the ``$-…-$`` is the live property reference and the
``<cache_string>`` holds the value SOLIDWORKS last rendered. We resolve each such
token to its cached value, strip any stray ``cache_string`` markup, and drop
unresolved bare ``$-…-$`` tokens — yielding human-readable text.

No SOLIDWORKS required; real-file capable (it is XML). Returns ``[]`` for a file
with no such stream. NOTE: dimension *values* are NOT here — a drawing dimension's
``DimText`` is ``"0"`` (meaning "show the value computed from geometry"), so the
numeric value lives in the binary ``Contents/Definition`` (the parked keystone),
not in this text stream. This module recovers NOTES/title-block/annotation text.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from swformat.io.reader import read_document

_DISPLAYITEMS_STREAM = "swXmlContents/CustomProperties"

# `$-PROPERTY-$<cache_string>VALUE</cache_string>` -> VALUE (the cached resolved
# value). Non-greedy; DOTALL so a value with newlines still resolves.
_TOKEN_WITH_CACHE = re.compile(r"\$-[^$]*-\$<cache_string>(.*?)</cache_string>", re.S)
_BARE_CACHE = re.compile(r"</?cache_string>")
_BARE_TOKEN = re.compile(r"\$-[^$]*-\$")


def resolve_annotation_text(raw: str) -> str:
    """Resolve SOLIDWORKS property tokens in an annotation ``Text`` value to their
    cached values, and strip residual token/cache markup. Whitespace-trimmed."""
    text = _TOKEN_WITH_CACHE.sub(lambda m: m.group(1), raw)
    text = _BARE_CACHE.sub("", text)
    text = _BARE_TOKEN.sub("", text)
    return text.strip()


def parse_annotation_text(xml_bytes: bytes) -> list[str]:
    """Parse the ``DisplayItems`` XML → de-duplicated, resolved annotation strings,
    in document order. Robust: returns ``[]`` for empty/malformed XML (a corrupt
    stream must not crash a corpus scan). Drops empty results and the placeholder
    ``"0"`` (an un-overridden dimension's ``DimText``)."""
    if not xml_bytes:
        return []
    try:
        root = ET.fromstring(xml_bytes.decode("utf-8", "ignore"))
    except ET.ParseError:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for el in root.iter():
        raw = el.get("Text")
        if not raw:
            continue
        value = resolve_annotation_text(raw)
        if not value or value == "0":
            continue
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def read_annotation_text(path: str | Path) -> list[str]:
    """Return a drawing's visible text annotations (notes, title-block values,
    callouts), de-duplicated, property tokens resolved to cached values, in
    document order. ``[]`` if the file has no annotation stream. No SOLIDWORKS
    required; real-file capable.
    """
    stream = read_document(path).streams().get(_DISPLAYITEMS_STREAM)
    return parse_annotation_text(stream) if stream else []
