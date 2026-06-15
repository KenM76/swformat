"""``swformat.api.cutlist_xml`` — read a weldment's cut-list properties from the
``docProps/Config-N-Cutlist-Properties.xml`` streams, **without SOLIDWORKS**.

A weldment part stores, per configuration, an XML cut-list summary:

    <Configuration id="0" Name="Default">
      <Feature Name="Cut-List-Item1" Type="Cut-List-Item" id="95">
        <CustomProperty Name="MATERIAL" Type="Text">Plain Carbon Steel</CustomProperty>
        <CustomProperty Name="QUANTITY" Type="Text">1</CustomProperty>
        <Quantity>1</Quantity>
        <ExcludeFromCutlist>FALSE</ExcludeFromCutlist>
        <CutlistType>1</CutlistType>
      </Feature>
    </Configuration>

This is a plain-XML **read mirror** of the binary cut-list store (``Contents/CusProps``
``moCutListPropContainer_c`` — which has SHIPPED value-EDITING via
:mod:`swformat.carchive.cusprops`). For READING cut-list item names, custom
properties (resolved values), quantities, the **exclude-from-cutlist** flag, and
the cut-list type per configuration, this XML is the cheapest path — no CArchive
walk, no SW. One file may have several ``Config-N-Cutlist-Properties.xml`` streams
(one per config); :func:`read_cutlist_xml` reads them all.

Scope / honesty: read-only mirror of the last-saved state. The authoritative
editable store is the binary CusProps stream (see the cut-list value-edit lesson).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from swformat.compat import warn_streams
from swformat.io.reader import read_document

_STREAM_RE = re.compile(r"docProps/Config-\d+-Cutlist-Properties\.xml$", re.IGNORECASE)


@dataclass
class CutlistItem:
    config: str | None
    feature_name: str | None
    feature_type: str | None
    quantity: str | None
    exclude_from_cutlist: bool
    cutlist_type: str | None
    properties: dict[str, str] = field(default_factory=dict)


def _attr(seg: str, name: str) -> str | None:
    m = re.search(name + r'="([^"]*)"', seg)
    return m.group(1) if m else None


def _eltext(scope: str, tag: str) -> str | None:
    m = re.search(r"<" + tag + r"\b[^>]*>(.*?)</" + tag + r">", scope, re.S)
    return m.group(1).strip() if m else None


def parse_cutlist(xml: bytes | str) -> list[CutlistItem]:
    """Parse ONE Config-N-Cutlist-Properties.xml into cut-list items (pure)."""
    txt = xml.decode("utf-8", "ignore") if isinstance(xml, (bytes, bytearray)) else xml
    if not txt:
        return []
    cfg_m = re.search(r"<Configuration\b[^>]*>", txt)
    config = _attr(cfg_m.group(0), "Name") if cfg_m else None
    out: list[CutlistItem] = []
    for fm in re.finditer(r"<Feature\b[^>]*>(.*?)</Feature>", txt, re.S):
        head = re.match(r"<Feature\b[^>]*>", fm.group(0)).group(0)
        body = fm.group(1)
        props: dict[str, str] = {}
        for pm in re.finditer(
            r'<CustomProperty\b[^>]*\bName="([^"]*)"[^>]*>(.*?)</CustomProperty>', body, re.S
        ):
            props[pm.group(1)] = pm.group(2).strip()
        out.append(CutlistItem(
            config=config,
            feature_name=_attr(head, "Name"),
            feature_type=_attr(head, "Type"),
            quantity=_eltext(body, "Quantity"),
            exclude_from_cutlist=(_eltext(body, "ExcludeFromCutlist") or "").upper() == "TRUE",
            cutlist_type=_eltext(body, "CutlistType"),
            properties=props,
        ))
    return out


def read_cutlist_xml(path: str | Path) -> list[CutlistItem]:
    """Read cut-list items across all Config-N-Cutlist-Properties.xml streams."""
    streams = read_document(path).streams()
    warn_streams(streams)
    out: list[CutlistItem] = []
    for name in sorted(streams):
        if _STREAM_RE.search(name):
            out.extend(parse_cutlist(streams[name]))
    return out
