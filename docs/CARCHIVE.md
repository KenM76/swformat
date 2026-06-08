# CArchive — MFC binary serialization in SOLIDWORKS streams (Layer 3 / M5.1)

> **Status:** read side shipped; write side de-risked (byte-exact recipe
> proven for one case) but the *general* object-map codec is not yet built.
> This document is the living specification for that codec — written so the
> implementation can be reconstructed from the docs alone (project rule:
> "the documentation is the logic; the code is just the syntax that enacts
> it"). It synthesizes the empirical decode in
> `research/empirical_findings/cusprops_carchive/log.md` (graduated to
> `lessons/lesson_20260608_cusprops_carchive_decode.md`).
>
> Verified against **SOLIDWORKS 2026** (`_MO_VERSION_19000`). Schema numbers
> and field widths may differ on 2020/2024 — flagged inline where known.

---

## 1. Where CArchive sits in the five layers

```
bytes (L0) → chunks (L1) → streams (L2) → CArchive (L3) → API (L4)
```

Several SOLIDWORKS streams are not XML or flat structs — they are **MFC
`CArchive` serializations** (the on-disk form produced by
`CObject::Serialize` + `CArchive::operator<<`). The ones SWFormat cares
about, in rough order of milestone:

| Stream | Milestone | Content |
|---|---|---|
| `Contents/CusProps` | M2 / M5.1 | binary mirror of custom + cut-list properties |
| `Contents/CMgrHdr2` (a.k.a. `Header2`) | M3 | configuration manager (config list, active config, derived flags) |
| `Contents/Definition` | M5 / M5.1.5 | feature tree (parts) / sheets+views+sketches (drawings); open-ended. Section map + `moRelMgr_c` standards-manager decode done; recursive walk to `moDrSheet` in progress — see §5A. |

All three are the **same CArchive family**, which is why the codec is built
once, here, and reused. M5.1 exists specifically to build this floor before
M3 can begin.

A CArchive stream is itself the *decompressed* payload of a Layer-1 chunk
(raw DEFLATE, `wbits=-15`). Editing one and writing it back is a
size-changing edit, so the outer file is reassembled with `write_with_toc`
(re-deflate + central-TOC offset-pointer fixup). CArchive is therefore the
*content* model; the chunk/TOC layer is the *container* model — keep them
separate.

---

## 2. The framing layer (SHIPPED — `carchive/archive.py`)

A CArchive stream is a sequence of objects, each introduced by a **16-bit
object tag**. This layer is generic (no per-class knowledge) and is fully
implemented + unit-tested.

### 2.1 Object tags (`read_object_tag` / `write_object_tag`)

| tag value | `TagKind` | meaning |
|---|---|---|
| `0x0000` | `NULL` | NULL object pointer; no body follows |
| `0xFFFF` | `NEW_CLASS` | a class def follows (schema+name), then the object body. The class is appended to the index map. |
| `tag & 0x8000` set | `CLASS_REF` | reference to an already-seen **class**, index = `tag & 0x7FFF`; a NEW object of that class follows (body) |
| `0 < tag < 0x8000` | `OBJECT_REF` | reference to an already-seen **object**, index = `tag`; **no body** |

The high bit (`0x8000`, `BIG_TAG`) distinguishes a class reference (new
object of a known class) from an object reference (a pointer to an existing
object). This is the single most error-prone distinction in the format.

### 2.2 Class definitions (`read_class_def` / `write_class_def`)

After a `NEW_CLASS` tag:

```
<schema:u16> <namelen:u16> <name: namelen ASCII bytes>
```

Verified opening of `Contents/CusProps`:
`FF FF | 01 00 | 0E 00 | "moCusPropMgr_c" | <body…>` — NEW_CLASS, schema 1,
the 14-char root class name, then the manager object's body. On SW 2026 all
the CusProps classes use **schema 1**; treat schema as per-class data, not a
constant, for cross-version safety.

### 2.3 Counts (`read_count` / `write_count`)

MFC's `ReadCount`/`WriteCount` escape, used for array/collection sizes:

```
u16 v;            if v != 0xFFFF      → count = v
else u32 v;       if v != 0xFFFFFFFF  → count = v
else u64 v;                           → count = v
```

### 2.4 Strings (`read_cstring` — `carchive/cstring.py`)

MFC Unicode `CString`: `FF FE FF <len:u8> <UTF-16LE × len>`. The `FF FE FF`
is MFC's "length-prefix + `0xFFFE` Unicode tag". Empty string = `FF FE FF 00`.
`<len>` is the UTF-16 **code-unit** count (the `CStringW` `wchar_t` count =
`len(utf16le_bytes) // 2`), **not** the Python `str`/code-point length — an
astral character is one code point but two code units, and conflating them
corrupts the round-trip (fixed in `encode_cstring`, 2026-06-08; see
`test_cstring_roundtrip_astral`). The `<len:u8>` form covers counts < 255; MFC
promotes to a wider `0xFF`-escaped field for ≥255 — re-verify if a long string
appears.

### 2.5 Class-table scan (`scan_class_defs` — SHIPPED 2026-06-10)

`scan_class_defs(data) → list[ClassDef]` forward-scans a stream for every
NEW_CLASS def (`FF FF <schema><namelen><name>`) and returns each
`ClassDef(offset, schema, name)` in file (= definition) order, **without** needing
any class's body layout. The framing is distinctive enough to locate by pattern
(plausible schema, `namelen` 3..64, ASCII identifier, `_c` suffix), so this
recovers the **class inventory + definition order** of a Definition/CusProps
stream cheaply. The default accepts ANY letter-led ASCII identifier — NOT just
the `_c` subset — because essential SW classes don't end in `_c` (`moDrSheet`
the sheet record, `sgSketch`, `suObList`, `su_CStringArray`, `uoSketch`). The
identifier + schema(`<=64`) + namelen(`3..64`) guards are empirically clean: 0
false positives on a 14.5 MB / 246-class real drawing (all distinct — NEW_CLASS
defs are unique). Pass `name_suffix=b"_c"` to get just the `_c` subset.

