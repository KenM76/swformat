"""The binary ``Contents/CusProps`` store: read, round-trip model, writer.

This is the CusProps-specific layer of the M5.1 CArchive work (the generic
framing primitives live in :mod:`swformat.carchive.archive`). The full decode
and its evidence are in ``research/empirical_findings/cusprops_carchive/log.md``
and the spec is ``docs/CARCHIVE.md``. The container hierarchy is
``moCusPropMgr_c → moCusPropContainer_c → moFilePropContainer_c → suObList →
moAdvCusPropList_c → N× moAdvCusProp_c`` (each property record wraps a
value-element: StringElem / PRP / MassProp), followed by a
``moCutListPropContainer_c`` and the archive's close-time object-map dump.

What this module provides, in three groups:

**Read** (no SW):
  - :func:`read_cusprops` — ``{name: value}`` for user props, via the
    robust schema-tolerant signal "a NAME is a CString immediately followed
    by ``00 00 00 00``". Names match ``docProps/custom.xml`` (the XML∩CusProps
    intersection SW surfaces — the M1.5/M2 finding).
  - :func:`read_cutlist_props` — cut-list/weldment resolved values.

**Structural decode + round-trip model** (M5.1 Step 1, no SW):
  - :func:`parse_container_header`, :func:`walk_user_records` — rigorous
    CArchive walks (object-map index tracked) over the header and records.
  - :func:`check_user_list_coverage` — the no-orphan-bytes gate for the
    user-record region.
  - :func:`reserialize_header`, :func:`serialize_user_records` — rebuild
    those regions *structurally* (byte-exact), and :func:`roundtrip` ties
    them together so ``roundtrip(data) == data`` (header + all record
    variants structural; cut-list/close-time-dump tail still verbatim).

**Write** (no SW; SW-verified output):
  - :func:`make_text_record`, :func:`add_text_properties` — append text
    properties, **byte-exact vs SOLIDWORKS** on the proven diff-pair.

NOTE: CString lengths are UTF-16 code units (see :mod:`swformat.carchive.cstring`);
schema is 1 for these classes on SW 2026 (re-verify cross-version).
"""
from __future__ import annotations

import struct

from swformat.carchive.archive import (
    TagKind,
    read_class_def,
    read_object_tag,
    write_class_def,
    write_object_tag,
)
from swformat.carchive.cstring import encode_cstring, read_cstring
from swformat.streams.custom_props import CustomPropsError

__all__ = [
    "CustomPropsError",
    "add_text_properties",
    "check_user_list_coverage",
    "cusprops_body_len",
    "make_text_record",
    "parse_container_header",
    "read_cusprops",
    "read_cutlist_props",
    "reserialize_header",
    "roundtrip",
    "serialize_user_records",
    "set_cutlist_value",
    "walk_cutlist_records",
    "walk_user_records",
]

_LIST_TAG = b"moAdvCusPropList_c"
_CUTLIST_TAG = b"moCutListPropContainer_c"
_NAME_TERMINATOR = b"\x00\x00\x00\x00"


def read_cusprops(data: bytes) -> dict[str, str]:
    """Return ``{name: value}`` for user custom properties in a CusProps blob.

    ``data`` is the *decompressed* ``Contents/CusProps`` stream. Returns an
    empty dict if the user-property section is absent. The leading codepage
    entry (empty name) is skipped.
    """
    start = data.find(_LIST_TAG)
    if start < 0:
        return {}
    end = data.find(_CUTLIST_TAG)
    if end < 0:
        end = len(data)

    out: dict[str, str] = {}
    i = start + len(_LIST_TAG)
    while i < end - 3:
        cs = read_cstring(data, i)
        if cs is None:
            i += 1
            continue
        text, nxt = cs
        # A NAME is a CString immediately followed by the 4-byte terminator.
        if text and data[nxt : nxt + 4] == _NAME_TERMINATOR:
            value = ""
            vcs = read_cstring(data, nxt + 4)
            if vcs is not None:
                value = vcs[0]
            out[text] = value
            i = nxt + 4
        else:
            i = nxt  # skip this CString (a value/resolved/formula)
    return out


def parse_container_header(data: bytes) -> tuple[int, int, dict[int, str]]:
    """Walk the CusProps outer object nesting → (n_props, first_record_off, class_map).

    Rigorously parses (via the CArchive primitives, not string search) the
    container chain that wraps the user-property list::

        NEW_CLASS moCusPropMgr_c        + 16-byte body (4×u32; [0]=mgr count)
        NEW_CLASS moCusPropContainer_c  (body = its child object, no scalars)
        NEW_CLASS moFilePropContainer_c (body = its child object)
        NEW_CLASS suObList              + u16 element-count
        NEW_CLASS moAdvCusPropList_c    + u16 property-count  ← n_props
        → first moAdvCusProp_c record starts here              ← first_record_off

    ``class_map`` maps the CArchive object-map index (1-based, in encounter
    order) to class name for these five classes. Raises
    :class:`CustomPropsError` if the stream doesn't match this shape.
    This is the structural foundation the M5.1 writer builds on (it needs the
    object-map indices); the per-record body walk is the remaining piece.
    """
    off = 0
    idx = 1
    class_map: dict[int, str] = {}

    def _new_class(o: int) -> int:
        nonlocal idx
        kind, _, o = read_object_tag(data, o)
        if kind is not TagKind.NEW_CLASS:
            raise CustomPropsError(f"expected NEW_CLASS at {o - 2}, got {kind}")
        _schema, name, o = read_class_def(data, o)
        class_map[idx] = name
        idx += 1
        return o, name

    off, n0 = _new_class(off)
    if n0 != "moCusPropMgr_c":
        raise CustomPropsError(f"root class is {n0!r}, not moCusPropMgr_c")
    # Empty store: moCusPropMgr_c is followed by an 0xFFFFFFFF "no properties"
    # sentinel (e.g. a part with zero custom properties) instead of the
    # count + container chain. Return n_props=0 without walking further.
    if struct.unpack_from("<I", data, off)[0] == 0xFFFFFFFF:
        return 0, off, class_map
    off += 16  # moCusPropMgr_c scalar body (count, 1, 1, 0)
    off, _ = _new_class(off)  # moCusPropContainer_c
    off, _ = _new_class(off)  # moFilePropContainer_c
    off, _ = _new_class(off)  # suObList
    off += 2  # suObList element count (u16)
    off, n4 = _new_class(off)  # moAdvCusPropList_c
    if n4 != "moAdvCusPropList_c":
        raise CustomPropsError(f"expected moAdvCusPropList_c, got {n4!r}")
    n_props = struct.unpack_from("<H", data, off)[0]
    off += 2
    return n_props, off, class_map


