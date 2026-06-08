# Changelog

All notable changes to SWFormat are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — grow-beyond-span on PARTS via relocate-to-EOF, SW-verified (2026-06-11)
- The SW-accepted way to add data that overflows a stream's original compressed
  span — on PARTS — without the (unsolved) central-directory rewrite: the grown
  stream is appended as a fresh chunk at the original EOF and ONLY its TOC record
  is repointed (`off8`/`csz`/`usz`); no other chunk moves, so no offset shift and
  no stale pointers. `io.writer._serialize_relocate_grow` + `_grown_chunk_bytes`;
  `serialize_with_toc`/`write_with_toc` gain `relocate_grow=False`.
- Relies on two **Layer-3-verified** SW facts: SW ignores bytes appended after the
  trailing directory, and SW locates each stream via its TOC `off8` pointer (not a
  sequential walk).
- `api.properties.edit_properties` and `api.configurations.rename_configuration`
  auto-enable `relocate_grow` when `detect_doc_type(path) == "part"`. So on parts:
  adding a brand-new global property, adding a config-scoped property, and renaming
  a configuration to a LONGER name now succeed even when the stream overflows its
  span. **SW-verified on real v19000 parts**: config-0 add, 40-property bulk global
  add, 80-char config rename — all reopen e=0 with the new values; plus re-save
  durability (open→SaveAs→reopen, property persists).
- ⚠ **PARTS ONLY.** Assemblies and drawings reject a relocated (size-growing)
  stream — root-caused to a file-size/tail validation parts lack — so a
  grow-beyond-span there raises `SpanPreserveError` (unchanged honest refusal).
- Test: `test_relocate_grow_part_roundtrips`. Lesson:
  `lesson_20260611_relocate_to_eof_grow_parts.md`.

### Changed — offset-shift grow writer re-FALSIFIED and demoted (2026-06-11)
- The `-8`-pointer + TOC-size "offset-shift" grow path produces files that
  RE-PARSE but that real SOLIDWORKS REJECTS (`swFileRequiresRepairError`,
  e=2097152). Re-falsified on real v19000 parts AND the v15000 washer fixture it
  was once wrongly believed verified against. **`serialize_with_toc` now refuses a
  grow-beyond-span by default** (raises `SpanPreserveError`) instead of silently
  emitting an SW-rejected file; the offset-shift code is retained ONLY behind an
  explicit `_experimental_offset_shift` opt-in and is never a consumer path.
- Method finding: RE-PARSE != SW-VALID — an 87-file "grew_ok" sweep that only
  checked `reconstruct()==data` gave false confidence (87/87 re-parse → 0/87 SW-
  valid). A write path is "SW-verified" only after a Layer-3 reopen at e=0.
- ROADMAP M1.5/M3/M4 and FEATURES corrected. Lesson:
  `lesson_20260611_offset_shift_writer_sw_invalid.md`.

### Changed — dimension reader honesty scope: not a reliable enumerator on real files (2026-06-10)
- `read_sketch_dimensions` docstring + FEATURES now document that the
  per-dimension anchor `_DIM_VALUE_SIG` is validated only on the synthetic
  few-dimension fixtures. On a real multi-sheet production drawing it has **no
  structural-validation gate** (unlike the sketch/relation readers) and
  **over-matches** — 2016 hits on a 14.5 MB `Definition` whose true dimension
  count is far lower (same failure mode as the earlier `moLengthParameter_c`
  over-claim). Accurate per-instance dimension counting + attribution on real
  files needs the parked CArchive class table (keystone). No code/behavior
  change — scope/documentation correction only. See
  `research/empirical_findings/sketch_dimensions/log.md` (2026-06-10 22:25).

### Changed — `move_sketch_point` reaches any sketch via `sketch_index` (2026-06-10)
- `api.sketches.move_sketch_point(..., sketch_index=0)` + CLI `--sketch-index N`
  now select which sketch to edit among ALL instances in file order — including
  the interned (CLASS_REF) sketches on a real multi-sheet drawing, not just the
  first/literal one. Added `body_offset` to the `Sketch` dataclass (set in
  `read_sketch_at` for both literal `offset+8` and interned `offset+2` bodies)
  so the modify can locate any sketch's point array. +1 test.

### Added — M5.8: first sketch MODIFY (read→modify→write), SW-verified (2026-06-10)
- `api.sketches.move_sketch_point(path, out, point_index, x, y)` — move a sketch
  point: edits the two f64 coordinates IN PLACE in `Contents/Definition` (entity
  records reference points by INDEX, so only the point array carries geometry)
  and writes span-preserving via `write_with_toc`. The first sketch-GEOMETRY
  modify.
- `api.sketches.move_dimension_text(path, out, x, y)` — move a dimension's text
  placement (annotation-only, no geometry/constraint impact).
- `api.sketches.set_dimension_value(path, out, value)` — set a dimension's value;
  **SW re-solves the driving dimension and RESIZES the geometry** (verified
  end-to-end via open→save→re-decode: a 0.10 m line-length dim set to 0.15 m
  makes SW move the line endpoint so the line is 0.15 m). The canonical
  edit-a-dimension operation.
