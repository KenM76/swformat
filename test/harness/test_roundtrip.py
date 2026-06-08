"""Layer 1/2 tests — the M1 writer round-trip.

Covers the writer's two contracts:

1. **Lazy round-trip is byte-exact.** ``serialize(read_document(f)) == f``
   for every corpus file. This is the strong form of no-orphan-bytes plus
   verbatim re-emission — the foundation M1's Layer-2 diagnostic relies on.
2. **Re-deflate preserves logical content.** After
   ``force_redeflate_all`` + serialize + re-parse, every stream's
   decompressed bytes are unchanged (only the compressed encoding/offsets
   differ).
3. **Targeted mutation round-trips.** ``set_stream_payload`` changes one
   stream; after serialize + re-parse that stream carries the new bytes
   and all others are untouched.

Corpus files are referenced by absolute path; absent files are skipped.
Marked ``layer2``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

import swformat  # noqa: E402
from swformat.io.writer import (  # noqa: E402
    force_redeflate_all,
    serialize,
    set_stream_payload,
)

CORPUS_CONFIG = ROOT / "test" / "corpus" / "corpus.config.json"


def _existing_corpus() -> list[tuple[str, Path]]:
    if not CORPUS_CONFIG.exists():
        return []
    files = json.loads(CORPUS_CONFIG.read_text(encoding="utf-8")).get("files", [])
    return [(e["tag"], Path(e["path"])) for e in files if Path(e["path"]).exists()]


CORPUS = _existing_corpus()
_IDS = [t for t, _ in CORPUS]
_skip = pytest.mark.skipif(not CORPUS, reason="no corpus files present on this machine")


@pytest.mark.layer2
@_skip
@pytest.mark.parametrize(("tag", "path"), CORPUS, ids=_IDS)
def test_lazy_echo_byte_identical(tag: str, path: Path) -> None:
    """serialize() of an unmodified parse equals the original bytes."""
    doc = swformat.read_document(path)
    assert serialize(doc) == doc.data, f"{tag}: lazy echo not byte-identical"


@pytest.mark.layer2
@_skip
@pytest.mark.parametrize(("tag", "path"), CORPUS, ids=_IDS)
def test_redeflate_preserves_all_streams(tag: str, path: Path) -> None:
    """Re-deflating every chunk keeps all decompressed stream content identical."""
    doc = swformat.read_document(path)
    before = doc.streams()
    n = force_redeflate_all(doc)
    assert n > 0, f"{tag}: nothing re-deflated"
    out = serialize(doc)
    # Re-parse the produced bytes and compare stream maps.
    from swformat.io.reader import read_document
    tmp = path.with_suffix(path.suffix + ".redeflate_tmp")
    try:
        tmp.write_bytes(out)
        after = read_document(tmp).streams()
    finally:
        tmp.unlink(missing_ok=True)
    assert after == before, f"{tag}: re-deflate changed decompressed content"


@pytest.mark.layer2
@_skip
def test_set_stream_payload_roundtrips() -> None:
    """A targeted payload edit survives serialize + re-parse; others untouched."""
    _tag, path = CORPUS[0]
    doc = swformat.read_document(path)
    streams = doc.streams()
    # Pick a small XML stream if present, else the first stream.
    target = next((n for n in streams if n.endswith(".xml")), sorted(streams)[0])
    new_payload = streams[target] + b"<!-- swformat test marker -->"
    assert set_stream_payload(doc, target, new_payload) >= 1

    out = serialize(doc)
    from swformat.io.reader import read_document
    tmp = path.with_suffix(path.suffix + ".mutate_tmp")
    try:
        tmp.write_bytes(out)
        after = read_document(tmp).streams()
    finally:
        tmp.unlink(missing_ok=True)

    assert after[target] == new_payload, "edited stream did not carry the new bytes"
    for n, v in streams.items():
        if n != target:
            assert after[n] == v, f"unrelated stream {n} changed"
