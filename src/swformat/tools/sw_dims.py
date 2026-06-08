"""``swf-dims`` — list a drawing's DISPLAYED dimension values (real-file capable).

Extracts each dimension's id + displayed value (``D4: 38"``) from the drawing's
``swXmlContents/KeyWords`` index **without SOLIDWORKS** (see
:mod:`swformat.api.dimensions`). This is the reliable real-file dimension reader —
the values SOLIDWORKS rendered on the sheets.

Usage::

    swf-dims FILE            # one "name <TAB> value" line per dimension
    swf-dims FILE --count    # just the number of dimensions

Read-only, pure-Python (no SW). Exit codes: 0 ok; 2 file-not-found.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from swformat.api.dimensions import read_dimension_values


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-dims", description=__doc__.split("\n")[0])
    p.add_argument("file", type=Path)
    p.add_argument("--count", action="store_true", help="print only the dimension count")
    args = p.parse_args(argv)

    if not args.file.exists():
        print(f"ERROR: not found: {args.file}", file=sys.stderr)
        return 2

    dims = read_dimension_values(args.file)
    if args.count:
        print(len(dims))
        return 0
    if not dims:
        print("(no dimension values found - not a drawing, or no KeyWords index)")
        return 0
    for d in dims:
        print(f"{d.name}\t{d.value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
