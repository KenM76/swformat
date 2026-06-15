"""``swformat.api.docprops`` — read OPC document metadata from ``docProps/core.xml``
and ``docProps/app.xml``, **without SOLIDWORKS**.

Every SOLIDWORKS file (part / assembly / drawing) is an OPC package and carries
the two standard Open-Packaging metadata streams that Office files use:

  * ``docProps/core.xml``  (Dublin-Core): ``dc:title``, ``dc:subject``,
    ``dc:creator``, ``cp:keywords``, ``cp:revision``, ``cp:lastModifiedBy``,
    ``dcterms:created`` / ``dcterms:modified`` (ISO-8601 timestamps).
  * ``docProps/app.xml``  (extended): ``Application`` + ``AppVersion`` (the
    SOLIDWORKS build that saved the file), ``Company``, ``Template`` (the
    template the doc was created from), ``TotalTime`` (cumulative edit minutes),
    ``DocSecurity``.

These are element-text XML values (not attributes) in streams SWFormat already
decompresses — so document-level provenance/metadata is a trivial, robust read
(no CArchive codec, no SW). Values are whatever was last SAVED.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from swformat.io.reader import read_document

_CORE = "docProps/core.xml"
_APP = "docProps/app.xml"


@dataclass
class DocMetadata:
    # from core.xml
    title: str | None = None
    subject: str | None = None
    creator: str | None = None
    keywords: str | None = None
    revision: str | None = None
    last_modified_by: str | None = None
    created: str | None = None
    modified: str | None = None
    # from app.xml
    application: str | None = None
    app_version: str | None = None
    company: str | None = None
    template: str | None = None
    total_edit_minutes: str | None = None
    doc_security: str | None = None


def _eltext(txt: str, tag: str) -> str | None:
    m = re.search(r"<" + re.escape(tag) + r"\b[^>]*>(.*?)</" + re.escape(tag) + r">", txt, re.S)
    return m.group(1).strip() if m else None


def parse_metadata(core_xml: bytes | str = b"", app_xml: bytes | str = b"") -> DocMetadata:
    """Parse core.xml + app.xml bytes/str into a :class:`DocMetadata` (pure)."""
    c = core_xml.decode("utf-8", "ignore") if isinstance(core_xml, (bytes, bytearray)) else core_xml
    a = app_xml.decode("utf-8", "ignore") if isinstance(app_xml, (bytes, bytearray)) else app_xml
    return DocMetadata(
        title=_eltext(c, "dc:title"),
        subject=_eltext(c, "dc:subject"),
        creator=_eltext(c, "dc:creator"),
        keywords=_eltext(c, "cp:keywords"),
        revision=_eltext(c, "cp:revision"),
        last_modified_by=_eltext(c, "cp:lastModifiedBy") or _eltext(c, "dc:lastModifiedBy"),
        created=_eltext(c, "dcterms:created"),
        modified=_eltext(c, "dcterms:modified"),
        application=_eltext(a, "Application"),
        app_version=_eltext(a, "AppVersion"),
        company=_eltext(a, "Company"),
        template=_eltext(a, "Template"),
        total_edit_minutes=_eltext(a, "TotalTime"),
        doc_security=_eltext(a, "DocSecurity"),
    )


def read_doc_metadata(path: str | Path) -> DocMetadata:
    """Read document metadata (core.xml + app.xml) from any SOLIDWORKS file."""
    streams = read_document(path).streams()
    return parse_metadata(streams.get(_CORE, b""), streams.get(_APP, b""))
