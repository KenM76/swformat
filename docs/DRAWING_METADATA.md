# Extracting drawing metadata (for a training / indexing pipeline)

This guide is for a **consumer** that opens a corpus of SOLIDWORKS drawing files
(`.SLDDRW`) and extracts structured metadata — e.g. to train or index a model on
drawings. Everything here runs **without SOLIDWORKS**, in pure Python, and is
**verified on real multi-sheet production drawings** (not just synthetic fixtures).

## TL;DR — one call gets everything (drawings, parts, AND assemblies)

```python
from swformat.api.metadata import read_metadata
meta = read_metadata("path/to/anything.SLDDRW")    # dispatches on doc_type
```
or from the shell, ideal for a pipeline — works on ANY SOLIDWORKS file:
```bash
swf-meta path/to/drawing.SLDDRW --json            # drawing record
swf-meta path/to/part.SLDPRT    --json            # part record
swf-meta path/to/assembly.SLDASM --json           # assembly record
swf-meta path/to/drawing.SLDDRW --json --no-rows  # omit large table row arrays
```

Every record carries a ``doc_type`` (``"drawing"`` / ``"part"`` / ``"assembly"``).
`read_metadata` dispatches: drawings → the schema below; parts/assemblies → the
**model** schema (`file`, `doc_type`, `properties` {PARTNO, DESCRIPTION, MATERIAL,
WEIGHT, REVISION, …}, `material`, `configurations`, feature `dimensions`,
`keyword_index_counts`). The drawing's `referenced_models` join straight to the
parts you then run `swf-meta` on — so one pass over a corpus covers a drawing and
the models it depicts.

### Drawing record schema

`read_drawing_metadata` returns a single JSON-serialisable dict. Every section
degrades independently to empty/zero (a part, or a drawing missing a stream, still
returns a well-formed dict). Keys:

| key | type | meaning |
|---|---|---|
| `file` | str | the file name |
| `sheet_count` | int | number of sheets |
| `sheet_names` | list[str] | sheet names, in order |
| `sheet_formats` | list[{name,width,height,scale}] | paper size (metres) + scale ratio (`"1:32"`) per sheet |
| `sheet_preview_count` | int | number of rendered-PNG previews available (extract via `extract_sheet_previews`) |
| `referenced_models` | list[str] | part/assembly model paths the drawing depicts |
| `views` | list[{view,model,sheet}] | per-view model + sheet binding |
| `tables` | list[{type,name,num_rows,num_cols,rows}] | BOM / Revision / … tables; `rows` = cell grid |
| `bom_count` | int | number of BOM tables |
| `annotation_text` | list[str] | notes / title-block text (property tokens resolved) |
| `dimensions` | list[{name,value}] | **displayed** dimension values (`"38\""`, `"23 1/2\""`) |
| `keyword_index_counts` | {type:int} | overview of the full entity index |

## The per-field tools (Python API + CLI)

Use these when you want one slice rather than the whole bundle. All are read-only,
pure-Python, real-file-capable.

| What | Python | CLI |
|---|---|---|
| Displayed dimension values | `api.dimensions.read_dimension_values(p)` | `swf-dims FILE` |
| Sheet names / count | `api.sheets.read_sheets(p)` / `sheet_count(p)` | `swf-sheets list\|count FILE` |
| Per-sheet paper size + scale | `api.sheets.read_sheet_formats(p)` | `swf-sheets formats FILE` |
| Per-sheet rendered PNGs | `api.sheets.extract_sheet_previews(p, out_dir)` | `swf-sheets previews FILE OUT_DIR` |
| Referenced models | `api.references.read_referenced_models(p)` | `swf-refs FILE` |
| Per-view model/sheet inventory | `api.views.read_views(p)` | `swf-views FILE` |
| Tables (BOM / Revision) | `api.tables.read_tables(p)` / `read_boms(p)` | `swf-tables list\|dump FILE [--type BOM]` |
| Notes / title-block text | `api.annotations.read_annotation_text(p)` | `swf-notes FILE` |
| Full entity index (all types) | `api.keywords.read_keyword_index(p)` | `swf-keywords FILE [--type T]` |
| Custom properties | `api.properties.read_properties(p)` | `swf-prop list\|get FILE` |
| Sketch geometry (lines/arcs/circles) | `api.sketches.read_sketches(p)` | `swf-sketch list\|dump FILE` |
| Any raw stream | — | `swf-dump-streams FILE OUT_DIR` |

