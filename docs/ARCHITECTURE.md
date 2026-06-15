# SWFormat Architecture

> The 5-layer model that organizes everything. Each layer has a single
> responsibility, clean seams above and below, and an explicit "tail
> bytes" invariant that propagates correctness through the stack.

---

## 1. The 5-layer model

```
                         consumers (CLIs, ScripTree apps, etc.)
                                          │
                                          ▼
                ┌──────────────────────────────────────────────┐
                │  Layer 4 — API                               │
                │  swformat.api.{properties, configurations,    │
                │    drawing, components, features}             │
                │  High-level Document object model.            │
                └──────────────────────────────────────────────┘
                                          ▲
                                          │ semantic operations
                                          ▼
                ┌──────────────────────────────────────────────┐
                │  Layer 3 — CArchive                          │
                │  swformat.carchive.{primitives, schema,       │
                │    classmap}                                  │
                │  MFC binary serialization decode/encode.     │
                │  Hardest layer; M5+ work.                     │
                └──────────────────────────────────────────────┘
                                          ▲
                                          │ structured object reads
                                          ▼
                ┌──────────────────────────────────────────────┐
                │  Layer 2 — Streams                           │
                │  swformat.streams.{base, catalog,             │
                │    custom_props, sheet_previews, ...}         │
                │  Per-stream-name handlers. XML, preview PNG, │
                │  ROL-decoded names, etc.                      │
                │  OpaqueStream pass-through for unknown names.│
                └──────────────────────────────────────────────┘
                                          ▲
                                          │ decompressed stream bytes
                                          ▼
                ┌──────────────────────────────────────────────┐
                │  Layer 1 — Chunks                            │
                │  swformat.chunks.{walker, rol}                │
                │  The `14 00 06 00 08 00` chunk walker.       │
                │  ROL codec for stream names. Raw DEFLATE.    │
                │  Format detection. Gap tracking.              │
                └──────────────────────────────────────────────┘
                                          ▲
                                          │ raw bytes
                                          ▼
                ┌──────────────────────────────────────────────┐
                │  Layer 0 — Bytes                             │
                │  swformat.io.{reader, writer}                │
                │  File I/O. mmap for large files.              │
                │  Format-agnostic.                             │
                └──────────────────────────────────────────────┘
```

## 2. Why this layering

**Each layer can be developed and tested in isolation.** Layer 1 has no
dependency on Layers 2-4. Layer 2 sees parsed chunks from Layer 1 but
knows nothing about CArchive. Layer 3 operates on stream bytes without
caring how they were chunked. Layer 4 is the consumer-facing surface
and never reaches below Layer 2-or-3 (depending on whether the stream is
CArchive-encoded or XML).

**Each layer has a clear "I don't know about this yet" mode.**

- Layer 1: unknown section types → emit a `Chunk` with the section type
  preserved. Unknown inter-chunk bytes → emit a `Gap`. **Every byte
  is accounted for.**
- Layer 2: unknown stream names → `OpaqueStream` handler that holds
  the decompressed bytes and re-serializes them unchanged.
- Layer 3: unknown CArchive classes → `OpaqueObject` (M5+) holds the
  raw bytes between known class tags.
- Layer 4: unknown high-level concepts simply aren't surfaced in the
  API yet.

**The "tail bytes" invariant is the load-bearing correctness property.**
Every layer reads bytes and either parses them or passes them through.
**No byte is silently dropped.** A round-trip read→write therefore
reconstructs the original file modulo whatever bytes the consumer
explicitly modified. This is what makes M1 (round-trip echo) tractable
even before any layer is fully decoded.

## 3. Layer-by-layer detail

### Layer 0 — Bytes

**Responsibility:** Get the raw bytes from disk. Get them back to disk.

**Surface:**
- `read_file(path: Path) -> Document` — Layer 0+1+2 entry point
- `Document.write(out_path: Path) -> None` — M1+ entry point

