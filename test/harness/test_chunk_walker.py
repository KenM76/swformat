"""Layer 1 tests — chunk walker correctness.

These are the standing falsification of the walker's two load-bearing
invariants (see ``swformat.types`` module docstring):

1. **No orphan bytes** — every byte of the input is owned by exactly one
   record, and concatenating the records reproduces the file exactly.
2. **Stream parity** — the productionised walker yields the same
   ``{name: decompressed}`` mapping as the imported reference reader.

Tests run against every file in ``corpus.config.json`` that exists on
this machine. Missing files (the corpus references absolute paths) are
skipped, not failed — so the suite is meaningful on any machine, and
fully exercised when local real files are configured.

Marked ``layer1`` per the pyproject marker registry.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

import swformat  # noqa: E402
from swformat import _imported_swx_reader_v0 as _ref  # noqa: E402
from swformat.chunks.walker import detect_format, rol_decode  # noqa: E402

CORPUS_CONFIG = ROOT / "test" / "corpus" / "corpus.config.json"


def _corpus_entries() -> list[dict]:
    if not CORPUS_CONFIG.exists():
        return []
    return json.loads(CORPUS_CONFIG.read_text(encoding="utf-8")).get("files", [])


def _existing_corpus() -> list[tuple[str, Path]]:
    out = []
    for e in _corpus_entries():
        p = Path(e["path"])
        if p.exists():
            out.append((e["tag"], p))
    return out


CORPUS = _existing_corpus()
# Parametrize on tag; xfail-skip the whole module if no corpus is present.
_IDS = [tag for tag, _ in CORPUS]


@pytest.mark.layer1
@pytest.mark.skipif(not CORPUS, reason="no corpus files present on this machine")
@pytest.mark.parametrize(("tag", "path"), CORPUS, ids=_IDS)
def test_no_orphan_bytes(tag: str, path: Path) -> None:
    """Records cover every byte once; reconstruct() == original."""
    doc = swformat.read_document(path)
    # Coverage is contiguous, gap-free, overlap-free.
    cursor = 0
    for it in doc.items:
        assert it.offset == cursor, f"{tag}: record gap/overlap at {it.offset} (expected {cursor})"
        cursor = it.end
    assert cursor == len(doc.data), f"{tag}: coverage {cursor} != file size {len(doc.data)}"
    # The strong form: byte-exact reconstruction.
    assert doc.reconstruct() == doc.data, f"{tag}: reconstruct() != original bytes"


@pytest.mark.layer1
@pytest.mark.skipif(not CORPUS, reason="no corpus files present on this machine")
@pytest.mark.parametrize(("tag", "path"), CORPUS, ids=_IDS)
def test_stream_parity_with_reference(tag: str, path: Path) -> None:
    """Productionised walker yields the same streams as the imported reader."""
    doc = swformat.read_document(path)
    _, ref_streams = _ref.read_file(path)
    assert doc.streams() == ref_streams, f"{tag}: stream mapping diverged from reference"


@pytest.mark.layer1
@pytest.mark.skipif(not CORPUS, reason="no corpus files present on this machine")
@pytest.mark.parametrize(("tag", "path"), CORPUS, ids=_IDS)
def test_read_document_bytes_matches_path(tag: str, path: Path) -> None:
    """read_document_bytes(data) is equivalent to read_document(path).

    Locks in the in-memory parse entry point that the TOC writer uses instead
    of round-tripping through a temp file.
    """
    from_path = swformat.read_document(path)
    from_bytes = swformat.read_document_bytes(path.read_bytes())
    assert from_bytes.fmt == from_path.fmt
    assert from_bytes.data == from_path.data
    assert from_bytes.reconstruct() == from_path.reconstruct()
    assert from_bytes.streams() == from_path.streams()
    assert len(from_bytes.chunks) == len(from_path.chunks)


@pytest.mark.layer1
@pytest.mark.skipif(not CORPUS, reason="no corpus files present on this machine")
@pytest.mark.parametrize(("tag", "path"), CORPUS, ids=_IDS)
def test_format_detected_modern(tag: str, path: Path) -> None:
    """All registered corpus files are modern-format (sanity on detection)."""
    assert detect_format(path.read_bytes()) == "modern", f"{tag}: not detected as modern"


@pytest.mark.layer1
def test_read_robustness_across_staged_corpus() -> None:
    """Broad no-orphan-bytes + stream-read validation across the staged real
    files (parts/assemblies/drawings from the W: tree), if present.

    The example `corpus.config.json` set is small; this additionally sweeps any
    real files staged in a local directory (gitignored, never committed) to catch
    walker/reader regressions the small corpus would miss. For every modern file:
    `reconstruct() == data` (no
    orphan bytes) and `streams()` returns a dict without raising. Skips if the
    staged tree is absent. No client data is asserted.
    """
    samples = ROOT / "research" / "empirical_findings" / "twin_save_baseline" / "samples"
    if not samples.exists():
        pytest.skip("no staged sample tree present")
    seen: set[str] = set()
    checked = 0
    for p in sorted(samples.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".sldprt", ".sldasm", ".slddrw"):
            continue
        if p.name.lower().startswith(("twina", "twinb")) or p.name in seen:
            continue
        seen.add(p.name)
        doc = swformat.read_document_bytes(p.read_bytes())
        if doc.fmt != "modern":
            continue
        assert doc.reconstruct() == doc.data, f"orphan bytes in {p.name}"
        assert isinstance(doc.streams(), dict), f"streams() failed on {p.name}"
        checked += 1
    if checked == 0:
        pytest.skip("staged tree present but no modern files found")


@pytest.mark.layer1
def test_early_coincidental_marker_does_not_abort_scan() -> None:
    """A marker byte-pattern in the first 4 bytes must not drop real chunks.

    Regression: the scan used to ``break`` whenever ``find`` returned a
    position < 4 — but that conflates "no marker found" (-1) with "a
    coincidental marker appears before offset 4". Breaking on the latter
    aborted the entire walk, so every real chunk (which all begin at m >= 4)
    was silently lost and the file degraded to one opaque gap. The scan must
    skip the early coincidental marker and still find the valid chunk.
    """
    import struct as _struct

    from swformat.chunks.walker import MARKER, iter_chunks

    name = b"TestStream"
    payload = b"ABCD"
    # 8 leading "header" bytes with a COINCIDENTAL marker at offset 0 and the
    # ROL key (byte 7) = 0 so the name decodes as identity/printable ASCII.
    lead = bytearray(8)
    lead[0:6] = MARKER          # early coincidental marker (offset 0)
    lead[7] = 0                 # ROL key = 0
    # A valid inline chunk starting at si = 8 (its marker lands at offset 12).
    hdr = bytearray(0x1E)
    hdr[4:10] = MARKER
    hdr[0x0A] = 0x10            # section_type (arbitrary)
    _struct.pack_into("<I", hdr, 0x0E, 65536)        # f1 (inline threshold)
    _struct.pack_into("<I", hdr, 0x12, len(payload))  # csz
    _struct.pack_into("<I", hdr, 0x16, len(payload))  # usz
    _struct.pack_into("<I", hdr, 0x1A, len(name))     # nsz
    data = bytes(lead) + bytes(hdr) + name + payload

    chunks = list(iter_chunks(data))
    assert [c.name for c in chunks] == ["TestStream"]
    assert chunks[0].offset == 8
    # and the no-orphan-bytes invariant still holds (early marker -> leading gap)
    doc = swformat.read_document_bytes(data)
    assert doc.reconstruct() == data


@pytest.mark.layer1
def test_detect_format_signatures() -> None:
    """Format detection keys off the right leading-byte signatures."""
    assert detect_format(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64) == "ole2"
    assert detect_format(b"PK\x03\x04" + b"\x00" * 64) == "opc"
    assert detect_format(b"\x00\x00\x00\x00" + b"\x14\x00\x06\x00\x08\x00" + b"\x00" * 54) == "modern"
    assert detect_format(b"not a solidworks file at all, no marker here....") == "unknown"


@pytest.mark.layer1
def test_rol_decode_roundtrip() -> None:
    """ROL decode with key 0 is identity; nonzero key is a left-rotation."""
    assert rol_decode(b"docProps/custom.xml", 0) == "docProps/custom.xml"
    # Rotate-left by k then the decode of the rotated-right source returns it.
    src = b"Header2"
    k = 3
    encoded = bytes(((b >> k) | (b << (8 - k))) & 0xFF for b in src)  # rotate right
    assert rol_decode(encoded, k) == "Header2"
