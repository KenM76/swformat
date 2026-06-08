# SWFormat Roadmap

> Honest milestones. Each is independently shippable. Effort estimates
> are part-time person-weeks with adversarial-review-adjusted ranges.
> Feasibility ratings reflect the worst-case interpretation, not the
> optimistic one.

---

## Milestone 0 — Bootstrap

**Pass criteria:** Chunk walker produces parsed `Document` objects on
N≥3 real SW files of varied type. Test harness passes. Documentation
satisfies the "reconstruct from docs alone" bar for M0 and M1. Reserved
namespace stubs exist for M1–M5.

**Effort:** 1-2 days.

**Status / Feasibility:** ✓ DONE — proof-of-life passes. (Current focus
has since advanced through M1/M1.5/M2 and into M5.1 Step 1.)

**Deliverables:**

- ✓ Directory tree
- ✓ Imported `_imported_swx_reader_v0.py` (seed parser port)
- ✓ `test/corpus/corpus.config.json` (template referencing local real files by path)
- ✓ `test/harness/proof_of_life.py` passing
- ✓ README, ARCHITECTURE, ROADMAP, REVERSE_ENGINEERING, TESTING, LEGAL docs
- ✓ Productionised `src/swformat/chunks/walker.py` (`iter_records` emits
  chunks + gaps; no-orphan-bytes verified) factored from the import
- ✓ `src/swformat/types.py` dataclasses (`Chunk`, `Gap`, `Document` with
  `reconstruct()`/`locate()`; lazy-roundtrip via `modified_payload`)
- ✓ `src/swformat/io/reader.py` entry point (`read_document` + legacy `read_file`)
- ✓ `src/swformat/tools/dump_streams.py` CLI
- ✓ `src/swformat/tools/diff_files.py` CLI (3-layer diff; reusable cluster API)
- ✓ Layer-1 tests `test/harness/test_chunk_walker.py` (incl. no-orphan-bytes)
- ✓ pyproject.toml, .gitignore, LICENSE, CHANGELOG, CONTRIBUTING
- ✓ `.github/workflows/ci.yml` (corpus-IP guard + ruff + mypy + pytest on Linux)
- ⏳ Fuzzing harness stub (atheris/hypothesis)
- ⏳ Performance baseline benchmark

---

## Milestone 0.5 — Empirical pre-M1 experiment (BLOCKING M1)

> **Added per adversarial review.** Without this data, M1's pass
> criteria are guesses.

**Status:** ✓ DONE 2026-06-08. 9 files (4 parts / 3 assemblies / 2
drawings; 10th excluded — Pack-and-Go failure on a top-level-tree
drawing). Report: `research/empirical_findings/twin_save_baseline/`
(`log.md` + `twin_save_report.json`). Driver:
`test/harness/twin_save_baseline.py`.

**Result (decisive):** **0/9 files are byte-length-stable** across an
unmodified 10 s-apart twin-save (Δsize +80 B … −23,225 B). Because file
length changes, a positional "stable mask" is *inapplicable*, not just
leaky. **9/9 `reconstruct() == original`** — our lazy-roundtrip writer is
byte-faithful on every fresh SW-2026 save. 5 streams differ in all 9
files (`3DExperienceExchange2`, `DisplayLists`, `Biography`,
`ISolidWorksInformation.xml`, `core.xml`); `Biography` is the universal
length-shifter; 73 stream names never differed. → **M1 Layer-2
byte-equal is dropped as primary gate; Layer 3 is THE gate** (details
below + in the log).

**Pass criteria:** For ≥10 corpus files, save each twice via SW (10s
apart, no changes). Run twin-save XOR analysis. Produce a report
(`research/empirical_findings/twin_save_baseline/log.md`) showing:

1. Total diff entropy per file (bytes-different / total-bytes)
2. Diff cluster locations (which chunks, which stream names)
3. Whether the lazy-roundtrip strategy (preserve unchanged compressed
   chunks verbatim) eliminates the diff or not
4. Empirical "stable mask" candidate regions

**Effort:** 1 day (run twin saves; 1 day post-processing the diff data).

**Feasibility:** ✓ HIGH — purely diagnostic.

**Why this exists:** M1's "byte-equal under stable mask" criterion is
either trivial or impossible depending on save non-determinism rates.
The adversarial reviewer flagged it: SW saves are non-deterministic in
ways we have not measured. Measure first, then design.

**Outputs feed into:** M1's pass-criteria choice. If twin-save diff
rates >0.1% of bytes, drop byte-equal-under-mask; make Layer-3 SW
equivalence the only gate.

---

## Milestone 1 — Round-trip read→write

**Status:** ✓ DONE 2026-06-08 for the lazy (unmodified) round-trip.
`io/writer.py` (`serialize`/`write`, lazy + re-deflate paths),
`io/stable_mask.py` (stream-name allow/deny list), `tools/echo_check.py`,
Layer-1/2 tests (`test_roundtrip.py`, 7 pass), and the Layer-3 harness
(`test/harness/layer3_reopen.py`) all landed. **Layer-3 gate PASSES**:
the lazy echo reopens in SW with equivalent semantics for a part
(a part), an assembly, and a drawing.
See `research/empirical_findings/m1_writer_roundtrip/log.md`.

**⚠ Re-deflation breaks reopen (key finding):** changing ANY chunk's
compressed size (even one stream, even −45 B) makes SW reject the file
with `swFileRequiresRepairError`. Cause: the section-0x37 TOC/directory
records store each stream's `csz`/`usz`; re-deflation desyncs them. So the
lazy verbatim path is the only safe writer today, and **M2 is gated on
M1.5 (TOC decode)** — it is NOT "trivially enabled by M1" as first assumed.

**Primary pass criterion (Layer 3 — SW equivalence):**

