"""Layer 3 — MFC CArchive decoding (M5.1, in progress).

The hardest layer: SOLIDWORKS serializes several streams (Contents/CusProps,
Contents/CMgrHdr2, Header2, Contents/Definition, …) as MFC ``CArchive``
binary. This package builds the primitives to read (and eventually write)
that format.

Shipped (see ``docs/CARCHIVE.md`` for the full spec):
- ``archive`` — framing primitives: object tags, class defs, ``ReadCount``.
- ``cstring`` — the MFC ``CStringW`` codec (``FF FE FF <len> <utf16le>``,
  length in UTF-16 code units), read + write.
- ``cusprops`` — ``Contents/CusProps`` read (:func:`read_cusprops`,
  :func:`read_cutlist_props`); the **M5.1 Step-1 round-trip model**
  (:func:`reserialize_header`, :func:`serialize_user_records`,
  :func:`roundtrip` — header + all record variants structural, byte-exact);
  and a byte-exact text-property writer (:func:`add_text_properties`).

Remaining M5.1 (the general object-map + CObList codec for insert/mutate of
arbitrary CArchive — needed for cut-list edits and M3/CMgrHdr2): the
cut-list container body + close-time object-map dump are still carried
verbatim. Needs fresh SW diff-pairs; see
``research/empirical_findings/cusprops_carchive/log.md`` and
``docs/CARCHIVE.md §3/§5``.
"""
from __future__ import annotations

from swformat.carchive.archive import (
    ClassDef,
    TagKind,
    read_class_def,
    read_count,
    read_object_tag,
    scan_class_defs,
    write_class_def,
    write_count,
    write_object_tag,
)
from swformat.carchive.cstring import read_cstring
from swformat.carchive.cusprops import (
    add_text_properties,
    check_user_list_coverage,
    make_text_record,
    parse_container_header,
    read_cusprops,
    read_cutlist_props,
    reserialize_header,
    roundtrip,
    serialize_user_records,
    walk_user_records,
)

__all__ = [
    "ClassDef",
    "TagKind",
    "add_text_properties",
    "check_user_list_coverage",
    "make_text_record",
    "parse_container_header",
    "read_class_def",
    "read_count",
    "read_cstring",
    "read_cusprops",
    "read_cutlist_props",
    "read_object_tag",
    "reserialize_header",
    "roundtrip",
    "scan_class_defs",
    "serialize_user_records",
    "walk_user_records",
    "write_class_def",
    "write_count",
    "write_object_tag",
]