# ---------------------------------------------------------------------------
# moAdvCusProp_c RECORD LAYOUT (one per user property) — self-contained spec.
# Decoded by SW diff-pairs; full evidence in
# research/empirical_findings/cusprops_carchive/log.md. SW 2026 (schema=1).
#
#   [wrap tag]   NEW_CLASS(moAdvCusProp_c) on the FIRST record,
#                else CLASS_REF 0x0B (moAdvCusProp_c's class-map index)
#   [flag:u16]   0 = no value-element ; 1 = a value-element child follows
#   [value-element child]  (present iff flag==1):
#       [elem tag]  NEW_CLASS(<elemclass>) first use of that class, else CLASS_REF
#       [elem body] depends on <elemclass> — see _ELEM_BODY:
#           moCusPropPRP_c         : <formula:CString> <resolved:CString> FF FF FF FF
#           moCusPropStringElem_c  : <value:CString>                       (no trailer)
#           moCusPropMassPropEle_c : <formula:CString> <resolved:CString>
#                                    08 00 00 00  FF FF FF FF FF FF FF FF
#   [idx:u32]      property's object-map index (0..n_props-1 within the list)
#   [field2:u32]   0x0000000B = parent container's object-map index (user props)
#   [name:CString]
#   [00 00 00 00]
#   [value:CString]
#   [resolved:CString]
#   [wrap trailer] FF FF FF FF 01 00 00 00 00 00 00 00   (12 bytes)
#
# CString = FF FE FF <len:u8> <UTF-16LE × len>  (see carchive.cstring).
# OBJECT-MAP NOTE: each NEW_CLASS consumes TWO map indices (class + object);
# a CLASS_REF/new-object consumes ONE (object). This is why moAdvCusProp_c's
# class-ref is 0x0B (it is the 11th map entry). The walker tracks this so the
# future writer can assign correct indices on insert.
# ---------------------------------------------------------------------------

# Value-element body shapes (after the elem class tag), keyed by elem class.
# NOTE: the wrapper's u16 "flag" is the value-element COUNT (0, 1, 2, …), not a
# boolean — a record can carry several value-elements (e.g. a weldment cut-list
# dimension prop = StringElem + DimEle). Verified on a real v19000 weldment.
_ELEM_BODY = {
    "moCusPropPRP_c": "cstr2_ffff",          # 2 CStrings + FF FF FF FF
    "moCusPropStringElem_c": "cstr1",         # 1 CString, no trailer
    "moCusPropMassPropEle_c": "cstr2_mass",   # 2 CStrings + 08 00 00 00 + 8×FF
    "moCusPropDimEle_c": "cstr2_dim",         # <formula:CStr> <u32><u32> <resolved:CStr> + 22B trailer
    "moCusPropSysDefEle_c": "cstr2_sysdef",   # cut-list system-defined: <formula><resolved> + 12B block
}


def _consume_elem_body(data: bytes, o: int, ename: str) -> int:
    """Advance ``o`` past one value-element body of class ``ename``. Returns new o.

    Body shapes (see :data:`_ELEM_BODY`):
      cstr1      : 1 CString.
      cstr2_ffff : 2 CStrings + 4 bytes (FF FF FF FF).
      cstr2_mass : 2 CStrings + 12 bytes (08 00 00 00 + 8×FF).
      cstr2_dim  : CString + 8 bytes (2×u32 dimension metadata) + CString.
    """
    shape = _ELEM_BODY[ename]
    if shape == "cstr1":
        return read_cstring(data, o)[1]
    if shape == "cstr2_ffff":
        o = read_cstring(data, o)[1]
        o = read_cstring(data, o)[1]
        return o + 4
    if shape == "cstr2_mass":
        o = read_cstring(data, o)[1]
        o = read_cstring(data, o)[1]
        return o + 12
    if shape == "cstr2_dim":
        o = read_cstring(data, o)[1]
        o += 8                                   # 2×u32 dimension metadata
        o = read_cstring(data, o)[1]
        return o + 22                            # 22-byte dimension-linkage trailer
    if shape == "cstr2_sysdef":
        o = read_cstring(data, o)[1]             # formula
        o = read_cstring(data, o)[1]             # resolved
        return o + 12                            # [u16=4][NULL][u16 item_id][NULL][FFFFFFFF]
    raise CustomPropsError(f"unknown value-element body shape {shape!r} for {ename!r}")


