"""``swf-views`` — per-view model/sheet inventory of a drawing.

Lists each drawing view with the model it projects and the sheet it sits on, from
``docProps/ISolidWorksInformation.xml`` **without SOLIDWORKS** (see
:mod:`swformat.api.views`). The relational layer joining views ↔ models ↔ sheets.

Usage::

    swf-views FILE              # one "view <TAB> sheet <TAB> model" line per view
    swf-views FILE --basenames  # model file names instead of full paths
    swf-views FILE --count      # just the number of views

Read-only, pure-Python (no SW). Exit codes: 0 ok; 2 file-not-found.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from swformat.api.views import read_views


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-views", description=__doc__.split("\n")[0])
    p.add_argument("file", type=Path)
    p.add_argument("--basenames", action="store_true", help="model file names, not full paths")
    p.add_argument("--count", action="store_true", help="print only the view count")
    args = p.parse_args(argv)

    if not args.file.exists():
        print(f"ERROR: not found: {args.file}", file=sys.stderr)
        return 2

    views = read_views(args.file)
    if args.count:
        print(len(views))
        return 0
    if not views:
        print("(no view inventory found - not a drawing, or no view-list property)")
        return 0
    for v in views:
        model = v.model_basename if args.basenames else v.model
        print(f"{v.name}\t{v.sheet}\t{model}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