> A SWFormat-written file, when reopened in real SOLIDWORKS via
> combridge, has **equivalent semantics** to the original. Equivalence
> is defined per use-case (see `TESTING.md` §"Layer 3 equivalence
> matrix"). At minimum: same configuration list, same custom property
> values, same sheet names, same mass, same component references.

**Diagnostic criterion (Layer 2 — self-consistency, REVISED per M0.5):**

> M0.5 proved two independent SW saves of an unmodified file differ in
> total LENGTH (0/9 size-stable), so "our output == a different SW save"
> is unachievable and a positional stable mask is inapplicable. Layer 2
> is therefore re-scoped to the form that IS achievable and meaningful:
> **`reconstruct(file) == file`** — our writer reproduces the *input*
> byte-for-byte (verified 9/9 in M0.5). The `stable_mask` becomes a
> **stream-name allow/deny list**: when comparing our modified output to
> the original, ignore the ~16 known-nondeterministic streams (timestamps,
> Biography, DisplayLists, 3DExperience*, GhostPartition, …) and flag any
> OTHER stream that changed. Diagnostic only; never blocks M1.

**Layer 1 criterion (chunk-walk preserved):**

> Every chunk read by SWFormat is writeable verbatim using the same
> stream name, section type, and decompressed bytes. Inter-chunk gaps
> are preserved as raw byte runs. **The "no orphan bytes" invariant
> holds: bytes-consumed == file-size.**

**Effort:** 4-8 weeks part-time for Layer 3 only. 8-20 weeks if Layer 2
byte-equal is also pursued.

**Feasibility:** MEDIUM for Layer 3. MEDIUM-LOW for Layer 2 (depends on
M0.5 findings).

**Deliverables:**

- `src/swformat/io/writer.py` — `write(doc, out_path)` reassembles a file
  byte-by-byte from the Document
- `src/swformat/io/stable_mask.py` — ByteMask helper computed from M0.5 data
- `src/swformat/tools/echo_check.py` — CLI: read a file, write it,
  diff against the original at all three layers
- Layer-3 test fixture in `test/harness/conftest.py` that uses combridge
  to open the round-tripped file in SW and assert equivalence
- Updated golden manifests for every corpus file
- Lesson: `lessons/lesson_<YYMMDD>_m1_roundtrip.md`

**Risks:**

- **TOC chunks (0xDF section type) may encode chunk offsets** that
  shift on rewrite. Recomputing the TOC requires understanding its
  format — a sub-RE project of its own.
- **`Contents/DisplayLists` regeneration**: this is the graphics
  cache; SW regenerates it on every save. May need to strip it
  before writing and let SW rebuild it on first open. Affects Layer 2
  byte-equal (will not match) but Layer 3 (file opens) is unaffected.
- **`_MO_VERSION_NNNNN/Biography` edit history**: contains a timestamp.
  May land in the stable mask.
- **Section type 0x1C ("mini") chunks**: openswx flags these as
  unknown-purpose. May carry checksums or back-references that break
  round-trip.

**Enables next:** M2 (modify properties) trivially. M3, M4 build on
the writer.

---

## Milestone 1.5 — Decode the chunk TOC/directory (NEW; gates M2/M3/M4)

> **Added 2026-06-08 from the M1 finding.** Any write that changes a
> chunk's compressed size is rejected on reopen unless the matching TOC
> record is updated. Every modify-milestone (M2/M3/M4) therefore needs
> this first.

**Status:** ⚠️ SOLVED for PARTS (span-preserve + relocate-to-EOF grow);
asm/drw grow-beyond-span still OPEN. (Offset-shift fixup RE-FALSIFIED 2026-06-11
and replaced by relocate-to-EOF.)
- **Span preservation (SHIPPED default, SW-verified, safe on ALL files):**
  re-deflate the edited chunk and pad it back to its original `csz` so no
  offsets shift; update only the modified chunk's TOC `usz`. Handles every edit
  whose payload fits the original span (`io/writer.serialize_with_toc` +
  `SpanPreserveError`). Reopens in SW at e=0 (re-verified 2026-06-11: pristine
  and span-preserve edits both open e=0).
- **Relocate-to-EOF grow (SHIPPED 2026-06-11, SW-verified on PARTS):** to grow a
  stream BEYOND its compressed span, append the grown stream as a fresh chunk at
  the original EOF and repoint ONLY its TOC record (`off8`/`csz`/`usz`); no other
  chunk moves → no offset shift, no stale pointers, NO central-directory rewrite.
  Relies on two SW facts (both Layer-3 verified): SW ignores tail-appended bytes,
  and SW locates streams via the TOC `off8` (not a sequential walk).
  `serialize_with_toc(..., relocate_grow=True)` + `_serialize_relocate_grow`;
  `edit_properties` auto-uses it when `detect_doc_type(path)=="part"`. SW-verified
  on real v19000 parts: global add, config-scoped add, 40-property bulk grow, AND
  re-save durability (open→SaveAs→reopen, property persists). ⚠ **PARTS ONLY** —
  assemblies & drawings REJECT a relocated stream (even a verbatim move), so they
  enforce an additional chunk-position invariant (a global offset index) parts
  lack; the writer gates relocate to parts, asm/drw still raise `SpanPreserveError`
  on a grow. Decoding that index is the remaining work to generalize. Evidence:
  m1_writer_roundtrip/log.md UPDATE 2026-06-11; `scripts/relocate_grow_probe.py`.
- **Offset-`-8` pointer fixup — NOT SW-VALID (do not use).** Re-deflate to a new
  `csz`, shift later chunks, rewrite all `-8` gap pointers + TOC csz/usz. The
  output RE-PARSES but real SOLIDWORKS REJECTS it with `swFileRequiresRepairError`
  (e=2097152, "custom property data corruption"). **Re-falsified 2026-06-11 on
  real v19000 client parts (global bulk-add AND config-add) AND on the v15000
  washer fixture this path was once wrongly believed verified against.** SW
  validates more than the `-8` pointers + TOC sizes around the property region;
  what else must move is undecoded. The path is now OFF by default in the writer
  (retained only behind `_experimental_offset_shift=True` for research); the
  default `serialize_with_toc` RAISES `SpanPreserveError` on a grow rather than
  silently emit a file SW rejects. The earlier "SW-verified e=0 on v15000/v19000"
  note mistook RE-PARSE for SW-acceptance. (Superseded for PARTS by relocate-to-
  EOF above; on asm/drw, grow-beyond-span remains BLOCKED on the global-offset-
  index decode.)

**Method lesson (why it regressed):** the Layer-1/2 tests + a 87-file sweep only
asserted the output RE-PARSES (`reconstruct()==data`, value readable). Re-parse
is necessary but NOT sufficient — our reader is lenient where SW is strict
(87/87 "grew_ok" by re-parse → 0/87 valid by SW). A write path is only
"SW-verified" via a Layer-3 reopen. Full evidence: the UPDATE 2026-06-11 entry in
`research/empirical_findings/m1_writer_roundtrip/log.md`.

**Progress 2026-06-08 (core LOCATED):** The integrity gate was decoded down
to a **central TOC** in the trailing region — a run of section-`0xDF`/`0xA4`/
`0xBC` marker records, one per stream, storing `csz`@+0x12 and `usz`@+0x16
(34/46 verified to match their data chunks). Ruled OUT (with experiments):
content checksum (same-`csz` edit reopens), per-stream `csz`-value check
(Test D reopens), total-file-size check (EOF-append reopens), and any
raw-uint32 offset table (whole-file scan negative). The gate is **chunk
offset/span preservation**; SW appears to accumulate `csz` from the TOC.
Patching only the TOC `csz` was insufficient — TOC records carry ~14
undecoded bytes before the name (likely offset/span/flags) that also need
maintaining. Full evidence: `research/empirical_findings/m1_writer_roundtrip/`.

**Pass criterion:** Re-deflate one stream, update its TOC record (csz/usz +
the ~14-byte fields) and any dependents, write, and SW reopens equivalent.

**Effort:** 1-3 weeks (decode the remaining TOC record fields via
controlled diff-pairs; handle secondary records + accumulation).

**Feasibility:** MEDIUM — TOC located and csz/usz confirmed; remaining is
the ~14-byte field decode.

**Deliverables:**
- `src/swformat/chunks/toc.py` — locate + parse + patch TOC records.
- Writer integration: `serialize` updates TOC records for modified chunks.
- A Layer-3 test: re-deflate-echo (currently rejected) reopens after TOC fix.

**M2 unblock available NOW without full M1.5 (verified):** the **preserve-
span** trick — re-deflate the edited stream then pad back to the original
`csz` (Test D) — reopens with zero TOC changes. Enables same-or-smaller
footprint property edits (delete / set-to-shorter) immediately; only
size-growing edits wait on the TOC field decode.

## Milestone 2 — Modify custom properties (CLI)

**Status:** ✅ DONE for global props, via SPAN PRESERVATION (re-verified
2026-06-08). `streams/custom_props.py` + `api/properties.py` + `swf-prop` CLI
(list/get/set/delete/cutlist); 74-test suite. **SW-verified this
session through the API:** SET an existing value, ADD a brand-new property,
and DELETE on a PART (all e=0, change reflected); SET + DELETE on an ASSEMBLY
(`DELETE.SLDASM`, e=0, change reflected). **Mechanism correction:** these work
because the edits fit the original compressed span (padded back to it, no
offset shift) — NOT via the offset-pointer fixup, which SW rejects (see M1.5).
An edit that grows a stream beyond its span raises `SpanPreserveError`.
Drawings should work identically (span-preservation is content-agnostic) but
no drawing test file was on hand to re-verify; the earlier drawing "verified"
used the now-falsified fixup path.

**The add-new-name gate (now solved):** SW reads the property list from the
**name dictionary** (`propertyNameDictionaryElement` entries) inside
`docProps/custom.xml` — not from the `<property>` value elements and not
from the binary store. `set_property` now writes the dictionary entry (+
`FPVals` + a correct pid), and `edit_properties` also keeps the binary
`Contents/CusProps` store consistent (via the byte-exact
`carchive.add_text_properties`). Adding to the dictionary alone is what SW
needs (verified); both stores are updated for fidelity.

**Remaining M2 follow-ups (small):**
- CONFIG-SCOPED properties (`docProps/Config-N-Properties.xml`): READ done
  (`read_properties(config=N)`, `swf-prop --config N`). ADD/EDIT routing done
  but **span-limited** — config streams are small with little compressed
  slack, so adding a property overflows the span and raises
  `SpanPreserveError`; unblocking it needs the M1.5 central-directory rewrite.
  (The earlier "config add SW-verified" used the now-falsified offset-fixup
  writer and is not reliable.)
- cutlist properties (`Config-N-Cutlist-Properties.xml` — different schema).

**Pass criterion:** `sw_prop` CLI can set/delete/add a custom property
on a real SW file, write it back, and SW reopens the file with the
expected property changes — verified via combridge round-trip equivalence
test.

**Effort:** 2-4 weeks.

**Feasibility:** HIGH. Custom properties are XML streams; the
modification surface is small; M1's writer does the heavy lifting.

**Deliverables:**

- `src/swformat/streams/custom_props.py` — parse/edit/serialize the
  `docProps/custom.xml` XML stream
- `src/swformat/streams/config_props.py` — same for
  `docProps/Config-N-Properties.xml`
- `src/swformat/api/properties.py` — high-level `Document.properties`,
  `Document.configurations[N].properties` mutable views
- `src/swformat/tools/sw_prop.py` — CLI: get / set / delete / list
- Test cases that mutate properties and verify SW round-trip
- Lesson capturing any XML-encoding quirks discovered

**Risks:**

- Linked-expression property values (computed from other properties) —
  not safe to overwrite blindly. Document and either skip or flag.
- Encoding sniffing (UTF-8 vs UTF-16) — XML declarations may lie about
  encoding; need a sniff-then-trust mechanism.

---

## Milestone 3 — Read+modify CMgrHdr2 (configuration manager)

**Status:** READ SIDE ✅ DONE 2026-06-08 — list configuration names + count
from `Contents/CMgrHdr2`, **SW-verified** to match `GetConfigurationNames`
across simple / dimensional / sheet-metal-derived / multi-config files.
**RENAME ✅ DONE 2026-06-09 for span-preserving renames** —
`swf-configs rename` / `api.rename_configuration`, **SW-verified** (SW 2026 /
v19000) on parts AND assemblies, including configs with config-specific
properties (those survive — their XML is keyed by config *index*, not name).
⚠️ **CORRECTION 2026-06-11:** only renames that FIT the original compressed span
(same-length, or a small grow with slack) are SW-valid; a rename to a LONGER name
that OVERFLOWS `Contents/CMgrHdr2`'s span (e.g. +27 chars: 213 B > 184 B csz) now
correctly raises `SpanPreserveError` — it would previously have taken the
offset-shift path, which is SW-INVALID (re-falsified 2026-06-11, see M1.5). So
rename-to-LONGER beyond span shares the unsolved grow-beyond-span limit. Key
finding from the
rename diff-pair: `Contents/CMgrHdr2` is **authoritative for the config-name
list** — editing just its NAME + display CStrings is accepted by SW with no
repair (errors=0, warnings=0); the object-map tail and trailing stamp are NOT
validated on open, so rename did **not** require the object-map codec after all
(a big scope reduction vs the earlier estimate). (`carchive/cmgrhdr2.py`,
`api/configurations.py`, `swf-configs list|count|rename`,
`test_configurations.py`; 91 tests.)
MODIFY side remaining: **set-active** + **derived flags** — now **deferred to
M5.x**. A set-active diff-pair (2026-06-09) decisively showed set-active is NOT a
CMgrHdr2 edit: `CMgrHdr2.field1` is only an active *mirror*, and grafting the
whole CfgB-active CMgrHdr2 — or Header2, or SwDocContentMgrInfo, or
ISolidWorksInformation.xml — does **not** change the active config (SW opens
e=0 w=0 but still reports the original). The active/last-saved config is
denormalized across many streams and centred on the binary `Contents/CMgr`,
which is tightly coupled (byte-grafting it crashes SW). So set-active needs the
coordinated multi-stream edit + the general CArchive **object-map codec** (M5.x),
not a localized field flip. Key asymmetry: **CMgrHdr2 is authoritative for the
config NAME (rename ✅) but only a mirror for the ACTIVE flag.**
Evidence: `research/empirical_findings/cmgrhdr2_configs/`.

**Pass criterion:** Can read configurations, rename a configuration,
change the active configuration, mark configurations as derived, and
write back; SW reopens cleanly with the changes visible.

**Effort:** 6-12 weeks (adversarial-adjusted from initial 3-5 estimate
because CMgrHdr2 is a CArchive stream, dragging in M5.1 prerequisites).

**Feasibility:** MEDIUM.

**Deliverables:**

- `src/swformat/carchive/primitives.py` — minimal CArchive primitive
  readers (CString, CObject map, schema dispatch) — the M5.1 floor
- `src/swformat/streams/cmgrhdr.py` — full read+write of CMgrHdr2,
  driven by the documented schema-version-threshold table
- `src/swformat/api/configurations.py` — high-level Configuration object
- `src/swformat/tools/sw_configs.py` — CLI

**Risks:**

- Hidden cross-references from CMgrHdr2 to other streams (Header2,
  Contents/Definition) — rename a config and a stream elsewhere may
  reference the old name by hash or by index
- Configuration ordering invariants (the active configuration is
  identified by index in some places, by name in others)

---

## Milestone 4 — Drawing sheet operations

**Status:** READ SIDE ✅ DONE 2026-06-09 — list sheet names + count from the
`SheetPreviews/SheetNames` stream (`[u16 count][count×CString]` + preview tail;
count matches the `Images/Sheet_N` stream count). `api/sheets.py`,
`swf-sheets list|count`, `test_sheets.py`. No SW. **RENAME ✅ SHIPPED 2026-06-09
(SW-verified)** — `swf-sheets rename` / `api.sheets.rename_sheet` edits the
authoritative sheet-name CString in `Contents/Definition` (+ `Header2`/
`SheetNames` mirrors for consistency); SW 2026 reopens (e=0 w=0) with
`GetSheetNames` reporting the new name. Same-length (or fits-span) = span-
preserve (SW-valid). ⚠️ **CORRECTION 2026-06-11:** rename to a LONGER name that
overflows the tiny `SheetPreviews/SheetNames` span (csz 20) now raises
`SpanPreserveError`; it previously took the offset-shift path, which is SW-INVALID
(re-falsified 2026-06-11, see M1.5) — so sheet rename-to-LONGER beyond span shares
the unsolved grow-beyond-span limit. The authoritative-stream question is
RESOLVED: `Contents/Definition` is what SW reads; `SheetNames` is a preview
mirror. REORDER / ADD are next.
Evidence: `research/empirical_findings/m4_drawing_sheets/`,
`research/empirical_findings/definition_decode/`.

**Pass criterion:** Can rename a sheet (✅ done), reorder sheets, and (the hard
case) add an empty new sheet, then SW reopens the drawing with the
expected sheet list and the existing views still bound to their
original sheets.

**Effort:** Rename DONE. Reorder mechanism RESOLVED 2026-06-09 (see below):
drawing tab order = the physical order of the `moDrSheet` records in
`Contents/Definition` (proven by diff-pair: the records swap; and by an
authoritative test: editing only `SheetNames` order is ignored by SW). So
reorder = move the `moDrSheet` record byte-blocks + **renumber the object-map
indices** (the recursive CArchive walk), not a permutable list and not a
geometry kernel. add_empty_sheet additionally requires byte synthesis.

**Feasibility:** MEDIUM for reorder (revised UP 2026-06-09): a 2-sheet drawing has
only ONE moRelMgr_c/moView_c/moLayerMgr_c/moBomInfoMgr_c — the heavy 17.5 KB
standards block, views, layers and BOM are DOCUMENT-LEVEL shared blocks that come
*after* the sheet records and DO NOT MOVE on reorder. The per-sheet moDrSheet
record is SMALL (~1800 B). So reorder = swap two small record blocks + renumber
the object-map indices bounded to those records + the refs crossing into/out of
them (doc→sheet, shared-block→sheet) — it still needs the M5.1.5 recursive walk
(now reaches moDrSheet @4033 with exact indices), but NOT a 25 KB block move and
NOT a geometry kernel. add_empty_sheet remains LOW (byte synthesis).

> **2026-06-10 — NEAR-WORKING PROTOTYPE built (`reorder_prototype.py`).** The full
> reorder transform is reverse-engineered and prototyped: verbatim prefix +
> shared tail; def-ownership transfer (the 7 sheet-record classes are first-defined
> in the FIRST sheet record, so a naive swap makes invalid forward refs — the
> new-first record's CLASS_REFs become NEW_CLASS defs and the old-first's defs
> become refs); relocation of FIRST-SHEET ARTIFACTS (`moCompRefPlane_c` moves to
> the new-first sheet, SW-confirmed); + a small re-index. It produces a
> `[BETA,ALPHA]` file SOLIDWORKS **nearly** accepts — `swFileRequiresRepairError`
> (repair-level, NOT a hard reject); the write path + SheetNames reorder are
> validated (no-op round-trip opens clean). The CLEAN, SW-accepted reorder is
> GATED on a content-general sheet-child parser (to count the variable child
> bodies' inline objects for the exact object-map slots — BETA has ~3 hidden
> objects vs ALPHA that surface analysis can't pin). Confirmed un-shortcuttable by
> blind byte-transforms. Lesson: `lesson_20260610_drawing_sheet_reorder_prototype.md`.

> **2026-06-09 re-scope (KEY).** The `moDrSheet` record in `Contents/Definition`
> IS the sheet's full content. Its single largest sub-structure — `moRelMgr_c`
> (~17.5 KB in a minimal drawing) — was decoded NOT as a geometry/sketch-relation
> blob but as the document's **detailing-standards manager** (units dictionary,
> annotation-priority enum, dimension standard, font table, line-font table). It
> is template-invariant across drawings (proven byte-identical on a synthetic
> pair; same structure on two real client drawings at ~500× scale). Consequence:
> the moDrSheet record's bulk is standards data, not a geometry kernel — but
> reorder still requires MOVING the whole moDrSheet record block and renumbering
> the object-map indices (the standards block is carry-verbatim *within* the
> moved record, but the record's global object indices all shift). The genuinely
> geometric content lives in the much smaller `moView_c` (~1.4 KB / view in an
> empty drawing) + `sgSketch`.
>
> **Reorder mechanism RESOLVED 2026-06-09** (`m4_drawing_sheets/log.md`): tab
> order = physical moDrSheet record order in Definition. Falsified the
> SheetNames-permutation shortcut (editing only SheetNames order is ignored by
> SW — it is a full mirror) and confirmed via diff-pair that the records swap.
> No list shortcut: reorder needs the record-move + index-renumber path.
> The blocker for reorder is therefore the recursive object-map walk that tracks
> indices through the whole stream (see M5 below). Lesson:
> `lessons/lesson_20260609_morelmgr_detailing_standards_not_geometry.md`.

**Deliverables:**

- `src/swformat/streams/sheet_previews.py` — read/write
  `SheetPreviews/SheetNames` UTF-16-LE length-prefixed format
- `src/swformat/streams/view_sheet_list.py` — read/write the
  `SW-Config-Model-View-Sheet-List` `@`-delimited record stream inside
  `docProps/ISolidWorksInformation.xml`
- `src/swformat/api/drawing.py` — high-level Drawing.sheets[N] views
- `src/swformat/tools/sw_sheets.py` — CLI

**Risks:**

- `add_empty_sheet` requires synthesizing bytes that didn't exist
  before — a qualitatively different problem from mutating existing
  bytes. The view collection inside Header2 (CArchive) references
  sheet structures by ID; an empty sheet still needs a skeleton view
  collection entry.
- Per-sheet preview PNGs are regenerated by SW on save; we can either
  strip them (let SW rebuild) or copy a blank PNG (cosmetic).

---

## Milestone 5 — CArchive decode for `Contents/Definition`

> **Open-ended research milestone.** No committed completion date.
> Each sub-milestone is independently shippable; partial completion
> still delivers value.

**Top-level criterion:** N+% of the feature tree of N test parts is
readable as structured Python objects with feature type, name, schema
version, and parent. (N is intentionally vague — useful coverage
emerges incrementally.)

**Effort:** Unbounded. Honest estimate based on prior art: 2-5 years
part-time would yield partial coverage of 5-15 common feature types.

**Feasibility:** LOW. `openswx` has had years and still does not
decode this. CAD format reverse engineering has a long-tail-forever
quality.

**Sub-milestones (independently shippable):**

- **M5.1 — CArchive primitives floor**: tag formats, schema dispatch,
  CString encoding, CObject map. Estimated 8-16 weeks. **Step 1 underway
  (2026-06-08).** It is the prerequisite for M3 (CMgrHdr2 is the same
  CArchive family) and for the *binary* side of edits (cut-list edits, the
  `Contents/CusProps` mirror of new global names). **NOTE — corrected this
  session:** M2's add-new-name and config-scoped props turned out to be
  **XML-only** (the `docProps/custom.xml` name dictionary), so they are
  already DONE and did NOT need this milestone; the earlier "M5.1 gates
  those" assumption was wrong.
  - **Shipped:** the framing layer (`carchive/archive.py`: object tags,
    class defs, ReadCount, CString — unit-tested); `Contents/CusProps`
    read (user + cut-list props); a byte-exact text-property writer proven
    against SW ground truth; and **M5.1 Step 1** — a round-trip-faithful
    model where `roundtrip(data) == data` with the **header and all user
    records re-serialized structurally** (`reserialize_header`,
    `serialize_user_records`; novalue/StringElem/PRP/MassProp) and only the
    cut-list/close-time-dump tail still verbatim.
  - **Remaining:** structurally decode the cut-list container body
    (`moCutListPropContainer_c` — confirmed NOT a flat tag stream) + the
    close-time object-map dump, then model-driven insertion (Step 2). This
    needs fresh SW diff-pairs. **Full spec + build plan: `docs/CARCHIVE.md`
    §3/§5.**
- **M5.1.5 — Recursive object-map walk of `Contents/Definition`** (NEW,
  2026-06-09): the flat object-map ledger (`carchive.objmap`) handles
  single-list streams (CusProps/CMgrHdr2) but is **structurally capped** on
  Definition — it desyncs at the first container whose parent resumes scalar
  fields *after* its inline children (proven: it dies at the doc-prefix
  node-name table, offset ~463 in `drawing_min`, mis-reading the "ALPHA"
  sheet-name CString's `FF FE` lead as a class-ref). The **recursive
  schema-driven engine** (`research/.../definition_decode/recursive.py`) is the
  required vehicle: a per-class `Serialize()` field schema, recursing on
  children, threading the object-map index (NEW_CLASS +2 / class-ref +1)
  through the recursion. This single primitive unblocks: (a) instance census
  (all `moView_c`/`moDrSheet`/`sgSketch` instances are class-refs by index — the
  name appears once), (b) index-contiguity proof for the standards block, and
  (c) M4 sheet reorder (index renumbering on block move). Section map +
  ordered schema build plan (moStamp_c → doc-logs/node-name table → moDrSheet):
  `research/empirical_findings/definition_decode/log.md`.
  **DONE 2026-06-10:** `recursive.py` threads EXACT, ground-truth-validated
  object-map indices seed-free from the root through the entire fixed-structure
  prefix to `moDrSheet @4033` (header → doc-logs incl. nested-stamp variant →
  node-name/external-ref table → units table [count=17] → 6 folders). Two hidden
  object-map drifts found+fixed via the ground-truth-CLASS_REF method (empty
  suObList in moExtObject_c; nested moLengthUserUnits_c). REMAINING: a
  content-general parser for the VARIABLE sheet-child bodies (the bounded ~6
  classes) to count their inline objects — the gate for index-exact reorder.
  Lessons: `lesson_20260610_definition_recursive_parser_exact_objmap_index.md`,
  `..._ground_truth_classref_index_drift_method.md`.
  **KEYSTONE PAYOFF — dimension COUNT solved on synthetic fixtures 2026-06-10
  (descent steps 1-13):** the `recursive.py` walk was extended class-by-class from
  the root through `moDrSheet → moRefPlane_c → moAbsoluteRefPlnData_c →
  moProfileFeature_c → sgSketch` (each a cross-validated frontier with exact
  object-map index threading on 3 fixtures), then through `sgSketch`'s scalar
  geometry (point array + a diff-pair-derived line entity span `92·nlines − 24`)
  to land on the dimension CLUSTER (`sgPntPntDist`). `count_dimensions()` then
  derives `moLengthParameter_c`'s object-map index (= cluster idx + 2) and counts
  its CLASS_REF tag (`0x8000|index`, a specific 2-byte tag at object boundaries) +
  1 = the count. **EXACT on all 4 synthetic line-dimension fixtures** incl.
  **ndim7 → 7** (the oracle) — succeeding where every byte anchor failed
  (`_DIM_VALUE_SIG` under-counts 2/7; `ff fe ff 02 44 00` over-counts 4128 on
  real). Supporting: `carchive.scan_class_defs` (class-table inventory, 0-FP on a
  246-class real drawing); `moLengthParameter_c` body (76 B, value f64 @+32).
  **REAL-FILE GENERALIZATION — content-general walk substantially advanced
  2026-06-10 (steps 19-30, after a SW session was relaunched):** the apparent
  "desyncs in populated content" (step 13) was diagnosed to the `moExtObjectList_c`
  external-object COLLECTION that model-referencing drawings have but bare-sheet
  synthetic fixtures lack. Built a clean rich synthetic fixture (`drawing_partview`
  = drawing_min + a model view, via `gen_drawing_partview.csx`), modeled the
  `ext_objects` BRANCH + the `ext_list` op + the part-ref `moExtObject_c`
  (diff-pair-cracked: handles + a fixed trailing with embedded objects), and the
  walk now **threads RICH (model-referencing) drawings END-TO-END to `moDrSheet` +
  the sketch** (the structural class of real files), with EXACT index, no
  regression. On the REAL drw_10 it now **parses the full doc-logs (15,575 obj) +
  reaches `moExtObjectList_c` + decodes element 0 + locates all 46 elements**
  (was: desync at the list). REMAINING for real files: the elements are
  VARIABLE-length (151-349 B; real parts carry config/associativity data minimal
  synthetic parts lack) and need content-general per-element FIELD decode (boundary
  byte-scanning over-matches — step 30), then the rich sheet content (multiple
  sheets, views, real dimensioned sketches w/ circles+arcs). This is the
  LOW-feasibility long-tail — now **de-risked + precisely characterized (tractable,
  parseable) rather than the resist-all wall it appeared as**. The dimension count
  stays **proven-on-synthetic**; `test_ndim7_dimension_count_oracle_xfail` stays
  xfail (asserts the SRC reader is fixed for REAL files — still false). See descent
  steps 1-30 in the definition_decode log + `count_dimensions`/`ext_list` in
  `recursive.py`.
- **M5.3 — Drawing sketch decoder** (`sgSketch`, drawing side): **MULTI-ENTITY
  READ SHIPPED 2026-06-10** (`api.sketches` + `swf-sketch list|dump`; 22 tests).
  `sgSketch` body = `[u32 pointcount][header][point array: pointcount
  × 142 B][entity array: type-sized][trailing]`, empty overhead 317 B; geometry is
  INLINE. Point payload is 2-D `(x, y)` (z fixed 0.0; the old "z@+16" was a
  structural over-read). The ENTITY ARRAY is now decoded per-entity: LINE (92 B),
  CIRCLE/ARC (112 B), array terminated by `0x8008`. Type tags: `u16@+16 == 0xbff0`
  → line, else curve with `u32@+30` closed/open flag (1=circle, −1=arc). Per-entity
  point→entity binding is **EXPLICITLY INDEXED** (PROVEN by a 4-line rectangle:
  4 shared corners, indices (0,1)(1,2)(2,3)(3,0) — the wrap closes the loop,
  impossible under positional). Index layout: line start/end `+2/+4`; curve center
  `+34`, circle perimeter `+16`, arc endpoints `+14`/`+16`. Validated on an
  SW-generated corpus (line/circle/arc/mixed/rectangle/5-line). Defensive: false-
  positive markers, OOB records, out-of-range indices → `invalid`/`[]`, no crash.
  Lesson: `lesson_20260610_sgsketch_drawing_sketch_entity_encoding.md`.
- **M5.4 — Sketch RELATIONS (constraints) read**: **SHIPPED 2026-06-10**
  (`read_sketch_relations` + `swf-sketch relations`; reports HORIZONTAL/PARALLEL/
  FIXED/... by `swConstraintType_e`). Relations are stored OUT-OF-LINE between
  the last entity record and the entity list's `0x8008` terminator; the reader
  anchors on the constant `02 00 00 00 00 00 fe ff 00 00` run present in every
  relation record (counts interned 2nd records correctly). FALSIFIED the earlier
  "entity-record busy-run = relation handle" reading. Entity binding = follow-up.
- **M5.5 — Sketch DIMENSIONS read**: **SHIPPED 2026-06-10**
  (`read_sketch_dimensions` + `swf-sketch dimensions`; reports kind + value).
  A dimension is a CArchive object cluster anchored by `moLengthParameter_c`
  (one per dim); value f64 at `+51`; kind from the display class
  (`moDisplayDistanceDim_c` / `moDisplayRadialDim_c` / ...); radial stores the
  DIAMETER. Lesson `lesson_20260610_sgsketch_drawing_dimension_encoding.md`.
  Entity binding + text placement = follow-up. ⚠️ The per-dimension VALUE/kind
  decode is correct, but the dimension COUNT/enumeration is NOT reliable beyond
  the synthetic fixtures — see the proven both-directions caveat under M5.7.
- **M5.6 — Sketch SPLINES read**: **SHIPPED 2026-06-10** (entity walker emits a
  `spline` entity bound to fit-point indices). Variable-size record tagged
  `modifSplineList_c`; fit points in the point array; point indices are
  global+sequential across entities. Known limit: control-point/knot block
  undecoded -> walk stops after a spline. Graduated into the sgSketch entity lesson.
- **M5.7 — MULTI-INSTANCE read on real multi-sheet drawings**: **SHIPPED
  2026-06-10**. MFC CArchive INTERNS class names (each written once, then
  `0x8000` CLASS_REF back-refs), so string-anchored readers saw only the FIRST
  instance. `read_sketches`/`read_sketch_entities` now `enumerate_sketches` — find
  the back-ref tags, accept any whose BODY decodes to a valid sketch with >=1
  entity (self-validating). Verified on real drawings: 13/38 sketch instances
  (290/457 entities) vs 1 by string search. `read_sketch_dimensions` anchors on a
  24-byte interning-immune value signature → all length-parameter VALUES.
  Relations were already byte-anchored. So the moDrSheet-schema marathon is NOT
  needed for sketch GEOMETRY/relations read (only for dimension COUNTING and the
  M4 reorder clean-close). CAVEAT — DIMENSION COUNT IS NOT TRUSTWORTHY (proven
  2026-06-10, supersedes the earlier "superset" framing): a controlled
  ground-truth test shows `read_sketch_dimensions` is unreliable in BOTH
  directions — it UNDER-counts (2 vs SW-authoritative **7** on the synthetic
  `ndim7` fixture: ALPHA 4 + BETA 3) AND OVER-counts (2016 on a 14.5 MB real
  `Definition`). Root cause: the anchor `_DIM_VALUE_SIG` is a fragment of the
  `moLengthParameter_c` body whose first bytes are a per-dimension id, so it
  matches only a sub-form; and even the invariant body marker `ff fe ff 02 44 00`
  (exact on all 8 synthetic fixtures) over-matches real text (4128). No byte
  signature isolates dimensions on real files — only the object-map walk (the
  keystone) does. A regression oracle (`test_ndim7_dimension_count_oracle_xfail`,
  xfail) flips when the walk lands. Lessons
  `lesson_20260610_carchive_interning_breaks_string_anchored_readers.md`,
  `lesson_20260610_count_drawing_sheet_dimensions.md` (the SW ground-truth method).
- Entity BINDING (relations + dimensions) and angular dims: **also SHIPPED
  2026-06-10** (relation `entity_indices`, dimension `refs`/`text_xy`, angular via
  `moAngleParameter_c`). Dimension-type coverage: distance/radial/angular (+
  diameter-as-radial); ordinate is view-based (out of sheet-sketch scope).
- **M5.8 — Sketch MODIFY (read→modify→write)**: **SHIPPED 2026-06-10**, all
  SW-verified (SW 2026, reopen errors=0/warnings=0). `move_sketch_point` (edit a
  point's f64 coords — geometry); `move_dimension_text` (annotation-only);
  `set_dimension_value` (edit the value f64 → SW RE-SOLVES the driving dimension
  and RESIZES the geometry — confirmed via open→save→re-decode: 0.10→0.15 moved
  the line endpoint). All span-preserving (`write_with_toc`); CLI `swf-sketch
  move-point|move-dim-text|set-dim-value`. The read→modify→write loop for sketch
  geometry + annotations + driven geometry. `move_sketch_point` now takes a
  `sketch_index` (added `Sketch.body_offset`) reaching ANY sketch incl. interned
  ones on multi-sheet drawings — **SW-verified 2026-06-10** (edited the 2nd
  sheet's interned sketch; reopen 0/0, both sheets intact). NEXT: add/delete
  entities; relation add/remove.
- **Sketch-read NEXT (the keystone — SYNTHETIC-PROVEN 2026-06-10; real-file
  BLOCKED)**: the CArchive CLASS TABLE / object-map walk — unblocks accurate
  dimension counting, interned kind/refs/placement resolution, AND the M4 reorder
  clean-close (one keystone, three payoffs). **DONE this session (synthetic):**
  the `recursive.py` walk reaches the dimension cluster via the full
  `moDrSheet→moRefPlane_c→moAbsoluteRefPlnData_c→moProfileFeature_c→sgSketch` chain
  (each frontier cross-validated, exact index threading) + the line `sketch_geom`
  span, and `count_dimensions()` returns the EXACT count on 4 fixtures (ndim7→7).
  Supporting: `carchive.scan_class_defs` (class-table inventory, 0-FP at 246
  classes incl. non-`_c` `moDrSheet`/`sgSketch`); `moLengthParameter_c` body (76 B,
  value @+32). **BLOCKED on real files:** the walk desyncs in populated
  folders/components (never reaches `moDrSheet`), so the count is synthetic-only;
  generalizing needs the content-general walk of populated `moDrawing_c` members +
  `moDrSheet` children — the same effort as M4 reorder (LOW feasibility /
  long-tail). REMAINING beyond that: angular dims (`moAngleParameter_c` parallel
  class-ref count); non-line `sketch_geom` (circle/arc 112-B records, empty
  sketches); closed splines + the spline control-point/knot block; write-side
  synthesis. See `research/empirical_findings/definition_decode/log.md` (descent
  steps 1-13) + `count_dimensions` in `recursive.py`.
> NOTE (2026-06-10): the estimates below are the ORIGINAL speculative figures
> and use a legacy numbering that PRE-DATES the shipped milestones above. They
> refer to the MODEL-side (part/assembly feature tree) effort, which is distinct
> from the DRAWING-side sketch read that actually shipped (M5.3–M5.6 above).
> Notably the "constraints and dimensions are a multi-year sub-project" estimate
> was FALSIFIED for the drawing-sketch READ side — constraints (M5.4) and
> dimensions (M5.5) read shipped same-day via the diff-pair method. The
> multi-year caution still stands for MODEL-side feature parsing and for the
> WRITE side of any of this.

- **(legacy) Document header (Header2 / Header3)**: doc-level data,
  UpdateStamp, last-saved-by, the view-collection reference for
  drawings. Estimated 6-12 weeks after M5.1.
- **(legacy) Model-side sketch decoder** (part/assembly feature-tree
  sketches; lines/arcs/circles): 8-16 weeks originally. The drawing-side
  equivalent shipped far faster (see M5.3–M5.6 above); the model side is still
  unstarted and the geometry-core caveats apply.
- **(legacy) Top-5 common feature types** (extrude, revolve, fillet,
  chamfer, hole): 12-26 weeks **per feature type** at the empirical
  rate. Probably more. (Unchanged — model-side feature parsing is the hard part.)

**Risks (numerous):**

- Schema version explosion: every feature has multiple schema
  versions across SW releases
- Class hierarchy is proprietary: we know `gfxSceneGraph_c` etc. exist
  from SLDWORKS.exe symbols but not their full Serialize() methods
- Parasolid-backed geometry payloads are opaque (Parasolid is itself
  a closed-source binary kernel; even if we decode the CArchive
  wrapping, the geometry inside is X_T-equivalent binary)
- Non-determinism inside CObject map ordering across saves

---

## Milestone 6 — Full read+write parity for parts (SPECULATIVE)

**Top-level criterion:** Generate any modification to a part
(add/remove features, edit sketch geometry, change configurations) and
write it; SW reopens and the changes are present with full fidelity.

**Effort:** 2+ years on top of M5.

**Feasibility:** SPECULATIVE. Requires complete-ish M5 coverage of
features, plus the inverse encoding pathway. Datakit took multi-decade
efforts to achieve this for commercial CAD formats.

**Status:** Aspirational. Not currently committed.

---

## Research aspirations (not on the roadmap)

These were originally proposed as M7 ("generative authoring") but
removed from the committed roadmap per adversarial review.

- **Generate SW files from scratch** without an existing file as
  template. Requires complete coverage of every required stream, every
  required CArchive class, every checksum/signature SW might validate,
  AND the implicit invariants between streams (feature-tree node count
  must match Definition stream object count, etc.). Probably
  impossible without leveraging Parasolid's X_T or a commercial CAD
  kernel as the geometry source.
- **Convert STEP/IGES/X_T → SLDPRT.** Possible-in-principle if M6 is
  reached; depends entirely on M5/M6 progress.
- **Convert SLDPRT → STEP/IGES** without SW. Easier than the inverse
  if M5 reaches geometry decoding; still requires a writer for the
  output format.

These are tracked in `docs/RESEARCH_ASPIRATIONS.md` (TBD); revisit if
M5 reaches meaningful coverage.

---

## Effort summary

| Milestone | Effort (part-time weeks) | Feasibility |
|---|---|---|
| M0 — Bootstrap | 1-2 days | ✓ DONE |
| M0.5 — Twin-save baseline | ✓ DONE (1 session) | ✓ DONE — Layer 3 is M1 gate |
| M1 — Round-trip (Layer 3 only) | 4-8 weeks | MEDIUM |
| M1 — Round-trip (Layer 2 + 3) | 8-20 weeks | MEDIUM-LOW |
| M2 — Custom properties | 2-4 weeks | HIGH |
| M3 — CMgrHdr2 + configs | 6-12 weeks | MEDIUM |
| M4 — Drawing sheet ops | 8-16 weeks | MEDIUM-LOW |
| M5 — CArchive deep | Unbounded (years) | LOW |
| M6 — Full parts parity | 2+ years on M5 | SPECULATIVE |

**Realistic year-one target:** M0 + M0.5 + M1 (Layer 3) + M2 + M3
(partial — read-only of CMgrHdr2). Roughly 4-6 months part-time. This
delivers: full inspection, bulk property edit, configuration listing
without SW running.

**Realistic year-two target:** M3 complete, M4 (rename/reorder),
M5.1 + M5.2 (CArchive floor + Header2 decode). Delivers: configuration
modification, drawing sheet ops, the foundation for further work.

**M5.3+ and beyond:** Tracked sub-milestone by sub-milestone, no
committed timeline.

## How to start the next milestone

1. Pick the next pending milestone from the table.
2. Read the milestone's "Deliverables" and "Risks" sections above.
3. Create `research/empirical_findings/<milestone>/log.md` and start a
   hypothesis log.
4. Build the deliverables. Each must pass its stated pass criterion.
5. When the milestone passes, write a lesson at
   `lessons/lesson_<YYMMDD>_m<N>_<short_slug>.md`.
6. Update this ROADMAP.md: move the milestone from pending → done,
   record the actual effort, list any risks that materialized.