This is the **class-table half of the keystone**. It is a SCAN, not the
authoritative sequential walk (§3): it does NOT assign MFC object-map indices and
therefore cannot COUNT class instances (a class is defined once via NEW_CLASS,
then re-instanced via CLASS_REF tags whose positions only the walk reveals —
scanning the 2-byte `80|idx` pattern at arbitrary offsets over-matches, the same
trap as the dimension reader's `_DIM_VALUE_SIG`). Verified on the synthetic
`ndim7` drawing (57 classes incl. `moLengthParameter_c` @6859,
`moDisplayDistanceDim_c` @7485). See `research/empirical_findings/definition_decode/log.md`
(2026-06-10 22:58) for the descent context and the dimension-enumeration success
criterion (`read_sketch_dimensions(ndim7) == 7`).

---

## 3. The object-map model (THE M5.1 CORE — not yet built)

The framing layer reads/writes one tag at a time, but a correct **writer**
needs the whole-stream model that MFC maintains implicitly: the
**shared class + object index map**.

### 3.1 What the map is

As MFC serializes, it assigns sequential indices from a **single shared
counter** to:

- every **class** the first time it is defined (`NEW_CLASS`), and
- every **object** as it is written.

(Index 0 is reserved / the archive itself; first real entry is 1.) Later
`CLASS_REF`/`OBJECT_REF` tags carry these indices. Because the counter is
**shared** between classes and objects, inserting an object early in the
stream shifts the indices of **every class and object defined after it** —
and every reference to them must be re-numbered. This is why a naive
"append one record" splice corrupts the file.

Empirically (CusProps, 10→12 props): the cut-list container's class-refs
`0x8020`/`0x8022` became `0x8024`/`0x8026` (+4) when 2 text properties (= 4
new object-map entries: 1 wrapper + 1 value-element each) were inserted
before them. The user-prop object indices (e.g. cut-list 0x0a/0x0b) were
unchanged; it is the **later class definitions** whose indices move.

### 3.2 The close-time object-map dump

At `CArchive::Close`, MFC writes a trailing **object-map dump** (tags +
per-object refs + a final count) — NOT a single patchable counter. An early
falsified encoder tried `b[-1] += N` / `b[-5] += N`; the real trailer is a
structured dump and that patch corrupted it. The codec must regenerate this
dump from the model, not patch it.

> ⚠️ The trailing 56 bytes were observed **stable** across add-0/1/2-prop
> saves of one file, which means for *that* shape the dump didn't change —
> but do not generalize this to a "stable tail" rule. The two falsified
> diffs (missing `00 00` CObList element tag before an appended record; a
> different trailing map dump) prove the tail is model-derived. Model it.

### 3.3 CObList element framing

`suObList` (the user-property list) is an MFC `CObList`. Each element is
framed by an object tag; **appending an element is not a raw byte splice** —
the list's per-element tags (`00 00` separators / object tags) must be
emitted exactly. The falsified encoder's missing `00 00` before the appended
record is the canonical bug here.

### 3.4 Two distinct counters (do not conflate)

| counter | location | increment per added text prop |
|---|---|---|
| **Property counter** | `moCusPropMgr_c` body @ offset 20 (u32) | +1 |
| **List count** | `moAdvCusPropList_c`, u16 after the class name | +1 |
| **Object-map counter** | the shared index map (drives §3.1 re-indexing) | +2 (wrapper + value-element) |

The record's own `idx` field uses the **property** counter; the re-index `K`
uses the **object-map** counter. Both are confirmed by the bytes; mixing
them produces `swFileRequiresRepairError`.

---

## 4. Worked example — `Contents/CusProps` (the reference decode)

### 4.1 Container hierarchy (MFC class names, ASCII length-prefixed)

```
moCusPropMgr_c
└─ moCusPropContainer_c
   └─ moFilePropContainer_c
      └─ suObList
         └─ moAdvCusPropList_c          (the user-property list)
            └─ N × moAdvCusProp_c       (one per property; each wraps a value element)
                  ├─ moCusPropStringElem_c   (plain text)
                  ├─ moCusPropPRP_c          ($PRP:-linked / formula)
                  └─ moCusPropMassPropEle_c  (mass-derived)
      └─ moCutListPropContainer_c        (defined AFTER the user list — its
         └─ moAdvSysDefCusProp_c /         class-refs are what shift on insert)
            moCusPropSysDefEle_c
```

Object-counting class-map indices observed on SW 2026 (for the writer's
parameter derivation): `mgr=1`, `container=3`, …, `advlist=9`,
`moAdvCusProp_c=11`, `moCusPropStringElem_c=17`. **Derive these by walking,
never hard-code** — they vary with the property mix.

### 4.2 `moAdvCusProp_c` record layout (text variant)

```
[wrap tag]   NEW_CLASS(moAdvCusProp_c) on FIRST record, else CLASS_REF 0B 80
[flag:u16]   0 = no value-element ; 1 = a value-element child follows
[value-element child]  (only if flag==1):
    [elem tag]  NEW_CLASS(<elemclass>) first use, else CLASS_REF <idx>80
    [elem body], by elem class:
        moCusPropPRP_c         : <formula:CString> <resolved:CString> FF FF FF FF
        moCusPropStringElem_c  : <value:CString>                       (no trailer)
        moCusPropMassPropEle_c : <formula:CString> <resolved:CString> 08 00 00 00 FF FF FF FF FF FF FF FF
[idx:u32]     property's object-map index (0-based within the user list)
[field2:u32]  class-map index of the parent moAdvCusPropList_c (0x0B for user props)
[name:CString]
[00 00 00 00]
[value:CString]
[resolved:CString]
[wrap trailer]  FF FF FF FF 01 00 00 00 00 00 00 00   (12 bytes)
```

Verified: the empty-value variant (DESCRIPTION) round-trips field-for-field;
the three value-element variants decoded as PARTNO=PRP, REVISION=StringElem,
WEIGHT=MassProp.

### 4.3 Proven byte-exact write recipe (one case)

`add_text_properties(c_B0 [10 props], [ZZ_ONE, ZZ_TWO]) == c_B2` (SW's own
12-prop output), **byte-for-byte**. `make_text_record` reproduces SW's
`ZZ_ONE` record byte-for-byte (136 B). Algorithm:

1. `walk_user_records(data)` → `records`, `end_off` (the `suObList`
   terminator `00 00`). Insert new record bytes at `end_off` (before `00 00`).
2. Build each record per §4.2 (StringElem/text variant).
3. `moCusPropMgr_c` property counter @ offset 20 (u32) += N.
4. `moAdvCusPropList_c` list count (u16 after class name) += N.
5. Re-index every class/object ref in the post-insertion (cut-list) region
   by `K = total new objects = 2·N` for text props (wrapper + elem).

Parameter-derivation rules for a *general* writer:

- `wrap_ref`  = class-map index of `moAdvCusProp_c`
- `elem_ref`  = class-map index of `moCusPropStringElem_c` (define via
  NEW_CLASS if no text prop exists yet — edge case)
- `field2`    = class-map index of `moAdvCusPropList_c`
- `idx_new(k)`= (count of already-indexed properties: user + cut-list) + k
- `K`         = 2 per text prop; bump every ref in the cut-list region by K

**This recipe is proven for the "≥1 text prop already exists, append text
props" case only.** The general codec (below) supersedes it.

---

## 5. The M5.1 build plan (safe, incremental, no-SW gates first)

The graduated-finding rule and the "round-trip first" discipline give a
low-risk path. Each step has a pure-Python falsification gate before any SW.

### Step 1 — Round-trip-faithful reader (`parse → serialize == original`)

Build a `CArchiveDocument` that parses an entire CusProps stream into an
object graph (classes, objects, the shared index map, list framing, the
close-time dump) and re-serializes it. **Gate:** `serialize(parse(B)) == B`
**byte-exact** for every CusProps in the corpus, no SW, no file output.
Model class bodies one at a time (start with the fully-decoded
`moAdvCusProp_c`; treat unknown bodies as opaque `tail_bytes` spans so
round-trip holds before every class is understood — the tail-bytes
invariant). This is the single most valuable artifact: once round-trip is
byte-exact, every mutation becomes a small, local graph edit.

> **Progress (2026-06-08):** Step 1 is landing incrementally, each piece
> corpus-parametrized (part-with-cutlist, assembly-without, empty store) so it
> generalizes beyond the single diff-pair the decode came from:
>
> 1. **Coverage gate** — `check_user_list_coverage` asserts the structured
>    record walk is contiguous, abuts the header, lands exactly on the
>    `suObList` terminator, and that the recognised boundary
>    (`moCutListPropContainer_c` by name, or trailing terminators) follows.
>    (`test_user_list_coverage_no_orphan_bytes`.)
> 2. **Structural header re-serializer** — `reserialize_header` rebuilds the
>    container-chain header from parsed fields (class chain + `moCusPropMgr_c`
>    body `[count,1,1,0]` + the two u16 counts; schemas read from the stream),
>    proven **byte-exact** vs the header slice. This is a *real* test of the
>    header model, not a slice tautology. (`test_reserialize_header_byte_exact`.)
> 3. **Structural user-record re-serializer** — `serialize_user_records`
>    re-emits the whole `moAdvCusProp_c` span from the decoded model (object
>    tags, class defs, and every CString), byte-exact across the corpus for all
>    variants present: `novalue`, `StringElem`, `PRP`, `MassProp`, with both
>    NEW_CLASS (first occurrence) and CLASS_REF tags. Tracks the shared
>    object-map index so CLASS_REFs resolve to the right body shape.
>    (`test_serialize_user_records_byte_exact`.)
> 4. **Full-stream acceptance gate** — `roundtrip(data) == data` reassembles
>    the whole stream as *structural header* + *structural user records* +
>    *verbatim cut-list/close-time-dump tail* (tail-bytes invariant).
>    (`test_cusprops_roundtrip`.)
>
> **Remaining for Step 1 (the only verbatim span left):** the **cut-list
> container** body (`moCutListPropContainer_c` has a scalar body + a `suObList`
> of `moAdvSysDefCusProp_c` items — confirmed NOT a flat tag stream, so it
> needs per-class body models) and the **close-time object-map dump**. The
> region is now **structurally mapped** (container header: item count + item-id
> `0x5F` + reused `moAdvCusPropList_c` (class 9) + prop count; record
> value-element `moCusPropSysDefEle_c` = `<formula><resolved>`; name+`00000000`;
> and record 1's class-refs `0x20`/`0x22` — exactly the refs
> `add_text_properties` re-indexes) — see the cut-list map entry in
> `research/empirical_findings/cusprops_carchive/log.md`. Still open: the exact
> **inter-record trailer** field layout and the close-time dump; nailing those
> wants a 1-vs-2-cut-list-item diff-pair to disambiguate counts from indices,
> which is a supervised-SW task. Until then the tail stays verbatim (correct;
> tail-bytes invariant) and the `roundtrip` gate keeps it honest. Then Step 2
> (model-driven insertion) makes the existing `add_text_properties` recipe —
> including its
> currently-heuristic cut-list re-index — a special case the model reproduces
> structurally.

### Step 2 — Insertion via the model

With a faithful model, "add a property" = insert an object into the graph +
let serialization recompute the shared index map, the CObList framing, the
counters, and the close-time dump. **Gate:** re-derive `c_B2` from `c_B0`
through the model and assert `== c_B2` (the recipe in §4.3 becomes a special
case the model reproduces for free). Then SW-reopen verify (combridge,
pinned empty session).

### Step 3 — Generalize beyond text props

Add PRP / MassProp value-element writers, the "no text prop exists yet" edge
case (must `NEW_CLASS` the elem class), deletion (remove object + re-index
down), and config-scoped **binary** stores if any prove to need it (note:
config XML props already work XML-only — see `api/properties.py`).

### Step 4 — Reuse for M3 (CMgrHdr2)

CMgrHdr2/Header2 is the same family. Once Steps 1–2 hold for CusProps, point
the reader at CMgrHdr2, decode its class bodies (config records), and the
same insertion/mutation machinery drives "rename a configuration / set
active config".

---

## 5A. `Contents/Definition` — the drawing CArchive object stream (M5 / M5.1.5)

`Contents/Definition` holds the document's full object graph (feature tree for
parts; sheets/views/sketches/annotations for drawings). It is the open-ended M5
target. Decode progress as of 2026-06-09 (drawing side):

