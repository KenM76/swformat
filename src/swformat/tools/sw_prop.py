"""``sw_prop`` — get/list/set/delete SOLIDWORKS custom properties (M2 CLI).

Reads and edits custom properties of a .sldprt/.sldasm/.slddrw **without
SOLIDWORKS installed**. Edits go through ``api.properties`` →
``write_with_toc`` (TOC-aware), so the output reopens in real SOLIDWORKS
with the changes.

Usage::

    swf-prop list   FILE            [--config N]
    swf-prop get    FILE NAME        [--config N]
    swf-prop set    FILE NAME VALUE  [--config N] [--out OUT | --in-place]
    swf-prop delete FILE NAME        [--config N] [--out OUT | --in-place]

``set`` updates an existing property or adds a new one. By default ``set``/
``delete`` write to ``FILE.swf<ext>`` unless ``--out`` or ``--in-place`` is
given. ``--in-place`` overwrites the input (a ``.bak`` copy is made first).

``--config N`` targets configuration N's property store
(``docProps/Config-N-Properties.xml``, 0-based index) instead of the
document-level (global) store. Omit it for global properties.

Exit codes: 0 ok; 1 not-found (``get`` of a missing property); 2 usage/IO.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import swformat
from swformat.api.properties import edit_properties, read_properties
from swformat.carchive.cusprops import read_cutlist_props
from swformat.streams.custom_props import CustomPropsError


def _default_out(path: Path) -> Path:
    return path.with_suffix(".swf" + path.suffix.lstrip("."))


def _resolve_out(path: Path, args: argparse.Namespace) -> Path:
    if getattr(args, "in_place", False):
        bak = path.with_suffix(path.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(path, bak)
        return path
    return Path(args.out) if args.out else _default_out(path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-prop", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def _add_config(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--config", type=int, default=None, metavar="N",
                            help="target configuration N's property store "
                                 "(0-based index) instead of the global store")

    sp = sub.add_parser("list", help="list all custom properties")
    sp.add_argument("file", type=Path)
    _add_config(sp)

    sp = sub.add_parser("get", help="print one property's value")
    sp.add_argument("file", type=Path)
    sp.add_argument("name")
    _add_config(sp)

    sp = sub.add_parser("cutlist", help="list cut-list / weldment properties (read-only)")
    sp.add_argument("file", type=Path)

    sp = sub.add_parser("cutlist-set",
                        help="set a user-text cut-list property value "
                             "(non-formula props only)")
    sp.add_argument("file", type=Path)
    sp.add_argument("name")
    sp.add_argument("value")
    sp.add_argument("--out", help="output path (default: FILE.swf<ext>)")
    sp.add_argument("--in-place", action="store_true",
                    help="overwrite input (makes a .bak first)")

    for cmd, helptext in (("set", "set/add a property"), ("delete", "delete a property")):
        sp = sub.add_parser(cmd, help=helptext)
        sp.add_argument("file", type=Path)
        sp.add_argument("name")
        if cmd == "set":
            sp.add_argument("value")
        _add_config(sp)
        sp.add_argument("--out", help="output path (default: FILE.swf<ext>)")
        sp.add_argument("--in-place", action="store_true",
                        help="overwrite input (makes a .bak first)")

    args = p.parse_args(argv)
    path: Path = args.file
    if not path.exists():
        print(f"ERROR: not found: {path}", file=sys.stderr)
        return 2

    config = getattr(args, "config", None)
    scope = "global" if config is None else f"config {config}"
    try:
        if args.cmd == "list":
            props = read_properties(path, config=config)
            if not props:
                print(f"(no custom properties - {scope})")
            for k, v in props.items():
                print(f"{k}={v}")
            return 0

        if args.cmd == "get":
            props = read_properties(path, config=config)
            if args.name not in props:
                print(f"ERROR: no such property ({scope}): {args.name}", file=sys.stderr)
                return 1
            print(props[args.name])
            return 0

        if args.cmd == "cutlist":
            cusprops = swformat.read_document(path).streams().get("Contents/CusProps")
            cut = read_cutlist_props(cusprops) if cusprops else {}
            if not cut:
                print("(no cut-list properties)")
            for k, v in cut.items():
                print(f"{k}={v}")
            return 0

        if args.cmd == "cutlist-set":
            from swformat.api.properties import edit_cutlist_value
            out = _resolve_out(path, args)
            edit_cutlist_value(path, args.name, args.value, out)
            print(f"cut-list {args.name}={args.value}  -> {out}")
            return 0

        out = _resolve_out(path, args)
        if args.cmd == "set":
            existed = args.name in read_properties(path, config=config)
            result = edit_properties(path, out, sets={args.name: args.value}, config=config)
            verb = "set" if existed else "added"
            print(f"{verb} {args.name}={args.value} ({scope})  -> {out}")
        else:  # delete
            result = edit_properties(path, out, deletes=[args.name], config=config)
            print(f"deleted {args.name} ({scope})  -> {out}")
        print(f"({len(result)} properties now: {', '.join(sorted(result))})")
        return 0
    except CustomPropsError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