def _emit_elem_body(data: bytes, o: int, ename: str, out: bytearray) -> int:
    """Structurally re-emit one value-element body into ``out``; return new o.

    Re-encodes the body's CStrings (validating the codec + their positions) and
    emits the fixed/scalar bytes — constants for the known trailers, raw for the
    DimEle dimension-metadata u32s. Mirror of :func:`_consume_elem_body`.
    """
    shape = _ELEM_BODY[ename]

    def _cstr() -> None:
        """Re-encode one CString at the current offset; advance ``o``."""
        nonlocal o
        s, o = read_cstring(data, o)
        out.extend(encode_cstring(s))

    if shape == "cstr1":
        _cstr()
        return o
    if shape == "cstr2_ffff":
        _cstr()
        _cstr()
        out.extend(b"\xff\xff\xff\xff")
        return o + 4
    if shape == "cstr2_mass":
        _cstr()
        _cstr()
        out.extend(b"\x08\x00\x00\x00" + b"\xff" * 8)
        return o + 12
    if shape == "cstr2_dim":
        _cstr()
        out.extend(data[o:o + 8])              # dimension metadata (2×u32), raw
        o += 8
        _cstr()
        out.extend(data[o:o + 22])             # dimension-linkage trailer, raw
        return o + 22
    if shape == "cstr2_sysdef":
        _cstr()                                # formula
        _cstr()                                # resolved
        out.extend(data[o:o + 12])             # [u16][NULL][u16 item_id][NULL][FFFFFFFF], raw
        return o + 12
    raise CustomPropsError(f"unknown value-element body shape {shape!r} for {ename!r}")


def walk_user_records(data: bytes) -> tuple[list[dict], int]:
    """Walk the user-property records → ``(records, end_offset)``.

    Each record dict has ``name``, ``value``, and ``start``/``end`` byte
    offsets (its exact span in ``data``). ``end_offset`` is where the record
    list ends (the suObList terminator ``00 00`` that precedes the
    ``moCutListPropContainer_c``, or the trailing terminators for docs with no
    cut list). Returns ``([], header_end)`` for the empty store.

    This is the rigorous segmentation the M5.1 writer needs: exact record
    boundaries plus a correctly-tracked CArchive object-map counter. The
    per-record body layout (4 value-element variants) is documented in the
    cusprops hypothesis log and verified by ``test_carchive`` (the walk lands
    exactly on the list terminator and reproduces the region byte-for-byte).
    """
    # Empty store: moCusPropMgr_c + 0xFFFFFFFF sentinel, no records.
    n_props, first_off, _ = parse_container_header(data)
    if n_props == 0:
        return [], first_off

    # Re-derive the object-map counter at the first record by re-walking the
    # header with full (class + object) index accounting.
    o = 0
    idx = 1
    class_map: dict[int, str] = {}

    def read_obj_header() -> str | None:
        """Advance past an object's tag (+class def). Returns class name."""
        nonlocal o, idx
        kind, val, o = read_object_tag(data, o)
        if kind is TagKind.NULL:
            return None
        if kind is TagKind.NEW_CLASS:
            _schema, name, o = read_class_def(data, o)
            class_map[idx] = name
            idx += 1            # class index
        elif kind is TagKind.CLASS_REF:
            name = class_map[val]
        else:  # OBJECT_REF — no body
            return class_map.get(val)
        idx += 1                # object index
        return name

    read_obj_header()           # moCusPropMgr_c
    o += 16                     # mgr scalar body
    read_obj_header()           # moCusPropContainer_c
    read_obj_header()           # moFilePropContainer_c
    read_obj_header()           # suObList
    o += 2                      # suObList element count (u16)
    read_obj_header()           # moAdvCusPropList_c
    o += 2                      # property count (u16)

    records: list[dict] = []
    for _ in range(n_props):
        start = o
        read_obj_header()          # moAdvCusProp_c (NEW_CLASS or ref)
        flag = struct.unpack_from("<H", data, o)[0]   # value-element COUNT
        o += 2
        for _ in range(flag):
            ename = read_obj_header()      # value-element class
            o = _consume_elem_body(data, o, ename)
        o += 8                              # idx:u32 + field2:u32
        pname, o = read_cstring(data, o)
        o += 4                              # 00 00 00 00
        pval, o = read_cstring(data, o)
        _resolved, o = read_cstring(data, o)
        o += 12                             # wrapper trailer
        records.append({"name": pname, "value": pval, "start": start, "end": o})
    return records, o


