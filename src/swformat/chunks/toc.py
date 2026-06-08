"""Central TOC helpers (TOC record location + size patching).

⚠️ SUPERSEDED APPROACH — READ THIS FIRST
----------------------------------------
``build_offset_map`` + ``fixup_offset_pointers`` (the offset-``-8`` pointer
rewrite below) were the original M1.5 plan for size-changing edits. **They are
NO LONGER used by the writer.** SW re-verification on 2026-06-08 showed that
re-deflating a chunk to a new ``csz`` shifts every later offset and SOLIDWORKS
**rejects the shifted file (`swFileRequiresRepairError`) even when all `-8`
gap pointers are correctly rewritten** (zero stale pointers, yet rejected) —
SW depends on chunk positions in a way the pointer rewrite does not fully
capture. See the "CRITICAL" entry in
``research/empirical_findings/m1_writer_roundtrip/log.md``.

The writer (``io.writer.serialize_with_toc``) now uses **span preservation**
instead: re-deflate the edited chunk and pad it back to its ORIGINAL ``csz``
so NO offset shifts (SW-verified e=0). The only TOC helpers it still uses are
:func:`_find_toc_record` and :func:`update_toc_sizes` (to patch the modified
chunk's ``usz``; ``csz`` is unchanged). ``fixup_offset_pointers`` /
``build_offset_map`` are kept (and unit-tested) for the eventual
central-directory rewrite that would enable grow-beyond-span edits, but they
are INSUFFICIENT for SW acceptance on their own.

THE PROBLEM THIS SOLVES
-----------------------
M1 proved that re-deflating any stream (changing its compressed size, hence
shifting later chunks' file offsets) makes SOLIDWORKS reject the file with
``swFileLoadError_e.swFileRequiresRepairError`` (0x200000). M1.5 decoded
why: the modern container keeps a **central TOC** in the file's trailing
region — a run of marker records (section_type 0xDF/0xA4/0xBC), one per
stream — and each record stores the chunk's absolute offset, plus a few
self/loose pointers elsewhere. Every such pointer uses the encoding

    stored_uint32  ==  target_file_offset - 8        (OFFSET_BIAS = 8)

and is laid out, within a TOC record, as::

    +0x12 csz   +0x16 usz   +0x1a nsz   +0x1e u32=0   +0x22 u32=0
    +0x26 u16=0   +0x28 (offset-8)   +0x2c name[nsz]   (ROL-encoded)

If a chunk's size changes, that chunk's TOC ``csz``/``usz`` and EVERY
pointer whose target lies after the edit must be updated, or SW reads the
wrong bytes → "corruption".

VERIFIED MECHANISM (research/empirical_findings/m1_writer_roundtrip/)
--------------------------------------------------------------------
End-to-end: editing ``docProps/custom.xml`` (REVISION ``0`` → ``ZZTESTREV``),
re-deflating, updating the TOC ``csz``/``usz`` for that record, and shifting
all ``-8`` pointers after the edit → SW reopens cleanly (e=0 w=0) and
reports the NEW value. ``custom.xml`` is authoritative for custom props
(the binary ``Contents/CusProps`` could be left stale and the edit still won).

SAFETY — ONLY PATCH GAP REGIONS, NEVER PAYLOADS
-----------------------------------------------
Offset pointers live in the TOC and file header — i.e. inside :class:`Gap`
records, never inside a chunk's compressed payload. A naive whole-file scan
for "uint32 V where V+8 is a known offset" risks a coincidental match
*inside* compressed data, which patching would CORRUPT. So
:func:`fixup_offset_pointers` confines edits to gap bytes. The set of
recognised targets (exact known offsets) keeps false positives negligible.
"""
from __future__ import annotations

import struct

from swformat.types import Document

OFFSET_BIAS = 8  # stored value == target_offset - OFFSET_BIAS


def build_offset_map(old: Document, new: Document) -> dict[int, int]:
    """Map every old chunk start-offset → its new start-offset.

    Relies on the writer preserving chunk identity, count and order (it only
    changes sizes), so the i-th chunk in file order corresponds across the
    two documents. Also maps the trailing-directory (largest gap) start, so
    the directory self-pointer is fixed up too.
    """
    old_chunks = sorted(old.chunks, key=lambda c: c.offset)
    new_chunks = sorted(new.chunks, key=lambda c: c.offset)
    if len(old_chunks) != len(new_chunks):
        raise ValueError(
            f"chunk count changed ({len(old_chunks)} -> {len(new_chunks)}); "
            "TOC fixup assumes add/remove-free edits"
        )
    omap = {oc.offset: nc.offset for oc, nc in zip(old_chunks, new_chunks, strict=False)}
    # trailing directory region (largest gap) — its start is itself a pointer target
    if old.gaps and new.gaps:
        old_tg = max(old.gaps, key=lambda g: len(g.raw_bytes))
        new_tg = max(new.gaps, key=lambda g: len(g.raw_bytes))
        omap[old_tg.offset] = new_tg.offset
    return omap


