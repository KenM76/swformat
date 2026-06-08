"""Layer-1 writer: :class:`~swformat.types.Document` -> bytes / file (M1).

PURPOSE
-------
Reassemble a SOLIDWORKS modern file from a parsed :class:`Document`,
honouring the **lazy round-trip** contract:

- A chunk whose ``modified_payload is None`` is re-emitted **verbatim**
  (its original header + original compressed bytes). No re-deflate. This
  is what makes an unmodified read->write byte-exact (see M0.5: 9/9
  ``reconstruct() == original``) and sidesteps DEFLATE encoder
  non-determinism.
- A chunk whose ``modified_payload`` is set has had its *logical* content
  changed by a consumer (M2+). The writer re-deflates that payload (raw
  DEFLATE, ``wbits=-15``) and rebuilds the chunk header's ``csz`` (compressed
  size) and ``usz`` (uncompressed size) fields in place. The stream name,
  ``section_type``, ``f1`` and every other header byte are preserved.
- :class:`Gap` records (leading header, padding, trailing TOC) are always
  written verbatim.

HEADER FIELD PATCHING
---------------------
``Chunk.header_bytes`` is the verbatim span ``[offset, data_offset)``. The
size fields live at fixed offsets relative to the chunk start (``si``),
which equal their offsets *within* ``header_bytes``:

    header_bytes[0x12:0x16] = csz (uint32 LE)
    header_bytes[0x16:0x1A] = usz (uint32 LE)

For a modified chunk we copy ``header_bytes``, patch those two fields, and
concatenate with the freshly deflated payload.

⚠ TOC / OFFSET RISK (documented M1 risk, see docs/ROADMAP.md)
-------------------------------------------------------------
Re-deflating a chunk changes ``csz`` and therefore shifts the file offsets
of every subsequent record. If the trailing TOC region (preserved as a
Gap) encodes **absolute chunk offsets**, those would become stale and SW
could reject the file. The lazy default (unmodified chunks verbatim) never
shifts anything, so it is always safe. Whether re-deflation is safe is an
empirical question probed by ``tools/echo_check.py`` (re-deflate-echo mode)
+ a Layer-3 reopen; findings are recorded in the M1 hypothesis log.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

from swformat.types import Chunk, Document, Gap

# Offsets of the size fields within a chunk's header_bytes (== relative to si).
_OFF_CSZ = 0x12
_OFF_USZ = 0x16

# Default DEFLATE level for re-compression. SW re-reads via inflate, so the
# specific level is irrelevant to validity; 6 is a balance of speed/size.
_DEFAULT_LEVEL = 6


class SpanPreserveError(Exception):
    """A modified chunk's payload won't fit in its original compressed span.

    Span-preserving writes (the only SW-accepted size-changing-edit path —
    see ``research/empirical_findings/m1_writer_roundtrip/log.md``) require the
    re-deflated payload to be <= the chunk's ORIGINAL ``csz`` so it can be
    padded back to the same span (no offset shift). When even maximum
    compression exceeds the original ``csz``, the edit genuinely GROWS the
    chunk, which would shift every later offset — and SW rejects shifted
    files (the central-directory rewrite that would fix this is not yet
    implemented). Raised instead of silently emitting a file SW will reject.
    """


def _deflate_raw(payload: bytes, level: int = _DEFAULT_LEVEL) -> bytes:
    """Raw DEFLATE (no zlib header/trailer), matching SW's ``wbits=-15``."""
    co = zlib.compressobj(level, zlib.DEFLATED, -15)
    return co.compress(payload) + co.flush()