def reserialize_header(data: bytes) -> bytes:
    """Structurally rebuild the CusProps container-chain header, byte-exact.

    This is the **structural** (not slice-based) half of the M5.1 Step-1
    round-trip gate for the *header* region: it re-emits the header purely
    from parsed fields, so a wrong model of the layout — class order, the
    ``moCusPropMgr_c`` scalar body, a count's width/position, a schema — makes
    the result differ from ``data[:first_record_off]`` and the round-trip test
    fails. (A slice-based round-trip would pass trivially and validate
    nothing.) Pure Python; no SOLIDWORKS.

    Header layout (verified SW 2026, all classes schema 1; schemas are read
    from the stream, not assumed, for cross-version tolerance)::

        NEW_CLASS moCusPropMgr_c        <16-byte body: 4×u32 = [count,1,1,0]>
        NEW_CLASS moCusPropContainer_c
        NEW_CLASS moFilePropContainer_c
        NEW_CLASS suObList              <u16 element count>
        NEW_CLASS moAdvCusPropList_c    <u16 property count = n_props>

    For the **empty store** the header is just ``NEW_CLASS moCusPropMgr_c``
    (the ``0xFFFFFFFF`` "no properties" sentinel that follows is part of the
    trailing region, not the header) — matched here so the round-trip holds
    for empty stores too.

    Returns the reconstructed header bytes. Compare against
    ``data[:parse_container_header(data)[1]]`` (the test does exactly this).
    """
    def _class(o: int) -> tuple[int, str, int, int]:
        kind, _, o2 = read_object_tag(data, o)
        if kind is not TagKind.NEW_CLASS:
            raise CustomPropsError(f"expected NEW_CLASS at {o}, got {kind}")
        schema, name, o3 = read_class_def(data, o2)
        return schema, name, o3, o

    out = bytearray()

    def _emit_class(schema: int, name: str) -> None:
        out.extend(write_object_tag(TagKind.NEW_CLASS))
        out.extend(write_class_def(schema, name))

    n_props, first_off, _ = parse_container_header(data)

    # moCusPropMgr_c (+ either 16-byte body, or — empty store — nothing here).
    schema, name, off, _ = _class(0)
    if name != "moCusPropMgr_c":
        raise CustomPropsError(f"root class {name!r} != moCusPropMgr_c")
    _emit_class(schema, name)
    if n_props == 0:
        # empty store: header is just the mgr class def (sentinel is tail).
        if len(out) != first_off:
            raise CustomPropsError(
                f"empty-store header len {len(out)} != first_off {first_off}"
            )
        return bytes(out)

    out.extend(data[off:off + 16])  # mgr scalar body [count,1,1,0]
    off += 16

    for expect in ("moCusPropContainer_c", "moFilePropContainer_c"):
        schema, name, off, _ = _class(off)
        if name != expect:
            raise CustomPropsError(f"expected {expect}, got {name!r}")
        _emit_class(schema, name)

    schema, name, off, _ = _class(off)  # suObList
    if name != "suObList":
        raise CustomPropsError(f"expected suObList, got {name!r}")
    _emit_class(schema, name)
    out.extend(data[off:off + 2])  # suObList element count (u16)
    off += 2

    schema, name, off, _ = _class(off)  # moAdvCusPropList_c
    if name != "moAdvCusPropList_c":
        raise CustomPropsError(f"expected moAdvCusPropList_c, got {name!r}")
    _emit_class(schema, name)
    out.extend(data[off:off + 2])  # property count (u16)
    off += 2

    return bytes(out)


def serialize_user_records(data: bytes) -> bytes:
    """Re-emit the user-record span ``[first_off, end_off)`` *structurally*.

    Walks the ``n_props`` ``moAdvCusProp_c`` records and rebuilds each from its
    decoded parts rather than slicing the original bytes, so a wrong model is
    falsified byte-for-byte by the round-trip gate. The parts that genuinely
    test the decode are re-emitted from the model:

    - **object tags** — NEW_CLASS (first occurrence of the wrapper / each
      value-element class) vs CLASS_REF (subsequent), with class defs rebuilt
      via :func:`write_class_def`; the shared object-map index is tracked so
      CLASS_REF tags resolve to the right class name (and hence body shape);
    - **every CString** — name, value, resolved, and the value-element body
      string(s) — re-encoded via :func:`encode_cstring`, proving the CString
      codec is bijective on real corpus data and that each string was located
      correctly;
    - **fixed markers** — the post-name ``00000000``, the 12-byte wrapper
      trailer, and the per-variant element trailers (PRP ``FFFFFFFF``;
      MassProp ``08000000`` + 8×``FF``) — emitted as the decoded constants, so
      a record that deviated would fail the gate.

    Pure scalars whose value we simply round-trip (``idx``/``field2`` u32s,
    the ``flag`` u16) are carried as-is — re-packing them would validate
    nothing beyond slicing. Returns ``b""`` for the empty store.

    Used by :func:`roundtrip`; validated against ``data[first_off:end_off]``
    by ``test_serialize_user_records_byte_exact``. Covers all variants present
    in the corpus: ``novalue`` (flag 0), ``StringElem``, ``PRP``, ``MassProp``.
    """
    n_props, _first_off, _ = parse_container_header(data)
    if n_props == 0:
        return b""

    o = 0
    idx = 1
    class_map: dict[int, str] = {}

    def _hdr() -> None:
        """Advance past a header object (tag [+ class def]); track the map."""
        nonlocal o, idx
        kind, _val, o = read_object_tag(data, o)
        if kind is TagKind.NULL:
            return
        if kind is TagKind.NEW_CLASS:
            _schema, name, o = read_class_def(data, o)
            class_map[idx] = name
            idx += 1
        idx += 1

    _hdr()          # moCusPropMgr_c
    o += 16
    _hdr()          # container
    _hdr()          # filecontainer
    _hdr()          # suObList
    o += 2          # suObList element count
    _hdr()          # moAdvCusPropList_c
    o += 2          # property count

    out = bytearray()

    def _emit_tag() -> str:
        """Consume an object tag, re-emit it structurally, return its class name."""
        nonlocal o, idx
        kind, val, o = read_object_tag(data, o)
        if kind is TagKind.NEW_CLASS:
            schema, name, o = read_class_def(data, o)
            out.extend(write_object_tag(TagKind.NEW_CLASS))
            out.extend(write_class_def(schema, name))
            class_map[idx] = name
            idx += 1
        else:  # CLASS_REF (records always use class-refs / new-classes, not obj-refs)
            name = class_map[val]
            out.extend(write_object_tag(TagKind.CLASS_REF, val))
        idx += 1
        return name

    for _ in range(n_props):
        _emit_tag()                            # moAdvCusProp_c wrapper
        flag = struct.unpack_from("<H", data, o)[0]
        o += 2
        out.extend(struct.pack("<H", flag))    # value-element COUNT
        for _ in range(flag):
            ename = _emit_tag()                # value-element class
            o = _emit_elem_body(data, o, ename, out)
        out.extend(data[o:o + 8])              # idx:u32 + field2:u32 (scalars)
        o += 8
        pname, o = read_cstring(data, o)
        out.extend(encode_cstring(pname))
        out.extend(b"\x00\x00\x00\x00")
        o += 4
        pval, o = read_cstring(data, o)
        out.extend(encode_cstring(pval))
        pres, o = read_cstring(data, o)
        out.extend(encode_cstring(pres))
        out.extend(b"\xff\xff\xff\xff\x01\x00\x00\x00\x00\x00\x00\x00")
        o += 12

    return bytes(out)


