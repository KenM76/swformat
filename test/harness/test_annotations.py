"""Tests for ``swformat.api.annotations`` — drawing text-annotation extraction.

Synthetic tests pin the property-token resolution and the DisplayItems parse;
a fixture cross-check (skip if absent) validates on a real drawing. No SW.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from swformat.api.annotations import (  # noqa: E402
    parse_annotation_text,
    read_annotation_text,
    resolve_annotation_text,
)


def test_resolve_token_with_cache() -> None:
    assert resolve_annotation_text(
        "OF $-COMPANY NAME-$<cache_string>Acme Co</cache_string>.") == "OF Acme Co."
    assert resolve_annotation_text("plain note") == "plain note"
    assert resolve_annotation_text("$-UNRESOLVED-$") == ""          # bare token stripped
    assert resolve_annotation_text("  spaced  ") == "spaced"


def test_parse_displayitems() -> None:
    # NOTE: SW escapes the inline cache_string markup inside the attribute value
    # (&lt;cache_string&gt;…), as real files do; ElementTree unescapes it on read.
    xml = (b'<DisplayItems>'
           b'<DisplayItem Text="GENERAL NOTE 1"/>'
           b'<DisplayItem Text="$-SW-Title-$&lt;cache_string&gt;BRACKET&lt;/cache_string&gt;"/>'
           b'<DisplayItem Text="0"/>'                  # dimension placeholder -> dropped
           b'<DisplayItem Text="GENERAL NOTE 1"/>'     # duplicate -> deduped
           b'<DisplayItem/>'                           # no Text -> skipped
           b'</DisplayItems>')
    assert parse_annotation_text(xml) == ["GENERAL NOTE 1", "BRACKET"]


def test_empty_and_malformed() -> None:
    assert parse_annotation_text(b"") == []
    assert parse_annotation_text(b"<broken") == []


def test_read_annotation_text(monkeypatch) -> None:
    import swformat.api.annotations as a
    xml = b'<DisplayItems><DisplayItem Text="NOTE A"/></DisplayItems>'

    class _Doc:
        def streams(self):
            return {"swXmlContents/CustomProperties": xml}

    monkeypatch.setattr(a, "read_document", lambda _p: _Doc())
    assert read_annotation_text("x") == ["NOTE A"]


def test_no_stream(monkeypatch) -> None:
    import swformat.api.annotations as a
    monkeypatch.setattr(a, "read_document",
                        lambda _p: type("D", (), {"streams": lambda s: {}})())
    assert read_annotation_text("x") == []
