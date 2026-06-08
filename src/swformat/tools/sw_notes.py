"""``swf-notes`` — list a drawing's visible text annotations (notes, title block).

Extracts the readable text of a `.SLDDRW` — general notes, title-block field
values, callouts — from the ``swXmlContents/CustomProperties`` (``DisplayItems``)
XML, resolving SOLIDWORKS property tokens to their cached values, **without
SOLIDWORKS** (see :mod:`swformat.api.annotations`).

Usage::

    swf-notes FILE            # one annotation string per line (de-duplicated)
    swf-notes FILE --count    # just the count

Read-only, pure-Python (no SW). Exit codes: 0 ok; 2 file-not-found.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from swformat.api.annotations import read_annotation_text


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-notes", description=__doc__.split("\n")[0])
    p.add_argument("file", type=Path)
    p.add_argument("--count", action="store_true", help="print only the count")
    args = p.parse_args(argv)

    if not args.file.exists():
        print(f"ERROR: not found: {args.file}", file=sys.stderr)
        return 2

    notes = read_annotation_text(args.file)
    if args.count:
        print(len(notes))
        return 0
    if not notes:
        print("(no text annotations found - not a drawing, or none present)")
        return 0
    for n in notes:
        print(n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
