"""``swf-refs`` — list the external model files a drawing references.

Extracts the part/assembly/drawing models a `.SLDDRW` depicts, from its
``Contents/Definition`` stream, **without SOLIDWORKS** (see
:mod:`swformat.api.references` for the method + scope). High-value metadata for
joining drawings to their source geometry or labelling a training corpus.

Usage::

    swf-refs FILE              # one referenced model path per line (sorted, unique)
    swf-refs FILE --basenames  # just the file names (Part13.SLDPRT) — drift-proof
    swf-refs FILE --count      # just the count

Output is read-only and pure-Python (no SW). Exit codes: 0 ok (even if zero refs);
2 file-not-found.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from swformat.api.references import read_referenced_models


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-refs", description=__doc__.split("\n")[0])
    p.add_argument("file", type=Path)
    p.add_argument("--basenames", action="store_true",
                   help="print just the file names, not the full stored paths")
    p.add_argument("--count", action="store_true", help="print only the count")
    args = p.parse_args(argv)

    if not args.file.exists():
        print(f"ERROR: not found: {args.file}", file=sys.stderr)
        return 2

    refs = read_referenced_models(args.file, basenames=args.basenames)
    if args.count:
        print(len(refs))
        return 0
    if not refs:
        print("(no referenced models found - not a drawing, or it references nothing)")
        return 0
    for r in refs:
        print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
