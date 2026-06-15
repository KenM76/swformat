"""``swf-cutlist`` — list a weldment's cut-list items + properties, **without
SOLIDWORKS** (from ``docProps/Config-N-Cutlist-Properties.xml``; see
:mod:`swformat.api.cutlist_xml`).

Usage::

    swf-cutlist FILE           # one item per line: config / feature [qty] flags + props
    swf-cutlist FILE --json    # full records as JSON

Read-only mirror of the binary cut-list store (which has SHIPPED value-editing).
Exit 0 ok; 2 not-found.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from swformat.api.cutlist_xml import read_cutlist_xml


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-cutlist", description=__doc__.split("\n")[0])
    p.add_argument("file", type=Path)
    p.add_argument("--json", action="store_true", help="emit full records as JSON")
    args = p.parse_args(argv)
    if not args.file.exists():
        print(f"ERROR: not found: {args.file}", file=sys.stderr)
        return 2

    items = read_cutlist_xml(args.file)
    if args.json:
        print(json.dumps([asdict(i) for i in items], indent=2))
        return 0
    if not items:
        print("(no cut-list - not a weldment, or none found)")
        return 0
    for it in items:
        flags = "EXCLUDED" if it.exclude_from_cutlist else "-"
        props = " ".join(f"{k}={v}" for k, v in it.properties.items())
        print(f"[{it.config}] {it.feature_name} (qty={it.quantity}, type={it.cutlist_type}) "
              f"{flags}  {props}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