def roundtrip(data: bytes) -> bytes:
    """Reassemble a CusProps stream from the M5.1 Step-1 model → must equal input.

    The Step-1 model partitions the stream into three regions and rebuilds it:

    1. **Header** — re-emitted *structurally* by :func:`reserialize_header`
       (container-class chain + scalar body + counts), so its correctness is
       genuinely tested, not assumed.
    2. **User records** — the ``moAdvCusProp_c`` span ``[first_off, end_off)``,
       re-emitted *structurally* by :func:`serialize_user_records` (object
       tags + class defs + every CString rebuilt from the decoded model; all
       variants — novalue / StringElem / PRP / MassProp — covered).
    3. **Tail** — everything from the ``suObList`` terminator onward (the
       cut-list container + the CArchive close-time object-map dump), carried
       verbatim as an opaque span (the **tail-bytes invariant**: never drop or
       silently reinterpret bytes we don't yet model).

    ``roundtrip(B) == B`` is the Step-1 acceptance gate. As region 3 gains a
    structural writer, its verbatim span shrinks and the same gate keeps it
    honest. Raises :class:`CustomPropsError` if reassembly drifts from the
    input (with the first differing offset, for diagnosis).
    """
    n_props, first_off, _ = parse_container_header(data)
    if n_props == 0:
        header = reserialize_header(data)
        out = header + data[first_off:]
    else:
        _records, end_off = walk_user_records(data)
        header = reserialize_header(data)
        out = header + serialize_user_records(data) + data[end_off:]
    if out != data:
        # find first divergence for a useful error
        n = min(len(out), len(data))
        i = next((k for k in range(n) if out[k] != data[k]), n)
        raise CustomPropsError(
            f"roundtrip mismatch at byte {i} "
            f"(len out={len(out)} vs in={len(data)})"
        )
    return out


def check_user_list_coverage(data: bytes) -> dict:
    """Verify the structured walk accounts for every byte of the user list.

    This is the **M5.1 Step-1 falsification gate** (see ``docs/CARCHIVE.md``
    §5): a round-trip-faithful reader must consume the whole user-property
    region with *no orphan bytes* and land exactly on the structural boundary
    that follows it. It is the CArchive analogue of the chunk-walker's
    no-orphan-bytes invariant, and it is what proves the per-record
    body-variant logic (StringElem / PRP / MassProp) is correct across the
    **whole corpus**, not just the single diff-pair the decode was derived
    from. Pure Python; no SOLIDWORKS.

    Checks (raises :class:`CustomPropsError` on any violation):

    1. ``walk_user_records`` returns exactly ``n_props`` records.
    2. The records are **contiguous** and the first starts at the header's
       ``first_record_off`` (no gap between header and records, none between
       records).
    3. ``end_off`` lands on the ``suObList`` terminator ``00 00``.
    4. What immediately follows the terminator is a recognised boundary:
       either the ``moCutListPropContainer_c`` container (NEW_CLASS + class
       def) or the document's trailing terminator bytes (no cut list).

    For the **empty store** (``n_props == 0``) only the no-records and
    boundary facts are asserted (the header itself ends at the ``0xFFFFFFFF``
    sentinel, validated by :func:`parse_container_header`).

    Returns a diagnostic report ``dict`` (``n_props``, ``records``,
    ``first_record_off``, ``end_off``, ``total_len``, ``has_cutlist``,
    ``trailing_bytes``) so callers/tests can inspect coverage.
    """
    n_props, first_off, _ = parse_container_header(data)
    records, end_off = walk_user_records(data)

    if len(records) != n_props:
        raise CustomPropsError(
            f"record count {len(records)} != header n_props {n_props}"
        )

    # (2) contiguity + abutment with the header.
    expect = first_off
    for r in records:
        if r["start"] != expect:
            raise CustomPropsError(
                f"non-contiguous user list: record at {r['start']} "
                f"expected {expect}"
            )
        expect = r["end"]
    if records and end_off != expect:
        raise CustomPropsError(
            f"end_off {end_off} != last record end {expect}"
        )

    cut_off = data.find(_CUTLIST_TAG)
    has_cutlist = cut_off >= 0

    # (3)/(4) boundary after the user list.
    if n_props > 0:
        if data[end_off : end_off + 2] != b"\x00\x00":
            raise CustomPropsError(
                f"end_off {end_off} not on suObList terminator "
                f"(found {data[end_off:end_off + 2].hex()})"
            )
        if has_cutlist:
            # terminator (2) then NEW_CLASS(0xFFFF) + schema(u16) + namelen(u16)
            # + 'moCutListPropContainer_c'. Confirm the container tag is right
            # after the terminator so we know the walk stopped at the true list
            # end (a drifting body-shape would miss this).
            after = end_off + 2
            kind, _, after2 = read_object_tag(data, after)
            if kind is not TagKind.NEW_CLASS:
                raise CustomPropsError(
                    f"expected NEW_CLASS for cut-list container after "
                    f"terminator at {after}, got {kind}"
                )
            _schema, name, _ = read_class_def(data, after2)
            if name != _CUTLIST_TAG.decode("ascii"):
                raise CustomPropsError(
                    f"expected moCutListPropContainer_c after user list, "
                    f"got {name!r}"
                )
        else:
            # No cut list: only trailing terminator bytes should remain.
            if data[end_off:].strip(b"\x00") != b"":
                raise CustomPropsError(
                    f"unexpected non-zero bytes after user list at {end_off}: "
                    f"{data[end_off:end_off + 16].hex()}"
                )

    return {
        "n_props": n_props,
        "records": len(records),
        "first_record_off": first_off,
        "end_off": end_off,
        "total_len": len(data),
        "has_cutlist": has_cutlist,
        "trailing_bytes": len(data) - end_off,
    }


