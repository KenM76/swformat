"""``swf-version`` — report a file's internal SOLIDWORKS format version and
whether it is inside SWFormat's tested envelope (telemetry / pre-flight).

Usage::

    swf-version FILE

Prints e.g. ``version: 19000  status: tested``. Status is one of ``tested`` /
``untested-modern`` / ``untested-newer`` / ``unsupported`` (legacy OLE2 / not a
modern file). See ``docs/COMPATIBILITY.md``. Exit 0 on a successful read; 2 if
the file is not found.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from swformat.compat import TESTED_VERSIONS, check_supported


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-version", description=__doc__.split("\n")[0])
    p.add_argument("file", type=Path)
    args = p.parse_args(argv)
    if not args.file.exists():
        print(f"ERROR: not found: {args.file}", file=sys.stderr)
        return 2
    version, status = check_supported(args.file, warn=False)
    print(f"version: {version}  status: {status}  (tested: {sorted(TESTED_VERSIONS)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
