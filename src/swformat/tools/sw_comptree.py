"""``swf-comptree`` — list an assembly's component tree + per-component state,
**without SOLIDWORKS** (from ``swXmlContents/COMPINSTANCETREE``; see
:mod:`swformat.api.components`).

One read yields every component instance with name, resolved path, referenced
config, and the exclude-from-BOM / flexible / hidden / suppressed / virtual flags
— replacing a per-component COM loop.

Usage::

    swf-comptree FILE              # one component per line: name [config] flags path
    swf-comptree FILE --excluded   # only components excluded from the BOM
    swf-comptree FILE --json       # full records as JSON

Read-only, pure-Python (reflects the last-SAVED state). Exit 0 ok; 2 not-found.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from swformat.api.components import read_component_tree, read_part_config_tree


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-comptree", description=__doc__.split("\n")[0])
    p.add_argument("file", type=Path)
    p.add_argument("--excluded", action="store_true",
                   help="list only components excluded from the BOM")
    p.add_argument("--json", action="store_true", help="emit full records as JSON")
    args = p.parse_args(argv)
    if not args.file.exists():
        print(f"ERROR: not found: {args.file}", file=sys.stderr)
        return 2

    comps = read_component_tree(args.file)
    if not comps and not args.excluded:
        # Not an assembly (no <swReference> components) — show the PART config tree.
        info = read_part_config_tree(args.file)
        if info.configs or info.path:
            if args.json:
                print(json.dumps(asdict(info), indent=2))
            else:
                print(f"PART  {info.path or ''}  (created {info.creation_time})")
                print(f"configs ({len(info.configs)}): {', '.join(info.configs)}"
                      f"  [active: {info.most_recent_config}]")
            return 0
    if args.excluded:
        comps = [c for c in comps if c.exclude_from_bom]
    if args.json:
        print(json.dumps([asdict(c) for c in comps], indent=2))
        return 0
    if not comps:
        print("(no components - not an assembly, or none found)")
        return 0
    for c in comps:
        flags = ",".join(f for f, v in (
            ("exclBOM", c.exclude_from_bom), ("flex", c.flexible),
            ("hidden", c.hidden), ("suppressed", c.suppressed),
            ("virtual", c.virtual)) if v) or "-"
        print(f"{c.name}\t[{c.config}]\t{flags}\t{c.path or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
