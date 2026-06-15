"""``swf-docinfo`` — document metadata (created/modified, author, revision,
authoring SOLIDWORKS build, company, template, edit time), **without SOLIDWORKS**
(from ``docProps/core.xml`` + ``docProps/app.xml``; see
:mod:`swformat.api.docprops`).

Usage::

    swf-docinfo FILE          # key: value per non-empty field
    swf-docinfo FILE --json   # full record as JSON

Read-only, pure-Python (last-SAVED values). Exit 0 ok; 2 not-found.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from swformat.api.docprops import read_doc_metadata


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-docinfo", description=__doc__.split("\n")[0])
    p.add_argument("file", type=Path)
    p.add_argument("--json", action="store_true", help="emit the full record as JSON")
    args = p.parse_args(argv)
    if not args.file.exists():
        print(f"ERROR: not found: {args.file}", file=sys.stderr)
        return 2

    d = asdict(read_doc_metadata(args.file))
    if args.json:
        print(json.dumps(d, indent=2))
        return 0
    for k, v in d.items():
        if v is not None and v != "":
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
