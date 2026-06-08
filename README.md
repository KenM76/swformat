# SWFormat — open-source partial I/O for the SOLIDWORKS native file format

> **Goal.** Read, and some slight modifcation support of SOLIDWORKS `.sldprt`,
> `.sldasm`, and `.slddrw` files without any commercial closed-source library.
>
> **Status (2026-06-08).** Read works (chunk walker, all streams, custom +
> cut-list + config properties). **Write works via SPAN PRESERVATION** —
> edit, delete, and add brand-new global custom properties: SOLIDWORKS reopens
> the result and reflects the change (SW-verified on a part). The writer
> re-deflates the edited stream and pads it back to its original compressed
> size so no chunk offsets shift — the only form SW accepts. **Limit:** an
> edit that grows a stream beyond its original compressed span (e.g. adding a
> property to a slack-less per-config stream) raises `SpanPreserveError`; that
> needs the central-directory rewrite (still open — see ROADMAP M1.5). The
> binary CArchive layer has a byte-exact round-trip model for the
> `Contents/CusProps` header + records (M5.1 Step 1). New here? Read
> `docs/FORMAT_GUIDE.md` (plain-English) then `docs/ROADMAP.md`.
>
> **History note:** an earlier offset-`-8` "TOC pointer fixup" was found (by
> SW re-verification) to be rejected by SW for shifted files; span
> preservation replaced it. See `CHANGELOG.md` and the M1 hypothesis log.
>
> **Honesty disclosure.** This is a multi-year reverse-engineering
> project at full ambition. Useful artifacts (bulk-property edit, sheet
> rename, drawing pre-scan) will land in months. Geometry decoding /
> generative authoring are open-ended research goals with no committed
> completion date.

---

## Why this exists

A SOLIDWORKS freeze that Claude worked 5 hours on to unfreeze ultimately
crashed and lost the last save of an inconsequential drawing. This test
exposed how dependent the existing automation stack was on SOLIDWORKS
being installed, licensed, responsive, and not catastrophically broken.
Every diagnostic path (read a doc's properties, list its configurations,
discover its drawing sheets, repair its auto-recovery) currently routes
through either:

- **The COM API** — requires SW running, costs ~3 seconds per file just
  to open silently, brittle if SW is mid-hang.
- **The Document Manager API (DocMgr)** — requires a paid license,
  closed-source DLL, narrow write surface.
- **`openswx` and similar read-only ports** — modern format only, don't
  touch CArchive-encoded streams. Solve the inspection problem; don't solve
  modification.

SWFormat fills the gap. Pure Python, no SW dependency, with a roadmap
toward modify-and-write capabilities. When complete enough, it replaces
DocMgr for inspection workloads and complements it for modification
workloads — eventually competes with it on shared workflows.
(At this point anything beyond the current state is Claude's ambition,
not the authors)

## Where it helps most: feeding AI / automation pipelines

A concrete payoff: training or fine-tuning an LLM/agent to **author** SOLIDWORKS
drawings starts with **mining a large corpus of existing drawings** — their
sheets, views, tables (BOM), notes, and properties — to learn structure and
house style. SWFormat does that read-heavy half cheaply:

- **Bulk + parallel** — reads a drawing's structure/tables/notes/properties
  straight from the file bytes, in-process, across many files at once. It is
  **potentially faster — for bulk, parallel metadata reads — than reading the
  same metadata through SOLIDWORKS itself (COM API) or the Document Manager /
  SOLIDWORKS Explorer API**, both of which parse every file through SOLIDWORKS'
  own engine (per-file overhead, plus license initialization for Document
  Manager).
- **Safe** — read-only on the originals by construction: no running session to
  contend with, no crash/hang risk, nothing written over your source files.
- **No license, no install** — runs on Linux / CI where SOLIDWORKS isn't present.

It is a **division of labor, not a replacement.** SWFormat can't read geometry
(bounding boxes, body counts) or geometry-computed dimension values, exact
per-instance dimension *counts* on real files are not yet reliable, and it
authors nothing — no view, dimension, or BOM *creation*. Those steps still need
the live modeler (e.g. via COM). The efficient pattern is the obvious one:
**SWFormat for cheap, parallel, safe bulk reads; live SOLIDWORKS for geometry
reads and all authoring.** See `docs/DRAWING_METADATA.md` for the mining
pipeline and a tool-by-tool comparison.

