"""``dump_streams`` — write every decompressed stream of a SOLIDWORKS file to a directory.

The simplest inspection instrument: it walks a modern-format file (Layer 1),
inflates every inline stream (Layer 2), and writes each stream's *decompressed*
payload to a file under an output directory. Stream names contain ``/`` (e.g.
``docProps/custom.xml``, ``Contents/CusProps``, ``_MO_VERSION_19000/...``); the
path separators are mirrored as sub-directories under ``OUTDIR`` so the layout
on disk matches the in-file stream namespace. No SOLIDWORKS required.

This is the read-only counterpart to ``diff_files``: where ``diff_files``
compares two files, ``dump_streams`` explodes one file so you can grep, hex-dump,
or eyeball individual streams with ordinary tools.

CLI
---
    python -m swformat.tools.dump_streams FILE OUTDIR [--list]

- ``FILE``   : a ``.sldprt`` / ``.sldasm`` / ``.slddrw`` (modern 2015+ format).
- ``OUTDIR`` : destination directory (created if absent). Each stream becomes
  ``OUTDIR/<stream-name>`` with parent dirs created as needed.
- ``--list`` : don't write anything; just print ``name<TAB>size`` for every
  stream (a quick table of contents).

Exit codes: 0 = streams written/listed; 2 = file not found, not a modern file,
or no decodable streams.

WHY DECOMPRESSED (not raw): the payload bytes are what every downstream tool
(XML readers, the CArchive decoders, image viewers for ``Preview*``) actually
consume. The raw-DEFLATE on-disk form is an encoding detail; ``swformat`` round-
trips it losslessly via the lazy writer, so dumping the inflated content is the
useful view. Streams that are non-inline or fail to inflate are skipped (they
carry no decodable payload) and reported on stderr.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import swformat


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="swf-dump-streams", description=__doc__.split("\n")[0]
    )
    p.add_argument("file", type=Path)
    p.add_argument("outdir", type=Path, nargs="?",
                   help="output directory (omit with --list)")
    p.add_argument("--list", action="store_true",
                   help="print name<TAB>size for each stream; write nothing")
    args = p.parse_args(argv)

    if not args.file.exists():
        print(f"ERROR: not found: {args.file}", file=sys.stderr)
        return 2
    if not args.list and args.outdir is None:
        print("ERROR: OUTDIR is required unless --list is given", file=sys.stderr)
        return 2

    doc = swformat.read_document(args.file)
    if doc.fmt != "modern":
        print(f"ERROR: {args.file} is not a modern-format file (detected "
              f"{doc.fmt!r}); nothing to dump", file=sys.stderr)
        return 2

    streams = doc.streams()
    if not streams:
        print(f"ERROR: no decodable streams in {args.file}", file=sys.stderr)
        return 2

    if args.list:
        for name, payload in sorted(streams.items()):
            print(f"{name}\t{len(payload)}")
        return 0

    outdir: Path = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, payload in sorted(streams.items()):
        # Mirror the stream namespace as sub-directories. Reject any name that
        # would escape OUTDIR (defensive against pathological ".." in a name).
        dest = (outdir / name).resolve()
        if not str(dest).startswith(str(outdir.resolve())):
            print(f"skip (unsafe name): {name}", file=sys.stderr)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        written += 1

    print(f"wrote {written} streams to {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
