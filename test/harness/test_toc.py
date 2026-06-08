"""Layer-2 tests — central TOC offset-pointer fixup (M1.5 / M2 core).

These guard the size-changing-edit writer (``serialize_with_toc``) against
regressions, in pure Python (no SW needed). They are deliberately
**layout-agnostic**: the TOC record format varies by section type
(0xDF/0xA4/0xBC), so rather than parse individual records we reason about
the *offset pointers* the way the production fixup does — a pointer is a
uint32 ``V`` in a gap region such that ``V + OFFSET_BIAS`` is a chunk's file
offset (the verified ``stored == offset - 8`` encoding).

The invariant after a size-changing edit: **no stale pointer remains** —
for every chunk whose offset moved, ``old_offset - 8`` no longer appears in
any gap, and ``new_offset - 8`` does. A stale pointer is exactly what makes
SOLIDWORKS reject the file (``swFileRequiresRepairError``), so this is the
property that matters. Full SW-reopen confirmation lives in the manual
``layer3_reopen.py`` harness; this is the fast regression guard.

See ``src/swformat/chunks/toc.py`` and
``research/empirical_findings/m1_writer_roundtrip/``. Marked ``layer2``.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

import swformat  # noqa: E402
from swformat.chunks.toc import OFFSET_BIAS  # noqa: E402
from swformat.io.writer import serialize_with_toc, set_stream_payload  # noqa: E402
from swformat.types import Document  # noqa: E402

CORPUS_CONFIG = ROOT / "test" / "corpus" / "corpus.config.json"


def _existing_corpus() -> list[tuple[str, Path]]:
    if not CORPUS_CONFIG.exists():
        return []
    files = json.loads(CORPUS_CONFIG.read_text(encoding="utf-8")).get("files", [])
    return [(e["tag"], Path(e["path"])) for e in files if Path(e["path"]).exists()]


CORPUS = _existing_corpus()
_IDS = [t for t, _ in CORPUS]
_skip = pytest.mark.skipif(not CORPUS, reason="no corpus files present on this machine")


def _gap_uint32_present(doc: Document, value: int) -> bool:
    """True if ``value`` appears as a uint32 LE anywhere in a gap region."""
    target = value & 0xFFFFFFFF
    for gap in doc.gaps:
        b = gap.raw_bytes
        for i in range(0, len(b) - 3):
            if struct.unpack_from("<I", b, i)[0] == target:
                return True
    return False


def _count_resolvable_pointers(doc: Document) -> int:
    """Count gap uint32s ``V`` where ``V + BIAS`` is some chunk's offset."""
    offsets = {c.offset for c in doc.chunks}
    n = 0
    for gap in doc.gaps:
        b = gap.raw_bytes
        for i in range(0, len(b) - 3):
            if (struct.unpack_from("<I", b, i)[0] + OFFSET_BIAS) in offsets:
                n += 1
    return n


def test_fixup_offset_pointers_unit() -> None:
    """Direct unit coverage of the pointer rewriter (no corpus needed).

    Builds a synthetic gap holding genuine ``offset-8`` pointers, a coincidental
    non-pointer value, and a pointer to an unmoved chunk, then asserts:
    moved-chunk pointers are remapped, the non-pointer is untouched, and the
    unmoved-chunk pointer is left exactly as-is. Also covers a repeated moved
    pointer (every occurrence remapped).
    """
    from types import SimpleNamespace

    from swformat.chunks.toc import fixup_offset_pointers

    offset_map = {0x100: 0x200, 0x300: 0x300}  # 0x100 moved, 0x300 unmoved

    def ptr(off: int) -> bytes:
        return struct.pack("<I", off - OFFSET_BIAS)

    data = ptr(0x100) + struct.pack("<I", 0xDEADBEEF) + ptr(0x300) + ptr(0x100)
    doc = SimpleNamespace(gaps=[SimpleNamespace(offset=0, end=len(data))])
    out = fixup_offset_pointers(bytes(data), doc, offset_map)

    assert struct.unpack_from("<I", out, 0)[0] == 0x200 - OFFSET_BIAS   # moved
    assert struct.unpack_from("<I", out, 4)[0] == 0xDEADBEEF            # non-ptr
    assert struct.unpack_from("<I", out, 8)[0] == 0x300 - OFFSET_BIAS   # unmoved
    assert struct.unpack_from("<I", out, 12)[0] == 0x200 - OFFSET_BIAS  # moved (repeat)
    assert len(out) == len(data)