- CLI: `swf-sketch move-point|move-dim-text|set-dim-value`.
- All **SW-verified** (SW 2026): the edited drawings reopen with errors=0,
  warnings=0 (no repair) and re-decode with the change. Demonstrates the
  read→modify→write loop for sketch geometry + annotations + driven geometry (the
  project's core purpose, cf. the property/sheet/config edits). +4 tests.

### Added — M5.7: MULTI-INSTANCE sketch + dimension read on real multi-sheet drawings (2026-06-10)
- `read_sketches` / `read_sketch_entities` now enumerate EVERY `sgSketch`
  instance — full-form (literal) AND interned (CArchive CLASS_REF `0x80NN`
  back-ref + body). New `enumerate_sketches(defn)` scans for back-ref tags and
  accepts any whose body decodes to a valid sketch with ≥1 entity (a cheap
  structural pre-check keeps it fast). Verified on real production drawings:
  **13 and 38 sketch instances** (290 / 457 line entities) where a class-name
  string search found only **1** — in ~1.7 / 5 s.
- `read_sketch_dimensions` now anchors on a 24-byte interning-immune signature
  in the `moLengthParameter_c` parameter object, so it enumerates every such
  length-parameter's VALUE (interned included). EXACT on pure sketch-dimension
  drawings (synthetic fixtures, `two_dim`). **Caveat (SW-verified):** on a real
  ASSEMBLY drawing it OVER-COUNTS display dimensions — `moLengthParameter_c` is a
  generic length parameter (display dims + embedded model params), so the reader
  reports a superset (drw_60: reader 727 vs SW's 106 view display dimensions).
  Distinguishing display dimensions needs the interned display-class resolved via
  the CArchive class table (follow-up). Kind/refs/placement come from the
  display/handle strings (full-form instance only → interned `kind="unknown"`).
- This solves the CArchive-interning single-instance limitation for the
  geometry/entities/splines/relations readers (and dimension *values*) WITHOUT
  the full `moDrSheet`-schema parse — the relations-reader byte-anchoring idea
  applied to sketch bodies + dimension parameters. The `moDrSheet` schema is now
  needed only for the separate M4 sheet-reorder clean-close. +5 tests
  (183→188). Read-only, no SW.

### Added — sketch relation + dimension ENTITY BINDING, dimension placement, angular dims (2026-06-10)
- `SketchRelation.entity_indices` — each constraint now reports the
  entity-array indices it joins, parsed from its `sgLineHandle`/`sgArcHandle`
  handle list (full + interned/back-ref forms); multi-entity relations chain
  handles (e.g. PARALLEL → `[1, 0]`). CLI `swf-sketch relations` prints bindings.
- `SketchDimension.refs` — each dimension reports the point/entity indices it
  references (distance dim → its line's two endpoint POINT indices `[0, 1]`;
  radial → the arc ENTITY index `[0]`, null `sgEntHandle` filtered).
- `SketchDimension.text_xy` — the dimension's text-placement point `(x, y)`,
  two f64 at a fixed offset past the display-class string (same constant for
  distance vs radial).
- ANGULAR dimensions decoded: they use a different parameter class
  (`moAngleParameter_c` vs `moLengthParameter_c`), so the reader anchors on
  both and reads the value relative to the parameter-string END; value UNITS
  depend on kind (distance/radial = meters, angular = radians). A DIAMETER
  dimension on a sketch circle uses `moDisplayRadialDim_c` (same as radius,
  value = diameter) — the R/⌀ distinction is a display flag, not the class.
  Sheet-sketch dimension-type coverage is complete (distance/radial/angular);
  ordinate dims are view-based (out of the sheet-sketch scope).

### Validated — relations reader correct on REAL production drawings (2026-06-10)
- Ran the relations reader (type + count + binding) on two real multi-sheet
  production drawings: no crashes; 3302 / 1176 relations; 100% binding success;
  every constraint TYPE has a single semantically-correct ARITY
  (HORIZONTAL/VERTICAL/FIXED = 1; COINCIDENT/SAMELENGTH/… = 2; ATINTERSECT = 3).
  The byte-anchored relations reader generalizes to real data.

### Known limitation + next step — CArchive interning / multi-instance (2026-06-10)
- The geometry / dimensions / spline readers locate their target by class-name
  STRING, but MFC CArchive INTERNS class names (each written in full once, then
  `0x8000` back-refs), so on a real multi-sheet drawing they see only the FIRST
  instance. The relations reader is exempt (byte-anchored). Enumerating all
  instances needs a content-general `moDrSheet`-child parser — the SAME blocker
  the M4 sheet-reorder clean-close hit, so one piece of work unblocks both. This
  is the flagged next deep-RE step. See
  `lesson_20260610_carchive_interning_breaks_string_anchored_readers.md`.

### Added — M5.5 (read side): read drawing sketch DIMENSIONS (2026-06-10)
- `api.sketches` gains `SketchDimension(kind, value)` and
  `read_sketch_dimensions(path)`; CLI `swf-sketch dimensions FILE`. Reports each
  driving dimension's kind (`distance` / `radial` / `angular` / …) and stored
  value across a drawing's sketches.
- Reverse-engineered (graduated lesson
  `lesson_20260610_sgsketch_drawing_dimension_encoding.md`): a dimension is a
  CArchive object cluster; the reader anchors on `moLengthParameter_c` (exactly
  one per dimension; absent from geometry/relation/spline sketches), reads the
  value f64 at `+51`, and classifies by the nearest display-class string
  (`moDisplayDistanceDim_c` → distance, `moDisplayRadialDim_c` → radial, …),
  bounded by the next param so multi-dimension clusters don't cross-classify.
  A **radial** dimension stores the **diameter** (r=0.04 → 0.08).
- Verified distance 0.10/0.15, radial 0.08; zero false positives on
  geometry/relation/spline fixtures. +9 tests. Suite 167 → 176. Scope: kind +
  value; entity binding / text placement are follow-ups. Read-only, no SW.

### Added — M5.4 (read side): read drawing sketch RELATIONS (constraints) (2026-06-10)
- `api.sketches` gains `SketchRelation(type_id, type_name)` and
  `read_sketch_relations(path)`; CLI `swf-sketch relations FILE`. Reports the
  geometric constraints (HORIZONTAL / PARALLEL / FIXED / …) across a drawing's
  sketches by `swConstraintType_e`.
- Reverse-engineered (graduated into the sgSketch entity lesson): relations are
  stored **out-of-line** in a block between the last entity record and the
  entity list's `0x8008` terminator. The reader anchors on the constant
  schema-marker+sentinel run `02 00 00 00 00 00 fe ff 00 00` present in EVERY
  relation record (full- AND interned-handle forms), with the `type:u32` in the
  4 bytes before it — so two HORIZONTAL relations on two lines both count (an
  earlier longer signature undercounted the interned 2nd record). Implausible
  types (outside 1..90) are rejected so a chance anchor can't fabricate a relation.
- Verified HORIZONTAL ×1/×2, PARALLEL, FIXED; zero false positives on
  geometry/dimension/spline fixtures. +9 tests. Suite 158 → 167. Scope: relation
  type + count; entity binding is a follow-up. Read-only, no SW.

### Added — M5.6 (read side): read drawing sketch SPLINES (2026-06-10)
- The `sgSketch` entity walker now also decodes the **spline** record — a
  variable-size, class-string-tagged (`modifSplineList_c`) record (unlike the
  fixed positional line/circle/arc records) — emitting a `spline`
  `SketchEntity` bound to its **fit-point indices**.
- Reverse-engineered from SW-generated 3/4/5-point splines + a mixed line+spline
  fixture: fit points live in the 142-B point array (pcount = fit-point count);
  the record carries a u16 fit-point count then a `sgPointHandle` list (each
  `0x8071` CArchive back-ref + u16 GLOBAL point index — same indexed model as
  relations). Point indices are **global+sequential** across entities (a
  line+spline sheet binds the line 0,1 and the spline 2,3,4); the mixed case
  previously decoded to `[]`.
- Known limit: the control-point/knot block is undecoded, so the spline record
  size is unknown — the walk **stops after a spline** (entities after one are
  truncated). +5 tests. Suite 153 → 158. Read-only, no SW.

### Added — M5.3: multi-entity sketch decode with indexed point binding (2026-06-10)
- `api.sketches` gains `SketchEntity`, `Sketch.entities`, and
  `read_sketch_entities(path)`. The decoder now walks the `sgSketch` ENTITY
  ARRAY and returns per-entity geometry — `line` (start/end), `circle`
  (center+perimeter), `arc` (center+2 endpoints) — resolving each entity's
  points by **explicit point-index binding**.
- Reverse-engineered from an SW-generated multi-entity corpus (line, circle,
  arc, line+circle, 4-line rectangle, 5-line). Indexed binding is PROVEN by the
  rectangle: 4 connected lines over 4 SHARED corner points bind indices
  (0,1)(1,2)(2,3)(3,0) — the wrap to 0 closes the loop and is impossible under
  positional binding. Entity layout: line 92 B / curve 112 B, array terminated
  by `0x8008`; type via `u16@+16==0xbff0` (line) else `u32@+30` closed/open flag
  (1=circle, −1=arc); index fields line `+2/+4`, curve center `+34`, circle
  perimeter `+16`, arc endpoints `+14`/`+16`.
- Defensive: a false-positive marker, an out-of-bounds record, an out-of-range
  point index, or a missing terminator yields `entities=[]` (never raises).
  Legacy single-entity behavior is preserved (no entity array → first-order
  `kind`). +8 committable synthetic-entity-array tests (incl. the wrapping-index
  falsifier and defensive cases). Suite 145 -> 153.

### Fixed — M5.3 sketch reader: 2-D point decode (z was an over-read of structure) (2026-06-10)
- `api.sketches.read_sketch_at` now reads the point record as 2-D `(x, y)` and
  reports `z = 0.0` explicitly. The original cut unpacked a 3rd f64 at
  `point+16` as "z", but on-disk dissection shows `+16` is structural (bytes
  `00 00 01 00 00 00 00 00`, a denormal that merely *rounded* to 0.0 — not a
  clean f64 zero). Drawing sketches are planar, so z is genuinely always 0.0.
  No behavioral change on valid sketches (coordinates identical) but robust
  against the structural bytes. Pinned by a regression test that puts a large
  f64 in the `+16` slot and asserts decoded `z == 0.0`. Suite 144 -> 145.

### Added — M5.3 (read side): read drawing sketch geometry without SOLIDWORKS (2026-06-10)
- `api.sketches` (`read_sketches`, `read_sketch_at`, `find_sketch_offsets`,
  `Sketch`) + `swf-sketch list|dump` CLI. Decodes each drawing sheet's
  `sgSketch` inline geometry: the `[u32 pointcount][80 B header][pointcount ×
  142 B point records][entity fields]` layout → entity kind (line / circle /
  arc / empty) + every `(x, y, z)` f64 coordinate triple. Read-only, pure
  Python. Promoted from the `definition_decode/read_sketch.py` prototype;
  validated against the single-primitive `geom_*` fixtures.
- Robustness: `read_sketch_at` sanity-caps the declared point count against the
  bytes the buffer can physically hold, so a false-positive `b"sgSketch"`
  marker hit on a non-drawing stream decodes to `invalid` (dropped by
  `read_sketches`) instead of crashing with `struct.error`. Verified a real
  `.SLDPRT` returns 0 sketches cleanly.
- Tests: 13 (synthetic byte-layout for line/circle/arc/empty/unknown + multi-
  sketch find + false-positive/truncation hardening + `geom_*` fixture
  cross-checks). Suite 131 -> 144.
- Multi-entity per-point binding is positional in the validated fixtures; an
  explicit per-entity binding (and dimensions/splines) is a documented research
  extension needing new diff-pairs.

### Added — M4 (modify): rename a drawing sheet without SOLIDWORKS (2026-06-09)
- `api.sheets.rename_sheet(path, old, new, out)` (+ `SheetError`, dup/exists
  guards) and `swf-sheets rename` CLI. Edits the sheet-name CString in the
  authoritative `Contents/Definition` plus the `Header2` + `SheetPreviews/
  SheetNames` mirrors. **SW-verified** (SW 2026): reopens e=0/w=0 with
  `GetSheetNames` reporting the new name. Finding: `SheetNames` is a preview
  MIRROR (editing it alone does nothing); `Contents/Definition` is authoritative
  (editing it alone suffices). Suite 130 -> 131.

### Added — M4 (read side): list drawing sheet names without SOLIDWORKS (2026-06-09)
- `api.sheets` (`read_sheets`, `sheet_count`, `read_sheet_names_from_stream`) +
  `swf-sheets list|count` CLI (replaces the M4 stub). Decodes the
  `SheetPreviews/SheetNames` stream (`[u16 count][count×CString]` + preview tail).
  Count matches the number of `Images/Sheet_N` streams (28/28, 10/10 on staged
  drawings). Tests: synthetic parser + empty + staged-drawing count-match.
  Suite 127 -> 130.
- Context: redirected here from the object-map codec, which reached a validated
  foundation but whose remaining steps are marginal-value (the add_text_properties
  heuristic already works byte-exact; cut-list insert is niche). Codec foundation
  documented + resumable in research/empirical_findings/objmap_codec/.

### Added — edit a weldment cut-list property VALUE without SOLIDWORKS (2026-06-09)
- `carchive.cusprops.walk_cutlist_records(data)` — structural walker over the
  `moCutListPropContainer_c`; returns each cut-list property's name, value,
  resolved, byte spans, and a `linked` flag (formula-driven/system-defined vs
  editable user-text).
- `carchive.cusprops.set_cutlist_value(data, name, new)` — edits a USER-TEXT
  cut-list property's value (the authoritative `moCusPropStringElem_c`
  elem-value plus the record-core value/resolved, consistently). Rejects
  formula-linked/system-defined props (MATERIAL/QUANTITY — SOLIDWORKS recomputes
  those) and unknown names.
- **SW-verified** (SW 2026 / v19000): editing `SWFTEST=hello` purely in the
  binary `CusProps` reopens in SOLIDWORKS (errors=0, warnings=0) with the
  cut-list folder reporting the new value. Authoritative-field RE: editing only
  the elem-value sticks; editing only `value` or `resolved` does not.
- New **synthetic, committable** fixture `weldment_min.SLDPRT` (a generated
  empty weldment + a `SWFTEST` cut-list property; no client data) +
  `gen_weldment_min.csx` recipe, registered in `corpus.config.json`
  (`license_status: synthetic`). First committable cut-list tests.
- Fixed a latent test-helper bug: the config-index parser now skips
  `Config-<n>-Cutlist-Properties.xml` (only bare-integer middles are configs).
- Suite 91 → 113 green.

### Added — cut-list value edit API + CLI surface (2026-06-09)
- `api.properties.edit_cutlist_value(path, name, value, out)` and
  `api.properties.read_cutlist_properties(path)`; new `swf-prop cutlist-set
  FILE NAME VALUE [--out|--in-place]` CLI subcommand. SW-verified end-to-end
  through the CLI (reopens e=0/w=0 with the new value). Suite 113 → 114.
- Diligence: confirmed the structural `walk_cutlist_records` must NOT yet replace
  the `read_cutlist_props` heuristic — on a 27-config weldment the walker misses
  `LENGTH`/`WIDTH` dimension props (records outside `field2∈{9,11}`). The reader
  stays; the walker is scoped to the editing path. Documented in the log.
- FOLLOW-UP (same day): closed that gap and UPGRADED the reader.
  `walk_cutlist_records` now accepts any plausible parent index
  (`0 < field2 < 0x8000`, not just {9,11}), catching dimension/config-specific
  records (`LENGTH`/`WIDTH`; field2 73/75/1035) — verified ZERO false positives
  vs the heuristic. `read_cutlist_props` now delegates to the structural walker:
  identical on DELETE (v11000) + the synthetic weldment, strictly cleaner + more
  complete on a 27-config weldment (drops value-as-name junk, adds dimension
  props). Suite 114 → 115.

### Investigated — M3 set-active is NOT a CMgrHdr2 edit (deferred to M5.x) (2026-06-09)
- A set-active diff-pair (synthetic `Default`+`CfgB`, saved each-active) showed
  `CMgrHdr2.field1` is an active *mirror* (active record = 1, others = 0) but
  **not authoritative**: grafting the entire CfgB-active `CMgrHdr2` — or
  `Header2`, `SwDocContentMgrInfo`, or `ISolidWorksInformation.xml` — onto the
  Default-active file leaves SW reporting Default active (opens e=0 w=0).
- The active/last-saved config is denormalized across many streams, centred on
  the binary `Contents/CMgr`, which is tightly coupled — byte-grafting it from
  another save **crashes SOLIDWORKS on open** (RPC 0x800706BE). Documented as a
  do-not-do. Set-active therefore needs the coordinated multi-stream edit + the
  CArchive object-map codec → **deferred to M5.x**. No code change.
- Asymmetry recorded: CMgrHdr2 is authoritative for the config NAME (rename ✅)
  but only a mirror for the ACTIVE flag.

### Added — M3 (modify): rename a configuration without SOLIDWORKS (2026-06-09)
- `carchive.cmgrhdr2.rename_configuration(data, old, new)` rewrites the target
  `dmConfigHeader_c` record's NAME + display CStrings (the object-map tail,
  ids, and trailing stamp are carried verbatim — tail-bytes invariant).