### Stream layout
- A 36-byte stream header (5 × u32 + a CLSID GUID
  `83a33d34-27c5-11ce-bfd4-00400513bb57`) precedes the CArchive root object.
- The CArchive root is `moDrawing_c` (drawing) at offset 36. Its serialized
  body is **scalars, then inline child objects, then the parent RESUMES its own
  scalar fields** — the classic recursive MFC `Serialize()` shape.

### Section map (synthetic `drawing_min.SLDDRW`, Definition len 29 KB)
```
@36    moDrawing_c → moHeader_c → su_CStringArray ×2 → suObList (doc-logs)
       → 5×{moLogs_c, moStamp_c}  (save history — out-of-scope content)
~463   doc-prefix trailing scalars + node-name table (the "ALPHA" sheet name
       lives here) → moExtObject_c / moCStringHandle_c / moNodeName_c
~1054  moUnitsTable_c + ~16 unit classes
2801   6 folder objects
4033   moDrSheet  ← the sheet record (the reorder/add unit)
         moRefPlane_c, moAbsoluteRefPlnData_c, moProfileFeature_c, sgSketch,
         moCompRefPlane_c, moDetailFolder_c,
5965     moRelMgr_c  (~17.5 KB — see below)
23525    moView_c (~1.4 KB), moSketchBlockMgr_c, moLayerMgr_c/moLayer_c,
         line styles/fonts, BOM, journal
```