def _object_map_walk(data: bytes) -> tuple[dict[str, int], int, int]:
    """Walk header+records tracking the CArchive object map.

    RETURNS ``(class_map, threshold, end_off)``:
    - ``class_map``: ``{class_name: class_map_index}`` using the TRUE
      object-counting indices (each NEW_CLASS consumes a class index AND an
      object index; each CLASS_REF/new-object consumes one). So e.g.
      ``moAdvCusPropList_c→9``, ``moAdvCusProp_c→11``,
      ``moCusPropStringElem_c→17`` on SW-2026 parts.
    - ``threshold``: the object-map counter value at the end of the user list
      — i.e. the first index that belongs to anything serialized AFTER the
      list (the cut-list classes). References with index ≥ threshold are the
      ones that shift when records are inserted.
    - ``end_off``: byte offset of the suObList terminator (the insertion point).

    PRECONDITION: a non-empty store (``parse_container_header`` n_props > 0).
    """
    o = 0
    idx = 1
    cmap: dict[str, int] = {}

    def rd() -> str | None:
        nonlocal o, idx
        kind, val, o = read_object_tag(data, o)
        if kind is TagKind.NULL:
            return None
        if kind is TagKind.NEW_CLASS:
            _sc, name, o = read_class_def(data, o)
            cmap[name] = idx
            idx += 1                         # class index
        elif kind is TagKind.CLASS_REF:
            name = next(n for n, i in cmap.items() if i == val)
        else:
            return None                      # OBJECT_REF: no body
        idx += 1                             # object index
        return name

    rd()            # moCusPropMgr_c
    o += 16
    rd()            # container
    rd()            # filecontainer
    rd()            # suObList
    o += 2          # suObList element count
    rd()            # moAdvCusPropList_c
    o += 2          # property count
    n_props, _, _ = parse_container_header(data)
    for _ in range(n_props):
        rd()                                  # moAdvCusProp_c
        flag = struct.unpack_from("<H", data, o)[0]   # value-element COUNT
        o += 2
        for _ in range(flag):
            ename = rd()
            o = _consume_elem_body(data, o, ename)
        o += 8
        o = read_cstring(data, o)[1]
        o += 4
        o = read_cstring(data, o)[1]
        o = read_cstring(data, o)[1]
        o += 12
    return cmap, idx, o


def make_text_record(wrap_ref: int, elem_ref: int, idx: int, field2: int,
                     name: str, value: str) -> bytes:
    """Encode one text (StringElem) ``moAdvCusProp_c`` record. Byte-exact vs SW.

    Layout (see the record-layout block above): wrapper CLASS_REF, flag=1
    (has value-element), StringElem CLASS_REF, the element's single CString
    value, then ``idx:u32``, ``field2:u32``, name, ``00000000``, value,
    resolved (== value), and the 12-byte trailer. Verified to reproduce
    SOLIDWORKS's own record byte-for-byte.
    """
    return (
        struct.pack("<H", 0x8000 | wrap_ref) + struct.pack("<H", 1)
        + struct.pack("<H", 0x8000 | elem_ref) + encode_cstring(value)
        + struct.pack("<I", idx) + struct.pack("<I", field2)
        + encode_cstring(name) + b"\x00\x00\x00\x00"
        + encode_cstring(value) + encode_cstring(value)
        + b"\xff\xff\xff\xff\x01\x00\x00\x00\x00\x00\x00\x00"
    )