def test_fixup_offset_pointers_only_touches_gaps() -> None:
    """Bytes outside any gap region are never modified, even if they'd match."""
    from types import SimpleNamespace

    from swformat.chunks.toc import fixup_offset_pointers

    offset_map = {0x100: 0x200}

    def ptr(off: int) -> bytes:
        return struct.pack("<I", off - OFFSET_BIAS)

    # [outside-ptr][GAP: ptr][outside-ptr] — only the middle should change
    data = ptr(0x100) + ptr(0x100) + ptr(0x100)
    doc = SimpleNamespace(gaps=[SimpleNamespace(offset=4, end=8)])
    out = fixup_offset_pointers(bytes(data), doc, offset_map)
    assert struct.unpack_from("<I", out, 0)[0] == 0x100 - OFFSET_BIAS   # outside: untouched
    assert struct.unpack_from("<I", out, 4)[0] == 0x200 - OFFSET_BIAS   # in gap: remapped
    assert struct.unpack_from("<I", out, 8)[0] == 0x100 - OFFSET_BIAS   # outside: untouched


@pytest.mark.layer2
@_skip
@pytest.mark.parametrize(("tag", "path"), CORPUS, ids=_IDS)
def test_original_has_offset_pointers(tag: str, path: Path) -> None:
    """Sanity: an unmodified file has gap pointers encoding chunk offsets."""
    doc = swformat.read_document(path)
    assert _count_resolvable_pointers(doc) >= len(doc.chunks) // 2, (
        f"{tag}: too few offset pointers found — decode/BIAS may be wrong"
    )


@pytest.mark.layer2
@_skip
def test_span_preserving_edit_keeps_offsets() -> None:
    """A size-changing edit via serialize_with_toc PRESERVES every chunk offset.

    The SW-accepted size-changing path (verified — see the 2026-06-08 CRITICAL
    entry in research/empirical_findings/m1_writer_roundtrip/log.md) re-deflates
    the edited chunk and pads it back to its ORIGINAL csz, so no later chunk
    shifts. Assert: the edit is reflected, every chunk offset is unchanged, the
    file size is unchanged, unrelated streams are intact, and no orphan bytes.
    (The old offset-`-8` pointer "fixup" is superseded: it left no stale
    pointers yet SW still rejected shifted files.)
    """
    target = "docProps/custom.xml"
    chosen = next(((t, p) for t, p in CORPUS
                   if target in swformat.read_document(p).streams()), None)
    if chosen is None:
        pytest.skip("no corpus file exposes docProps/custom.xml")
    _tag, path = chosen

    old = swformat.read_document(path)
    before = old.streams()
    edited = before[target] + b"<!-- swf -->"  # small grow, fits in original csz
    set_stream_payload(old, target, edited)

    out = serialize_with_toc(old)
    assert len(out) == len(old.data), "span not preserved (file size changed)"
    new = swformat.read_document_bytes(out)
    new_streams = new.streams()

    # content correctness
    assert new_streams[target] == edited
    for n, v in before.items():
        if n != target:
            assert new_streams.get(n) == v, f"unrelated stream {n} changed"
    # no orphan bytes
    assert new.reconstruct() == new.data
    # EVERY chunk offset preserved (span preservation => nothing moved)
    old_off = [c.offset for c in sorted(old.chunks, key=lambda c: c.offset)]
    new_off = [c.offset for c in sorted(new.chunks, key=lambda c: c.offset)]
    assert old_off == new_off, "chunk offsets shifted — span not preserved"