## What works today (read + edit, no SOLIDWORKS)

Read (no SW): chunk walk, stream list/decompress, custom + config properties,
configurations, drawing sheets / views / tables (BOM) / notes / displayed
dimensions / referenced models / per-sheet previews, and drawing sketch geometry.
Edit (no SW, SW-verified): custom-property set/delete/add, config and sheet
rename, sketch geometry/dimension edits — span-preserving; plus **grow-beyond-span
on parts** (add new / config-scoped properties, rename configs to longer names)
via relocate-to-EOF. See `docs/FEATURES.md` for the full, current capability list.

```bash
cd swformat
python test/harness/proof_of_life.py     # read-side smoke test
python -m pytest test/harness/ -q        # walker, round-trip, TOC, properties, CArchive, config/sheet/sketch, …
```

Output running the read-side smoke test against a few real SW files (your own —
none are distributed with the package):

```
[OK]   delete_part_smallest  fmt=modern  chunks=39   decompressed=252,873b   file=81,519b
[OK]   washer_simple         fmt=modern  chunks=40   decompressed=397,284b   file=104,639b
[OK]   delete_assembly       fmt=modern  chunks=38   decompressed=1,654,344b file=369,002b
PASS: all corpus files parsed cleanly
```

The chunk walker (ported from `openswx`) handles the modern SW 2015+
format. Each file's streams are enumerable, decompressable, and the
documented XML / SheetNames / preview streams are directly readable.

**Editing custom properties — no SOLIDWORKS needed:**

```bash
swf-prop list   part.sldprt
swf-prop set    part.sldprt REVISION B --out part_revB.sldprt
swf-prop delete part.sldprt OldField   --in-place
```

```python
from swformat.api.properties import read_properties, edit_properties
read_properties("part.sldprt")                       # {'PARTNO': '...', 'REVISION': '0', ...}
edit_properties("in.sldprt", "out.sldprt", sets={"REVISION": "B"}, deletes=["Scrap"])
```

SOLIDWORKS reopens `out.sldprt` and reports `REVISION = B`. Verified on
parts, assemblies, and drawings for editing and deleting (span-preserving).
Adding brand-new property names and per-configuration properties
(`swf-prop set part.sldprt CfgMat "A36" --config 0`) is SW-verified on **parts**
even when the addition grows the file (relocate-to-EOF); on assemblies/drawings
an addition that overflows the stream's compressed span raises `SpanPreserveError`
(grow-beyond-span there is not yet supported — see `docs/FEATURES.md`).

## What works / what does NOT yet

| Capability | Status | Milestone |
|---|---|---|
| Read modern-format chunks (every byte accounted for) | ✓ done | M0 |
| Read XML streams + custom properties | ✓ done | M0/M2 |
| **Round-trip echo: read→write byte-identical** | ✓ done | M1 |
| **SW-equivalent round-trip: SW reopens cleanly** | ✓ done (part/asm/drw) | M1 (primary gate) |
| **Edit/delete EXISTING custom properties, SW accepts result** | ✓ done (part SW-verified; span-preserving) | M2 |
| **Add a BRAND-NEW global property name** (custom.xml name dictionary) | ✓ done (SW-verified; fits-in-span) | M2 |
| Read config-scoped props (`Config-N-Properties.xml`, `--config N`) | ✓ done | M2 |
| Add/edit config-scoped props | ✓ PARTS (SW-verified via relocate-to-EOF); fits-in-span on all files; ❌ asm/drw grow | M2/M1.5 |
| Edit a user-text **cut-list** property value | ✓ done (SW-verified) | M5.1 |
| Size-changing write that GROWS a stream beyond its span | ✓ PARTS (relocate-to-EOF, SW-verified); ❌ asm/drw (validate file size — size-field decode open). Offset-shift fixup was falsified (SW-rejected) → demoted to experimental | M1.5 |
| Binary `Contents/CusProps` round-trip model (header + records structural) | ✓ done (no SW) | M5.1 Step 1 |
| General CArchive object-map **ledger** (validated; cross-checked vs `_object_map_walk`) | ✓ foundation done; insert/mutate parked (marginal) | M5.1 Step 2 |
| Read pre-2015 OLE2 format | ⏵ planned via `olefile` | deferred |
| Read ZIP/OPC (3DExperience) format | ⏵ standard zipfile parse | deferred |
| **List configurations** (names + count from CMgrHdr2) | ✓ done (SW-verified) | M3 read |
| **Rename** a configuration | ✓ done (SW-verified, parts+asm) | M3 write |
| Set-active / derived configuration | ❌ deferred (authoritative in binary `CMgr`) | M5.x |
| **List + rename** drawing sheets | ✓ done (SW-verified) | M4 |
| Drawing sheet reorder / add empty | ❌ structural (`Contents/Definition`) | M4 |
| **Read drawing sketch geometry** (per-entity line/circle/arc coords from `sgSketch`, indexed point binding) | ✓ done (multi-entity read; no SW) | M5.3 |
| Decode `Contents/Definition` CArchive (geometry, feature tree) | ⏵ read-side in progress (recursive parser → `moDrSheet`; sketch read shipped) | M5 |
| Full geometry round-trip parity | ❌ probably impossible without external kernel | M6 (speculative) |
| Generate SW files from scratch | ❌ probably impossible | research aspiration |