def serialize_chunk(chunk: Chunk, level: int = _DEFAULT_LEVEL, *,
                    span_preserve: bool = False) -> bytes:
    """Return the on-disk bytes for one chunk.

    Verbatim if unmodified. If ``modified_payload`` is set, re-deflate it:

    - ``span_preserve=False`` (default): emit ``header(csz=new, usz=new) +
      comp``. The chunk's compressed size changes, so EVERY later chunk's file
      offset shifts. This is the raw form — used for diagnostics
      (``force_redeflate_all``) and lazy/same-span writes. **SW rejects files
      whose offsets shifted** (see :class:`SpanPreserveError` / the M1
      hypothesis log), so this form is NOT SW-safe on its own.

    - ``span_preserve=True``: emit ``header(csz=ORIGINAL, usz=new) +
      comp_padded_to_original_csz``. The chunk keeps its exact original span,
      so NO later offset shifts — the only SW-accepted size-changing path.
      DEFLATE self-terminates, so the trailing zero padding is ignored by
      inflate. The header ``csz`` is left at the original value; only ``usz``
      (the uncompressed length, which SW uses to know how many inflated bytes
      to read) is updated. Raises :class:`SpanPreserveError` if the payload
      won't fit even at maximum compression.
    """
    if chunk.modified_payload is None:
        return chunk.raw_bytes()
    payload: bytes = chunk.modified_payload
    comp = _deflate_raw(payload, level)
    new_header = bytearray(chunk.header_bytes)
    if span_preserve:
        if len(comp) > chunk.csz:
            comp = _deflate_raw(payload, 9)  # try hardest to fit the original span
        if len(comp) > chunk.csz:
            raise SpanPreserveError(
                f"chunk {chunk.name!r}: edited payload compresses to "
                f"{len(comp)} B > original csz {chunk.csz} B; a span-preserving "
                f"write cannot grow a chunk (central-directory rewrite for "
                f"offset shifts is not yet implemented)."
            )
        comp = comp + b"\x00" * (chunk.csz - len(comp))  # pad back to original span
        # csz stays at the original (header_bytes already holds it); update usz.
        struct.pack_into("<I", new_header, _OFF_USZ, len(payload))
        return bytes(new_header) + comp
    struct.pack_into("<I", new_header, _OFF_CSZ, len(comp))
    struct.pack_into("<I", new_header, _OFF_USZ, len(payload))
    return bytes(new_header) + comp


def serialize(doc: Document, level: int = _DEFAULT_LEVEL, *,
              span_preserve: bool = False) -> bytes:
    """Reassemble the whole document to bytes.

    For an unmodified document this equals :meth:`Document.reconstruct`
    and (per M0.5) equals the original file byte-for-byte. ``span_preserve``
    is forwarded to :func:`serialize_chunk` for every modified chunk.
    """
    parts: list[bytes] = []
    for it in doc.items:
        if isinstance(it, Gap):
            parts.append(it.raw_bytes)
        else:  # Chunk
            parts.append(serialize_chunk(it, level, span_preserve=span_preserve))
    return b"".join(parts)


def write(doc: Document, out_path: str | Path, level: int = _DEFAULT_LEVEL) -> None:
    """Serialize ``doc`` and write it to ``out_path`` (no TOC fixup).

    Safe only for the lazy round-trip (no modified chunks) or
    same-span edits. For size-changing edits use :func:`write_with_toc`.
    """
    Path(out_path).write_bytes(serialize(doc, level))


# ⚠ The `-8` pointer fixup ("offset-shift") does NOT produce SW-valid files on
# ANY version (re-falsified 2026-06-11: v15000 washer AND real v19000 parts both
# open as swFileRequiresRepairError / e=2097152). This threshold no longer gates
# a "safe" path — it only narrows the EXPERIMENTAL, SW-REJECTED offset-shift code
# (off by default) to modern files for research. The earlier "v15000+ ACCEPT it"
# note was wrong (re-parse was mistaken for SW-acceptance). See the UPDATE
# 2026-06-11 entry in research/empirical_findings/m1_writer_roundtrip/log.md.
_MIN_OFFSET_SHIFT_VERSION = 15000


def _patch_toc_sizes(raw: bytes, modified_names: list[str]) -> bytes:
    """Patch each modified chunk's TOC record csz/usz to its actual new values.

    Re-parses ``raw`` in-memory, finds each modified stream's directory record
    by name, and writes the chunk's current csz/usz. Used by both write paths:
    for span-preservation csz is unchanged (only usz moves); for the offset-
    shift path both move.
    """
    from swformat.chunks.toc import _find_toc_record, update_toc_sizes
    from swformat.io.reader import read_document_bytes

    new_doc = read_document_bytes(raw)
    patched = bytearray(raw)
    key = raw[7]
    new_by_name = {c.name: c for c in new_doc.chunks}
    for name in modified_names:
        si = _find_toc_record(bytes(patched), new_doc, key, name)
        if si is not None:
            nc = new_by_name[name]
            update_toc_sizes(patched, si, nc.csz, nc.usz)
    return bytes(patched)