@pytest.mark.layer2
@_skip
def test_grow_beyond_span_refused_by_default() -> None:
    """A grow-beyond-span edit is REFUSED by default (raises SpanPreserveError).

    The writer must never silently emit a file SW will reject. Grow-beyond-span
    is not yet SW-valid: the offset-shift fixup that *would* produce a
    re-parseable larger file was re-falsified 2026-06-11 (real v19000 parts AND
    the v15000 washer reopen as swFileRequiresRepairError / e=2097152). So the
    default ``serialize_with_toc`` must RAISE rather than fall back to it. This
    guards against re-introducing the silent-corruption regression.
    """
    from swformat.io.writer import SpanPreserveError

    target = "docProps/custom.xml"
    chosen = next(((t, p) for t, p in CORPUS
                   if target in swformat.read_document(p).streams()), None)
    if chosen is None:
        pytest.skip("no modern corpus file with docProps/custom.xml")
    _tag, path = chosen

    old = swformat.read_document(path)
    blob = bytes((i * 73 + 11) & 0xFF for i in range(8000))  # incompressible → grows past span
    set_stream_payload(old, target, old.streams()[target] + blob)
    with pytest.raises(SpanPreserveError):
        serialize_with_toc(old)  # default refuses the grow


@pytest.mark.layer2
@_skip
def test_experimental_offset_shift_reparses_but_is_sw_invalid() -> None:
    """The retained EXPERIMENTAL offset-shift path still mechanically re-parses.

    This documents (and pins) the research code: with the explicit
    ``allow_grow=True, _experimental_offset_shift=True`` opt-in on a modern file
    the writer produces output that OUR reader re-parses with no orphan bytes
    and offsets shifted. **This is NOT an SW-valid file** — SW rejects it
    (e=2097152). The test asserts ONLY the pure-Python invariants, exactly the
    insufficient signal that caused the earlier false-confidence regression; it
    exists to keep the experimental path callable, not to claim correctness.
    """
    from swformat.chunks.walker import doc_version

    target = "docProps/custom.xml"
    chosen = None
    for t, p in CORPUS:
        doc = swformat.read_document(p)
        ver = doc_version({c.name: b"" for c in doc.chunks}) or 0
        if target in doc.streams() and ver >= 15000:
            chosen = (t, p)
            break
    if chosen is None:
        pytest.skip("no modern (v>=15000) corpus file with docProps/custom.xml")
    _tag, path = chosen

    old = swformat.read_document(path)
    blob = bytes((i * 73 + 11) & 0xFF for i in range(8000))
    edited = old.streams()[target] + blob
    set_stream_payload(old, target, edited)
    # Explicit experimental opt-in — produces an SW-REJECTED (but re-parseable) file.
    out = serialize_with_toc(old, allow_grow=True, _experimental_offset_shift=True)

    new = swformat.read_document_bytes(out)
    assert new.streams()[target] == edited            # grown content present
    assert new.reconstruct() == new.data              # no orphan bytes (re-parse only!)
    old_off = [c.offset for c in sorted(old.chunks, key=lambda c: c.offset)]
    new_off = [c.offset for c in sorted(new.chunks, key=lambda c: c.offset)]
    assert old_off != new_off, "expected offset shift on the experimental path"


