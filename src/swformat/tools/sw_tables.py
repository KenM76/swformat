"""``swf-tables`` — list/extract a drawing's tables (BOM, Revision, …).

Parses the ``swXmlContents/Tables`` XML of a `.SLDDRW` into structured rows
**without SOLIDWORKS** (see :mod:`swformat.api.tables`). High-value structured
metadata for a drawing corpus: bills of materials (item/part/desc/qty) and
revision history.

Usage::

    swf-tables list FILE                 # summary: each table's type, name, rows×cols
    swf-tables dump FILE [--type BOM]    # print every table's rows (optionally filtered)

Output is read-only and pure-Python (no SW). Exit codes: 0 ok; 2 file-not-found.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from swformat.api.tables import read_tables


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-tables", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list", help="summarise each table (type, name, size)")
    lp.add_argument("file", type=Path)
    dp = sub.add_parser("dump", help="print every table's rows (CSV per table)")
    dp.add_argument("file", type=Path)
    dp.add_argument("--type", default=None, help="only this Table_Type (e.g. BOM)")

    args = p.parse_args(argv)
    path: Path = args.file
    if not path.exists():
        print(f"ERROR: not found: {path}", file=sys.stderr)
        return 2

    tables = read_tables(path, table_type=getattr(args, "type", None))
    if not tables:
        print("(no tables found - not a drawing, or it has no tables)")
        return 0

    if args.cmd == "list":
        print(f"{len(tables)} table(s):")
        for t in tables:
            print(f"    {t.table_type:12s} {t.num_rows}x{t.num_cols}  {t.name}")
        return 0

    # dump: CSV per table to stdout
    w = csv.writer(sys.stdout)
    for t in tables:
        print(f"# {t.table_type}: {t.name} ({t.num_rows}x{t.num_cols})")
        for row in t.rows:
            w.writerow(row)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
