"""Read the configuration list from ``Contents/CMgrHdr2`` (M3, read side).

``Contents/CMgrHdr2`` is the configuration-manager header — an MFC ``CArchive``
stream (same family as ``Contents/CusProps``) that records a document's
configurations. This module extracts the **configuration names** (and count)
WITHOUT SOLIDWORKS — the first M3 capability (listing configurations otherwise
needs the COM API + a running SW).

STREAM STRUCTURE (decoded 2026-06-08; see
``research/empirical_findings/cmgrhdr2_configs/log.md``)
----------------------------------------------------------------------------
::

    NEW_CLASS dmConfigMgrHeader_c           (schema 1)
    u16  config_count
    per configuration (class dmConfigHeader_c):
        [tag]   NEW_CLASS dmConfigHeader_c on the FIRST config,
                else CLASS_REF <its object-map class index> for the rest
        u32     config index (0,1,2,…)
        NAME:CString                        ← the configuration name
        … (display name, flags, an optional `$PRP:"…"` config-property
           linkage CString, derived-config fields, object-ref trailer) …

The robust extraction keys on each config record's leading tag (the NEW_CLASS
for config 0, a CLASS_REF to ``dmConfigHeader_c`` for the rest) followed by
``<u32 index><NAME:CString>``, taking the first ``config_count`` distinct names
and skipping ``$PRP:``-prefixed linkage strings. **SW-verified**: the result
equals ``IModelDoc2.GetConfigurationNames`` exactly across simple, dimensional
(+`$PRP`), sheet-metal-derived, and multi-config files (suite + manual Layer-3).

This is READ-only; the modify surface (rename / set-active / derived flags) is
later M3 work.
"""
from __future__ import annotations

import struct

from swformat.carchive.archive import TagKind, read_class_def, read_object_tag
from swformat.carchive.cstring import encode_cstring, read_cstring

_HEADER_CLASS = "dmConfigMgrHeader_c"
_CONFIG_CLASS = "dmConfigHeader_c"
_PRP_PREFIX = "$PRP:"


class ConfigMgrError(Exception):
    """Raised when ``Contents/CMgrHdr2`` is absent or not the expected shape."""


def _header_and_count(data: bytes) -> tuple[int, int]:
    """Validate the ``dmConfigMgrHeader_c`` header → ``(config_count, offset)``.

    ``offset`` points just past the count (where the per-config records begin).
    Raises :class:`ConfigMgrError` if the stream isn't a config-manager header.
    """
    kind, _val, off = read_object_tag(data, 0)
    if kind is not TagKind.NEW_CLASS:
        raise ConfigMgrError("CMgrHdr2 does not start with a NEW_CLASS tag")
    _schema, name, off = read_class_def(data, off)
    if name != _HEADER_CLASS:
        raise ConfigMgrError(f"root class is {name!r}, not {_HEADER_CLASS}")
    if off + 2 > len(data):
        raise ConfigMgrError("CMgrHdr2 truncated before config count")
    return struct.unpack_from("<H", data, off)[0], off + 2


def read_configuration_count(data: bytes) -> int:
    """Return the number of configurations from a ``Contents/CMgrHdr2`` blob.

    Reliable across all tested files (the count is a plain ``u16`` right after
    the ``dmConfigMgrHeader_c`` class definition).
    """
    return _header_and_count(data)[0]


def read_configuration_names(data: bytes) -> list[str]:
    """Return the configuration names (in file order) from ``Contents/CMgrHdr2``.

    SW-verified to equal ``GetConfigurationNames``. Walks the per-config
    records: each begins with the ``dmConfigHeader_c`` tag (NEW_CLASS for the
    first config, then CLASS_REF to it) followed by ``<u32 index><NAME:CString>``;
    ``$PRP:``-prefixed config-property-linkage strings are skipped. Stops once
    ``config_count`` distinct names are collected.

    Raises :class:`ConfigMgrError` if the header is malformed. (If fewer than
    ``count`` names are recoverable — an undecoded record variant — returns what
    was found; callers can compare ``len(names)`` to
    :func:`read_configuration_count`.)
    """
    count, off = _header_and_count(data)

    # Track the CArchive object-map index so we know dmConfigHeader_c's class
    # index (each NEW_CLASS consumes a class index + an object index). The
    # header class dmConfigMgrHeader_c took class index 1; dmConfigHeader_c will
    # be the next NEW_CLASS we meet (typically class index 3).
    cfg_class_index: int | None = None
    names: list[str] = []
    pos = off
    n = len(data)
    while pos < n - 4 and len(names) < count:
        kind, val, after_tag = read_object_tag(data, pos)
        is_record_start = False
        body = after_tag
        if kind is TagKind.NEW_CLASS:
            try:
                _schema, cname, body = read_class_def(data, after_tag)
            except (UnicodeDecodeError, struct.error):
                pos += 1
                continue
            if cname == _CONFIG_CLASS:
                # object-map index of this class: header(class 1,obj 2) → 3.
                if cfg_class_index is None:
                    cfg_class_index = 3
                is_record_start = True
        elif kind is TagKind.CLASS_REF and val == (cfg_class_index or 3):
            is_record_start = True

        if is_record_start and body + 4 <= n:
            cs = read_cstring(data, body + 4)  # skip the u32 config index
            if cs is not None and cs[0] and cs[0].isprintable():
                nm = cs[0]
                if not nm.startswith(_PRP_PREFIX) and nm not in names:
                    names.append(nm)
                pos = cs[1]
                continue
        pos += 1
    return names