See `docs/ROADMAP.md` for full milestone definitions, effort estimates
(honest ranges), feasibility ratings, and pass criteria.

## Architecture in one diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                       LAYER 4 — API                              │
│   High-level Document object: properties, configurations,        │
│   sheets, components, features. Stable surface for consumers.    │
└──────────────────────────────────────────────────────────────────┘
                              ↕
┌──────────────────────────────────────────────────────────────────┐
│                    LAYER 3 — CArchive                            │
│   MFC binary serialization protocol decoder.                     │
│   Per-class schema dispatch. (M5+: Contents/Definition,           │
│   Header2, full CMgrHdr2.) Hardest layer.                        │
└──────────────────────────────────────────────────────────────────┘
                              ↕
┌──────────────────────────────────────────────────────────────────┐
│                    LAYER 2 — streams                             │
│   Per-stream handlers: XML streams, preview PNGs, sheet name     │
│   tables, ROL-decoded names. OpaqueStream pass-through for       │
│   any stream we don't yet decode.                                │
└──────────────────────────────────────────────────────────────────┘
                              ↕
┌──────────────────────────────────────────────────────────────────┐
│                    LAYER 1 — chunks                              │
│   The `14 00 06 00 08 00` chunk walker. ROL codec. Raw DEFLATE.  │
│   Format detection (modern / OLE2 / OPC). Inter-chunk gap        │
│   tracking — every byte is accounted for.                        │
└──────────────────────────────────────────────────────────────────┘
                              ↕
