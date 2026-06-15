"""``swf-material`` — applied material(s) + physical properties, **without
SOLIDWORKS** (from ``swXmlContents/MATERIALTREE``; see
:mod:`swformat.api.materials`).

Usage::

    swf-material FILE          # classification | name (matid) then property = value lines
    swf-material FILE --json   # full records as JSON

Property values are the raw SI strings SOLIDWORKS stored (density kg/m^3, moduli
Pa, …). Read-only, pure-Python. Exit 0 ok; 2 not-found.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from swformat.api.materials import read_materials


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-material", description=__doc__.split("\n")[0])
    p.add_argument("file", type=Path)
    p.add_argument("--json", action="store_true", help="emit full records as JSON")
    args = p.parse_args(argv)
    if not args.file.exists():
        print(f"ERROR: not found: {args.file}", file=sys.stderr)
        return 2

    mats = read_materials(args.file)
    if args.json:
        print(json.dumps([asdict(m) for m in mats], indent=2))
        return 0
    if not mats:
        print("(no applied material found)")
        return 0
    for m in mats:
        print(f"{m.classification} | {m.name} (matid={m.matid})")
        for k, v in m.properties.items():
            print(f"    {k} = {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
