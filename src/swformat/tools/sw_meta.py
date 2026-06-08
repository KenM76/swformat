"""``swf-meta`` — one-call structured metadata for a drawing (JSON for pipelines).

Bundles sheets, referenced models, tables/BOM, and text annotations into one
JSON-serialisable record **without SOLIDWORKS** (see
:mod:`swformat.api.metadata`). Designed for a corpus/training pipeline: one call
per drawing → all the metadata.

Usage::

    swf-meta FILE              # human summary (counts + a few samples)
    swf-meta FILE --json       # full metadata as JSON (for a pipeline)
    swf-meta FILE --json --no-rows   # JSON without the (large) table row arrays

Read-only, pure-Python (no SW). Exit codes: 0 ok; 2 file-not-found.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from swformat.api.metadata import read_metadata


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swf-meta", description=__doc__.split("\n")[0])
    p.add_argument("file", type=Path)
    p.add_argument("--json", action="store_true", help="emit the full metadata as JSON")
    p.add_argument("--no-rows", action="store_true",
                   help="with --json, omit the large table row arrays")
    args = p.parse_args(argv)

    if not args.file.exists():
        print(f"ERROR: not found: {args.file}", file=sys.stderr)
        return 2

    meta = read_metadata(args.file, include_table_rows=not args.no_rows)

    if args.json:
        json.dump(meta, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    # human summary — shape depends on doc_type (drawing vs part/assembly)
    print(f"{meta['file']}  [{meta.get('doc_type', '?')}]")
    if meta.get("doc_type") == "drawing":
        print(f"  sheets:            {meta['sheet_count']} ({', '.join(meta['sheet_names'][:6])}"
              f"{' …' if len(meta['sheet_names']) > 6 else ''})")
        print(f"  sheet previews:    {meta['sheet_preview_count']} PNG(s)")
        print(f"  referenced models: {len(meta['referenced_models'])}")
        print(f"  tables:            {len(meta['tables'])} ({meta['bom_count']} BOM)")
        print(f"  annotation strings:{len(meta['annotation_text'])}")
        print(f"  dimensions:        {len(meta['dimensions'])}")
    else:  # part / assembly
        print(f"  PARTNO:            {meta['properties'].get('PARTNO', '')}")
        print(f"  DESCRIPTION:       {meta['properties'].get('DESCRIPTION', '')}")
        print(f"  material:          {meta['material']}")
        print(f"  configurations:    {', '.join(meta['configurations'])}")
        print(f"  properties:        {len(meta['properties'])}")
        print(f"  dimensions:        {len(meta['dimensions'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