**Implementation:**
- M0: `Path.read_bytes()` — fine up to ~100 MB files
- M1+: mmap-backed reading for >100 MB assembly files (deferred until
  the benchmark shows it's needed)

**No knowledge of:** chunk format, streams, anything above.

### Layer 1 — Chunks

**Responsibility:** Walk the modern-format chunk structure. Detect
format. Decode ROL-encoded stream names. Decompress chunk payloads via
raw DEFLATE. Track inter-chunk gaps as raw byte runs.

**Surface** (`swformat.chunks.walker`; re-exported from `swformat`):
- `detect_format(data: bytes) -> Literal['modern', 'ole2', 'opc', 'unknown']`
- `iter_records(data: bytes) -> Iterator[Chunk | Gap]` — every byte, in
  file order (the no-orphan-bytes walk)
- `iter_chunks(data: bytes) -> Iterator[Chunk]` — chunks only (convenience)
- `doc_version(streams: dict[str, bytes]) -> int | None` — largest
  `_MO_VERSION_NNNNN`
- `rol_decode(name_bytes: bytes, key: int) -> str` — the ROL codec

**Data shapes** (as actually defined in `swformat.types`):

```python
@dataclass(slots=True)
class Chunk:
    offset:              int     # file offset of chunk start (si = marker - 4)
    section_type:        int     # byte at +0x0a (data chunks: 0x37)
    f1: int; csz: int; usz: int; nsz: int
    name:                str     # ROL-decoded UTF-8 stream name
    header_bytes:        bytes   # verbatim [offset, data_offset) (fixed hdr + name)
    original_compressed: bytes   # verbatim raw-DEFLATE payload (lazy round-trip)
    modified_payload:    bytes | None = None
    # None  → writer emits header_bytes + original_compressed verbatim.
    # bytes → writer re-deflates this and rebuilds csz/usz in the header
    #         (and write_with_toc fixes the central-TOC pointers — see §3.5).
    # Methods: is_inline, data_offset, end, __len__, raw_bytes(), decompressed()

@dataclass(slots=True)
class Gap:
    offset:    int
    raw_bytes: bytes   # non-chunk bytes (header, padding, the TOC); verbatim on write
```

`Document` (also in `types`) holds `fmt`, `data`, ordered `items: list[Chunk|Gap]`,
and helpers `chunks`, `gaps`, `streams()`, `locate(offset)`, `reconstruct()`.

**Format detection priority:**
1. OLE2 magic `D0 CF 11 E0 A1 B1 1A E1` → pre-2015 format (deferred)
2. ZIP magic `50 4B 03 04` → 3DEXPERIENCE OPC (deferred)
3. Scan first 4 KiB for chunk marker `14 00 06 00 08 00` → modern
4. Otherwise → unknown

**Chunk header layout** (offsets from chunk start `si = marker_pos - 4`):

| Offset | Field | Notes |
|---|---|---|
| `si+0x00` | val_a (uint32 LE) | file-specific, not used for type detection |
| `si+0x04` | 0x14 | fixed separator byte |
| `si+0x05` | `00 06 00 08 00` | 5 core bytes of the 6-byte marker |
| `si+0x0a` | section_type (u8) | **empirically (SW 2026): data chunks = 0x37; central-TOC records = 0xDF/0xA4/0xBC.** (The older openswx-era note "0xDF=TOC, 0xFD=data, 0x1C=mini" did not match our corpus.) Distinguish a payload-bearing data chunk by `f1 ≥ 65536`, not by section type. |
| `si+0x0b` | 3-byte suffix | file-specific |
| `si+0x0e` | f1 (uint32 LE) | ≥ 65536 for inline chunks (with payload) |
| `si+0x12` | csz (uint32 LE) | compressed data size |
| `si+0x16` | usz (uint32 LE) | uncompressed data size |
| `si+0x1a` | nsz (uint32 LE) | stream name length in bytes |
| `si+0x1e` | name[nsz] | ROL-encoded; decode with key from `data[7]` |
| `si+0x1e+nsz` | data[csz] | raw DEFLATE — only for inline chunks |

**Sanity caps:** `nsz ≤ 512`, `csz ≤ 64 MiB`. (Marker bytes can occur
in random data; without caps, false positives blow up the parse.)

**Errors:**
- `FormatError` — corrupt, truncated, or unrecognized
- `ChunkError` — single chunk failed but parse continues (caller decides
  whether to surface)

### Layer 2 — Streams

**Responsibility:** Given a `Chunk`'s `stream_name` and `decompressed`
bytes, return a typed `StreamHandler` that knows how to parse and
re-serialize that stream.

**Surface:**
- `StreamHandler` protocol — `parse(data, doc_version)`, `serialize() → bytes`
- `OpaqueStream` — default handler; pass-through
- `STREAM_HANDLERS` dict — registry mapping name patterns (literal or
  regex) to handler classes
- `get_handler(stream_name) -> type[StreamHandler]`

> **Shipped reality (2026-06-08):** the first handler landed as a
> **functional module** (`swformat.streams.custom_props`: `list/get/set/
> delete_property`) rather than a `StreamHandler` class — simpler for a
> surgical XML editor, and it handles both the old bare-`<property>` and
> new `TypeID="30"`/`<FPVals>` schema variants. The `StreamHandler`
> protocol below remains the intended shape as more streams are added.

**Known handlers (by milestone):**

| Stream name | Handler | Milestone | Status |
|---|---|---|---|
| `docProps/custom.xml` | `streams.custom_props` (functional XML editor) | M2 | **SHIPPED** — list/get/set/delete; name-dict aware; handles bare + TypeID/FPVals variants; `&quot;`-safe attr escaping. Write via **span preservation** (`serialize_chunk(span_preserve=True)`): re-deflate + pad to original csz + update usz; raises `SpanPreserveError` if grow-beyond-span. SW-verified (part, all ops). |
| `docProps/Config-N-Properties.xml` | `streams.custom_props` (same editor, routed via `api.properties(config=N)`) | M2 | **SHIPPED (READ + EDIT/DELETE)** — XML-only; no binary CusProps counterpart needed. **SPAN-LIMITED:** config streams are small; ADD raises `SpanPreserveError` (needs M1.5 central-directory rewrite to unlock grow-beyond-span). SW-verified for READ. |
| `docProps/core.xml` + `docProps/app.xml` | `api.docprops` (`read_doc_metadata`) + `swf-docinfo` | M2 | **SHIPPED (READ)** — document metadata: title/subject/creator/revision/lastModifiedBy + created/modified (core.xml, Dublin-Core), and authoring `Application`+`AppVersion` (the SW build that saved it), `Company`, `Template`, `TotalTime` (cumulative edit minutes), `DocSecurity` (app.xml). Plain element-text XML; all file types; read-only (last-saved). |
| `docProps/ISolidWorksInformation.xml` | `IsWInformationStream` | M2 + M4 | pending |
| `PreviewPNG`, `Config-N-PreviewPNG` | `PreviewStream` | M2 | pending |
| `Contents/CusProps` | `carchive.cusprops` — **SHIPPED read + user-write + cut-list value-edit** | M5.1 | **SHIPPED** — `read_cusprops`, `read_cutlist_props`, `check_user_list_coverage`, `reserialize_header`, `serialize_user_records`, `roundtrip`; write (`add_text_properties`) byte-exact vs SW (suite 91). **NEW 2026-06-09:** `walk_cutlist_records` + `set_cutlist_value` — cut-list property VALUE editing for user-text props (not formula-linked/system-defined); SW-verified (`SWFTEST=hello→EDITED`, e=0 w=0, SW 2026 / v19000). Authoritative field = **elem-value** (`moCusPropStringElem_c` CString in the value-element block). Synthetic fixture `weldment_min.SLDPRT` (generated by `gen_weldment_min.csx`; `license_status: synthetic`; registered in `corpus.config.json`). Suite 91→113. Tail (close-time object-map dump) verbatim (tail-bytes invariant). OPEN: API/CLI surface; `read_cutlist_props` heuristic replacement; multi-config weldment editing; precise object-map re-index for inserts. |
| `Contents/CMgrHdr2` / `Contents/CMgrHdr` | `carchive.cmgrhdr2` (`read_configuration_count`, `read_configuration_names`, `rename_configuration`) + `api.configurations` + `tools.sw_configs` | M3 | **SHIPPED (read: list + count; write: rename)** — Read: `config_count` u16 at byte 25; per-config `dmConfigHeader_c` records keyed on NEW_CLASS/CLASS_REF-3 tag; `$PRP:`-filter + sheet-metal display-string filter; exact names 87/87 corpus; SW-verified vs `GetConfigurationNames()`. Write/rename: `rename_configuration(data, old, new)` rewrites both NAME CString and DISPLAY CString in the fixed record prefix; verbatim tail (trailing stamp not validated by SW). Authoritative stream — all ~7 other name-carriers tolerated stale. Config-specific props survive rename (keyed by integer index, not name, in `docProps/Config-N-Properties.xml`). Span-preserve for same-len (all files); rename-to-LONGER (grow) via relocate-to-EOF on PARTS (SW-verified, 2026-06-11), raises `SpanPreserveError` on assemblies. SW-verified: part (3 conditions) + assembly — all e=0 w=0. Suite 91. **SCOPED (2026-06-09):** set-active is NOT a CMgrHdr2 edit — `field1` (u32 before NAME CString) is a non-authoritative active mirror; six graft tests confirmed editing CMgrHdr2 alone does not change the active config SW opens. Set-active requires coordinated multi-stream edit centred on the binary `Contents/CMgr` + general CArchive object-map codec. **Deferred to M5.x.** See `lessons/lesson_20260609_cmgrhdr2_set_active_negative.md`. Note: byte-grafting `Contents/CMgr` crashes SW (RPC 0x800706BE) ONLY against MISMATCHED siblings; a structurally-compatible same-assembly graft is SAFE (2026-06-13 — see the `Contents/CMgr` row below). Derived-config `disc`/`flag` field semantics also pending SW diff-pairs. |
| `Contents/CMgr` | (no handler — `set_stream_payload` graft only) | M5.x (research) | **OBSERVED (diff-pair, no handler shipped, 2026-06-13)** — binary CArchive config-manager. **AUTHORITATIVE for the active config AND a flexible subassembly instance's per-instance referenced configuration** (`CMgrHdr2`, `Contents/Config-0-LWDATA`, `swXmlContents/COMPINSTANCETREE` are MIRRORS SW regenerates). Grafting `Contents/CMgr` from a structurally-compatible donor (same assembly, different config) via `set_stream_payload`+`write_with_toc` (span-preserve) flips a flexible instance's config at the byte level — reopens e=0, durable — which the COM API (`ModifyDefinition`) refuses in-context. A CMgr graft crashes (RPC 0x800706BE) only against mismatched siblings (dangling object-map refs). FROM-SCRATCH synthesis (authoring CMgr's object graph) still needs the general CArchive object-map codec (M5.1, parked). |
| `SheetPreviews/SheetNames` | `SheetNamesStream` | M4 | **read SHIPPED** — sheet names + count (`api.sheets`); preview MIRROR only (authoritative copy is in `Contents/Definition`). |
| `swXmlContents/COMPINSTANCETREE` | `api.components` (`read_component_tree`) + `swf-comptree` | M4 | **SHIPPED (READ)** — assembly component tree: per `<swReference>` instance → name, resolved path (`swModelRef`→`swModel`→`swFile.swPath` join), referenced config, and **exclude-from-BOM / flexible / hidden / suppressed / virtual** flags + `swTransform`. One read replaces a per-component COM loop (`GetExcludeFromBOM2` + `GetParent`/`GetPathName`). **READ-only MIRROR** — `swExcludeFromBOM` verified to flip NO→YES on exclude (twin diff-pair, SW 2026); editing it alone is NOT SW-clean (authoritative store is `Contents/CMgr`; a COMPINSTANCETREE-only edit hung the load). `swObjCount` grows when an instance references a different config. |
| `swXmlContents/MATERIALTREE` | `api.materials` (`read_materials`) + `swf-material` | — | **SHIPPED (READ)** — applied material(s) + physical properties (`DENS`/`EX`/`NUXY`/`GXY`/`ALPX`/`SIGYLD`/`SIGXT`/`KX`/`C`…) per classification. UTF-16 XML (`mstns:materials`). Material name+matid+engineering props with no SW / no license / no material DB. Read-only. |
| `swXmlContents/Features` | `api.components` (part config tree — observed) | — | **OBSERVED (READ)** — a PART's config/model tree (the part analog of COMPINSTANCETREE): config names, model/file refs, creation/modified stamps. Plain XML, readable now; a thin typed reader is a trivial follow-up. |
| `docProps/Config-N-Cutlist-Properties.xml` | (readable XML — thin reader pending) | — | **OBSERVED (READ)** — weldment cut-list properties per config in XML: `CustomProperty`, **`ExcludeFromCutlist`**, `Quantity`, `CutlistType`. A readable mirror of the binary cut-list store (which already has SHIPPED value-editing via `carchive.cusprops`). |
| `Contents/Definition` | `DefinitionStream` | M5 (long-tail) / M5.1.5 | **read-side decode in progress** — full section map of a drawing's object graph (moDrawing_c → … → `moDrSheet` @4033); `moRelMgr_c` (~17.5 KB) decoded as the detailing-**standards** manager (units/priority/dimension-standard/font/line-font tables — template-invariant, carry-verbatim under index renumbering), NOT a geometry blob. **Authoritative for drawing sheet names** (sheet rename SHIPPED, SW-verified). Flat object-map ledger is structurally capped here (parent-resumes-after-children); the **recursive schema engine** (`research/.../definition_decode/recursive.py`) is the required walk and the single primitive gating instance census, index-contiguity, and sheet reorder. See `docs/CARCHIVE.md` §5A + `research/empirical_findings/definition_decode/log.md`. |
| `Contents/Config-0`, `Contents/Config-0-LWDATA`, `Contents/Config-0-ModelHeader` | `OpaqueStream` (read pass-through) | M5.x (research) | **OBSERVED (diff-pair, 2026-06-13)** — per-configuration component data for the active (Config-0) config. `Config-0-LWDATA` carries per-component lightweight/instance data INCLUDING a flexible instance's per-instance solved pose for an UNDER-DEFINED DOF (verified config-free: two flexible instances held +25°/+75° with NO config created). For a DRIVEN mate the side is config-bound (lives in `Contents/CMgr`); these streams MIRROR that selection. No handler shipped. |
| `Header2` / `Header3` | `HeaderStream` | M5 | pending |
| `Contents/DisplayLists` | `OpaqueStream` (strip-and-let-SW-rebuild on write) | M1 design decision | OpaqueStream permanent |
| `ThirdPtyStore/*` | `OpaqueStream` (never decode; pass through) | always | OpaqueStream permanent |
| `_MO_VERSION_NNNNN/*` | `OpaqueStream` (version is in the name, not payload) | always | OpaqueStream permanent |

**Handlers MUST be deterministic on serialize.** A handler that parses
bytes `B` and re-serializes (without modification) MUST emit `B` — or
must explicitly mark its stream as "non-deterministic, falls under the
stable mask." This is the gate that makes M1 Layer-2 byte-equality
checks meaningful.

### Layer 3 — CArchive (M5.1, read side in progress)

**Responsibility:** Decode MFC `CArchive` binary streams into structured
Python objects. Re-encode structured objects back to CArchive bytes.

**Shipped so far (2026-06-08; package `swformat.carchive`):**
- `archive.py` — generic object-framing primitives, reversible + unit-tested:
  - `read_object_tag`/`write_object_tag` — the 16-bit tag grammar:
    `0x0000`=NULL, `0xFFFF`=NEW_CLASS, `0x8000|idx`=class-ref, else object-ref.
  - `read_class_def`/`write_class_def` — `<schema:u16><namelen:u16><ascii>`.
  - `read_count`/`write_count` — MFC `ReadCount` (`0xFFFF→u32→u64` escape).
- `cstring.py` — `read_cstring`/`encode_cstring` (`FF FE FF <len> <utf16le>`).
- `cusprops.py` — `read_cusprops` (user props), `read_cutlist_props`
  (weldment/cut-list props), `parse_container_header` (rigorously walks the
  `moCusPropMgr_c → … → moAdvCusPropList_c` nesting via the primitives →
  property count + first-record offset + object-map class indices), and
  `walk_user_records` (segments the property list into exact per-record byte
  spans with a correctly-tracked object-map counter, modeling the four
  `moAdvCusProp_c` body variants — empty / PRP / StringElem / MassProp).
  Handles the empty-store variant (`moCusPropMgr_c` + `0xFFFFFFFF` sentinel).
  Verified against `docProps/custom.xml` and by a landing/round-trip test
  (the walk ends exactly on the suObList terminator before the cut-list
  container).

**Surface (full M5.1, planned):**
- `read_object(data: bytes, schema_class: type) -> CObjectInstance`
- `write_object(obj: CObjectInstance) -> bytes`
- `register_class(name: str, parser: Callable, serializer: Callable)`

**WRITE side — recipe fully derived (2026-06-08), implementation next.**
Verified byte-exact against SW ground truth (`c_B0` 10-prop → `c_B2`
12-prop): to add N text properties — insert the new records at the
list-terminator (`walk_user_records` end offset), bump the `moCusPropMgr_c`
property counter (offset 20) and the `moAdvCusPropList_c` list count by N,
and re-index the trailing cut-list class-refs by `K = 2·N` (each text prop
adds a `moAdvCusProp_c` wrapper + a value-element object to the map). Also
add the name to `docProps/custom.xml` and emit via `write_with_toc`. Full
recipe + byte evidence in `research/empirical_findings/cusprops_carchive/log.md`.
**SHIPPED 2026-06-08:** `add_text_properties` writes text props into
`Contents/CusProps` **byte-exact vs SOLIDWORKS** (test reproduces SW's
`c_B2`). GATE RESOLVED: the reason brand-new property names didn't surface
was NOT the binary store — SW reads the property list from the
`propertyNameDictionaryElement` **name dictionary** in `docProps/custom.xml`
(Layer 2). With that dictionary entry written, **add-new-name is SW-verified
end-to-end** (`swf-prop set <newname>` → SW shows it). The CusProps writer is
kept for store consistency/fidelity and as the M3/CMgrHdr2 foundation.
Details in the cusprops log.

**De-risked 2026-06-08:** the writer is PROVEN byte-exact — both the framing
(insert + count bumps + cut-list ref re-index) AND a from-scratch record
generator independently reproduce SW's `c_B2` to the byte. Record params
(class-map indices): wrap=moAdvCusProp_c, elem=moCusPropStringElem_c,
field2=moAdvCusPropList_c; `idx`=property counter; re-index K=2·N (object
counter). Remaining is engineering: expose the object-counting class-map from
the walk, implement `add_text_property` with these derivation rules, verify
byte-exact + SW-reopen, then wire into `api.properties`/`swf-prop` for
brand-new names and config props.

**Why this is the hardest layer:**
- Per-class `Serialize()` methods are SOLIDWORKS-proprietary
- Each class has multiple schema versions across SW releases
- Pointer cycles in the CObject map make naive recursion impossible
- Geometry payloads are Parasolid-backed (X_T binary inside the CArchive
  wrapping)

See `docs/CARCHIVE.md` for the format spec (M5 deliverable, scaffold
exists now).

### Layer 4 — API (M2+)

**Responsibility:** Present a stable, semantic surface to consumers
that hides the layering below.

> **Shipped reality (M2, 2026-06-08):** custom-property access landed as a
> small **functional** API, `swformat.api.properties.read_properties(path)`
> and `edit_properties(in, out, sets=…, deletes=…)` (the latter routes
> through `write_with_toc`), plus the `swf-prop` CLI
> (`swformat.tools.sw_prop`: list/get/set/delete/cutlist). **M2 COMPLETE:**
> SW-verified for get/list, editing EXISTING values, ADDING brand-new
> properties, and deleting — without SOLIDWORKS. The richer
> `Document.properties`/`set_property` object model below is still the
> intended end state. **Resolved gate:** SW reads the property list from the
> `propertyNameDictionaryElement` name dictionary in `docProps/custom.xml`;
> `set_property` now writes it for new names, and `edit_properties` also keeps
> the binary `Contents/CusProps` store consistent for fidelity.

**Surface (filled in incrementally per milestone):**

```python
# M0: skeleton only
class Document:
    path: Path
    format: Literal['modern', 'ole2', 'opc', 'unknown']
    doc_version: int | None
    chunks: list[Chunk]          # raw access for diagnostics
    gaps: list[Gap]
    streams: StreamView          # dict-like, lazy

    def write(self, out_path: Path) -> None: ...  # M1+

# M2:
    @property
    def properties(self) -> dict[str, Any]: ...    # custom props
    def set_property(self, name: str, value) -> None: ...
    def delete_property(self, name: str) -> None: ...

# M3:
    @property
    def configurations(self) -> list[Configuration]: ...

# M4 (drawings only):
    @property
    def sheets(self) -> list[Sheet]: ...
```

## 3.5 The central TOC and offset pointers (M1.5 — the key to writing)

> Decoded 2026-06-08. This is *the* thing that makes size-changing edits
> hard, and the mechanism that `write_with_toc` reproduces. Full evidence:
> `research/empirical_findings/m1_writer_roundtrip/`; plain-English version
> in `docs/FORMAT_GUIDE.md` §7.

A modern SW file is an **OPC-like container** (it carries
`[Content_Types].xml` and `_rels/.rels`). Near the **end** of the file is a
**central TOC / directory**: a run of marker records (section types
`0xDF`/`0xA4`/`0xBC`), one per stream, that records each stream's `csz`,
`usz`, **and absolute file offset**. SOLIDWORKS locates streams on open via
this TOC — **not** by marker-scanning — so the TOC must stay consistent.

**The offset encoding (verified exactly):** every position pointer — TOC
record offset fields, a directory self-pointer, and other loose pointers —
stores its target as a `uint32` with the rule

```
stored_value == target_file_offset - 8        # OFFSET_BIAS = 8
```

These pointers live **only in gap/directory regions, never inside a chunk's
compressed payload** — so rewriting them is safe as long as you confine
edits to gaps. (Earlier whole-file scans missed the encoding because the
low bytes are zero; reading the uint32 at the correct position yields
`offset-8` cleanly.)

**Why a size change breaks reopen, and how we fix it.** Re-deflating a
stream changes its `csz`, which shifts every later chunk's offset. The TOC
pointers then point to stale positions → SW reads garbage → it rejects the
file with `swFileLoadError_e.swFileRequiresRepairError` (0x200000). What was
ruled out by experiment: it is **not** a content checksum (a same-`csz`
re-encode reopens), **not** a per-stream `csz`-value check (patching `csz`
while preserving offsets reopens), and **not** a total-file-size check
(appending at EOF reopens). The gate is **offset preservation**.

The fix (`swformat.chunks.toc` + `io.writer.serialize_with_toc`):

1. Serialize (re-deflate modified chunks; later offsets shift).
2. Re-parse the output to learn the new offsets.
3. Build an old→new offset map and **rewrite every `-8` pointer in gap
   regions** to the new target (payloads are never touched).
4. Patch each modified chunk's TOC record `csz`/`usz`.

Result reopens in SOLIDWORKS with the edit intact (SW-verified, parts and
assemblies). TOC record field layout (the `0xDF` variant; layout varies by
section type, so prefer the position-encoding approach over field parsing):

```
+0x12 csz   +0x16 usz   +0x1a nsz   +0x1e u32=0   +0x22 u32=0
+0x26 u16=0   +0x28 (offset-8)   +0x2c name[nsz]   (ROL-encoded)
```

**Lazy round-trip stays free of all this:** if no chunk is modified,
nothing shifts, so no TOC fixup is needed and the output is byte-identical
to the input. `serialize_with_toc` detects "no modifications" and skips the
fixup. `serialize_with_toc` currently assumes the chunk *set* is stable
(no whole streams added/removed) — editing content *inside* an existing
stream (the M2 property case) is fully supported.

## 4. Data flow

### Read

```
  Path
   │
   ▼  swformat.io.reader.read_file
  Layer 0: data = Path.read_bytes()
   │
   ▼
  Layer 1: detect_format(data) → 'modern'
           records = list(iter_chunks(data))
           chunks = [r for r in records if Chunk]
           gaps   = [r for r in records if Gap]
   │
   ▼
  Layer 2: streams = StreamView.from_chunks(chunks)
           # lazy: each stream is parsed by its handler only on access
   │
   ▼
  Layer 4: Document(path, fmt, doc_version, chunks, gaps, streams)
```

### Write (M1+)

```
  Document
   │
   ▼  Document.write(out_path)
  Layer 4: collect modifications from .properties, .configurations, etc.
           → mutate the relevant Chunk.modified_payload
   │
   ▼
  Layer 2: for each modified stream, handler.serialize() →
            chunk.modified_payload = bytes
   │
   ▼
  Layer 1: for each chunk:
            if modified_payload is None:
                emit header_bytes + original_compressed verbatim
            else:
                recompress modified_payload (raw DEFLATE)
                rebuild chunk header (new csz, usz)
                emit new_header + new_compressed
            interleave with Gap.raw_bytes in original offset order
   │
   ▼  (size-changing edits only) swformat.chunks.toc
  TOC fixup: re-parse output → old→new offset map →
             rewrite every `offset-8` pointer in gap regions →
             patch modified chunks' TOC record csz/usz
   │
   ▼
  Layer 0: out_path.write_bytes(...)
```

Two writer entry points (`swformat.io.writer`):

- `serialize` / `write` — the **lazy round-trip**: any chunk whose payload
  was not modified is written verbatim, avoiding DEFLATE non-determinism.
  Safe for unmodified or same-span edits; does **no** TOC fixup.
- `serialize_with_toc` / `write_with_toc` — for **size-changing edits**:
  does the lazy serialize, then the TOC offset-pointer fixup above (see
  §3.5). This is what M2 property edits use. For an unmodified document it
  is identical to `serialize` (it detects "nothing modified" and skips).

## 5. Non-determinism boundaries

The M0.5 twin-save experiment (✓ done 2026-06-08;
`research/empirical_findings/twin_save_baseline/`) measured this directly:
**0 of 9 files were byte- or length-stable** across two unmodified saves
10 s apart, while **all 9 satisfied `reconstruct() == original`** (our
writer is faithful; the instability is purely SW-side). That result set the
M1 gate to **Layer 3 (semantic equivalence on reopen)**, not byte equality.
The on-disk format has at least these non-deterministic regions:

| Region | Why | Strategy |
|---|---|---|
| `Contents/DisplayLists` | Graphics cache regenerated on save | Strip on write; let SW rebuild on next open. Affects Layer 2 byte-equal; Layer 3 equivalence unaffected. |
| `_MO_VERSION_NNNNN/Biography` | Save history with timestamps | Pass through verbatim on lazy round-trip; never modify unless explicitly requested. |
| CObject map ordering inside CArchive streams | MFC heap-pointer-influenced | M5 problem; out of scope for M1-M4. |
| Inter-chunk pad bytes | Possibly random | Captured by Gap records; written verbatim. |

The stable mask (`swformat.io.stable_mask`, M1) is **a stream-NAME
allow/deny list**, not a positional byte mask — M0.5 proved a fixed-offset
mask is inapplicable because file length itself changes between saves.
`is_nondeterministic(stream_name)` returns True for the ~16 known
save-noise streams (timestamps, `Biography`, `DisplayLists`, `3DExperience*`,
`GhostPartition`, …); a Layer-2 diagnostic flags any OTHER differing stream
as a real regression.

## 6. Threading and concurrency

**Not threaded.** Bulk-edit workflows are I/O-bound, not CPU-bound;
threading adds complexity without performance benefit for the file
sizes SWFormat targets. A bulk edit of 1000 files runs N processes
in parallel via `multiprocessing.Pool` if the consumer wants
parallelism — the library itself stays single-threaded.

## 7. Error handling

Errors propagate via exceptions, not error codes:

- `swformat.FormatError` — file is not in any recognized format, or
  is corrupt enough that no recovery is possible
- `swformat.ChunkError` — a single chunk failed to parse; the rest of
  the file may be salvageable
- `swformat.StreamError` — a stream handler failed; the chunk is
  available via `chunk.decompressed` for manual inspection
- `swformat.CArchiveError` (M5+) — a CArchive object failed to decode

The `read_file` entry point catches `ChunkError` and `StreamError` by
default and logs warnings; pass `strict=True` to make them terminal.

## 8. Memory profile

| File size | Read profile |
|---|---|
| <10 MB part | `read_bytes()` whole file → ~3x size in memory (decompressed streams). Trivial. |
| 10-100 MB | Same; under 1 GB working set. |
| >100 MB assembly | M1+: mmap the file for chunk-walking, lazy-decompress streams on access. Target working-set: <2x file size. |

The lazy-decompress strategy (defer the DEFLATE decompression until a
stream is actually accessed via `streams[name]`) is M1; M0 decompresses
everything eagerly because the test corpus is small.

## 9. Versioning policy

- The `Document` class signature is stable from 0.1.0 onward.
- The chunk-walker `Chunk`/`Gap` dataclass fields are stable; new
  fields are additive only.
- The `StreamHandler` Protocol is stable; new handlers can be added at
  any minor version.
- The `carchive` Layer 3 API is unstable until M5 ships.
- Pre-1.0: any pre-1.0 release may introduce backward-incompatible
  changes to internal layers (Layers 1-3); the API (Layer 4) remains
  stable.