- `api.configurations.rename_configuration(path, old, new, out)` (+
  `ConfigurationError`, dup-name guard) and the `swf-configs rename` CLI.
- **SW-verified** (SW 2026 / v19000) via a synthetic rename diff-pair: SW
  reopens the edited file with **no repair (errors=0, warnings=0)** and reports
  the new name from `GetConfigurationNames`, on **parts AND assemblies**, for
  same-length and length-changing names, and for configs carrying
  config-specific custom properties (those survive — the property XML is keyed
  by config *index*, not name).
- Finding: `Contents/CMgrHdr2` is **authoritative for the config-name list**;
  the name is denormalized across ~7 carriers + caches but SW tolerates them
  stale on open. Rename therefore did **not** require the general object-map
  codec (scope reduction vs the earlier M3 estimate).
- Tests: 6 new (L1 handler reversibility / length-change / unknown-name; L2
  write round-trip / dup-rejection / grow). Suite 85→91.

### Added — M3 (read side): list configurations without SOLIDWORKS (2026-06-08)
- Decoded `Contents/CMgrHdr2` (the MFC-CArchive config-manager header):
  `dmConfigMgrHeader_c` + `u16 count` + per-config `dmConfigHeader_c` records
  (NAME = first CString after the tag + index; `$PRP:`-linkage strings skipped).