┌──────────────────────────────────────────────────────────────────┐
│                   LAYER 0 — bytes                                │
│   File I/O. mmap-backed for large files. Format-agnostic.        │
└──────────────────────────────────────────────────────────────────┘
```

Each layer has a single responsibility and clean seams to the layer
above and below. The "tail bytes" invariant — every byte read must be
writeable verbatim, even if unknown — flows from Layer 0 through every
layer, enabling the lazy round-trip strategy that makes M1 tractable.

See `docs/ARCHITECTURE.md` for the full data flow and per-layer
contracts.

## Project structure

```
swformat/
├── README.md                   # this file
├── LICENSE                     # Apache-2.0
├── CHANGELOG.md
├── CONTRIBUTING.md
├── pyproject.toml              # Hatchling-built, no runtime deps
├── docs/
│   ├── FORMAT_GUIDE.md         # START HERE: plain-English, noob-friendly guide to the format
│   ├── FEATURES.md             # concise capability list: what works / partly / not yet
│   ├── ARCHITECTURE.md         # 5-layer model, data flow, per-layer contracts
│   ├── CARCHIVE.md             # Layer-3 MFC CArchive spec + M5.1 object-map codec plan
│   ├── ROADMAP.md              # M0..M7 milestones, feasibility, pass criteria
│   ├── REVERSE_ENGINEERING.md  # Methodology: diff-pair RE, hypothesis logs, lesson capture
│   └── LEGAL.md                # Interoperability RE basis (17 USC 1201(f), EU 2009/24)
├── TESTING.md                  # Test corpus + 3-layer test pyramid
├── src/swformat/
│   ├── __init__.py             # re-exports read_document/read_file, Document, Chunk, Gap
│   ├── types.py                # Chunk/Gap/Document (no-orphan-bytes, lazy round-trip)
│   ├── _imported_swx_reader_v0.py  # historical openswx-derived reference (not the live path)
│   ├── chunks/                 # Layer 1
│   │   ├── walker.py           # the chunk walker (markers, ROL, DEFLATE, gaps)
│   │   └── toc.py              # central-TOC offset-pointer fixup (M1.5)
│   ├── streams/                # Layer 2
│   │   └── custom_props.py     # docProps/custom.xml editor (M2; attr-escaping + name dict)
│   ├── carchive/               # Layer 3 — MFC CArchive (M5.1)
│   │   ├── archive.py          # framing primitives: object tags, class defs, ReadCount
│   │   ├── cstring.py          # MFC CStringW codec (FF FE FF <len> utf16le; code-units)
│   │   ├── cusprops.py         # Contents/CusProps read + round-trip model + text-prop writer
│   │   └── cmgrhdr2.py         # Contents/CMgrHdr2 — configuration list decode (M3 read)
│   ├── io/
│   │   ├── reader.py           # read_document / read_document_bytes / read_file
│   │   ├── writer.py           # serialize/write + serialize_with_toc/write_with_toc (M1/M1.5)
│   │   └── stable_mask.py      # stream-NAME non-determinism allow/deny list (M1)
│   ├── api/
│   │   ├── properties.py       # read_properties / edit_properties (M2; config= param)
│   │   ├── configurations.py   # read_configurations / configuration_count (M3 read)
│   │   ├── sheets.py           # read_sheets / rename_sheet (M4)
│   │   └── sketches.py         # read_sketches / read_sketch_entities: per-entity sgSketch geometry (M5.3 read)
│   └── tools/                  # CLIs (console-scripts in pyproject.toml)
│       ├── dump_streams.py     # explode a file's streams to a dir / --list (M0)
│       ├── diff_files.py       # 3-layer diff (M0)
│       ├── echo_check.py       # read→write→diff self-consistency (M1)
│       ├── sw_prop.py          # custom-property list/get/set/delete/cutlist (M2; --config N)
│       ├── sw_configs.py       # list/count/rename configurations (M3)
│       ├── sw_sheets.py        # drawing sheet list/count/rename (M4)
│       └── sw_sketch.py        # drawing sketch-geometry list/dump (M5.3 read)
├── test/
│   ├── corpus/
│   │   ├── corpus.config.json  # registry of test files (paths only — IP-clean)
│   │   ├── parts/{2020,2024,2026}/
│   │   ├── assemblies/
│   │   ├── drawings/
│   │   └── README.md
│   └── harness/
│       ├── proof_of_life.py       # M0 read-side smoke test
│       ├── test_chunk_walker.py   # Layer 1: no-orphan-bytes, stream parity
│       ├── test_roundtrip.py      # Layer 2: lazy round-trip, re-deflate, mutation
│       ├── test_toc.py            # M1.5: TOC offset-pointer fixup ("no stale pointer")
│       ├── test_properties.py     # M2: custom-property read/edit (global + config)
│       ├── test_carchive.py       # M5.1: CArchive primitives, CusProps read + round-trip
│       ├── twin_save_baseline.py  # M0.5 orchestrator/analyzer (combridge; manual)
│       └── layer3_reopen.py       # Layer 3: SW-reopen equivalence (combridge; manual)
└── .github/workflows/ci.yml    # pytest + ruff + mypy on push
```

> Note: the reverse-engineering scratch (`research/`) and the SOLIDWORKS-API
> corpus generators (`.csx`) are kept local-only and are not part of the
> distributed package — the pure-Python library lives under `src/`, `test/`,
> and `docs/`.

## Related projects and prior art

- **`schwitters/openswx`** — the C++20 reference implementation
  SWFormat's chunk walker is ported from. MIT-licensed prior art, used as
  reference under license, not as a runtime dependency. The verbatim import
  lives at `src/swformat/_imported_swx_reader_v0.py`; it was productionised
  into `src/swformat/chunks/walker.py` during M0 cleanup.
- **`combridge`** — a COM-automation bridge used (optionally, locally) for
  corpus generation and Layer-3 SW-equivalence testing (open a
  round-tripped file in real SOLIDWORKS and compare semantics). Not required
  for the pure-Python read/write paths.
- **`xarial/xcad`** (external) — a modern C# wrapper around the SOLIDWORKS
  Document Manager API. Reference for what DocMgr exposes; SWFormat targets a
  wider surface but a narrower install footprint (no DocMgr DLL / license / SW).
- **`blussyya/sldprt-converter`** (external) — a JS project that decodes
  `Contents/DisplayLists` and triangulates to STL. A geometry-extraction
  reference for any future M5 geometry work (approximate — triangulates NURBS
  control points; does not evaluate B-splines).

## Legal posture

**SWFormat is interoperability reverse engineering** under:

- **United States**: 17 U.S.C. § 1201(f) — explicit DMCA exemption for
  reverse engineering "for the sole purpose of identifying and analyzing
  those elements of the program that are necessary to achieve
  interoperability of an independently created computer program with
  other programs."
- **European Union**: Directive 2009/24/EC, Article 6 — equivalent
  interoperability exemption.

The project:

1. **Does NOT decrypt** any encrypted SW files (3DExperience-vault
   files are detected and reported as unsupported).
2. **Does NOT circumvent** any technical protection measure.
3. **Targets users with legitimate access** to the files they process —
   their own work product, files they have license to read/edit.
4. **Does NOT replicate** the SOLIDWORKS application; it operates only
   on files the user already has.

If you are a SOLIDWORKS / Dassault Systèmes representative with a
license, IP, or technical concern, contact information is in
`docs/LEGAL.md`.

## Quick start (developer)

```bash
cd swformat
pip install -e ".[dev]"