### `moRelMgr_c` is the detailing-STANDARDS manager (NOT a geometry blob)
The single largest sub-structure of `moDrSheet`. Decoded 2026-06-09: it is the
document's Document-Properties / detailing standards, NOT freeform sketch
relations. It decomposes into enumerable, fixed-size sub-tables:
- SI **units dictionary** (m/mm/cm/km/um/µm/nm/s/Hz/Pa)
- annotation **priority/status enum** (Description/High/Low Priority/Complete/Reminder)
- **dimension standard** name (e.g. `ANSI-MODIFIED`)
- **font table** — repeating fixed records; the text-format record is:
  `[CString font name][f64 text height = 0.0035 m / 3.5 mm][flags…][f64 = 1.0
  scale][u32 = 400 = Windows LOGFONT lfWeight][f64 = 0.785398 = π/4 angle]
  [f32 −1.0 sentinels]` — a Windows-LOGFONT-derived structure.
- **line-font table** (`CONTINUOUS`; real drawings add `HIDDEN/CENTER/PHANTOM`)

It is **template-invariant**: byte-identical structure across a synthetic
2-sheet pair (only the offset shifts), and the same KIND of structure on two
real client drawings at ~500× scale. **Roadmap consequence:** for sheet
reorder/add this block is **carry-verbatim under object-map index renumbering**,
not a geometry-kernel problem. Tool: `relmgr_profile.py`. Lesson:
`lessons/lesson_20260609_morelmgr_detailing_standards_not_geometry.md`.