@pytest.mark.layer2
@_skip
def test_relocate_grow_part_roundtrips() -> None:
    """Grow-beyond-span via relocate-to-EOF (``relocate_grow=True``) on a PART:
    the write succeeds, the grown stream's content reads back, NO existing chunk
    offset shifted (only an append at EOF), and there are no orphan bytes.

    This guards the SW-verified PARTS grow path (SW reopen at e=0 confirmed in
    the m1_writer_roundtrip log, UPDATE 2026-06-11). The relocate path appends
    the grown stream past the original EOF and repoints its TOC ``off8``, so —
    unlike the falsified offset-shift path — every pre-existing chunk keeps its
    offset (no shift, no stale pointers).
    """
    from swformat.chunks.walker import doc_version

    target = "docProps/custom.xml"
    chosen = None
    for t, p in CORPUS:
        if p.suffix.lower() != ".sldprt":          # relocate is SW-valid on PARTS only
            continue
        doc = swformat.read_document(p)
        ver = doc_version({c.name: b"" for c in doc.chunks}) or 0
        if target in doc.streams() and ver >= 15000:
            chosen = (t, p)
            break
    if chosen is None:
        pytest.skip("no modern (v>=15000) PART with docProps/custom.xml")
    _tag, path = chosen

    old = swformat.read_document(path)
    orig_offsets = {c.name: c.offset for c in old.chunks}
    orig_size = len(old.data)
    # incompressible blob → guarantees the stream overflows its compressed span
    blob = bytes((i * 73 + 11) & 0xFF for i in range(8000))
    edited = old.streams()[target] + blob
    set_stream_payload(old, target, edited)

    out = serialize_with_toc(old, relocate_grow=True)   # must NOT raise on a part
    assert len(out) > orig_size, "relocate-grow should append at EOF (file grows)"

    new = swformat.read_document_bytes(out)
    assert new.reconstruct() == new.data                 # no orphan bytes
    assert new.streams()[target] == edited               # grown content resolvable
    # every PRE-EXISTING chunk keeps its offset (the relocated copy is the new
    # one appended at EOF; nothing in the body shifted).
    new_first = {}
    for c in sorted(new.chunks, key=lambda c: c.offset):
        new_first.setdefault(c.name, c.offset)
    for name, off in orig_offsets.items():
        if name == target:
            continue
        assert new_first.get(name) == off, f"chunk {name!r} offset shifted"


@pytest.mark.layer2
@_skip
def test_relocate_grow_mixed_fit_and_overflow() -> None:
    """Mixed edit on a PART: one modified stream OVERFLOWS its span (relocated to
    EOF) while another FITS (span-preserved in place). Both resolve correctly,
    the fitting stream's offset is unchanged, and there are no orphan bytes.

    Guards the mixed branch of ``_serialize_relocate_grow`` (SW-verified e=0 with
    both values readable — m1_writer_roundtrip log, 2026-06-11): the relocate
    path must span-preserve the fitting stream IN PLACE (patch usz only) while
    relocating only the overflowing one.
    """
    from swformat.chunks.walker import doc_version

    A = "docProps/custom.xml"
    B = "docProps/Config-0-Properties.xml"
    chosen = None
    for t, p in CORPUS:
        if p.suffix.lower() != ".sldprt":
            continue
        doc = swformat.read_document(p)
        ver = doc_version({c.name: b"" for c in doc.chunks}) or 0
        streams = doc.streams()
        if ver >= 15000 and A in streams and B in streams:
            chosen = (t, p)
            break
    if chosen is None:
        pytest.skip("no modern PART with both custom.xml and Config-0-Properties.xml")
    _tag, path = chosen

    old = swformat.read_document(path)
    b_off = next(c.offset for c in old.chunks if c.name == B)
    a_big = old.streams()[A] + bytes((i * 73 + 11) & 0xFF for i in range(8000))  # overflow
    b_fit = old.streams()[B] + b"<!-- swf -->"                                   # fits span
    set_stream_payload(old, A, a_big)
    set_stream_payload(old, B, b_fit)

    out = serialize_with_toc(old, relocate_grow=True)
    new = swformat.read_document_bytes(out)
    assert new.reconstruct() == new.data
    assert new.streams()[A] == a_big          # overflowing stream resolved (relocated)
    assert new.streams()[B] == b_fit          # fitting stream resolved (in place)
    # the fitting stream stayed put (span-preserved); only A was appended at EOF
    assert min(c.offset for c in new.chunks if c.name == B) == b_off
