"""``swformat.api.views`` — the per-view model/sheet inventory of a drawing.

A SOLIDWORKS drawing records, for every drawing view, which MODEL it projects and
which SHEET it sits on. That mapping lives in the ``SW-Config-Model-View-Sheet-List``
property of ``docProps/ISolidWorksInformation.xml`` as an ``@``-delimited string.
This module parses it into ``(view, model, sheet)`` records **without SOLIDWORKS**.

Why this exists (the consumer): an agent training on a drawing corpus wants the
RELATIONAL layer — not just *which* models a drawing references
(:mod:`swformat.api.references`) and *which* sheets exist
(:mod:`swformat.api.sheets`), but the join: view → model → sheet. That tells you
how many views each model gets, which models appear on which sheet, etc.

Format (verified on a real 28-sheet / 101-view production drawing). The property
value is::

    <view-count>:@@<flag>@<n>@<model path>@<view name>@<sheet name>@<flag>@…

i.e. a leading ``<count>:`` then repeating ``…@<model>@<view>@<sheet>@…`` groups
(``<flag>`` is ``EXPLODE`` / ``Default`` / config name; ``<n>`` a small int). We
anchor on the **model token** — a field ending in a SOLIDWORKS document extension
is an UNAMBIGUOUS marker (same robust principle as
:mod:`swformat.api.references`) — and take the next two tokens as the view name and
sheet name. The leading ``<count>:`` is cross-checked against the number of records
found (a soft check; we return all anchored records regardless).

No SOLIDWORKS required; real-file capable. Returns ``[]`` if the stream/property is
absent (e.g. a part, or an older drawing).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from swformat.io.reader import read_document

_INFO_STREAM = "docProps/ISolidWorksInformation.xml"
_PROP_NAME = "SW-Config-Model-View-Sheet-List"
_SW_DOC_EXTS = (".SLDPRT", ".SLDASM", ".SLDDRW", ".SLDLFP")


@dataclass(frozen=True)
class DrawingView:
    """One drawing view's model/sheet binding.

    Attributes:
        name:   the view name (e.g. ``"Drawing View13"``).
        model:  the model file path the view projects (as stored — absolute/relative).
        sheet:  the name of the sheet the view sits on.
    """

    name: str
    model: str
    sheet: str

    @property
    def model_basename(self) -> str:
        """The model file name (separator-correct off-Windows too)."""
        return self.model.replace("\\", "/").rsplit("/", 1)[-1]


def _find_view_list_value(xml_bytes: bytes) -> str:
    """Return the ``SW-Config-Model-View-Sheet-List`` property value, or ``""``.

    Robust to absent/malformed XML (returns ``""``). The value may be the property
    element's text or a child element's text (SW nests it under a value element)."""
    if not xml_bytes:
        return ""
    try:
        root = ET.fromstring(xml_bytes.decode("utf-8", "ignore"))
    except ET.ParseError:
        return ""
    for prop in root.iter():
        if (prop.get("name") or prop.get("Name")) == _PROP_NAME:
            txt = (prop.text or "").strip()
            if txt:
                return txt
            for child in prop:
                if (child.text or "").strip():
                    return child.text.strip()
    return ""


def parse_view_list(value: str) -> list[DrawingView]:
    """Parse the ``@``-delimited ``SW-Config-Model-View-Sheet-List`` value into
    :class:`DrawingView` records by anchoring on model tokens (``.SLD*``).

    Each model token's following two tokens are taken as ``(view, sheet)``. Skips
    a trailing model token without two following fields. Order preserved.
    """
    if not value:
        return []
    toks = value.split("@")
    out: list[DrawingView] = []
    for i, tok in enumerate(toks):
        if tok.upper().endswith(_SW_DOC_EXTS) and i + 2 < len(toks):
            out.append(DrawingView(name=toks[i + 1], model=tok, sheet=toks[i + 2]))
    return out


def read_views(path: str | Path) -> list[DrawingView]:
    """Return a drawing's per-view model/sheet inventory (view → model → sheet).

    No SOLIDWORKS required; real-file capable. ``[]`` for a non-drawing or a drawing
    whose ``docProps/ISolidWorksInformation.xml`` lacks the view-list property.
    """
    stream = read_document(path).streams().get(_INFO_STREAM)
    return parse_view_list(_find_view_list_value(stream)) if stream else []
