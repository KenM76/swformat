"""MFC CArchive object-framing primitives (M5.1 foundation).

The SOLIDWORKS property/header streams are MFC ``CArchive`` serializations.
A CArchive stream is a sequence of objects, each introduced by a 16-bit
**tag** (read with :func:`read_object_tag`):

| tag value | meaning |
|---|---|
| ``0x0000`` (``NULL_TAG``)        | a NULL object pointer (no body follows) |
| ``0xFFFF`` (``NEW_CLASS_TAG``)   | a NEW class def follows (schema + name), then the object body. The class is appended to the archive's class/object map. |
| ``tag & 0x8000`` (``BIG_TAG``)   | a reference to an already-seen CLASS, index ``tag & 0x7FFF``; a NEW object of that class follows (body). |
| otherwise (``0 < tag < 0x8000``) | a reference to an already-seen OBJECT, index = ``tag`` (no body). |

A NEW_CLASS def is ``<schema:u16> <namelen:u16> <name: namelen ASCII bytes>``
(read with :func:`read_class_def`). Verified against real
``Contents/CusProps`` bytes, which begin
``FF FF | 01 00 | 0E 00 | "moCusPropMgr_c" | <body…>`` — i.e. NEW_CLASS,
schema 1, the 14-char root class name, then that object's body.

Counts (array/collection sizes) use MFC's ``ReadCount`` escape
(:func:`read_count`): a ``u16``; if ``0xFFFF`` a ``u32`` follows; if that is
``0xFFFFFFFF`` a ``u64`` follows.

**Scope:** this module is the generic *framing* layer only. Walking an
object's *body* needs that class's ``Serialize`` layout (the per-class
models are the remaining M5.1 work; see
``research/empirical_findings/cusprops_carchive/log.md``). These primitives
are pure, reversible, and unit-tested independent of any class body.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from enum import Enum

NULL_TAG = 0x0000
NEW_CLASS_TAG = 0xFFFF
BIG_TAG = 0x8000  # high bit set => class reference (index in low 15 bits)


class TagKind(Enum):
    NULL = "null"
    NEW_CLASS = "new_class"
    CLASS_REF = "class_ref"   # value = referenced class index
    OBJECT_REF = "object_ref"  # value = referenced object index


def read_object_tag(data: bytes, offset: int) -> tuple[TagKind, int, int]:
    """Read a 16-bit object tag at ``offset`` → ``(kind, value, next_offset)``.

    ``value`` is the referenced index for CLASS_REF/OBJECT_REF, else 0.
    """
    tag = struct.unpack_from("<H", data, offset)[0]
    nxt = offset + 2
    if tag == NULL_TAG:
        return TagKind.NULL, 0, nxt
    if tag == NEW_CLASS_TAG:
        return TagKind.NEW_CLASS, 0, nxt
    if tag & BIG_TAG:
        return TagKind.CLASS_REF, tag & 0x7FFF, nxt
    return TagKind.OBJECT_REF, tag, nxt


def write_object_tag(kind: TagKind, value: int = 0) -> bytes:
    """Inverse of :func:`read_object_tag` (for the future M5.1 writer)."""
    if kind is TagKind.NULL:
        return struct.pack("<H", NULL_TAG)
    if kind is TagKind.NEW_CLASS:
        return struct.pack("<H", NEW_CLASS_TAG)
    if kind is TagKind.CLASS_REF:
        if not (0 <= value < 0x8000):
            raise ValueError(f"class index out of range: {value}")
        return struct.pack("<H", BIG_TAG | value)
    if kind is TagKind.OBJECT_REF:
        if not (0 < value < 0x8000):
            raise ValueError(f"object index out of range: {value}")
        return struct.pack("<H", value)
    raise ValueError(kind)


def read_class_def(data: bytes, offset: int) -> tuple[int, str, int]:
    """Read a class def (after a NEW_CLASS tag): ``(schema, name, next_offset)``.

    Layout: ``<schema:u16> <namelen:u16> <name: ASCII × namelen>``.
    """
    schema = struct.unpack_from("<H", data, offset)[0]
    namelen = struct.unpack_from("<H", data, offset + 2)[0]
    start = offset + 4
    end = start + namelen
    name = data[start:end].decode("ascii")
    return schema, name, end


def write_class_def(schema: int, name: str) -> bytes:
    """Inverse of :func:`read_class_def`."""
    enc = name.encode("ascii")
    return struct.pack("<HH", schema, len(enc)) + enc


def read_count(data: bytes, offset: int) -> tuple[int, int]:
    """MFC ``ReadCount`` with the 0xFFFF→u32→u64 escape. ``(count, next)``."""
    v = struct.unpack_from("<H", data, offset)[0]
    if v != 0xFFFF:
        return v, offset + 2
    v = struct.unpack_from("<I", data, offset + 2)[0]
    if v != 0xFFFFFFFF:
        return v, offset + 6
    v = struct.unpack_from("<Q", data, offset + 6)[0]
    return v, offset + 14


def write_count(value: int) -> bytes:
    """Inverse of :func:`read_count`."""
    if value < 0xFFFF:
        return struct.pack("<H", value)
    if value < 0xFFFFFFFF:
        return struct.pack("<HI", 0xFFFF, value)
    return struct.pack("<HIQ", 0xFFFF, 0xFFFFFFFF, value)


@dataclass(frozen=True)
class ClassDef:
    """One NEW_CLASS definition located in a CArchive stream.

    Attributes:
        offset: byte offset of the ``FF FF`` NEW_CLASS tag.
        schema: the class's MFC schema (version) number.
        name:   the class name (e.g. ``"moDisplayDistanceDim_c"``).
    """

    offset: int
    schema: int
    name: str


# A CArchive class name is a C++ identifier; the SOLIDWORKS document classes
# all end ``_c`` (``moDrawing_c``, ``sgSketch`` is a separate non-CArchive name).
# Used to validate a candidate NEW_CLASS framing during the heuristic scan.
_CLASS_NAME_RE = re.compile(rb"[A-Za-z][A-Za-z0-9_]*")


def scan_class_defs(
    data: bytes,
    *,
    name_suffix: bytes = b"",
    min_namelen: int = 3,
    max_namelen: int = 64,
    max_schema: int = 64,
) -> list[ClassDef]:
    """Forward-scan ``data`` for every NEW_CLASS definition, in file order.

    This is the **class-table half of the CArchive "keystone"**: it recovers the
    ordered list of classes a stream defines *without* needing each class's body
    layout. A NEW_CLASS def has the highly distinctive framing
    ``FF FF <schema:u16> <namelen:u16> <name: ASCII×namelen>`` (see the module
    docstring), so it can be located by pattern with a very low false-positive
    rate: we require a plausible ``schema`` (``<= max_schema``), a plausible
    ``namelen`` (``min_namelen``..``max_namelen``), and a name that is a pure
    ASCII C++ identifier (letter-led ``[A-Za-z][A-Za-z0-9_]*``).

    The default accepts ANY such identifier (``name_suffix=b""``). Do NOT default
    to the ``_c`` suffix: several essential SOLIDWORKS document classes do not end
    in ``_c`` — e.g. ``moDrSheet`` (the sheet record), ``sgSketch``, ``suObList``,
    ``su_CStringArray``, ``uoSketch`` — and the keystone needs them. The
    identifier + schema/namelen guards alone are empirically clean: zero false
    positives across the synthetic fixtures AND a 14.5 MB / 246-class real
    production drawing (every hit a valid SW class name, all distinct — NEW_CLASS
    defs are unique). Pass ``name_suffix=b"_c"`` only to get just the ``_c``
    subset.

    IMPORTANT — this is a SCAN, not the authoritative sequential walk. It tells
    you *which* classes are defined and *where* (and therefore their definition
    ORDER), which is exactly what the multi-instance / dimension-enumeration work
    needs as input. It does NOT assign MFC object-map INDICES (that requires
    walking every object body to count map slots — the second half of the
    keystone) and so cannot by itself COUNT class instances (a class is defined
    once via NEW_CLASS, then re-instanced via CLASS_REF tags whose positions are
    only discoverable by the walk). Treat the result as the class inventory, not
    an instance census.

    Verified on the synthetic ``ndim7`` drawing (57 classes, incl.
    ``moLengthParameter_c`` and ``moDisplayDistanceDim_c``) and the ``CusProps``
    fixtures. Returns ``[]`` for a stream with no recognisable class defs.

    Args:
        data:        the (decompressed) CArchive stream bytes.
        name_suffix: required class-name suffix (``b"_c"`` for SW doc classes;
                     pass ``b""`` to accept any identifier).
        min_namelen/max_namelen: bounds on the class-name length.
        max_schema:  reject a candidate whose ``schema`` u16 is implausibly large
                     (a real false-positive ``FF FF`` rarely has a small schema
                     AND a valid-looking name after it).

    Returns:
        ``list[ClassDef]`` in ascending ``offset`` order (i.e. definition order).
    """
    out: list[ClassDef] = []
    n = len(data)
    i = 0
    while True:
        i = data.find(b"\xff\xff", i)
        if i < 0 or i + 6 > n:
            return out
        schema = struct.unpack_from("<H", data, i + 2)[0]
        namelen = struct.unpack_from("<H", data, i + 4)[0]
        name_start = i + 6
        name_end = name_start + namelen
        if (schema <= max_schema and min_namelen <= namelen <= max_namelen
                and name_end <= n):
            name = data[name_start:name_end]
            m = _CLASS_NAME_RE.fullmatch(name)
            if m and name.endswith(name_suffix):
                out.append(ClassDef(offset=i, schema=schema,
                                    name=name.decode("ascii")))
                i = name_end                 # resume past this def
                continue
        i += 2                               # not a class def; advance past the FF FF