### Flat ledger vs recursive engine — why the recursive walk is REQUIRED
The flat object-map ledger (`carchive.objmap`) works for single-list streams
(CusProps, CMgrHdr2) but is **structurally capped** on Definition: its model is
"scalars → inline children → nothing after the last child", so it desyncs at the
first parent that resumes scalar fields after its children (proven: it dies at
the node-name table ~463, mis-reading the "ALPHA" CString's `FF FE` lead as a
bogus class-ref). The fix is **not** a body-oracle patch — it is the recursive
schema engine (`research/.../definition_decode/recursive.py`): a per-class field
schema, recursing on `('child',)`/`('children_u16',)` ops, threading the
object-map index through the recursion. Because every `moView_c`/`moDrSheet`/
`sgSketch` instance is a **class-ref by index** (the class name is serialized
only once), instance census, index-contiguity, and reorder all depend on this
one primitive. Ordered build plan + full decode history:
`research/empirical_findings/definition_decode/log.md`.

---

## 6. Failure modes (learned the hard way — do not repeat)

| Symptom | Root cause | Fix |
|---|---|---|
| SW: `swFileRequiresRepairError` on reopen | object-map indices inconsistent after a byte-splice | model the shared index map; re-index all later refs |
| Encoder output differs from SW by a missing `00 00` before the record | CObList element framing omitted | emit the list's per-element object tags |
| Trailing bytes wrong after add | patched the close-time map dump as if it were a counter | regenerate the dump from the model |
| `_next_pid` returns ~16.7M garbage | SW-internal property elements carry pids `0x0100000N` | exclude pids ≥ `0x01000000` (this is an XML-side note, kept here for cross-ref) |

## 7. Cross-references

- Framing implementation: `src/swformat/carchive/archive.py`
- CString: `src/swformat/carchive/cstring.py`
- Read side + proven write recipe: `src/swformat/carchive/cusprops.py`
  (`read_cusprops`, `read_cutlist_props`, `parse_container_header`,
  `walk_user_records`, `make_text_record`, `add_text_properties`)
- Tests: `test/harness/test_carchive.py`
- Empirical decode (full history incl. falsified paths):
  `research/empirical_findings/cusprops_carchive/log.md`
- Graduated lesson:
  `lessons/lesson_20260608_cusprops_carchive_decode.md`
- `Contents/Definition` decode (drawing CArchive — §5A): section map, flat-walk
  root cause, recursive-engine plan: `research/empirical_findings/definition_decode/`
  (`log.md`, `relmgr_profile.py`, `walk.py`, `recursive.py`). Graduated lesson:
  `lessons/lesson_20260609_morelmgr_detailing_standards_not_geometry.md`.
- Container vs content boundary: `docs/ARCHITECTURE.md`; milestone framing:
  `docs/ROADMAP.md` (M5.1, M5.1.5, M4, M3).