- New `carchive.cmgrhdr2` (`read_configuration_count`, `read_configuration_names`),
  `api.configurations` (`read_configurations`, `configuration_count`), and the
  `swf-configs list|count` CLI (replaces the stub).
- **SW-verified** to equal `IModelDoc2.GetConfigurationNames` on simple,
  dimensional (+`$PRP`), sheet-metal-derived, and multi-config files
  (a 20-config part, a 4-config part, a 4-config assembly, a 2-config washer,
  a 1-config part — all exact). Count exact across 87/87 staged parts/assemblies.
- Tests `test_configurations.py` (known sets + invariants + absent-stream).
  Suite 78→85. First M3 capability; modify surface (rename/active/derived) next.

### Validated — write path robust across 91 real files (property add) (2026-06-08)
- No-SW write sweep: `edit_properties(add SWF_SWEEP)` over all **91 unique real
  files** (parts/assemblies/drawings) — every output re-parses with no orphan
  bytes and reads the new property back. **0 failures.** Exercises
  `set_property` (incl. empty-store add) + `write_with_toc` across real-world
  variety. (Span-preservation sufficed for all — a single small prop fits each
  file's span; the offset-shift grow path is covered separately by
  `test_grow_beyond_span_modern_file_succeeds` + the SW-verified config/bulk
  adds.) Not added as a standing test (heavy); the read-sweep guard + targeted
  property tests cover regressions.

### Validated — reader robust across 91 real files (parts/assemblies/drawings) (2026-06-08)
- Swept the foundational read pipeline over **91 unique real W:-drive files**
  (64 parts, 23 assemblies, 4 drawings; up to 373 KB): **0 exceptions, 0
  no-orphan-bytes failures, 0 stream-read failures, all detected modern**. The
  3-file corpus had under-sampled real-world variety (esp. drawings + large
  weldments); this confirms Layers 0–2 (chunk walker, gap model, stream
  inflate) are solid corpus-wide. New regression guard
  `test_read_robustness_across_staged_corpus` (skips if the staged tree is
  absent). Suite 77→78.

### Improved — CArchive CusProps parser: value-element COUNT + `moCusPropDimEle_c` (2026-06-08)
- The wrapper `flag` is the value-element COUNT (0/1/2+), not a boolean —
  `walk_user_records`/`_object_map_walk`/`serialize_user_records` now loop
  `flag` times via shared `_consume_elem_body`/`_emit_elem_body` helpers.
- Added the `moCusPropDimEle_c` value-element (dimension-linked props on
  weldments): body `<formula:CString> <u32><u32> <resolved:CString>` + a
  22-byte dimension-linkage trailer.
- Driven by a real v19000 weldment fixture (local, non-distributed). **The
  v19000 weldment's full user-record region now parses
  and re-serializes byte-exact** (regression guard
  `test_weldment_dimele_user_records_roundtrip`, suite 77). Backward-compatible.
  Remaining for full-stream round-trip: the v19000 post-user-list framing +
  cut-list / config-cutlist containers (see the cusprops log).

### Added — grow-beyond-span writes (incl. config-property ADD) on modern files (2026-06-08)
- **SW re-verification on a FRESH SW save revealed the offset-`-8` pointer fixup
  is NOT universally broken** — it was generalized from one peculiar 2019 test
  file (`DELETE.SLDPRT`, `_MO_VERSION_11000`, 6× directory self-pointers). On
  modern files (`v15000` washer, `v19000` SW-2026) the fixup reopens cleanly
  for grow, shrink, dir-only and all-chunks shifts (SW-verified e=0).
- `serialize_with_toc`/`write_with_toc` now use **two strategies in order**:
  (1) span preservation (safe on ALL files; tried first); (2) when an edit
  GROWS a chunk beyond its span, an **offset-shift fixup** — gated to
  `doc_version >= 15000` (`_MIN_OFFSET_SHIFT_VERSION`) so it never emits a
  rejected file on the old structure. Old (`v11000`) docs still raise
  `SpanPreserveError` for grows. New `allow_grow` flag (default True).
