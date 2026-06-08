"""``sw_configs`` — list / rename a SOLIDWORKS file's configurations (M3).

Reads (``list`` / ``count``) the configuration names recorded in
``Contents/CMgrHdr2`` **without SOLIDWORKS installed** (decoded from the binary
config-manager header; SW-verified to match ``GetConfigurationNames``), and
renames a configuration (``rename``) by editing that authoritative stream —
SW-verified (SW 2026 / v19000) to reopen with no repair and report the new name.
Other modify ops (set-active / derived flags) remain later M3 work.

Usage::

    swf-configs list   FILE                       # one configuration name per line
    swf-configs count  FILE                        # just the number of configurations
    swf-configs rename FILE OLD NEW [-o OUT]       # rename a configuration

For ``rename`` the default output is ``FILE`` with a ``.renamed`` suffix before
the extension; pass ``-o/--out`` to choose the path. The input file is never
modified in place.

Exit codes: 0 ok; 2 file-not-found / no configurations found / rename error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from swformat.api.configurations import (
    ConfigurationError,
    configuration_count,
    read_configurations,
    rename_configuration,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-configs", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd, helptext in (("list", "list configuration names"),
                          ("count", "print the number of configurations")):
        sp = sub.add_parser(cmd, help=helptext)
        sp.add_argument("file", type=Path)
    rp = sub.add_parser("rename", help="rename a configuration")
    rp.add_argument("file", type=Path)
    rp.add_argument("old", help="current configuration name")
    rp.add_argument("new", help="new configuration name")
    rp.add_argument("-o", "--out", type=Path, default=None,
                    help="output path (default: FILE with a .renamed suffix)")

    args = p.parse_args(argv)
    path: Path = args.file
    if not path.exists():
        print(f"ERROR: not found: {path}", file=sys.stderr)
        return 2

    if args.cmd == "count":
        print(configuration_count(path))
        return 0

    if args.cmd == "rename":
        out: Path = args.out or path.with_suffix(f".renamed{path.suffix}")
        try:
            names = rename_configuration(path, args.old, args.new, out)
        except ConfigurationError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"renamed {args.old!r} -> {args.new!r}; wrote {out}")
        print("configurations: " + ", ".join(names))
        return 0

    names = read_configurations(path)
    if not names:
        print("(no configurations found)")
        return 2
    for name in names:
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