def _grown_chunk_bytes(chunk: Chunk, level: int) -> bytes:
    """Build a fresh on-disk chunk for a grown payload (no span constraint).

    Header copied from the original chunk with ``csz``/``usz`` patched to the
    re-deflated payload's sizes, followed by the new compressed bytes. Used by
    the relocate-grow path to emit the grown copy that is appended at EOF.

    Precondition: ``chunk.modified_payload is not None`` (the caller only emits
    grown copies for modified chunks).
    """
    payload = chunk.modified_payload
    assert payload is not None  # caller guarantees a modified chunk
    comp = _deflate_raw(payload, level)
    new_header = bytearray(chunk.header_bytes)
    struct.pack_into("<I", new_header, _OFF_CSZ, len(comp))
    struct.pack_into("<I", new_header, _OFF_USZ, len(payload))
    return bytes(new_header) + comp


def _serialize_relocate_grow(doc: Document, level: int) -> bytes:
    """Grow-beyond-span via STREAM RELOCATION TO EOF (SW-verified on PARTS).

    The SW-accepted way to grow a stream past its compressed span WITHOUT the
    central-directory rewrite, discovered 2026-06-11 (see
    ``research/empirical_findings/m1_writer_roundtrip/log.md``). It relies on two
    SW behaviours, both Layer-3 verified on real v19000 PARTS:

    * SW does NOT validate file size / tail — bytes appended after the trailing
      directory are ignored.
    * SW locates every stream via its TOC record's ``off8`` pointer, NOT by a
      sequential walk.

    So a grown stream is APPENDED as a fresh chunk at EOF and its TOC record is
    repointed there; no other chunk moves, so no offset shifts and no stale
    pointers — sidestepping the (unsolved, and for the offset-shift path
    SW-invalid) central-directory rewrite.

    The body is LENGTH-PRESERVING: unmodified chunks verbatim; modified chunks
    that still fit their span are span-preserved IN PLACE (same ``csz``);
    modified chunks that OVERFLOW are NEUTRALISED in place (header kept, payload
    zeroed → same ``csz``; inflate fails so a sequential reader skips the stale
    copy and resolves the appended one, matching SW). Because the body keeps its
    length, the trailing directory stays at its original offset and TOC records
    are located via ``doc``'s own gaps. The grown copies are then appended and
    each gets ``off8``/``csz``/``usz`` patched in its TOC record.

    ⚠ PARTS ONLY. Assemblies and drawings REJECT a relocated stream (even a
    verbatim move) — they enforce an additional chunk-position invariant (a
    global offset index) that parts lack. The caller must gate this to parts;
    using it on an assembly/drawing produces an SW-rejected file.
    """
    from swformat.chunks.toc import OFFSET_BIAS, _find_toc_record, update_toc_sizes

    body: list[bytes] = []
    inplace: list[Chunk] = []          # modified chunks span-preserved in place
    to_append: list[tuple[str, bytes]] = []  # (name, grown chunk bytes) for EOF
    for it in doc.items:
        if isinstance(it, Gap):
            body.append(it.raw_bytes)
            continue
        ch = it
        if ch.modified_payload is None:
            body.append(ch.raw_bytes())
            continue
        try:
            body.append(serialize_chunk(ch, level, span_preserve=True))
            inplace.append(ch)
        except SpanPreserveError:
            # neutralise the old chunk in place (same span, zeroed payload) and
            # queue the grown copy for append at EOF.
            body.append(bytes(ch.header_bytes) + b"\x00" * ch.csz)
            to_append.append((ch.name, _grown_chunk_bytes(ch, level)))

    raw = bytearray(b"".join(body))  # same length as the original file body
    key = raw[7]

    # span-preserved (in-place) modified chunks: patch usz only (csz unchanged).
    for ch in inplace:
        payload = ch.modified_payload
        assert payload is not None  # inplace holds only modified chunks
        si = _find_toc_record(bytes(raw), doc, key, ch.name)
        if si is not None:
            update_toc_sizes(raw, si, ch.csz, len(payload))

    # relocated chunks: append at EOF, repoint TOC off8 + csz + usz.
    for name, nb in to_append:
        off = len(raw)
        csz = struct.unpack_from("<I", nb, _OFF_CSZ)[0]
        usz = struct.unpack_from("<I", nb, _OFF_USZ)[0]
        raw += nb
        si = _find_toc_record(bytes(raw), doc, key, name)
        if si is None:
            raise SpanPreserveError(
                f"relocate-grow: TOC record for {name!r} not found (cannot repoint)"
            )
        struct.pack_into("<I", raw, si + 0x12, csz)
        struct.pack_into("<I", raw, si + 0x16, usz)
        struct.pack_into("<I", raw, si + 0x28, (off - OFFSET_BIAS) & 0xFFFFFFFF)
    return bytes(raw)