- **This unblocks config-scoped property ADD and large value edits on modern
  files.** SW-verified end-to-end through `edit_properties(..., config=0)` on
  washer (`v15000`): config "Default" reopens showing the new property, global
  store untouched. (The user's SW-2026 files are `v19000` → supported.)
- Tests: `test_grow_beyond_span_modern_file_succeeds` (offset-shift path on a
  modern file), `test_span_preserve_overflow_raises` reworked to use
  `allow_grow=False`. Suite 74→75. The version/structure characterization is in
  `research/empirical_findings/m1_writer_roundtrip/log.md`; `toc.py`'s
  `fixup_offset_pointers`/`build_offset_map` are back in active use (no longer
  "superseded").

### Fixed — CRITICAL: size-changing property writes were rejected by SW; replaced with span-preservation (2026-06-08)
- **SW re-verification (pinned empty session) revealed `write_with_toc` produced
  files SOLIDWORKS REJECTS (`swFileRequiresRepairError`) for any edit that
  shifts chunk offsets** — i.e. the common case. The offset-`-8` pointer
  "fixup" left zero stale pointers yet SW still rejected; the earlier
  "M1.5/M2 SW-verified" was only ever exercised on edits whose re-deflate
  happened to land on the same `csz` (no shift). This was a latent
  incompleteness, not introduced this session (the fixup output is byte-
  identical to before).
- **Fix: span preservation** (`io.writer`, SW-verified e=0 + GetAll3 reflects
  the change). A modified chunk is re-deflated and **padded back to its
  original `csz`**, so its span is unchanged and **no later offset shifts** —
  the central TOC's offset pointers stay valid (no fixup needed); only the
  modified chunk's TOC `usz` is updated. `serialize`/`serialize_chunk` gain a
  `span_preserve` flag; `serialize_with_toc`/`write_with_toc` now use it.
- New `SpanPreserveError`: when an edit grows a chunk beyond its original
  compressed span even at max deflate, the writer **refuses** (rather than
  emit a file SW rejects). `edit_properties` drops the best-effort binary
  `CusProps` mirror if only IT overflows (custom.xml is authoritative).
- **SW-verified via the API** (`layer3_propedit.py`, session pid:16804):
  on a PART — edit (REVISION→L3CHK), delete (count 10→9, name gone), add-new
  (SWF_L3_NEW visible); on an ASSEMBLY (`DELETE.SLDASM`) — edit + delete; all
  reopen `e=0` with the change reflected. Config-scoped ADD is span-limited on
  typical (slack-less) config streams → `SpanPreserveError` (honest limit;
  needs the central-directory rewrite).
- Tests: `test_span_preserving_edit_keeps_offsets` (offsets preserved, same
  file size), `test_span_preserve_overflow_raises`; config test reworked to
  document the span limit. The old offset-fixup `fixup_offset_pointers` /
  `build_offset_map` remain (unit-tested) but are NO LONGER used by the writer
  and are insufficient for SW acceptance alone. Full evidence + recipe in
  `research/empirical_findings/m1_writer_roundtrip/log.md`. Suite 73→74.

### Fixed — broken console-script entry points; added the promised `dump_streams` CLI (2026-06-08)
- `pyproject.toml` declared three `[project.scripts]` entry points whose modules
  did not exist — `swf-dump-streams`→`tools.dump_streams`, `swf-configs`→
  `tools.sw_configs`, `swf-sheets`→`tools.sw_sheets`. After `pip install -e .`
  those commands failed with `ModuleNotFoundError`, and the README quickstart
  even invoked `python -m swformat.tools.dump_streams`.
- Created `tools/dump_streams.py` as a real M0 tool: writes every decompressed
  stream to a directory (mirroring the `name/with/slashes` namespace as
  sub-dirs), with `--list` for a name+size table; path-escape guard on stream
  names. Verified on a real part (39 streams).
- Created `tools/sw_configs.py` (M3) and `tools/sw_sheets.py` (M4) as honest
  stubs that print a clear "not yet implemented" status and exit 2, so the
  entry points resolve instead of crashing.
- Corrected the stale `pyproject.toml` comments (echo-check and sw-prop are
  shipped tools, not stubs) and the README structure listing.

### Fixed — chunk walker aborted the whole scan on an early coincidental marker (2026-06-08)
- `_scan_chunks` did `if m < 4: break`, conflating `find`'s "not found" (-1)
  with "a marker byte-pattern appears before offset 4". A coincidental early
  marker would `break` the entire scan, silently dropping **every** real chunk
  (all begin at `m >= 4`, since a header starts at `si = m-4 >= 0`) and
  degrading the file to one opaque gap with no decoded streams. Now `-1` breaks
  but `0..3` skips (`pos = m+1`) and the scan continues. Strict no-op on every
  real/corpus file (their first marker is always at `m >= 4`); strictly more
  robust on edge inputs. Regression test
  `test_early_coincidental_marker_does_not_abort_scan`. Suite 72→73.
- Note: `doc_version(streams: dict)` matches the reference reader's signature
  (the brief's `doc_version(data)` wording is imprecise; no caller passes bytes)
  — reviewed, no change needed.

### Fixed — `serialize_with_toc` shared-temp-file concurrency hazard (2026-06-08)
- The TOC writer re-parsed its freshly serialized output by writing it to a
  **fixed-name temp file** (`%TEMP%/_swformat_toc_tmp.sld`) and reading it
  back. Two concurrent size-changing edits (batch/parallel processing) would
  clobber each other's temp file → wrong offsets / corrupt output. Added an
  in-memory `io.reader.read_document_bytes(data)` parse entry point (the parse
  is pure on bytes) and switched the writer to it — eliminating the race and
  the disk round-trip. `read_document_bytes` is also re-exported at the package
  top level. Equivalence test `test_read_document_bytes_matches_path`
  (corpus-parametrized). Suite 69→72.

### Fixed — XML property editor: delete leaked dictionary entries; special-char names (2026-06-08)
- **`delete_property` now removes the `propertyNameDictionaryElement` entry too**,
  not just the `<property>` element — mirroring the add path. SW reads the
  visible property list from the name dictionary, so leaving the entry risked
  the deleted name lingering, and a later re-add of the same name created a
  DUPLICATE dictionary entry plus `_next_pid` drift (the stale pid kept being
  counted). The two stores are now kept in sync (the exact inverse of an add).
- **Property names with XML metacharacters (`& < > "`) now round-trip.** Names
  sit in an XML attribute, so they are escaped for attribute context — incl.
  `&quot;`, which the default `escape` omits — and the regex searches now use
  that same escaped form (previously a name was stored escaped but searched
  raw, so it could be added but never found/updated/deleted). `list_properties`
  reverses `&quot;` explicitly (`saxutils.unescape` doesn't by default), so the
  returned name matches the input. Helpers `_attr_escape`/`_attr_unescape`
  centralize this.
- Tests `test_delete_removes_name_dictionary_entry` (incl. no-duplicate-on-readd)
  and `test_special_char_name_roundtrip` (`& < > "`, add/get/update/delete).
  Suite 67→69. Found in a focused review sweep of `streams/custom_props.py`.

### Hardened — `fixup_offset_pointers` cascade-safety + direct unit tests (2026-06-08)
- The TOC offset-pointer rewriter scans gap bytes unaligned (to catch "loose"
  pointers not at the known `+0x28` slot). It previously read match candidates
  from the same buffer it was mutating, so a rewrite could change the bytes an
  overlapping later window — or a genuine pointer just after a spurious match —
  was read from, a cascade that could corrupt a real pointer. Now every 4-byte
  window is evaluated against an **immutable snapshot** of the input and writes
  land in a separate buffer: order-independent, cascade-free, and byte-for-byte
  identical to the prior behavior on real files. Added a skip-no-op guard for
  unmoved chunks. Expanded the docstring with the design rationale + the
  accepted residual risk (coincidental match on a *moved* offset).
- Added direct unit tests `test_fixup_offset_pointers_unit` (remap moved,
  leave non-pointers + unmoved untouched, repeated pointers) and
  `test_fixup_offset_pointers_only_touches_gaps` — the function previously had
  only end-to-end coverage. Suite 65→67.

### Fixed — `encode_cstring` corrupted astral (non-BMP) strings (2026-06-08)
- The MFC CString length field is a UTF-16 **code-unit** count (matching
  `CStringW` and `read_cstring`, which reads `len*2` bytes), but
  `encode_cstring` wrote `len(text)` — the Python **code-point** count. For
  Basic-Multilingual-Plane text these are equal, but an astral character
  (emoji, CJK Ext-B, …) is one code point and **two** UTF-16 code units, so the
  declared length was too small and the round-trip broke (the reader read a
  lone surrogate → decode failure). Any custom-property value containing such a
  character, written via the binary `add_text_properties`/`make_text_record`
  path, would have corrupted `Contents/CusProps`. Fixed to use `len(enc)//2`;
  the `>= 255` guard is now measured in code units. Found in a static/edge-case
  sweep; regression test `test_cstring_roundtrip_astral`. Suite 64→65.

### Fixed — `CustomPropsError` raised as `NameError` in CArchive layer (2026-06-08)
- `carchive/cusprops.py` referenced `CustomPropsError` in every error path
  (`parse_container_header`) but never imported it, so a malformed CArchive
  stream raised a bare `NameError` instead of the intended, catchable
  `CustomPropsError`. Added the import (a valid Layer-3→Layer-2 downward
  dependency) and an explicit `__all__`. Regression test
  `test_parse_container_header_raises_custompropserror`.

### Added — M5.1 Step 1: structural user-record re-serializer (all variants) (2026-06-08)
- `carchive.cusprops.serialize_user_records(data)` — re-emits the whole
  `moAdvCusProp_c` record span **structurally** (object tags + class defs +
  every CString rebuilt from the decoded model; the shared object-map index is
  tracked so CLASS_REFs resolve to the right body shape). Proven **byte-exact**
  vs `data[first_off:end_off]` across the corpus, covering all record variants
  present — `novalue` (flag 0), `StringElem`, `PRP`, `MassProp` — and both
  first-occurrence NEW_CLASS and subsequent CLASS_REF tags. Incidentally proves
  the CString codec is bijective on real corpus strings.
- `roundtrip()` now rebuilds the record region structurally (was verbatim); only
  the cut-list container + close-time object-map dump tail remains verbatim
  (tail-bytes invariant, deferred pending more diff-pairs). Test
  `test_serialize_user_records_byte_exact`. Suite 61→64.

### Added — M5.1 Step 1: structural header re-serializer + full-stream round-trip (2026-06-08)
- `carchive.cusprops.reserialize_header(data)` — rebuilds the CusProps
  container-chain header **structurally** (NEW_CLASS tags + class defs +
  `moCusPropMgr_c` scalar body `[count,1,1,0]` + the `suObList`/`moAdvCusPropList_c`
  u16 counts), reading schemas from the stream for cross-version tolerance.
  Proven **byte-exact** vs the original header slice across the corpus
  (part-with-cutlist, assembly-without, empty store). Unlike a slice-based
  round-trip this genuinely tests the header model — a wrong class order /
  body width / count position would diverge.
- `carchive.cusprops.roundtrip(data)` — the Step-1 acceptance gate: reassemble
  the whole stream from the model (structural header + verbatim user-record
  span + verbatim cut-list/close-time-dump tail, per the tail-bytes invariant)
  and require `== data`. Raises with the first differing offset on drift. As
  the record/tail regions gain structural writers, their verbatim spans shrink
  and the same gate keeps them honest.
- Tests `test_reserialize_header_byte_exact` + `test_cusprops_roundtrip`
  (corpus-parametrized). Suite 55→61.

### Added — M5.1 Step 1: CArchive user-list coverage gate (2026-06-08)
- `carchive.cusprops.check_user_list_coverage(data)` — the no-orphan-bytes
  falsification gate for the CusProps user-property region (the CArchive
  analogue of the chunk-walker's coverage invariant): verifies the structured
  record walk is contiguous, abuts the header, lands exactly on the `suObList`
  terminator, and that the recognised boundary (the `moCutListPropContainer_c`
  container by NAME, or trailing terminators) follows. Raises
  `CustomPropsError` on any drift; returns a diagnostic report dict.
- Corpus-parametrized test `test_user_list_coverage_no_orphan_bytes` proves
  the CusProps decode generalizes across the whole corpus (part-with-cutlist,
  assembly-without, empty store), not just the single diff-pair it was derived
  from. Suite 51→55.
- This is Step 1 of the `docs/CARCHIVE.md §5` build plan toward the M5.1
  object-map codec.

### Added — `docs/CARCHIVE.md` Layer-3 spec + M5.1 codec plan (2026-06-08)
- New living design doc for the MFC CArchive layer, synthesizing the decoded
  `Contents/CusProps` findings into a reconstruction-grade spec: the framing
  layer (tags / class defs / counts / CString), the **shared class+object
  index map** model, the close-time object-map dump, CObList element framing,
  the two distinct counters, the worked `moAdvCusProp_c` record layout, the
  proven byte-exact write recipe, and a safe incremental M5.1 build plan
  (round-trip-faithful reader → insertion via the model → reuse for M3) with
  a failure-modes table. Documentation-first prelude to M5.1 (no code change).
- Linked from `README.md` (docs tree) and `docs/ROADMAP.md` (M5.1).

### Added — Config-scoped custom properties (2026-06-08)
- **Per-configuration properties** now read/add/edit/delete through the same
  API and CLI as global properties. `api.properties.read_properties` and
  `edit_properties` take a keyword `config: int | None = None`; `config=N`
  targets `docProps/Config-N-Properties.xml` (0-based config index) instead
  of the document-level `docProps/custom.xml`. New `_props_stream(config)`
  helper centralizes the stream-name mapping.
- `swf-prop list/get/set/delete` gain `--config N`. Output messages now state
  the scope ("global" vs "config N").
- **SW-verified**: adding `CfgMat="A36 plate"` to `Config-0-Properties.xml`
  via the dictionary-fixed `set_property` makes SOLIDWORKS show it for that
  configuration on reopen (`configPropCount` reflects it) — XML-only, no
  binary CusProps counterpart needed for config props.
- Tests `test_read_config_properties` (parametrized over config-bearing
  corpus) + `test_edit_config_properties_roundtrip` (asserts the new name
  lands only in the config stream, global store untouched, no orphan bytes).
  Suite now 51.

### Done — M2 COMPLETE: add brand-new custom properties (2026-06-08)
- **Root cause of the long-standing "add doesn't show" gate found & fixed:**
  SOLIDWORKS reads the property list from the **name dictionary**
  (`propertyNameDictionaryElement` entries) in `docProps/custom.xml`, not
  from the `<property>` value elements. `streams/custom_props.set_property`
  now writes a dictionary entry (+ `FPVals` + correct pid) when adding a new
  name; `_next_pid` now ignores SW-internal `0x0100000N` pids.
- `api/properties.edit_properties` keeps the binary `Contents/CusProps` store
  consistent for new names (best-effort, via the byte-exact
  `carchive.add_text_properties`); `swf-prop set` reports add vs set and no
  longer warns (the limitation is resolved).
- **SW-verified end-to-end**: `swf-prop set <newname>` → SW reopens and shows
  the new property (count 10→11). Test
  `test_add_new_property_updates_both_stores` (suite now 47). M2 is complete:
  get/list/set-existing/add-new/delete all work without SOLIDWORKS.

### Added — M5.1 CArchive writer (byte-exact) + docs (2026-06-08)
- `src/swformat/carchive/cusprops.py` — `add_text_properties()` /
  `make_text_record()` / `_object_map_walk()`: write text custom properties
  into the binary `Contents/CusProps` store. **Byte-exact vs SOLIDWORKS** —
  `add_text_properties(c_B0, [ZZ_ONE, ZZ_TWO]) == c_B2` (SW's own output),
  covered by `test_add_text_properties_byte_exact_vs_sw` (suite now 46).
  Inserts records at the list terminator, bumps the property counter +
  list count, and re-indexes the cut-list class-refs (K=2·N).
- End-to-end check: a file with a brand-new property added to both stores
  via `write_with_toc` **opens cleanly in SW** (writer emits valid CArchive)
  — but SW does not yet surface the new NAME, indicating a third
  registration gate still to decode (see the cusprops log). Editing/deleting
  EXISTING properties remains fully SW-verified.
- `docs/FEATURES.md` — concise capability list (works / partly / not yet /
  out-of-scope), linked from the README.

### Added — M5.1 CArchive read side (CusProps reader) (2026-06-08)
- `src/swformat/carchive/archive.py` — MFC CArchive object-framing
  primitives: `read_object_tag`/`write_object_tag` (NULL / NEW_CLASS /
  CLASS_REF / OBJECT_REF via the `0x8000` high-bit + `0xFFFF` rules),
  `read_class_def`/`write_class_def` (`<schema:u16><namelen:u16><ascii>`),
  and `read_count`/`write_count` (the `0xFFFF→u32→u64` escape). Reversible,
  unit-tested on synthetic data AND the real CusProps header (which begins
  NEW_CLASS + the `moCusPropMgr_c` def). The generic framing floor for the
  M5.1 writer; per-class body models layer on next.
- `src/swformat/carchive/cstring.py` — MFC CArchive CString primitive
  (`read_cstring`/`encode_cstring`; `FF FE FF <len> <utf16le>`).
- `src/swformat/carchive/cusprops.py` — `walk_user_records()` segments the
  property list into exact per-record byte spans (modeling all four
  `moAdvCusProp_c` body variants: empty / PRP / StringElem / MassProp) with a
  correctly-tracked CArchive object-map counter; verified by a landing test
  (the walk ends precisely on the suObList terminator before the cut-list
  container) and name/value match vs the reader. The segmentation the M5.1
  writer needs.
- `src/swformat/carchive/cusprops.py` — `parse_container_header()` walks the
  `moCusPropMgr_c → moCusPropContainer_c → moFilePropContainer_c → suObList →
  moAdvCusPropList_c` nesting *via the CArchive primitives* (not string
  search), returning the property count, first-record offset, and object-map
  class indices; handles the empty-store sentinel. `ARCHITECTURE.md` Layer 3
  updated to reflect the shipped read side.
- `src/swformat/carchive/cusprops.py` — `read_cusprops()` extracts user
  custom properties (name→value) from the binary `Contents/CusProps` store,
  and `read_cutlist_props()` extracts cut-list / weldment properties
  (resolved values, e.g. `MATERIAL`, `QUANTITY`) — useful for steel-fab /
  sheet-metal docs. Both schema-tolerant (key on "name CString followed by
  `00 00 00 00`"), avoiding full object-tag walking.
- `test/harness/test_carchive.py` — verifies binary CusProps names equal
  the `docProps/custom.xml` user-property names across the corpus, and the
  cut-list reader (suite now 35). This is the read half of M5.1; the write
  side (object-map re-indexing) remains — see the refined spec below.
- `swf-prop cutlist FILE` — CLI to list cut-list / weldment properties
  (read-only) from the binary store, no SOLIDWORKS needed.

### Research — Contents/CusProps CArchive format decoded (2026-06-08)
- Decoded the binary custom-property store (`Contents/CusProps`, MFC
  CArchive) via SW diff-pairs (add 1 / add 2 properties): container class
  hierarchy, CString encoding (`FF FE FF <len> <utf16le>`), the object
  counter at offset 20, per-property record layout
  (`<obj_index><field2><name><value><resolved>`), the `moAdvCusPropList_c`
  list count, and the trailing object-map count. Documented in
  `research/empirical_findings/cusprops_carchive/log.md`.
- Confirms M5.1 (CArchive object-map codec) is the prerequisite to *add a
  brand-new property name*, edit *config-scoped* props, and reach M3
  (CMgrHdr2 is the same CArchive family). A safe encoder needs the
  object-map model (consistent indices + map count), so no hand-patched
  writer is shipped (it would emit subtly-corrupt files).

### Added — M2 custom-property editing (SET/DELETE) + CLI (2026-06-08)
- `src/swformat/streams/custom_props.py` — surgical, TypeID-agnostic editor
  for `docProps/custom.xml` (handles both the older bare-`<property>` schema
  and the newer `TypeID="30"`/`<FPVals>` schema): `list/get/set/delete`.
- `src/swformat/api/properties.py` — `read_properties` / `edit_properties`
  (edit → `write_with_toc`); `src/swformat/tools/sw_prop.py` — `swf-prop`
  CLI (`list`/`get`/`set`/`delete`, `--out`/`--in-place`).
- `test/harness/test_properties.py` — unit + corpus round-trip tests
  (suite now 27 green).
- **SW-verified on parts, assemblies, AND drawings**: editing an existing
  property value and deleting an existing property both round-trip (SW
  reopens and reflects the change).
- Mapped the M2 boundary: config-scoped props
  (`docProps/Config-N-Properties.xml`) share the XML schema and our editor
  handles them, but — like adding a brand-new global name — they don't
  surface in SW until registered in the binary property store (same
  intersection rule); that CArchive work is the M2 follow-up.
- **Finding**: SOLIDWORKS surfaces only the *intersection* of property
  names in `docProps/custom.xml` AND the binary `Contents/CusProps`, with
  values from the XML. So edit/delete of EXISTING props needs only the XML,
  but ADDING a brand-new property name also requires registering it in
  `CusProps` (the file opens but SW hides the new name until then; the CLI
  warns). M2 follow-up.

### Added — docs (2026-06-08)
- `docs/FORMAT_GUIDE.md` — a plain-English, beginner-friendly guide to the
  SOLIDWORKS modern file format (container/chunks/streams/ROL/DEFLATE, the
  TOC and its `offset-8` pointers, save non-determinism, and how we
  read/modify/write safely), with analogies, a worked example, and a
  glossary. Linked as "START HERE" from the README doc map.

### Added — M1.5 TOC decode + M2 core (size-changing edits) (2026-06-08)
- **Decoded the central TOC offset encoding**: each TOC record (and loose/
  self pointers) stores a target file offset as `uint32 == offset - 8`.
  Layout varies by section type (0xDF/0xA4/0xBC), so fixup is position-
  encoding-based and confined to gap regions (never compressed payloads).
- `src/swformat/chunks/toc.py` — `OFFSET_BIAS`, `build_offset_map`,
  `fixup_offset_pointers` (payload-safe), TOC record helpers.
- `src/swformat/io/writer.py` — `serialize_with_toc` / `write_with_toc`:
  re-deflate modified chunks, re-derive new offsets, remap every `-8`
  pointer, and patch modified chunks' TOC `csz`/`usz`. Enables
  **size-changing edits that reopen in SOLIDWORKS**.
- `test/harness/test_toc.py` — layout-agnostic regression guard ("no stale
  offset pointer remains after a size-changing edit"); suite now 22 green.
- **M2 core proven end-to-end (SW-verified)**: edited a custom property's
  value via `set_stream_payload` + `write_with_toc`; SW reopens cleanly and
  `CustomPropertyManager.GetAll3` returns the new value. Finding:
  `docProps/custom.xml` is authoritative — the binary `Contents/CusProps`
  can be left stale. Full evidence:
  `research/empirical_findings/m1_writer_roundtrip/`.

### Added — M1 round-trip writer + Layer-3 gate (2026-06-08)
- `src/swformat/io/writer.py` — `serialize()` / `write()` reassemble a
  Document; lazy verbatim path for unmodified chunks, re-deflate path
  (patches `csz`/`usz` header fields) for `modified_payload`. Mutation
  seams `set_stream_payload` / `force_redeflate_all`.
- `src/swformat/io/stable_mask.py` — re-scoped per M0.5 as a stream-NAME
  allow/deny list (`is_nondeterministic`), not a positional byte mask.
- `src/swformat/tools/echo_check.py` — `swf-echo-check`: read→write→diff
  at three layers (lazy + `--redeflate`).
- `test/harness/test_roundtrip.py` — Layer-1/2 round-trip suite (7 tests).
- `test/harness/layer3_reopen.py` — Layer-3 reopen-equivalence harness
  (combridge): re-deflate/lazy echo → reopen in SW → compare configs,
  custom props (raw, skipping filename-derived), mass, bodies, sheets.
- **M1 result:** lazy round-trip PASSES the Layer-3 gate for part,
  assembly, and drawing (echo reopens with equivalent semantics).
  **Re-deflation is REJECTED by SW** (`swFileRequiresRepairError`) — even
  a single-stream, −45 B change — because section-0x37 TOC records store
  each stream's `csz`/`usz`. Added Milestone 1.5 (decode the TOC) as the
  prerequisite for all modify-milestones (M2/M3/M4).
  See `research/empirical_findings/m1_writer_roundtrip/`.

### Added — M0 productionisation + M0.5 twin-save baseline (2026-06-08)
- Productionised the chunk layer from the imported snapshot (which is kept
  verbatim as the historical reference):
  - `src/swformat/types.py` — `Chunk` / `Gap` / `Document` with the
    **no-orphan-bytes** invariant, **lazy round-trip** (`modified_payload`),
    `reconstruct()`, and `locate(offset)`.
  - `src/swformat/chunks/walker.py` — `iter_records()` emits chunks AND
    gaps covering every byte; plus `detect_format`, `rol_decode`,
    `iter_chunks`, `doc_version`.
  - `src/swformat/io/reader.py` — `read_document()` + backward-compatible
    `read_file()`; package `__init__` re-exports the public API.
- `src/swformat/tools/diff_files.py` — three-layer structural diff CLI
  (`swf-diff-files`); reusable `byte_diff_clusters` / `locate_clusters`.
- `test/harness/test_chunk_walker.py` — Layer-1 suite (11 tests,
  incl. `test_no_orphan_bytes`, reconstruct round-trip, stream parity).
- `test/harness/twin_save_baseline.py` — M0.5 orchestrator/analyzer
  (acquire / twinsave / analyze) driving SW via combridge; Pack-and-Go
  isolation for assemblies/drawings.
- M0.5 result: 9 files saved twice 10 s apart — **0/9 byte-length-stable**,
  **9/9 `reconstruct()==original`**. M1 Layer-2 byte-equal **dropped as
  primary gate; Layer 3 (SW-equivalence on reopen) confirmed as THE gate**.
  Full data: `research/empirical_findings/twin_save_baseline/`.

### Added
- Project scaffold:
  - `README.md` with five-layer architecture overview, value proposition,
    legal posture summary, project structure, quick start, and non-goals.
  - `docs/ARCHITECTURE.md` — full data-flow diagrams, per-layer contracts,
    `tail_bytes` invariant explanation, non-determinism boundaries.
  - `docs/ROADMAP.md` — M0 through M6 milestones with effort estimates,
    feasibility ratings (HIGH / MEDIUM / LOW / SPECULATIVE), and
    adversarial-review-adjusted ranges. M7 generative authoring removed
    from committed roadmap per workflow adversarial review.
  - `docs/REVERSE_ENGINEERING.md` — diff-pair RE methodology, hypothesis
    logs, falsification protocol, tail-bytes discipline, lesson template.
  - `docs/LEGAL.md` — interoperability RE posture under 17 U.S.C. § 1201(f)
    (US) and EU Directive 2009/24/EC Article 6. Contact mechanism for
    Dassault correspondence.
  - `TESTING.md` — three-layer test pyramid; M1 pass criterion reframed
    to Layer 3 (SW-equivalence on reopen) as primary gate, byte-equality
    as diagnostic only.
  - `pyproject.toml` — Hatchling build, Python 3.11+, zero runtime deps.
    Dev extras for pytest, mypy, ruff, hypothesis. Console scripts for
    every M-stage CLI (working CLIs for M0; stubs that print pointers
    to ROADMAP.md for M1+).
  - `LICENSE` — Apache-2.0 (compatible with porting MIT-licensed
    `openswx` algorithms).
  - `.gitignore` — enforces corpus IP rules (all `*.sld*` blocked by
    default; only `synthetic` corpus files can be whitelisted).
- Imported snapshot of a prior internal Python port at
  `src/swformat/_imported_swx_reader_v0.py` as the M0 chunk-walker
  starting point.
- `test/corpus/corpus.config.json` — registry of test files (local real
  files referenced by absolute path; no IP committed).
- `test/harness/proof_of_life.py` — M0 smoke test verifying the imported
  parser works against real SW files.

### Workflow research findings (informing the design)
- `schwitters/openswx` confirmed as MIT-licensed primary prior art; only
  2 commits ever (2026-04-13); 1 unanswered issue; effectively
  unmaintained.
- `blussyya/sldprt-converter` discovered (last commit 2026-06-05): first
  known effort to decode `Contents/DisplayLists` for geometry extraction
  via NURBS control-point triangulation. Approximate output (not exact
  B-rep); valuable reference for M5 geometry work.
- `xarial/xcad` confirmed as actively-maintained C# wrapper around
  DocMgr; useful reference, not a runtime dependency.
- SOLIDWORKS Document Manager API can read most metadata but has narrow
  write surface and requires a paid license — SWFormat's value
  proposition is "no SW install + open source + wider write surface."

### Validated against real files
- DELETE.SLDPRT: 39 chunks parse cleanly, decompress to 252,873 bytes
- flat washer: 40 chunks; doc_version=15000 (SW 2023 SP3 vintage)
- DELETE.SLDASM: 38 chunks, decompresses to 1,654,344 bytes
  (`Contents/DisplayLists` is 1 MB of that — the graphics cache identified
   as the primary non-determinism source for M1)

## [0.0.1] — 2026-06-07

Initial scaffold. Not yet released.
