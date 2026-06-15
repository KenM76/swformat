"""``swformat.api.components`` — read an assembly's component tree (and its
per-component state) from the plain-XML ``swXmlContents/COMPINSTANCETREE`` stream,
**without SOLIDWORKS** and **without the CArchive object-map codec**.

A SOLIDWORKS assembly is an OPC package; alongside the binary config-manager
(``Contents/CMgr``) it stores a human-readable XML mirror of its component
instance tree in ``swXmlContents/COMPINSTANCETREE``. Each component instance is a
``<swReference>`` element whose attributes record its state directly:

    <swReference swComponentName="arm" swConfigurationName="Default"
                 swExcludeFromBOM="NO" swFlexible="NO" swHidden="NO"
                 swSuppressed="NO" swIsVirtualComponent="NO"
                 swTransform="m11 m12 ... tz s ..." swModelRef="8" .../>

The on-disk file path is a two-hop id join (the ``<swReference>`` does not carry
the path itself):

    swReference.swModelRef  ->  <swModel id=..  swFileRef=..>  ->  <swFile id=..  swPath="...">

So one parse of this stream yields, for every component instance: name, resolved
file path, referenced configuration, and the exclude-from-BOM / flexible / hidden
/ suppressed / virtual flags + the placement transform — replacing a per-component
COM loop (``GetExcludeFromBOM2`` + ``GetParent``/``GetPathName`` …) with a single
~50–100 ms read, no SW, no license.

Scope / honesty (verified, SW 2026):
  * ``swExcludeFromBOM`` flips ``NO``->``YES`` exactly when a component is excluded
    (twin-controlled diff-pair) and ``swFlexible`` correctly marks flexible
    instances — i.e. this stream faithfully reflects the SAVED state.
  * It is a **READ-only MIRROR**: editing ``COMPINSTANCETREE`` alone does NOT
    change SOLIDWORKS' behaviour (the authoritative store is the binary
    ``Contents/CMgr``; a COMPINSTANCETREE-only edit is ignored / not SW-clean).
    Treat everything here as read-only.
  * It is the LAST-SAVED state, not live in-memory; and per-component flags are
    tagged with the referenced configuration (``config``), so a config-specific
    flag belongs to that config.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from swformat.compat import warn_streams
from swformat.io.reader import read_document

_STREAM = "swXmlContents/COMPINSTANCETREE"


@dataclass
class Component:
    """One component instance in an assembly's saved component tree."""
    name: str | None
    path: str | None          # resolved on-disk path (via swModelRef -> swFile)
    config: str | None        # referenced configuration name
    exclude_from_bom: bool
    flexible: bool
    hidden: bool
    suppressed: bool
    virtual: bool
    transform: str | None = None      # raw 16-value placement matrix string
    bounding_box: str | None = None   # resolved via swModelRef -> swModel.swBoundingBox
    model_ref: str | None = None


def _attr(seg: str, name: str) -> str | None:
    m = re.search(name + r'="([^"]*)"', seg)
    return m.group(1) if m else None


def _yn(v: str | None) -> bool:
    # SW writes these flags as the literal strings YES / NO.
    return (v or "").strip().upper() == "YES"


def parse_component_tree(xml: bytes | str) -> list[Component]:
    """Parse COMPINSTANCETREE XML bytes/str into a list of :class:`Component`.

    Pure function (no I/O) so it is unit-testable with synthetic XML.
    """
    txt = xml.decode("latin1", "ignore") if isinstance(xml, (bytes, bytearray)) else xml
    if not txt:
        return []
    # id -> file path, and model id -> file id, for the two-hop path join.
    file_path = {_attr(m.group(0), "id"): _attr(m.group(0), "swPath")
                 for m in re.finditer(r"<swFile\b[^>]*>", txt)}
    models = [m.group(0) for m in re.finditer(r"<swModel\b[^>]*>", txt)]
    model_file = {_attr(s, "id"): _attr(s, "swFileRef") for s in models}
    model_bbox = {_attr(s, "id"): _attr(s, "swBoundingBox") for s in models}

    out: list[Component] = []
    for m in re.finditer(r"<swReference\b[^>]*>", txt):
        s = m.group(0)
        mref = _attr(s, "swModelRef")
        path = file_path.get(model_file.get(mref)) if mref else None
        out.append(Component(
            name=_attr(s, "swComponentName") or _attr(s, "swName"),
            path=path,
            config=_attr(s, "swConfigurationName"),
            exclude_from_bom=_yn(_attr(s, "swExcludeFromBOM")),
            flexible=_yn(_attr(s, "swFlexible")),
            hidden=_yn(_attr(s, "swHidden")),
            suppressed=_yn(_attr(s, "swSuppressed")),
            virtual=_yn(_attr(s, "swIsVirtualComponent")),
            transform=_attr(s, "swTransform"),
            bounding_box=model_bbox.get(mref),
            model_ref=mref,
        ))
    return out


def read_component_tree(path: str | Path) -> list[Component]:
    """Read the component tree of an assembly file (``.SLDASM``).

    Returns ``[]`` for a non-assembly or a file lacking the stream (safe to run
    across a mixed corpus).
    """
    streams = read_document(path).streams()
    warn_streams(streams)
    return parse_component_tree(streams.get(_STREAM, b""))


# --- part config tree (swXmlContents/Features) -------------------------------
# A PART's config/model tree — the part analog of COMPINSTANCETREE. Plain XML:
#   <swFile id=.. swDocType="PART" swCreationTime=.. swPath=..>
#   <swModel id=.. swConfigurationName=.. swFileRef=..>
#   <swConfiguration swName=".." swMostRecentConfiguration="YES|NO" ...>
_PART_STREAM = "swXmlContents/Features"


@dataclass
class PartInfo:
    """A part's identity + configuration list, read from swXmlContents/Features."""
    path: str | None
    doc_type: str | None
    creation_time: str | None
    configs: list[str]
    most_recent_config: str | None = None


def parse_part_config_tree(xml: bytes | str) -> PartInfo:
    """Parse swXmlContents/Features into a :class:`PartInfo` (pure)."""
    txt = xml.decode("utf-8", "ignore") if isinstance(xml, (bytes, bytearray)) else xml
    f = re.search(r"<swFile\b[^>]*>", txt)
    file_tag = f.group(0) if f else ""
    configs: list[str] = []
    most_recent: str | None = None
    for m in re.finditer(r"<swConfiguration\b[^>]*>", txt):
        s = m.group(0)
        nm = _attr(s, "swName")
        if nm:
            configs.append(nm)
            if _yn(_attr(s, "swMostRecentConfiguration")):
                most_recent = nm
    return PartInfo(
        path=_attr(file_tag, "swPath"),
        doc_type=_attr(file_tag, "swDocType"),
        creation_time=_attr(file_tag, "swCreationTime"),
        configs=configs,
        most_recent_config=most_recent,
    )


def read_part_config_tree(path: str | Path) -> PartInfo:
    """Read a part's identity + configuration list (``.SLDPRT``)."""
    streams = read_document(path).streams()
    warn_streams(streams)
    return parse_part_config_tree(streams.get(_PART_STREAM, b""))