### Per-sheet preview images
```python
from swformat.api.sheets import extract_sheet_previews
paths = extract_sheet_previews("drawing.SLDDRW", "out/")   # writes 00_<sheet name>.png, …
```
Each PNG is named `<NN>_<sheet name>.png` (zero-padded sheet index + name), so the
images are labelled for training. The drawing's overall thumbnail is the
`PreviewPNG` stream (get it via `swf-dump-streams`).

## Why this is the right tool for the read phase (and where it isn't)

Learning to author drawings from a corpus is two jobs: **reading** what existing
drawings contain (structure, tables, notes, properties — the bulk of the corpus
work) and **authoring** new drawings (creating views, placing dimensions,
inserting BOMs). SWFormat targets the first job and deliberately leaves the
second to the live modeler.

For the **read** job there are three ways to get the same metadata. They differ
mostly in per-file cost and in what they require:

| Approach | How it reads | Parallel? | License / install | Can author? |
|---|---|---|---|---|
| **SWFormat** (this project) | byte-level, in-process — no SOLIDWORKS | yes — many files at once | none (pure Python, any OS) | no |
| **Document Manager / SOLIDWORKS Explorer API** | loads each file through SOLIDWORKS' parser DLL | limited | paid license, closed DLL | no |
| **Live SOLIDWORKS (COM API)** | opens + rebuilds each file in the running app | no (one session; instances aren't safe to multiply) | full SOLIDWORKS install + license | **yes** |

So for **bulk, parallel metadata reads**, SWFormat is **potentially faster** than
either SOLIDWORKS-based path — both of those parse every file through
SOLIDWORKS' own engine (per-file overhead, plus license initialization for
Document Manager), whereas SWFormat reads the bytes directly and fans out across
files. Document Manager sits in the middle: faster than a full SW open (no UI,
no rebuild), slower than an in-process byte read, and — like SWFormat — it
**cannot author**, so it never replaces the live-SW half of the work.

**Where SWFormat is not the tool:** anything that needs geometry or the running
modeler. It does not read geometry (bounding boxes, body counts) or
geometry-computed dimension values; exact per-instance dimension *counts* on
real files are not yet reliable (see the gap below); and it authors nothing.
Those steps stay in live SOLIDWORKS. The efficient pipeline uses each where it
wins: **SWFormat for cheap, parallel, safe bulk reads; live SOLIDWORKS for
geometry reads and all authoring.**

## How this is robust on real files (and the one gap)

The reliable readers anchor on **unambiguous markers** — an XML stream, or a
length-prefixed `CStringW` ending in a SOLIDWORKS document extension — rather than
fuzzy byte signatures. So they work on real production drawings without the
content-general CArchive object-map walk (the "keystone", which is open-ended).

- **Dimension VALUES**: use `read_dimension_values` — it returns the values
  SOLIDWORKS *rendered* (from the `KeyWords` index). This is the recommended
  reader.
- **Not available without the keystone** (NOT needed for the above): the
  geometry-*computed* dimension value and the exact per-instance dimension *count*
  in the binary `Contents/Definition`. `api.sketches.read_sketch_dimensions` is a
  synthetic-fixture-validated byte-signature reader that **over/under-matches on
  real files** — do not use it for a real-file dimension count; use
  `read_dimension_values` for displayed values instead.
- **Geometry cache** (`Contents/DisplayLists`) is treated as an opaque stream
  (non-deterministic graphics cache); it is not decoded.

## Scope notes

- View ids (`D1`…, `Drawing View13`) are per-view, not globally unique.
- Referenced-model and view paths are stored as SOLIDWORKS wrote them (absolute or
  relative) and may point at files that have since moved; use `--basenames` /
  `model_basename` when joining by name.
- All readers return `[]` / `{}` / `0` for a non-drawing or a missing stream rather
  than raising — safe to run across a mixed corpus.

See `docs/FEATURES.md` for per-capability detail and verification notes.
