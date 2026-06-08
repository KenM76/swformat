"""Layer 2 handler for ``docProps/custom.xml`` — custom property edits (M2).

WHAT THIS STREAM IS
-------------------
``docProps/custom.xml`` is the OPC-style XML store of a document's custom
properties (PARTNO, MATERIAL, REVISION, WEIGHT, …). M1.5 established that
this XML stream is **authoritative** for the API-visible property values
(the parallel binary ``Contents/CusProps`` can be left stale), so editing
this stream + writing with TOC fixup (``io.writer.write_with_toc``) is
enough for SOLIDWORKS to report the new values on reopen.

XML SHAPE (observed; SW 2026)
-----------------------------
Two ``<propertySection>`` blocks. The user-facing ones live in the section
named ``UserDefinedProperties``; each property looks like::

    <property name="REVISION" pid="5" IsEquation="False" TypeID="30">
        <vt:lpstr>0</vt:lpstr>
        <FPVals><vt:lpstr>0</vt:lpstr></FPVals>   <!-- optional cached copy -->
    </property>

(All on one line, no whitespace, in the real file.) Text properties use
``TypeID="30"`` and ``<vt:lpstr>``. A leading ``name="" pid="1" TypeID="0"``
codepage entry is NOT a user property and is skipped (its TypeID is 0).

DESIGN: SURGICAL STRING EDITS, NOT RE-SERIALIZATION
---------------------------------------------------
We edit the raw XML bytes with targeted regex rather than parse→mutate→
re-serialize via ElementTree. Re-serialization could reorder attributes,
change whitespace, or rewrite the XML declaration — all of which risk
upsetting SOLIDWORKS. Surgical edits change only the bytes that must
change. Values are XML-escaped on write and unescaped on read.

All functions are pure: they take the decompressed ``custom.xml`` bytes and
return new bytes (or a value). Higher-level wiring (read chunk → edit →
``set_stream_payload`` → ``write_with_toc``) lives in ``api/properties.py``.
"""
from __future__ import annotations

import re
from xml.sax.saxutils import escape, unescape

STREAM_NAME = "docProps/custom.xml"

# The user-facing section. Edits are confined to it so we never touch the
# internal DocumentSummaryInformation section.
_USERDEF = b'name="UserDefinedProperties"'

# One property element. Group 1 = name, group 2 = inner XML. We do NOT require
# the TypeID attribute: older SW files omit it (and IsEquation), e.g.
# `<property name="REVISION" pid="5"><vt:lpstr>0</vt:lpstr></property>`, while
# newer files add `IsEquation="False" TypeID="30"`. We identify TEXT properties
# by the presence of a <vt:lpstr> value (the codepage entry uses <vt:i2> and an
# empty name, so it's filtered out).
_PROP_RE = re.compile(rb'<property name="([^"]*)"[^>]*>(.*?)</property>', re.DOTALL)
# The primary value element inside a property.
_VAL_RE = re.compile(rb"<vt:lpstr>(.*?)</vt:lpstr>", re.DOTALL)
# The name-dictionary entry (SW reads its property NAME LIST from these).
_DICT_CLOSE = b"</propertyNameDictionaryElement>"


# Property NAMES sit in an XML ATTRIBUTE (name="..."), so they must be escaped
# for attribute context — which includes the double-quote, NOT covered by the
# default ``escape`` (that only handles & < >). Using a *consistent* attribute
# escaping on BOTH write and regex-search is essential: otherwise a name with
# &, <, > or " would be stored escaped but searched raw (or vice-versa), so it
# could be added but never found/updated/deleted. VALUES, by contrast, sit in
# element CONTENT and use plain ``escape`` (quotes need not be escaped there).
_ATTR_ENTITIES = {'"': "&quot;"}
# Inverse of _ATTR_ENTITIES. NOTE: saxutils.unescape only reverses
# &amp;/&lt;/&gt; by default — it does NOT handle &quot; — so we must pass the
# quote mapping explicitly or a quoted name comes back still-escaped.
_ATTR_UNENTITIES = {"&quot;": '"'}


def _attr_escape(name: str) -> bytes:
    """Escape ``name`` for an XML attribute value (incl. the double-quote)."""
    return escape(name, _ATTR_ENTITIES).encode("utf-8")


def _attr_unescape(name: str) -> str:
    """Inverse of :func:`_attr_escape` (reverses & < > AND &quot;)."""
    return unescape(name, _ATTR_UNENTITIES)


def _named_prop_re(name: str) -> re.Pattern[bytes]:
    """A whole property element by exact name (TypeID-agnostic).

    Matches the *attribute-escaped* form of ``name`` so names containing XML
    metacharacters (stored escaped on disk) are located correctly.
    """
    esc = re.escape(_attr_escape(name))
    return re.compile(rb'<property name="' + esc + rb'"[^>]*>.*?</property>', re.DOTALL)


def _named_dict_re(name: str) -> re.Pattern[bytes]:
    """A whole ``propertyNameDictionaryElement`` entry by exact name.

    Mirrors :func:`_named_prop_re` for the name dictionary, so deletes can keep
    the dictionary and the property elements in sync (SW reads the visible
    property list from the dictionary).
    """
    esc = re.escape(_attr_escape(name))
    return re.compile(
        rb'<propertyNameDictionaryElement name="' + esc + rb'"[^>]*>.*?'
        + re.escape(_DICT_CLOSE),
        re.DOTALL,
    )


class CustomPropsError(Exception):
    """Raised on malformed custom.xml or impossible edits."""