def add_text_properties(data: bytes, items: list[tuple[str, str]]) -> bytes:
    """Add text custom properties to a CusProps blob → new CusProps bytes.

    Implements the writer recipe proven byte-exact against SOLIDWORKS
    (`c_B0` + [ZZ_ONE, ZZ_TWO] == SW's `c_B2`; see
    `research/empirical_findings/cusprops_carchive/log.md`):

    1. Insert N new StringElem records at the user-list terminator.
    2. ``moCusPropMgr_c`` property counter (offset 20, u32) ``+= N``.
    3. ``moAdvCusPropList_c`` list count (u16) ``+= N``.
    4. Re-index the cut-list region's class-refs by ``K = 2·N`` (each text
       prop adds a wrapper + value-element to the object map).

    Record params are derived from the file via :func:`_object_map_walk`
    (wrap=moAdvCusProp_c, elem=moCusPropStringElem_c, field2=moAdvCusPropList_c
    class indices; ``idx`` = existing user + cut-list property count + k).

    PRECONDITION: non-empty store (at least one existing property, so the
    container chain + StringElem class exist). Empty-store and define-new-
    StringElem-class cases are not yet handled (raise/return unchanged) — see
    Limits in the log.
    LIMIT: the cut-list re-index scans the post-insertion region for class-ref
    u16s with index in ``[threshold, threshold+20]`` and bumps them by K. This
    is validated byte-exact on the diff-pair; cut-list payloads are
    ASCII-UTF16 + small ints so genuine ``0x80xx`` class-ref tags don't
    collide. RAISES KeyError if required classes are absent (empty store).
    """
    if not items:
        return data
    cmap, threshold, end_off = _object_map_walk(data)
    n_user, _, _ = parse_container_header(data)
    n_cut = len(read_cutlist_props(data))
    wrap = cmap["moAdvCusProp_c"]
    elem = cmap["moCusPropStringElem_c"]
    field2 = cmap["moAdvCusPropList_c"]
    n = len(items)
    k_shift = 2 * n

    recs = b"".join(
        make_text_record(wrap, elem, n_user + n_cut + k, field2, name, value)
        for k, (name, value) in enumerate(items)
    )
    out = bytearray(data[:end_off]) + recs + bytearray(data[end_off:])
    struct.pack_into("<I", out, 20, struct.unpack_from("<I", data, 20)[0] + n)
    lp = out.find(b"moAdvCusPropList_c") + len(b"moAdvCusPropList_c")
    struct.pack_into("<H", out, lp, struct.unpack_from("<H", data, lp)[0] + n)

    # Re-index trailing (cut-list) class-refs whose index shifted by k_shift.
    i = end_off + len(recs)
    while i < len(out) - 1:
        v = struct.unpack_from("<H", out, i)[0]
        if (v & 0x8000) and threshold <= (v & 0x7FFF) <= threshold + 20:
            struct.pack_into("<H", out, i, v + k_shift)
            i += 2
        else:
            i += 1
    return bytes(out)


def read_cutlist_props(data: bytes) -> dict[str, str]:
    """Return ``{name: resolved_value}`` for cut-list / weldment properties.

    Cut-list properties live in the ``moCutListPropContainer_c`` section
    (sheet-metal / weldment items: MATERIAL, QUANTITY, lengths, …). Each
    record is ``<NAME:CString> 00 00 00 00 <FORMULA:CString> <RESOLVED:CString>``
    — so the value we want is the SECOND CString after the name terminator
    (the resolved value, e.g. ``"Plain Carbon Steel"`` / ``"1"``), not the
    first (the ``$PRP``-style formula). Read-only; no SW required.

    NOTE: if a document has multiple cut-list items with the same property
    name, later items overwrite earlier ones in the returned dict (the
    common single-item case is unaffected). Returns ``{}`` if there is no
    cut-list container.

    Implemented via the structural :func:`walk_cutlist_records` walker (anchored
    on the record core ``idx | field2 | NAME | 0x00000000 | value | resolved``),
    which is exact on the staged corpus. This supersedes the earlier loose
    ``<CString><0x00000000>`` scan, which mis-read resolved VALUES (e.g.
    ``"Plain Carbon Steel"``) and ``$PRP`` formula fragments as property NAMES on
    multi-config weldments, and conversely missed dimension props (LENGTH/WIDTH).
    Verified: identical to the old reader on DELETE (v11000) and the synthetic
    weldment, and strictly cleaner + more complete on a 27-config weldment.
    """
    # later occurrence wins (documented behaviour) — plain dict build.
    return {r["name"]: r["resolved"] for r in walk_cutlist_records(data)}


# --------------------------------------------------------------------------- #
# Cut-list property STRUCTURAL walker + VALUE editor (M5.1, SW-verified).
#
# Decoded + SW-verified 2026-06-09 (see
# research/empirical_findings/cusprops_carchive/log.md). A cut-list property
# record in the moCutListPropContainer_c shares the user-property record core:
#     idx:u32 | field2:u32 (parent list index, 0x09/0x0B) | NAME:CString
#     | 0x00000000 | value:CString | resolved:CString
# preceded by a value-element block. For a USER-TEXT property (moCusPropString-
# Elem_c) the element's value (the "elem-value" CString) ends exactly at the
# record's idx offset and is what SOLIDWORKS surfaces as the property value
# (SW-verified: editing only the elem-value changes the value on reopen; editing
# only `value`/`resolved` does not). SYSTEM-DEFINED props (moCusPropSysDefEle_c)
# carry a "...@@@..." formula as their value and are recomputed by SW on
# rebuild — they are not directly editable here.
# --------------------------------------------------------------------------- #

_FORMULA_MARK = "@@@"


def _u32(data: bytes, o: int) -> int:
    return struct.unpack_from("<I", data, o)[0]


def _cstr_at(data: bytes, o: int):
    """read_cstring only if a CString prefix is present at ``o``."""
    return read_cstring(data, o) if data[o : o + 3] == b"\xff\xfe\xff" else None


