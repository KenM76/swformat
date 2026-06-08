"""``sw_sketch`` — drawing sketch-geometry reader CLI (M5.3, read side).

Decode the ``sgSketch`` point/entity arrays in a drawing's ``Contents/Definition``
and print each sheet sketch's classification + coordinates, **without
SOLIDWORKS** (see :mod:`swformat.api.sketches` for the byte layout + scope).

Usage::

    swf-sketch list          FILE              # index, kind, point count per sgSketch
    swf-sketch dump          FILE              # the above plus every (x, y, z) triple
    swf-sketch relations     FILE              # geometric constraints (type) across sketches
    swf-sketch dimensions    FILE              # driving dimensions (kind + value)
    swf-sketch move-point    FILE OUT I X Y [--sketch-index N]  # MODIFY: move point I of sketch N to (X,Y)
    swf-sketch move-dim-text FILE OUT X Y      # MODIFY: move the dimension text to (X,Y), write OUT

Output is read-only and pure-Python (no SW). Coordinates are in the drawing's
sheet space (meters, the SW SI convention), rounded to 6 dp.

Exit codes: 0 ok; 2 file-not-found / not a drawing / no sketches.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from swformat.api.sketches import (
    move_dimension_text,
    move_sketch_point,
    read_sketch_dimensions,
    read_sketch_relations,
    read_sketches,
    set_dimension_value,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-sketch", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd, helptext in (("list", "list each sketch's kind + point count"),
                          ("dump", "list sketches and all coordinate triples"),
                          ("relations", "list the geometric constraints (type) in the sketches"),
                          ("dimensions", "list the driving dimensions (kind + value)")):
        sp = sub.add_parser(cmd, help=helptext)
        sp.add_argument("file", type=Path)
    # modify subcommands (write to OUT): edit geometry / annotation, span-preserving.
    mp = sub.add_parser("move-point", help="move a sketch point: FILE OUT INDEX X Y [--sketch-index N]")
    mp.add_argument("file", type=Path)
    mp.add_argument("out", type=Path)
    mp.add_argument("index", type=int)
    mp.add_argument("x", type=float)
    mp.add_argument("y", type=float)
    mp.add_argument("--sketch-index", type=int, default=0, help="0-based sketch index (default 0)")
    md = sub.add_parser("move-dim-text", help="move a dimension's text: FILE OUT X Y")
    md.add_argument("file", type=Path)
    md.add_argument("out", type=Path)
    md.add_argument("x", type=float)
    md.add_argument("y", type=float)
    sv = sub.add_parser("set-dim-value", help="set a dimension's value (resizes geometry): FILE OUT VALUE [--index N]")
    sv.add_argument("file", type=Path)
    sv.add_argument("out", type=Path)
    sv.add_argument("value", type=float)
    sv.add_argument("--index", type=int, default=0, help="0-based dimension index (default 0)")

    args = p.parse_args(argv)
    path: Path = args.file
    if not path.exists():
        print(f"ERROR: not found: {path}", file=sys.stderr)
        return 2

    if args.cmd == "move-point":
        pts = move_sketch_point(path, args.out, args.index, args.x, args.y,
                                sketch_index=args.sketch_index)
        print(f"moved sketch[{args.sketch_index}] point {args.index} -> ({args.x}, {args.y}); wrote {args.out}")
        print(f"  output sketch[{args.sketch_index}] points: {pts}")
        return 0

    if args.cmd == "move-dim-text":
        xy = move_dimension_text(path, args.out, args.x, args.y)
        print(f"moved dimension text -> {xy}; wrote {args.out}")
        return 0

    if args.cmd == "set-dim-value":
        v = set_dimension_value(path, args.out, args.value, dim_index=args.index)
        print(f"set dimension[{args.index}] value -> {v} (SW resizes the geometry on reopen); wrote {args.out}")
        return 0

    if args.cmd == "relations":
        rels = read_sketch_relations(path)
        if not rels:
            print("(no sketch relations found - not a drawing, or sketches carry no constraints)")
            return 2
        counts = Counter(r.type_name for r in rels)
        print(f"{len(rels)} relation(s):")
        for name, n in sorted(counts.items()):
            print(f"    {name}: {n}")
        print("  bindings (entity indices):")
        for i, r in enumerate(rels):
            joins = ", ".join(str(x) for x in r.entity_indices) or "?"
            print(f"    [{i}] {r.type_name} -> entities [{joins}]")
        return 0

    if args.cmd == "dimensions":
        dims = read_sketch_dimensions(path)
        if not dims:
            print("(no sketch dimensions found - not a drawing, or sketches carry no dimensions)")
            return 2
        print(f"{len(dims)} dimension(s):")
        for i, d in enumerate(dims):
            val = "?" if d.value is None else d.value
            note = "  (value = diameter)" if d.kind == "radial" else ""
            refs = ", ".join(str(x) for x in d.refs) or "?"
            place = f"  @ {d.text_xy}" if d.text_xy else ""
            print(f"    [{i}] {d.kind}: {val}{note}  refs [{refs}]{place}")
        return 0

    sketches = read_sketches(path)
    if not sketches:
        print("(no sketches found - not a drawing, or no Contents/Definition sgSketch)")
        return 2

    for i, s in enumerate(sketches):
        print(f"sketch[{i}] @{s.offset}: {s.description}")
        if args.cmd == "dump":
            for x, y, z in s.points:
                print(f"    ({x}, {y}, {z})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