def _userdef_span(xml: bytes) -> tuple[int, int]:
    """Return (section_start, close_tag_pos) of the UserDefinedProperties block."""
    i = xml.find(_USERDEF)
    if i < 0:
        raise CustomPropsError("UserDefinedProperties section not found")
    sec_start = xml.rfind(b"<propertySection", 0, i)
    sec_end = xml.find(b"</propertySection>", i)
    if sec_start < 0 or sec_end < 0:
        raise CustomPropsError("malformed propertySection")
    return sec_start, sec_end


def list_properties(xml: bytes) -> dict[str, str]:
    """Return ``{name: value}`` for all user-defined TEXT properties."""
    s, e = _userdef_span(xml)
    region = xml[s:e]
    out: dict[str, str] = {}
    for m in _PROP_RE.finditer(region):
        name = _attr_unescape(m.group(1).decode("utf-8"))
        if not name:
            continue  # codepage / unnamed internal entry
        vm = _VAL_RE.search(m.group(2))
        if vm is None:
            continue  # not a text property (e.g. <vt:i2> codepage)
        out[name] = unescape(vm.group(1).decode("utf-8"))
    return out


def get_property(xml: bytes, name: str) -> str | None:
    """Return one property's value, or None if absent."""
    return list_properties(xml).get(name)


def _next_pid(xml: bytes) -> int:
    """Next free ``pid`` within the user-defined section (max real pid + 1).

    NOTE: SOLIDWORKS embeds a couple of internal property elements with huge
    pids of the form ``0x0100000N`` (e.g. 16777220, 16777222) that are NOT
    part of the user numbering. We exclude pids ``>= 0x01000000`` so the next
    pid follows the real sequence (matches SW: e.g. real pids 1..12 → 13).
    """
    s, e = _userdef_span(xml)
    pids = [int(p) for p in re.findall(rb'pid="(\d+)"', xml[s:e]) if int(p) < 0x01000000]
    return (max(pids) + 1) if pids else 1


def set_property(xml: bytes, name: str, value: str) -> bytes:
    """Set ``name`` to ``value`` (update if present, else add). Returns new XML.

    For an existing property, replaces BOTH the primary ``<vt:lpstr>`` and the
    cached ``<FPVals>`` copy (if present) so SW and its cache agree. For a new
    property, inserts a fresh ``<property>`` element (TypeID=30, next pid,
    IsEquation=False) just before the section's closing tag.
    """
    enc = escape(value).encode("utf-8")
    pat = _named_prop_re(name)
    m = pat.search(xml)
    if m:
        elem = m.group(0)
        # Replace every <vt:lpstr>...</vt:lpstr> in this element (primary + FPVals).
        new_elem = _VAL_RE.sub(b"<vt:lpstr>" + enc + b"</vt:lpstr>", elem)
        return xml[: m.start()] + new_elem + xml[m.end():]
    # --- ADD a brand-new property -------------------------------------------
    # CRITICAL (decoded 2026-06-08, see cusprops_carchive log): SOLIDWORKS
    # reads the property NAME LIST from the **name dictionary** —
    # `<propertyNameDictionaryElement name="X" pid="N"></...>` entries — NOT
    # from the `<property>` value elements. Adding only a `<property>` element
    # leaves SW's count unchanged (the property is silently ignored). So we
    # must add BOTH a dictionary entry AND the property element, with a correct
    # sequential pid and (newer-schema) an FPVals cached-value copy.
    #
    # Placement (matches SW byte-pattern + groups correctly across repeated
    # calls): the dictionary entry goes right after the LAST existing
    # `</propertyNameDictionaryElement>`; the `<property>` element goes just
    # before `</propertySection>`. For c_B0-style files the two spots coincide,
    # yielding `…[dicts][new dict][new prop]</propertySection>` like SW.
    pid = _next_pid(xml)
    name_enc = _attr_escape(name)
    pid_enc = str(pid).encode()
    s, e = _userdef_span(xml)  # e = offset of </propertySection>
    newer = b"TypeID=" in xml[s:e]
    attrs = b' IsEquation="False" TypeID="30"' if newer else b""
    fpvals = b"<FPVals><vt:lpstr>" + enc + b"</vt:lpstr></FPVals>" if newer else b""
    dict_elem = (
        b'<propertyNameDictionaryElement name="' + name_enc + b'" pid="' + pid_enc
        + b'"></propertyNameDictionaryElement>'
    )
    prop_elem = (
        b'<property name="' + name_enc + b'" pid="' + pid_enc + b'"' + attrs
        + b"><vt:lpstr>" + enc + b"</vt:lpstr>" + fpvals + b"</property>"
    )
    # last existing dictionary entry within the user section (else fall back to e)
    last_dict = xml.rfind(_DICT_CLOSE, s, e)
    dict_at = (last_dict + len(_DICT_CLOSE)) if last_dict >= 0 else e
    # insert dict entry at dict_at, prop element at e (>= dict_at)
    return xml[:dict_at] + dict_elem + xml[dict_at:e] + prop_elem + xml[e:]


def delete_property(xml: bytes, name: str) -> bytes:
    """Remove a property by name. Returns new XML (unchanged if absent).

    Removes BOTH the ``<property>`` value element AND the
    ``propertyNameDictionaryElement`` entry, mirroring :func:`set_property`'s
    add path. SW reads the visible property list from the name dictionary, so a
    delete that left the dictionary entry behind would (a) risk SW still
    listing the now value-less name, (b) make a later re-add of the same name
    create a DUPLICATE dictionary entry, and (c) keep the stale pid in the
    sequence (``_next_pid`` drift). Removing both keeps the two in sync — the
    exact inverse of an add.

    Returns the original bytes unchanged if neither element is present.
    """
    out = xml
    pm = _named_prop_re(name).search(out)
    if pm:
        out = out[: pm.start()] + out[pm.end():]
    dm = _named_dict_re(name).search(out)
    if dm:
        out = out[: dm.start()] + out[dm.end():]
    return out