def walk_cutlist_records(data: bytes) -> list[dict]:
    """Structurally walk the ``moCutListPropContainer_c`` records.

    Returns a list of dicts, one per cut-list property occurrence, each with:
    ``name``, ``value``, ``resolved`` (decoded strings); ``idx``, ``field2``;
    the byte spans ``name_span``/``value_span``/``resolved_span``; the
    ``elem_value_span`` (the authoritative element value CString, or ``None``
    for system-defined records); and ``linked`` (``True`` for formula-driven /
    system-defined props that SOLIDWORKS recomputes — NOT directly editable).

    Anchored on the record core (``idx | field2∈{9,0xB} | NAME | 0x00000000 |
    value | resolved``) — the grammar that generalises across simple and
    config-specific cut-list data (see the hypothesis log). Read-only; no SW.
    """
    start = data.find(_CUTLIST_TAG)
    if start < 0:
        return []
    out: list[dict] = []
    i, n = start, len(data)
    while i < n - 16:
        # field2 is the parent-list object-map index: 0x09/0x0B for the main
        # cut-list lists, but dimension / config-specific records use other
        # indices (e.g. 73/75/1035). Accept any plausible object index
        # (0 < x < 0x8000); the strict NAME / 0x00000000 / value / resolved
        # checks below reject coincidental matches. (Verified: catches
        # LENGTH/WIDTH on a 27-config weldment with ZERO false positives.)
        if 0 < _u32(data, i + 4) < 0x8000:
            name = _cstr_at(data, i + 8)
            if (name and name[0] and name[0].isprintable()
                    and data[name[1] : name[1] + 4] == b"\x00\x00\x00\x00"):
                value = _cstr_at(data, name[1] + 4)
                if value:
                    resolved = _cstr_at(data, value[1])
                    if resolved:
                        # elem-value = the CString ending exactly at idx offset i
                        # (present for user-text StringElem records).
                        elem_span = None
                        j = i - 1
                        while j >= max(start, i - 64):
                            if data[j : j + 3] == b"\xff\xfe\xff":
                                c = read_cstring(data, j)
                                if c and c[1] == i:
                                    elem_span = (j, c[1])
                                break
                            j -= 1
                        linked = elem_span is None or _FORMULA_MARK in value[0]
                        out.append({
                            "name": name[0], "idx": _u32(data, i), "field2": _u32(data, i + 4),
                            "name_span": (i + 8, name[1]),
                            "value": value[0], "value_span": (name[1] + 4, value[1]),
                            "resolved": resolved[0], "resolved_span": (value[1], resolved[1]),
                            "elem_value_span": elem_span, "linked": linked,
                        })
                        i = resolved[1]
                        continue
        i += 1
    return out


def set_cutlist_value(data: bytes, name: str, new_value: str) -> bytes:
    """Return ``Contents/CusProps`` bytes with user-text cut-list property
    ``name`` set to ``new_value``.

    Edits the authoritative **elem-value** CString plus the record-core
    ``value`` and ``resolved`` CStrings consistently (so a later SW re-save sees
    no stale copy). SW-verified: SOLIDWORKS reopens the file (errors=0,
    warnings=0) and the cut-list folder reports the new value.

    Targets the FIRST record matching ``name``. (A multi-config weldment may
    carry per-config occurrences; per-config editing is future work.)

    :raises CustomPropsError: if ``name`` is absent, or is a formula-linked /
        system-defined cut-list property (those are recomputed by SOLIDWORKS and
        must be changed via their source, not here).
    """
    for r in walk_cutlist_records(data):
        if r["name"] != name:
            continue
        if r["linked"]:
            raise CustomPropsError(
                f"cut-list property {name!r} is formula-linked/system-defined "
                "(SOLIDWORKS recomputes it); not directly editable"
            )
        enc = encode_cstring(new_value)
        spans = [s for s in (r["elem_value_span"], r["value_span"], r["resolved_span"]) if s]
        out = data
        # splice from highest offset to lowest so earlier spans stay valid
        for s, e in sorted(spans, reverse=True):
            out = out[:s] + enc + out[e:]
        return out
    raise CustomPropsError(f"cut-list property {name!r} not found")


# --------------------------------------------------------------------------- #
# CusProps body-length oracle for the object-map ledger (M5.1 Step 2).
# Lets swformat.carchive.objmap.walk_objmap walk a Contents/CusProps stream:
# returns each object's body extent and the object-map indices its inline
# sub-objects consume. Covers the header chain + property-record wrappers
# (header + user records round-trip byte-exact via the ledger; the cut-list
# region + close-time object-map dump tail are later steps).
# --------------------------------------------------------------------------- #

_CUSPROPS_HEADER_BODY = {
    "moCusPropMgr_c": 16, "moCusPropContainer_c": 0, "moFilePropContainer_c": 0,
    "suObList": 2, "moAdvCusPropList_c": 2,
}


def cusprops_body_len(data: bytes, offset: int, ctx: dict):
    """Body-length oracle for :func:`swformat.carchive.objmap.walk_objmap`.

    For the header chain classes returns the fixed body size. For a property
    wrapper record (e.g. ``moAdvCusProp_c``) parses ``[flag:u16] +
    flag×(inline value-element sub-object) + idx:u32 + field2:u32 + NAME:CString
    + 0x00000000 + value:CString + resolved:CString + 12-byte trailer`` and
    returns ``(body_end, nested_indices)`` where ``nested_indices`` counts the
    object-map indices the inline elements consumed (NEW_CLASS element +2,
    class-ref +1). Registers nested element class names into the ledger's
    ``class_names`` so later class-ref elements resolve.
    """
    cn = ctx["class_name"]
    if cn in _CUSPROPS_HEADER_BODY:
        return offset + _CUSPROPS_HEADER_BODY[cn]
    led = ctx["ledger"]
    o = offset
    flag = struct.unpack_from("<H", data, o)[0]
    o += 2
    nested = 0
    cur = ctx["obj_index"] + 1  # global object-map index just past this wrapper
    for _ in range(flag):
        kind, val, a = read_object_tag(data, o)
        if kind is TagKind.NEW_CLASS:
            _schema, ename, a = read_class_def(data, a)
            led.class_names[cur] = ename
            cur += 2
            nested += 2
        else:  # class-ref element
            ename = led.class_names.get(val)
            cur += 1
            nested += 1
        o = _consume_elem_body(data, a, ename)
    o += 8                              # idx + field2
    o = read_cstring(data, o)[1]        # NAME
    o += 4                              # 0x00000000
    o = read_cstring(data, o)[1]        # value
    o = read_cstring(data, o)[1]        # resolved
    o += 12                             # trailer
    return o, nested
