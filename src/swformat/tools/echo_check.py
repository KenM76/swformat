"""``echo_check`` — read a SW file, write it back, diff at three layers (M1).

THE M1 SELF-CONSISTENCY INSTRUMENT
----------------------------------
Reads a file into a :class:`Document`, writes it via the M1 writer, and
compares the output to the original across the three layers. Two modes:

- **lazy** (default): unmodified read->write. Per the lazy round-trip
  contract this MUST be byte-identical to the input (Layer 0 exact). A
  failure here is a P0 walker/writer bug.
- **--redeflate**: forces every inline chunk to be re-compressed from its
  own decompressed content (identical logical content, different bytes).
  Layer 0 will differ (csz/offsets shift); **Layer 2 — decompressed
  stream content — must be identical** for every stream. This mode exists
  to (a) exercise the writer's re-deflate path and (b) feed a Layer-3
  reopen test that probes whether SW accepts our DEFLATE output and
  whether the trailing TOC tolerates offset shifts (the documented M1
  risk). Layer-2 differences in this mode are filtered through the
  M0.5 stable-mask (see ``io/stable_mask.py``); any NON-masked Layer-2
  difference is a real writer bug.

CLI
---
    python -m swformat.tools.echo_check FILE [--redeflate] [--out PATH] [--keep]

Exit 0 = echo faithful (Layer-0 exact in lazy mode; Layer-2 clean modulo
mask in redeflate mode); 1 = a real difference; 2 = usage/file error.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from swformat.io.reader import read_document
from swformat.io.stable_mask import is_nondeterministic
from swformat.io.writer import force_redeflate_all, serialize
from swformat.types import Document


def _layer2_diffs(original: Document, echoed: Document) -> tuple[list[str], list[str]]:
    """Return (real_diffs, masked_diffs) of differing decompressed streams."""
    so, se = original.streams(), echoed.streams()
    real, masked = [], []
    for n in sorted(set(so) & set(se)):
        if so[n] != se[n]:
            (masked if is_nondeterministic(n) else real).append(n)
    # streams present in one but not the other are always "real"
    for n in sorted(set(so) ^ set(se)):
        real.append(n)
    return real, masked


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="swf-echo-check",
        description="Read a SW file, write it back, and diff at three layers.",
    )
    p.add_argument("file", type=Path)
    p.add_argument("--redeflate", action="store_true",
                   help="re-compress every stream (probe writer + TOC tolerance)")
    p.add_argument("--out", type=Path, help="write the echo here (default: temp, deleted)")
    p.add_argument("--keep", action="store_true", help="keep the echo file (implies temp path printed)")
    args = p.parse_args(argv)

    if not args.file.exists():
        print(f"ERROR: not found: {args.file}", file=sys.stderr)
        return 2

    doc = read_document(args.file)
    print(f"file: {args.file}  (format={doc.fmt}, {len(doc.chunks)} chunks, {len(doc.gaps)} gaps)")
    if doc.fmt != "modern":
        print("NOTE: non-modern file — echo is a single verbatim gap.")

    mode = "redeflate" if args.redeflate else "lazy"
    if args.redeflate:
        n = force_redeflate_all(doc)
        print(f"mode: redeflate — re-compressed {n} inline streams")
    else:
        print("mode: lazy (verbatim round-trip)")

    echoed_bytes = serialize(doc)
    original_bytes = args.file.read_bytes()

    # --- Layer 0 ---
    same_size = len(echoed_bytes) == len(original_bytes)
    byte_equal = echoed_bytes == original_bytes
    print("\n=== Layer 0 - raw bytes ===")
    print(f"  original={len(original_bytes)}  echo={len(echoed_bytes)}  "
          f"byte_equal={byte_equal}")

    # --- determine output path & write ---
    if args.out:
        out_path = args.out
    elif args.keep:
        out_path = args.file.with_suffix(args.file.suffix + ".echo")
    else:
        out_path = Path(tempfile.gettempdir()) / (args.file.stem + ".echo" + args.file.suffix)
    out_path.write_bytes(echoed_bytes)

    # --- Layer 1 + Layer 2 (re-parse the echo) ---
    echoed_doc = read_document(out_path)
    so, se = doc.streams(), echoed_doc.streams()
    print("\n=== Layer 1 - stream set ===")
    added = sorted(set(se) - set(so))
    removed = sorted(set(so) - set(se))
    if not added and not removed:
        print(f"  same stream set ({len(so)} streams)")
    else:
        print(f"  + {added}\n  - {removed}")

    real, masked = _layer2_diffs(doc, echoed_doc)
    print("\n=== Layer 2 - decompressed stream content ===")
    print(f"  identical streams: {len(set(so) & set(se)) - len(real) - len(masked)}")
    if masked:
        print(f"  masked (expected save-noise) diffs: {len(masked)} -> {masked}")
    if real:
        print(f"  REAL diffs (writer bug!): {len(real)} -> {real}")

    if not args.keep and not args.out:
        out_path.unlink(missing_ok=True)
    else:
        print(f"\necho written: {out_path}")

    # --- verdict ---
    print("\n=== Result ===")
    if mode == "lazy":
        ok = byte_equal
        print("  FAITHFUL (byte-identical)" if ok else "  BROKEN — lazy echo not byte-identical (P0)")
    else:
        ok = not real
        print(f"  re-deflate echo: Layer-0 same_size={same_size}; "
              f"Layer-2 {'CLEAN (logical content preserved)' if ok else 'has REAL diffs (BUG)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