def serialize_with_toc(doc: Document, level: int = _DEFAULT_LEVEL, *,
                       allow_grow: bool = False,
                       relocate_grow: bool = False,
                       _experimental_offset_shift: bool = False) -> bytes:
    """Serialize ``doc`` for a size-changing edit, the **SW-accepted** way.

    **The primary SW-verified strategy is SPAN PRESERVATION.** Re-deflate each
    modified chunk and PAD it back to its original ``csz`` so the chunk's span
    is unchanged and no later offset shifts; then only the modified chunks' TOC
    ``usz`` is patched (``csz`` unchanged). This works on every document
    (including the old ``_MO_VERSION_11000`` structure) and is SW-verified to
    reopen (errors=0). It handles every edit whose payload still fits the
    original compressed span: value changes, deletes, and adds with slack.

    If a modified chunk's payload no longer fits its original span (a **GROW** —
    e.g. adding a property to a tight stream), span preservation cannot proceed
    and raises :class:`SpanPreserveError` — UNLESS ``relocate_grow`` is set:

    **``relocate_grow=True`` — grow via STREAM RELOCATION TO EOF (PARTS ONLY).**
    Appends the grown stream as a fresh chunk at EOF and repoints its TOC record
    there; no other chunk moves (see :func:`_serialize_relocate_grow`). This is
    SW-verified on real v19000 PARTS (global + config props, large grows, SW
    re-save durable). It is the SW-accepted way to grow beyond span without the
    (unsolved) central-directory rewrite. ⚠ **PARTS ONLY** — assemblies and
    drawings reject a relocated stream, so the caller MUST gate this to parts
    (e.g. ``detect_doc_type(path) == "part"``). Defaults to False.

    With neither ``relocate_grow`` nor the experimental path, a grow-beyond-span
    still raises :class:`SpanPreserveError` (honest refusal).

    ⚠ THE OFFSET-SHIFT FIXUP IS NOT SW-VALID (re-falsified 2026-06-11).
    -----------------------------------------------------------------
    The alternative "re-deflate to a new ``csz`` then rewrite every ``-8`` gap
    pointer + TOC ``csz``/``usz``" path (:func:`fixup_offset_pointers`) produces
    files that RE-PARSE but that **real SOLIDWORKS REJECTS** with
    ``swFileRequiresRepairError`` (e=2097152, "custom property data
    corruption") — verified on real v19000 parts AND on the v15000 washer
    fixture the path was once (wrongly) believed verified against. SW validates
    more than the ``-8`` pointers + TOC sizes around the property region; what
    else must move is undecoded. The path is therefore **OFF by default**. It is
    retained ONLY for research, behind ``_experimental_offset_shift=True``, and
    even then ONLY fires on modern files; it will emit an SW-rejected file, so
    never use it in a consumer path. ``allow_grow`` is retained for source
    compatibility but does nothing unless ``_experimental_offset_shift`` is also
    set. See ``research/empirical_findings/m1_writer_roundtrip/log.md`` (UPDATE
    2026-06-11) and ``lesson_20260608_span_preservation_write_method.md``.

    Requires the edit to not add/remove chunks (stable chunk set).
    """
    modified_names = [c.name for c in doc.chunks if c.modified_payload is not None]
    if not modified_names:
        return serialize(doc, level, span_preserve=True)  # nothing changed

    try:
        raw = serialize(doc, level, span_preserve=True)
    except SpanPreserveError:
        if relocate_grow:
            # SW-accepted grow-beyond-span for PARTS (caller must gate to parts).
            return _serialize_relocate_grow(doc, level)
        if not (allow_grow and _experimental_offset_shift):
            # Honest default: refuse to emit a file SW will reject. Grow-beyond-
            # span is unsolved (the offset-shift fixup is SW-invalid — see above).
            raise
        # EXPERIMENTAL ONLY — produces an SW-REJECTED file. Never a consumer path.
        from swformat.chunks.walker import doc_version
        ver = doc_version({c.name: b"" for c in doc.chunks}) or 0
        if ver < _MIN_OFFSET_SHIFT_VERSION:
            raise SpanPreserveError(
                "edit grows a chunk beyond its compressed span; grow-beyond-"
                "span is not yet SW-valid on any version (offset-shift fixup is "
                "falsified — see writer docstring / m1 log)."
            ) from None
        from swformat.chunks.toc import build_offset_map, fixup_offset_pointers
        from swformat.io.reader import read_document_bytes
        raw = serialize(doc, level, span_preserve=False)
        new_doc = read_document_bytes(raw)
        offset_map = build_offset_map(doc, new_doc)
        raw = fixup_offset_pointers(raw, new_doc, offset_map)
        return _patch_toc_sizes(raw, modified_names)

    # Span preservation succeeded — offsets unchanged; patch usz (csz unchanged).
    return _patch_toc_sizes(raw, modified_names)


