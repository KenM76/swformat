"""``swformat.api.materials`` — read assigned material(s) and their physical
properties from ``swXmlContents/MATERIALTREE``, **without SOLIDWORKS**.

When a material is applied to a part (or per-body), SOLIDWORKS embeds the full
material definition in ``swXmlContents/MATERIALTREE`` — a **UTF-16-LE** XML stream
(``mstns:materials`` schema). It carries, per assigned material:

  * ``classification`` (e.g. "Steel"), ``name`` (e.g.
    "SOLIDWORKS Materials|Plain Carbon Steel"), ``matid``;
  * a ``<physicalproperties>`` block of named values — ``DENS`` (density),
    ``EX`` (elastic modulus), ``NUXY`` (Poisson's ratio), ``GXY`` (shear
    modulus), ``ALPX`` (thermal expansion), ``SIGYLD`` (yield strength),
    ``SIGXT`` (tensile strength), ``KX`` (conductivity), ``C`` (specific heat), …

So the applied material AND its engineering properties are readable straight from
the bytes — no SOLIDWORKS, no license (the COM API charges a full document open +
``GetMaterialPropertyName2`` / material-database lookups for the same data).

Scope / honesty: this is the material(s) as SAVED in the file's material tree
(read-only). Property values are returned as the raw strings SW stored (SI units,
e.g. density in kg/m^3, moduli in Pa); the consumer can ``float()`` them. The
stream is UTF-16; this module detects the BOM. Empty list for files with no
applied material.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from swformat.io.reader import read_document

_STREAM = "swXmlContents/MATERIALTREE"


@dataclass
class Material:
    classification: str | None
    name: str | None
    matid: str | None
    properties: dict[str, str] = field(default_factory=dict)


def _decode(xml: bytes | str) -> str:
    if isinstance(xml, str):
        return xml
    if xml[:2] == b"\xff\xfe":
        return xml.decode("utf-16-le", "ignore")
    if xml[:2] == b"\xfe\xff":
        return xml.decode("utf-16-be", "ignore")
    return xml.decode("utf-8", "ignore")


def _attr(seg: str, name: str) -> str | None:
    m = re.search(name + r'="([^"]*)"', seg)
    return m.group(1) if m else None


def parse_materials(xml: bytes | str) -> list[Material]:
    """Parse MATERIALTREE bytes/str into a list of :class:`Material` (pure)."""
    txt = _decode(xml)
    if not txt:
        return []
    # (offset, name) of each classification, to attribute each material to one.
    classes = [(m.start(), _attr(m.group(0), "name"))
               for m in re.finditer(r"<classification\b[^>]*>", txt)]
    out: list[Material] = []
    for mm in re.finditer(r"<material\b[^>]*>(.*?)</material>", txt, re.S):
        open_tag = re.match(r"<material\b[^>]*>", mm.group(0)).group(0)
        props: dict[str, str] = {}
        for pm in re.finditer(r'<(\w+)\b[^>]*\bvalue="([^"]*)"[^>]*/>', mm.group(1)):
            props[pm.group(1)] = pm.group(2)
        cls = None
        for pos, nm in classes:
            if pos < mm.start():
                cls = nm
            else:
                break
        out.append(Material(classification=cls, name=_attr(open_tag, "name"),
                            matid=_attr(open_tag, "matid"), properties=props))
    return out


def read_materials(path: str | Path) -> list[Material]:
    """Read applied material(s) + physical properties from a SOLIDWORKS file."""
    streams = read_document(path).streams()
    return parse_materials(streams.get(_STREAM, b""))