# Verify the parser works against real files
python test/harness/proof_of_life.py

# Dump every stream of a file as a directory of payload bytes
python -m swformat.tools.dump_streams test/corpus/parts/2026/cube_100mm_one_extrude.sldprt tmp/streams_out
ls tmp/streams_out

# Diff two SW files at all three layers (bytes / chunks / streams)
python -m swformat.tools.diff_files a.sldprt b.sldprt

# Run the full test suite
pytest -q
```

For non-developers: there is no end-user tool yet. Useful CLIs ship
with M2 (custom property batch-edit) and M4 (drawing sheet ops).

## Non-goals

So the scope is unambiguous:

- **NOT a B-rep kernel.** SWFormat will never compute geometry; it
  reads and writes the bytes that contain geometry (eventually) but
  cannot, e.g., tessellate a face or evaluate a B-spline.
- **NOT a PDM vault replacement.** Vault-managed files have their own
  encryption layer; SWFormat operates on unencrypted on-disk files.
- **NOT a COM API replacement.** SWFormat does not run SOLIDWORKS
  commands; it edits files at rest.
- **NOT a 3DEXPERIENCE format library.** The OPC-based 3DEXPERIENCE
  files are a different format; some inspection support may land
  someday but generative authoring of 3DEXPERIENCE objects is not in
  scope.

## Versioning

Semantic versioning. The Document object and `read_file` signature are
stable across minor versions. Internal layer APIs (chunks/, streams/,
carchive/) may change between minor versions until the project reaches
1.0.

See `CHANGELOG.md`.

## License

Apache-2.0. See `LICENSE`.

This is compatible with porting algorithms from MIT-licensed prior art
(`openswx`); attribution is recorded with the project's reverse-engineering
notes.

## Trademarks

SOLIDWORKS is a registered trademark of Dassault Systèmes SolidWorks
Corporation. SWFormat is an **independent, unofficial** project and is **not
affiliated with, authorized by, endorsed by, or sponsored by** Dassault
Systèmes or any of its affiliates. All product and company names are the
property of their respective owners; references to them are nominative —
solely to describe the file formats this project interoperates with. See
`docs/LEGAL.md` for the project's legal posture.