def write_with_toc(doc: Document, out_path: str | Path, level: int = _DEFAULT_LEVEL,
                   *, allow_grow: bool = False, relocate_grow: bool = False,
                   _experimental_offset_shift: bool = False) -> None:
    """Size-changing write to ``out_path`` (see :func:`serialize_with_toc`).

    Span-preserving when the edit fits the original compressed span. For a
    grow-beyond-span: ``relocate_grow=True`` writes via stream relocation to EOF
    (SW-verified on PARTS — caller MUST gate to parts); otherwise raises
    :class:`SpanPreserveError` (honest refusal). ``allow_grow`` /
    ``_experimental_offset_shift`` gate the research-only offset-shift path,
    which emits an SW-REJECTED file and must never be used in a consumer."""
    Path(out_path).write_bytes(serialize_with_toc(
        doc, level, allow_grow=allow_grow, relocate_grow=relocate_grow,
        _experimental_offset_shift=_experimental_offset_shift))


# --- mutation helpers (the seam M2+ builds on) -----------------------------
def set_stream_payload(doc: Document, name: str, new_payload: bytes) -> int:
    """Set ``modified_payload`` on every inline chunk named ``name``.

    Returns the number of chunks updated. The next :func:`write`/:func:`serialize`
    will re-deflate those chunks. This is the low-level seam; M2's
    high-level property API sits on top of it.
    """
    n = 0
    for ch in doc.chunks:
        if ch.name == name and ch.is_inline:
            ch.modified_payload = new_payload
            n += 1
    return n


def force_redeflate_all(doc: Document) -> int:
    """Mark every inline chunk modified with its OWN decompressed content.

    A diagnostic: produces a file with identical *logical* content to the
    input but every payload re-compressed (so byte layout shifts). Used by
    ``echo_check --redeflate`` to probe whether SW accepts our DEFLATE
    output and whether the TOC tolerates offset shifts. Returns count.
    """
    n = 0
    for ch in doc.chunks:
        if not ch.is_inline:
            continue
        payload = ch.decompressed()
        if payload is None:
            continue
        ch.modified_payload = payload
        n += 1
    return n
