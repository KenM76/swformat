"""``diff_files`` — three-layer structural diff of two SOLIDWORKS files.

This is the fundamental reverse-engineering instrument (see
``docs/REVERSE_ENGINEERING.md`` §1 "the diff-pair method"). Given two
files that differ in exactly one knowable way, it localises *where* in
the format that difference lives, across three layers:

- **Layer 0 — raw bytes.** File sizes; if equal, the count and clustering
  of differing byte positions. A sanity layer: huge raw diffs on an
  "unchanged" twin-save are the save non-determinism M0.5 quantifies.

- **Layer 1 — chunks/streams.** Which stream names were added, removed,
  or changed compressed size. Operates on the walker's chunk records.

- **Layer 2 — stream content.** For streams present in both files, whether
  their *decompressed* payloads differ, with the first differing offset
  and a short hex window. This is where a custom-property edit or a
  dimension change actually surfaces.

CLI
---
    python -m swformat.tools.diff_files A.sldprt B.sldprt [--max-streams N]

Exit codes: 0 = files identical at all layers; 1 = differences found;
2 = usage / file error. (A nonzero "differences found" code makes the
tool scriptable in falsification loops.)

REUSABLE API
------------
:func:`byte_diff_clusters` and :func:`locate_clusters` are imported by
``test/harness/twin_save_baseline.py`` (M0.5) so the twin-save analysis
and the diff CLI share one definition of "where do these files differ".
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from swformat.io.reader import read_document
from swformat.types import Chunk, Document


@dataclass(slots=True)
class Cluster:
    """A maximal run of consecutive differing byte positions.

    ``start`` is the file offset of the first differing byte; ``length``
    the number of consecutive differing bytes. Adjacent differing bytes
    separated by fewer than ``gap_tolerance`` matching bytes are merged
    into one cluster (so a field with a couple of equal bytes in the
    middle still reads as a single locus, not noise).
    """

    start: int
    length: int

    @property
    def end(self) -> int:
        return self.start + self.length


def byte_diff_clusters(
    a: bytes, b: bytes, *, gap_tolerance: int = 8
) -> list[Cluster]:
    """Return clusters of differing byte positions between equal-length buffers.

    Requires ``len(a) == len(b)`` (the caller checks; size differences are
    a Layer-0 finding handled separately). Two differing positions within
    ``gap_tolerance`` bytes of each other are merged into one cluster.
    """
    if len(a) != len(b):
        raise ValueError("byte_diff_clusters requires equal-length buffers")
    clusters: list[Cluster] = []
    run_start = -1
    last_diff = -1
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        if x != y:
            if run_start < 0:
                run_start = i
            elif i - last_diff > gap_tolerance:
                clusters.append(Cluster(run_start, last_diff - run_start + 1))
                run_start = i
            last_diff = i
    if run_start >= 0:
        clusters.append(Cluster(run_start, last_diff - run_start + 1))
    return clusters


def locate_clusters(doc: Document, clusters: list[Cluster]) -> list[tuple[Cluster, str]]:
    """Label each cluster with the record/stream it falls in (against ``doc``).

    The label is ``"<stream-name> [payload|header]"`` for a chunk, or
    ``"<gap @offset>"`` for a gap. A cluster spanning a record boundary is
    labelled by the record owning its first byte (rare; flagged with ``+``).
    """
    out: list[tuple[Cluster, str]] = []
    for cl in clusters:
        rec = doc.locate(cl.start)
        if rec is None:
            out.append((cl, "<unmapped>"))
            continue
        if isinstance(rec, Chunk):
            region = "header" if cl.start < rec.data_offset else "payload"
            label = f"{rec.name} [{region}]"
        else:
            label = f"<gap @{rec.offset}>"
        if cl.end > rec.end:
            label += "+"  # spills past this record into the next
        out.append((cl, label))
    return out


def _hex_window(data: bytes, offset: int, width: int = 8) -> str:
    """A short hex preview of ``data`` starting at ``offset``."""
    chunk = data[offset : offset + width]
    return chunk.hex(" ")


def diff_documents(doc_a: Document, doc_b: Document, *, max_streams: int = 25) -> bool:
    """Print the three-layer diff. Return True if any difference was found."""
    differ = False
    a, b = doc_a.data, doc_b.data

    # --- Layer 0: raw bytes -------------------------------------------------
    print("=== Layer 0 - raw bytes ===")
    print(f"  size A = {len(a)}   size B = {len(b)}")
    if len(a) != len(b):
        print(f"  -> sizes DIFFER by {abs(len(a) - len(b))} bytes "
              f"(chunk layout shifted; Layer-0 byte diff skipped)")
        differ = True
    else:
        clusters = byte_diff_clusters(a, b)
        total_diff = sum(c.length for c in clusters)
        if total_diff == 0:
            print("  -> byte-identical")
        else:
            differ = True
            pct = 100.0 * total_diff / len(a) if a else 0.0
            print(f"  -> {total_diff} bytes differ in {len(clusters)} clusters "
                  f"({pct:.4f}% of file)")
            for cl, label in locate_clusters(doc_a, clusters)[:max_streams]:
                print(f"       @{cl.start:>9}  len={cl.length:<6} {label}")
            if len(clusters) > max_streams:
                print(f"       ... and {len(clusters) - max_streams} more clusters")

    # --- Layer 1: chunks / streams -----------------------------------------
    print("\n=== Layer 1 - chunks / streams ===")
    # Use compressed size per (first) chunk of each name, mirroring stream id.
    csz_a = _first_chunk_csz(doc_a)
    csz_b = _first_chunk_csz(doc_b)
    names_a, names_b = set(csz_a), set(csz_b)
    added = sorted(names_b - names_a)
    removed = sorted(names_a - names_b)
    common = sorted(names_a & names_b)
    resized = [(n, csz_a[n], csz_b[n]) for n in common if csz_a[n] != csz_b[n]]
    if not (added or removed or resized):
        print("  -> same stream set, same compressed sizes")
    else:
        differ = True
        for n in added:
            print(f"  + added   {n}  (csz={csz_b[n]})")
        for n in removed:
            print(f"  - removed {n}  (csz={csz_a[n]})")
        for n, ca, cb in resized:
            print(f"  ~ resized {n}  csz {ca} -> {cb}")

    # --- Layer 2: stream content -------------------------------------------
    print("\n=== Layer 2 - stream content (decompressed) ===")
    streams_a = doc_a.streams()
    streams_b = doc_b.streams()
    content_diffs = 0
    for n in sorted(set(streams_a) & set(streams_b)):
        pa, pb = streams_a[n], streams_b[n]
        if pa == pb:
            continue
        content_diffs += 1
        if content_diffs <= max_streams:
            first = next((i for i in range(min(len(pa), len(pb))) if pa[i] != pb[i]),
                         min(len(pa), len(pb)))
            print(f"  ~ {n}")
            print(f"      len {len(pa)} -> {len(pb)}; first diff @{first}")
            print(f"        A: {_hex_window(pa, first)}")
            print(f"        B: {_hex_window(pb, first)}")
    if content_diffs == 0:
        print("  -> all shared streams have identical decompressed content")
    else:
        differ = True
        if content_diffs > max_streams:
            print(f"  ... and {content_diffs - max_streams} more streams differ")

    return differ


def _first_chunk_csz(doc: Document) -> dict[str, int]:
    """Map stream name -> compressed size of its first chunk occurrence."""
    out: dict[str, int] = {}
    for ch in doc.chunks:
        out.setdefault(ch.name, ch.csz)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="swf-diff-files",
        description="Three-layer structural diff of two SOLIDWORKS files.",
    )
    parser.add_argument("file_a", type=Path)
    parser.add_argument("file_b", type=Path)
    parser.add_argument(
        "--max-streams", type=int, default=25,
        help="Max clusters/streams to print per layer (default 25).",
    )
    args = parser.parse_args(argv)

    for p in (args.file_a, args.file_b):
        if not p.exists():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            return 2

    doc_a = read_document(args.file_a)
    doc_b = read_document(args.file_b)
    print(f"A: {args.file_a}  (format={doc_a.fmt})")
    print(f"B: {args.file_b}  (format={doc_b.fmt})\n")
    if doc_a.fmt != "modern" or doc_b.fmt != "modern":
        print("NOTE: one or both files are not modern-format; "
              "Layer 1/2 will be empty for non-modern inputs.")

    differ = diff_documents(doc_a, doc_b, max_streams=args.max_streams)
    print("\n=== Result ===")
    print("  DIFFERENCES FOUND" if differ else "  IDENTICAL at all layers")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
