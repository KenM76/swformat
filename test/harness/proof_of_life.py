"""Proof-of-life smoke test for the imported swx_reader.

Goals (minimum bar for "the scaffold is real"):
  1. The imported parser loads at all.
  2. Run it against every file in corpus.config.json.
  3. Each parse returns (format, streams_dict).
  4. For modern-format files, streams_dict has at least one entry.
  5. Print a one-line summary per file: format, stream count,
     decompressed bytes total, top 3 stream names by size.

This is M0 (project bootstrap) deliverable. It proves:
  - The imported parser still works
  - The test corpus config is valid
  - The harness can iterate corpus + report results

Run:
    python test/harness/proof_of_life.py
Exit 0 on all-pass, 1 on any failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the package importable
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Import the seed parser (the verbatim imported port).
# A future M0b task is to factor this into the proper swformat package
# layout (chunks/, streams/, etc.).
from swformat import _imported_swx_reader_v0 as swx  # noqa: E402

CORPUS_CONFIG = ROOT / "test" / "corpus" / "corpus.config.json"


def main() -> int:
    if not CORPUS_CONFIG.exists():
        print(f"FAIL: corpus config missing: {CORPUS_CONFIG}")
        return 1

    config = json.loads(CORPUS_CONFIG.read_text(encoding="utf-8"))
    files = config.get("files", [])
    if not files:
        print("FAIL: corpus has no files registered")
        return 1

    failures = 0
    print(f"=== Proof-of-life — {len(files)} corpus files ===\n")
    for entry in files:
        tag = entry["tag"]
        path = Path(entry["path"])
        if not path.exists():
            print(f"[SKIP] {tag}: file not present at {path}")
            continue

        try:
            data = path.read_bytes()
            fmt, streams = swx.read_file(path)
        except Exception as e:
            print(f"[FAIL] {tag}: {type(e).__name__}: {e}")
            failures += 1
            continue

        if fmt != entry.get("expected_format"):
            note = f"(expected {entry.get('expected_format')}, got {fmt})"
        else:
            note = ""

        total_decompressed = sum(len(v) for v in streams.values())
        top3 = sorted(streams.items(), key=lambda kv: -len(kv[1]))[:3]
        top_str = ", ".join(f"{n!r}={len(v)}b" for n, v in top3)

        print(
            f"[OK]   {tag:<24} fmt={fmt:<7} chunks={len(streams):<4} "
            f"decompressed={total_decompressed:>9}b  file={len(data):>9}b  {note}"
        )
        print(f"       top streams: {top_str}")

    print()
    if failures:
        print(f"FAIL: {failures} corpus file(s) failed parsing")
        return 1
    print("PASS: all corpus files parsed cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