def rename_configuration(data: bytes, old_name: str, new_name: str) -> bytes:
    """Return ``Contents/CMgrHdr2`` bytes with configuration ``old_name`` renamed.

    Rewrites exactly the target ``dmConfigHeader_c`` record's **NAME** CString
    and the immediately-following **display** CString (when that display string
    still equals ``old_name`` — i.e. it has not been independently set, as on a
    previously-renamed or derived config). Every other byte — the record's
    ``id``/``ordinal``/``disc``/``flag`` fields, the object-tag tail, and the
    archive's terminal stamp — is carried through verbatim (tail-bytes
    invariant).

    WHY this is sufficient (SW-verified 2026 / v19000, see the hypothesis log
    ``research/empirical_findings/cmgrhdr2_configs/log.md`` 2026-06-09):
    a configuration's name is denormalized across several streams (``CMgr``,
    ``SwDocContentMgrInfo``, ``DisplayLists``, the ``docProps`` XMLs, …), but
    ``Contents/CMgrHdr2`` is **authoritative for the configuration-name list**.
    SOLIDWORKS reopens a file whose name was changed only here with no repair
    (errors=0, warnings=0) and reports the new name from
    ``IModelDoc2.GetConfigurationNames``. The other carriers are tolerated stale
    and reconciled/regenerated by SW; the object-map tail and the trailing
    4-byte stamp are NOT validated on open, so they are left untouched.
    Config-specific custom properties survive because they live in
    ``docProps/Config-<INDEX>-Properties.xml`` — keyed by the config's stable
    integer index, never by name.

    The record-boundary walk keys on each record's leading tag (NEW_CLASS
    ``dmConfigHeader_c`` for config 0, CLASS_REF to it for the rest) followed by
    ``<u32 index><NAME:CString>`` — the FIXED record prefix, confirmed universal
    across the staged corpus. The CString match is length-prefixed, so a name
    that is a prefix of another (``CfgB`` vs ``CfgB2``) cannot collide.

    Renames may change the stream length; the caller's writer
    (``write_with_toc``) handles that via span-preservation when the new name
    fits the original compressed span, or — when it overflows — relocate-to-EOF
    on PARTS only (SW-verified). On assemblies an overflowing rename raises
    ``SpanPreserveError`` (grow-beyond-span on asm is not yet SW-valid).

    DISPLAY-CSTRING + DERIVED-PARENT BEHAVIOUR (verified structurally on a
    multi-config corpus file with a derived/renamed record, 2026-06-09): the
    record's display CString is rewritten only when it currently equals
    ``old_name`` (the normal case, where display == name). When it diverges —
    a derived/flat-pattern config, or a config that records a different display
    string — the display CString is left untouched. A consequence: renaming a
    configuration that is the *parent* of a derived config leaves the child's
    parent-reference (its display CString) pointing at the OLD name. This is
    harmless for this function's contract — the configuration-name LIST that SW
    reads (from the NAME fields) is correct, count is preserved, and the stream
    re-parses cleanly. It only matters for derived-parent *semantics*, which are
    deferred to M5.x (see the hypothesis log). All these cases were checked to
    not corrupt the stream (names + count round-trip).

    :raises ConfigMgrError: if the header is malformed or ``old_name`` is not
        found among the configuration records.
    :raises ValueError: if ``new_name`` exceeds the CString length limit
        (>= 255 UTF-16 code units; see :func:`encode_cstring`).
    """
    count, off = _header_and_count(data)

    cfg_class_index: int | None = None
    pos = off
    n = len(data)
    seen = 0
    while pos < n - 4 and seen < count:
        kind, val, after_tag = read_object_tag(data, pos)
        is_record_start = False
        body = after_tag
        if kind is TagKind.NEW_CLASS:
            try:
                _schema, cname, body = read_class_def(data, after_tag)
            except (UnicodeDecodeError, struct.error):
                pos += 1
                continue
            if cname == _CONFIG_CLASS:
                if cfg_class_index is None:
                    cfg_class_index = 3
                is_record_start = True
        elif kind is TagKind.CLASS_REF and val == (cfg_class_index or 3):
            is_record_start = True

        if is_record_start and body + 4 <= n:
            name_off = body + 4  # skip the u32 config index
            cs = read_cstring(data, name_off)
            if cs is not None and cs[0] and cs[0].isprintable():
                nm, name_end = cs
                if not nm.startswith(_PRP_PREFIX):
                    seen += 1
                    if nm == old_name:
                        return _splice_record_name(
                            data, name_off, name_end, old_name, new_name
                        )
                pos = name_end
                continue
        pos += 1

    raise ConfigMgrError(f"configuration {old_name!r} not found")


def _splice_record_name(
    data: bytes, name_off: int, name_end: int, old_name: str, new_name: str
) -> bytes:
    """Rewrite the NAME CString at ``[name_off:name_end]`` and the display
    CString that follows (``name_end + 8`` — past the ordinal+id u32s) when the
    display still equals ``old_name``. Returns the new stream bytes.
    """
    new_cs = encode_cstring(new_name)
    out = bytearray()
    out += data[:name_off]
    out += new_cs
    # The display CString sits after two u32 fields (ordinal, id).
    disp_off = name_end + 8
    disp = read_cstring(data, disp_off) if disp_off + 4 <= len(data) else None
    if disp is not None and disp[0] == old_name:
        disp_end = disp[1]
        out += data[name_end:disp_off]
        out += new_cs
        out += data[disp_end:]
    else:
        # Display name diverges (previously renamed / derived) — leave it.
        out += data[name_end:]
    return bytes(out)
