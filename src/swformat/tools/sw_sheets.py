"""``sw_sheets`` — drawing sheet operations CLI (M4).

Read side implemented: list a drawing's sheet names + count from the
``SheetPreviews/SheetNames`` stream **without SOLIDWORKS** (see
:mod:`swformat.api.sheets`). Modifying sheets (rename / reorder / add) is later
M4 work.

Usage::

    swf-sheets list     FILE          # one sheet name per line
    swf-sheets count    FILE          # number of sheets
    swf-sheets previews FILE OUT_DIR  # extract each sheet's rendered PNG preview
    swf-sheets formats  FILE          # per-sheet paper size + scale

Exit codes: 0 ok; 2 file-not-found / no sheets found.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from swformat.api.sheets import (
    SheetError,
    extract_sheet_previews,
    read_sheet_formats,
    read_sheets,
    rename_sheet,
    sheet_count,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-sheets", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd, helptext in (("list", "list sheet names"),
                          ("count", "print the number of sheets")):
        sp = sub.add_parser(cmd, help=helptext)
        sp.add_argument("file", type=Path)
    rp = sub.add_parser("rename", help="rename a sheet")
    rp.add_argument("file", type=Path)
    rp.add_argument("old", help="current sheet name")
    rp.add_argument("new", help="new sheet name")
    rp.add_argument("-o", "--out", type=Path, default=None,
                    help="output path (default: FILE with a .renamed suffix)")
    pp = sub.add_parser("previews", help="extract each sheet's rendered PNG preview")
    pp.add_argument("file", type=Path)
    pp.add_argument("out_dir", type=Path, help="directory to write the sheet PNGs into")
    fp = sub.add_parser("formats", help="per-sheet paper size + scale")
    fp.add_argument("file", type=Path)

    args = p.parse_args(argv)
    path: Path = args.file
    if not path.exists():
        print(f"ERROR: not found: {path}", file=sys.stderr)
        return 2

    if args.cmd == "count":
        print(sheet_count(path))
        return 0

    if args.cmd == "formats":
        formats = read_sheet_formats(path)
        if not formats:
            print("(no sheet format data found - not a drawing, or unavailable)")
            return 2
        for f in formats:
            print(f"{f.name}\t{f.width*1000:.0f}x{f.height*1000:.0f}mm\tscale {f.scale}")
        return 0

    if args.cmd == "previews":
        written = extract_sheet_previews(path, args.out_dir)
        if not written:
            print("(no sheet preview images found - not a drawing, or no Images/Sheet_N)")
            return 2
        print(f"wrote {len(written)} sheet preview PNG(s) to {args.out_dir}")
        for w in written:
            print(f"  {w.name}")
        return 0

    if args.cmd == "rename":
        out: Path = args.out or path.with_suffix(f".renamed{path.suffix}")
        try:
            names = rename_sheet(path, args.old, args.new, out)
        except SheetError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"renamed sheet {args.old!r} -> {args.new!r}; wrote {out}")
        print("sheets: " + ", ".join(names))
        return 0

    names = read_sheets(path)
    if not names:
        print("(no sheets found - not a drawing, or no SheetPreviews/SheetNames)")
        return 2
    for name in names:
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