def fixup_offset_pointers(new_data: bytes, new_doc: Document, offset_map: dict[int, int]) -> bytes:
    """Rewrite every ``-8`` offset pointer in the GAP regions of ``new_data``.

    For each gap, scan its bytes for a uint32 ``V`` such that ``V + OFFSET_BIAS``
    is a key in ``offset_map`` (i.e. it points at a chunk/dir whose offset
    moved); replace it with ``offset_map[V + OFFSET_BIAS] - OFFSET_BIAS``.
    Only gap bytes are touched, so compressed payloads can never be corrupted.

    Returns the patched bytes. ``new_doc`` must be the parse of ``new_data``
    (its gaps locate the regions to scan).

    WHY THE SCAN IS UNALIGNED, AND WHY MATCHES ARE READ FROM A SNAPSHOT
    ------------------------------------------------------------------
    Most offset pointers sit at a fixed slot within a TOC record (``+0x28``),
    but the format also has "loose" self/header pointers whose positions we
    have not all enumerated, so the fixup scans every byte offset rather than
    only known slots — missing a stale pointer is what triggers
    ``swFileRequiresRepairError``, so the scan errs toward catching all.

    Two consequences are handled here:

    * **Read matches from an immutable snapshot, write into a separate buffer.**
      The unaligned scan visits overlapping 4-byte windows. If we read from the
      buffer we are mutating, a rewrite at offset ``i`` would change the bytes
      that the windows at ``i+1..i+3`` (and a preceding spurious match's write
      could change the bytes a genuine pointer is later read from) — i.e. a
      cascade that could corrupt a real pointer. Evaluating every window
      against the original ``new_data`` makes the result order-independent and
      cascade-free, and is byte-for-byte identical to the old behavior on real
      files (genuine pointers are aligned and don't overlap each other).
    * **Skip no-op rewrites for chunks that did not move.** If a target maps to
      itself, writing it back is a no-op for a genuine pointer and harmless for
      a coincidental match — skipping it shrinks the write surface without
      changing output.

    The residual (accepted) risk: a coincidental 4-byte window equal to
    ``(moved_offset - 8)`` for a *moved* chunk would be rewritten. With exact
    known offsets as the match set this is negligible and has never been
    observed on the SW-verified corpus; narrowing to known slots would risk
    missing loose pointers, so it is deliberately not done. See the regression
    guards in ``test/harness/test_toc.py``.
    """
    src = new_data            # immutable snapshot: every match is evaluated here
    buf = bytearray(new_data)  # rewrites land here, never re-read during the scan
    # Match on stored value V where (V + BIAS) is an OLD offset key (a chunk
    # or directory whose position moved).
    old_keys = set(offset_map)
    for gap in new_doc.gaps:
        start, end = gap.offset, gap.end
        for i in range(start, end - 3):
            v = struct.unpack_from("<I", src, i)[0]
            tgt = v + OFFSET_BIAS
            if tgt in old_keys:
                new_off = offset_map[tgt]
                if new_off != tgt:  # chunk moved → remap; unmoved → leave as-is
                    struct.pack_into("<I", buf, i, (new_off - OFFSET_BIAS) & 0xFFFFFFFF)
    return bytes(buf)


def _find_toc_record(buf: bytes, doc: Document, key: int, name: str) -> int | None:
    """Return the file offset (si) of the TOC record for ``name``, or None.

    Searches the trailing directory gap for a marker record whose name field
    (at si+0x2c, ``nsz`` bytes, ROL-decoded with ``key``) equals ``name``.
    """
    from swformat.chunks.walker import MARKER, rol_decode

    if not doc.gaps:
        return None
    tg = max(doc.gaps, key=lambda g: len(g.raw_bytes))
    pos = tg.offset
    while True:
        m = buf.find(MARKER, pos)
        if m < 4 or m >= tg.end:
            return None
        si = m - 4
        pos = m + len(MARKER)
        if si + 0x2c > len(buf):
            continue
        nsz = struct.unpack_from("<I", buf, si + 0x1a)[0]
        if not (0 < nsz < 128) or si + 0x2c + nsz > len(buf):
            continue
        if rol_decode(buf[si + 0x2c : si + 0x2c + nsz], key) == name:
            return si
    return None


def update_toc_sizes(buf: bytearray, si: int, csz: int, usz: int) -> None:
    """Patch a TOC record's csz (+0x12) and usz (+0x16) in place."""
    struct.pack_into("<I", buf, si + 0x12, csz)
    struct.pack_into("<I", buf, si + 0x16, usz)
