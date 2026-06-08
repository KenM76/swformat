"""General MFC ``CArchive`` object-map ledger (M5.1 Step 2 — the codec core).

An MFC ``CArchive`` stream is a sequence of objects, each introduced by a 16-bit
tag (see :mod:`swformat.carchive.archive`). As objects are stored/loaded, MFC
maintains a single running **object map** assigning a sequential index (from 1)
to every class runtime AND every object instance, so later references can be
written compactly as a class-ref (``0x8000|classindex``) or object-ref
(``index``). To INSERT/DELETE/MUTATE objects and keep a stream valid, we must
reproduce that index assignment exactly and renumber every reference that moves.

This module provides the generic, reversible **ledger**:

- :func:`walk_objmap` walks the tag stream, assigning the MFC index to each
  object, and records each entry's tag, class, indices, and body byte-span.
  Object *bodies* are class-specific and opaque to this layer, so the caller
  supplies a ``body_len(data, offset, ctx) -> body_end`` oracle (the per-class
  body descriptors live with the stream handlers — CusProps, CMgrHdr2, …).
- :func:`serialize_objmap` rebuilds the byte stream from a ledger. For an
  unmodified ledger this is byte-exact (the round-trip gate).

INDEX RULE (MFC, confirmed empirically across this project):
  running index starts at 1.
  * ``NEW_CLASS``  → assign a CLASS index, then an OBJECT index for the instance
    that follows (net +2).
  * class-ref (``0x8000|cidx``) → a new OBJECT of an already-stored class
    (net +1, the object index).
  * ``OBJECT_REF`` (``0 < tag < 0x8000``) → a back-reference to an existing
    object (net 0, no body).
  * ``NULL`` → a null pointer (net 0, no body).

This is Step A (ledger + byte-exact re-serialize). Steps B+ (reference
consistency check, insert/delete with renumbering) build on it — see
``research/empirical_findings/objmap_codec/log.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from swformat.carchive.archive import (
    TagKind,
    read_class_def,
    read_object_tag,
    write_class_def,
    write_object_tag,
)


class ObjMapError(Exception):
    """Raised when a CArchive object-map walk hits an inconsistency."""


@dataclass
class ObjEntry:
    """One object-map event in encounter order.

    ``kind`` is the tag kind. For NEW_CLASS: ``schema``/``class_name`` are set and
    ``class_index``/``obj_index`` are the two assigned indices. For a class-ref
    new object: ``class_index`` is the referenced class, ``obj_index`` the new
    object's index. For OBJECT_REF: ``ref_index`` is the referenced object. NULL
    carries nothing. ``body`` is the opaque body bytes (empty for NULL/OBJECT_REF).
    """
    kind: TagKind
    schema: int = 0
    class_name: str | None = None
    class_index: int | None = None
    obj_index: int | None = None
    ref_index: int | None = None
    nested_indices: int = 0  # object-map indices consumed by inline sub-objects
    body: bytes = b""


@dataclass
class Ledger:
    entries: list[ObjEntry] = field(default_factory=list)
    # object-map index -> class name (for class-ref resolution / debugging)
    class_names: dict[int, str] = field(default_factory=dict)
    end_offset: int = 0
    # running object-map index AFTER the last walked object — i.e. the next
    # index a newly-inserted object would receive. For a walk stopped at the end
    # of the user-property list this is the renumber THRESHOLD: every reference
    # with index >= next_index shifts when objects are inserted here.
    next_index: int = 1


def walk_objmap(data: bytes, body_len, *, start: int = 0, stop: int | None = None) -> Ledger:
    """Walk the CArchive tag stream in ``data[start:stop]`` into a :class:`Ledger`.

    ``body_len(data, offset, ctx) -> body_end | (body_end, nested_indices)``
    returns the byte offset just past the object body that begins at ``offset``,
    where ``ctx`` is a dict with keys ``kind``, ``class_name``, ``schema``,
    ``class_index``, ``obj_index`` and the live ``ledger``. It is called for
    every object that HAS a body (NEW_CLASS and class-ref objects), never for
    NULL/OBJECT_REF.

    CArchive bodies may serialize **inline sub-objects** (``ar << pSub``), which
    consume their own object-map indices ahead of any subsequent top-level
    object. If a body does so, the oracle must return ``(body_end,
    nested_indices)`` where ``nested_indices`` is the number of object-map
    indices those inline sub-objects consumed (NEW_CLASS sub = +2, class-ref sub
    = +1, NULL/object-ref = 0). Returning a bare ``body_end`` means zero nested
    indices (a flat body). Getting this count right is what keeps later records'
    indices correct — it is the crux of the codec.
    """
    def _split(r):
        return r if isinstance(r, tuple) else (r, 0)
    n = len(data) if stop is None else stop
    led = Ledger()
    idx = 1
    o = start
    while o < n:
        kind, val, after = read_object_tag(data, o)
        if kind is TagKind.NULL:
            led.entries.append(ObjEntry(kind=TagKind.NULL))
            o = after
            continue
        if kind is TagKind.OBJECT_REF:
            led.entries.append(ObjEntry(kind=TagKind.OBJECT_REF, ref_index=val))
            o = after
            continue
        if kind is TagKind.NEW_CLASS:
            schema, cname, body_start = read_class_def(data, after)
            class_index = idx
            obj_index = idx + 1
            led.class_names[class_index] = cname
            ctx = {"kind": kind, "class_name": cname, "schema": schema,
                   "class_index": class_index, "obj_index": obj_index, "ledger": led}
            body_end, nested = _split(body_len(data, body_start, ctx))
            led.entries.append(ObjEntry(
                kind=TagKind.NEW_CLASS, schema=schema, class_name=cname,
                class_index=class_index, obj_index=obj_index,
                nested_indices=nested, body=data[body_start:body_end]))
            idx += 2 + nested
            o = body_end
            continue
        # CLASS_REF: a new object of an already-stored class
        cname = led.class_names.get(val)
        obj_index = idx
        ctx = {"kind": kind, "class_name": cname, "schema": 0,
               "class_index": val, "obj_index": obj_index, "ledger": led}
        body_end, nested = _split(body_len(data, after, ctx))
        led.entries.append(ObjEntry(
            kind=TagKind.CLASS_REF, class_name=cname, class_index=val,
            obj_index=obj_index, nested_indices=nested, body=data[after:body_end]))
        idx += 1 + nested
        o = body_end
    led.end_offset = o
    led.next_index = idx
    return led


def serialize_objmap(led: Ledger) -> bytes:
    """Rebuild the byte stream from a ledger. Inverse of :func:`walk_objmap`;
    byte-exact for an unmodified ledger (the round-trip gate)."""
    out = bytearray()
    for e in led.entries:
        if e.kind is TagKind.NULL:
            out += write_object_tag(TagKind.NULL)
        elif e.kind is TagKind.OBJECT_REF:
            out += write_object_tag(TagKind.OBJECT_REF, e.ref_index)
        elif e.kind is TagKind.NEW_CLASS:
            out += write_object_tag(TagKind.NEW_CLASS)
            out += write_class_def(e.schema, e.class_name)
            out += e.body
        else:  # CLASS_REF
            out += write_object_tag(TagKind.CLASS_REF, e.class_index)
            out += e.body
    return bytes(out)
