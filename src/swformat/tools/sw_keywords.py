"""``swf-keywords`` — the full searchable entity index of a drawing.

Dumps SOLIDWORKS' own ``swXmlContents/KeyWords`` index — every entity (dimensions,
notes, views, features, sketches, references, …) — **without SOLIDWORKS** (see
:mod:`swformat.api.keywords`). For full-content indexing of a drawing corpus. For
the high-value structured slices prefer the dedicated tools (`swf-dims`,
`swf-notes`, `swf-views`).

Usage::

    swf-keywords FILE                 # per-type counts (summary)
    swf-keywords FILE --type Feature  # dump "name <TAB> text" for one entity type

Read-only, pure-Python (no SW). Exit codes: 0 ok; 2 file-not-found.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from swformat.api.keywords import read_keyword_index


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-keywords", description=__doc__.split("\n")[0])
    p.add_argument("file", type=Path)
    p.add_argument("--type", default=None, help="dump entries of this entity type")
    args = p.parse_args(argv)

    if not args.file.exists():
        print(f"ERROR: not found: {args.file}", file=sys.stderr)
        return 2

    index = read_keyword_index(args.file)
    if not index:
        print("(no keyword index found - not a drawing, or no KeyWords stream)")
        return 0

    if args.type:
        entries = index.get(args.type, [])
        if not entries:
            print(f"(no '{args.type}' entries; types present: {', '.join(sorted(index))})")
            return 0
        for name, text in entries:
            print(f"{name}\t{text}")
        return 0

    # summary: per-type counts (descending)
    for typ, entries in sorted(index.items(), key=lambda kv: -len(kv[1])):
        print(f"    {typ:14s} {len(entries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
