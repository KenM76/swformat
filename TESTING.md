# SWFormat Testing

> Three-layer pyramid that mirrors the architecture. Each layer
> answers a different question. M1's pass criterion is **Layer 3
> equivalence**; byte-equality is a diagnostic, not a gate.

---

## 1. The three test layers

| Layer | Question it answers | Run frequency | Tooling |
|---|---|---|---|
| **Layer 1 — chunk walker correctness** | "Did the parser see the right chunks, decode the right stream names, decompress to the right bytes?" | Every commit (CI) | pytest against golden manifests |
| **Layer 2 — byte equality under stable mask** | "If I round-trip an unmodified file, do the bytes match modulo known non-determinism?" | Per-PR + diagnostic-mode runs | `echo_check` CLI |
| **Layer 3 — SOLIDWORKS equivalence on reopen** | "Does the written file open in real SW with semantics equivalent to the original?" | Per-milestone manual + (later) automated via combridge | `combridge solidworks run-script` verifier |

### Current test suite (as shipped, 2026-06-08)

`python -m pytest test/harness/ -q` → **73 passing**. Plus
`python test/harness/proof_of_life.py` (read smoke test).

| File | Layer | Tests | Covers |
|---|---|---|---|
| `test_chunk_walker.py` | 1 | 15 | no-orphan-bytes, `reconstruct()==orig`, stream parity vs the imported reference, format detection, ROL codec, `read_document_bytes` equivalence, early-coincidental-marker scan guard |
| `test_roundtrip.py` | 2 | 7 | lazy round-trip byte-identical; re-deflate preserves all stream content; targeted mutation round-trips |
| `test_toc.py` | 2 | 6 | TOC offset-pointer invariant ("no stale pointer remains"); `fixup_offset_pointers` unit tests (remap/skip/gap-only) |
| `test_properties.py` | 2 | 12 | custom-prop XML editor (set/add/delete/escape, both schema eras, special-char names, delete↔name-dictionary symmetry); global + config-scoped `edit_properties` round-trip |
| `test_carchive.py` | 2/3 | 33 | CArchive framing primitives, CString codec (incl. astral), CusProps header/record decode, no-orphan coverage gate, structural header + record re-serialization, full `roundtrip()`, byte-exact text-prop writer vs SW, cut-list read |
| `proof_of_life.py` | 1 | — | reads every corpus file cleanly |
| `twin_save_baseline.py` | — | M0.5 orchestrator/analyzer (combridge; run manually) |
| `layer3_reopen.py` | 3 | SW reopen-equivalence harness (combridge; run manually with `--session pid:N`) |

Layer-3 (live SOLIDWORKS) runs are manual today (require a pinned SW
session via combridge); CI runs Layers 1–2 only.

## 2. M1 pass criterion (the corrected one)

**Per adversarial review of the workflow output**: Layer 2 byte-equal
under stable mask is **NOT** the primary M1 gate. It's a useful
diagnostic. The primary gate is Layer 3 equivalence:

> A SWFormat-written file, reopened in real SOLIDWORKS, has equivalent
> semantics to the original. Equivalence is defined per use-case via
> the equivalence matrix below.

Why this reframe matters — now confirmed by experiment (M0.5, ✓ done):
across 9 varied files, **0 were byte- or length-stable** across two
unmodified saves 10 s apart, yet **all 9** satisfied `reconstruct() ==
original` (our writer is faithful; SW is the non-deterministic party).
Requiring byte-equal-to-a-fresh-SW-save would block M1 even when the
written file functions perfectly. M1.5 later pinned the actual integrity
mechanism: it's **not** a checksum — it's the **central TOC's offset
pointers** (`stored == offset - 8`), which `write_with_toc` keeps
consistent. So Layer 2 is re-scoped to the *achievable* self-consistency
check `reconstruct(file) == file` plus a stream-NAME stable mask; Layer 3
(SW reopen) is the gate.

**Status:** M1 Layer-3 gate is MET — the lazy round-trip reopens with
equivalent semantics for a part, an assembly, and a drawing. M2 global
property edits reopen with the change reflected — set/delete EXISTING and
ADD brand-new names — **via span preservation** (re-verified on a part this
session through the API: `layer3_propedit.py`). NOTE: size-changing writes
only work when the edited stream fits its original compressed span; the
earlier offset-pointer "fixup" was falsified (SW rejects shifted files) and
replaced. Config-scoped ADD is span-limited. Evidence in
`research/empirical_findings/{twin_save_baseline,m1_writer_roundtrip,cusprops_carchive}/`.

### Layer 3 equivalence matrix

| Property | Test method | Required for M1? |
|---|---|---|
| Format magic + chunk markers | Re-read written file via SWFormat; parser identifies it as modern | YES |
| Stream name list | Set-equal to original's | YES |
| Custom property values (all) | Via combridge: open written file, dump custom props, set-equal to original | YES |
| Configuration count and names | Via combridge: enumerate configs | YES |
| Active configuration | Via combridge: read GetActiveConfiguration().Name | YES |
| Mass (parts) | Via combridge: read mass-property; equal within 0.01% | YES |
| Center of gravity (parts) | Via combridge: equal within 0.0001 mm | YES |
| Surface area (parts) | Via combridge: equal within 0.01% | YES |
| Sheet count and names (drawings) | Via combridge: equal | YES |
| Component count and references (assemblies) | Via combridge: equal | YES |
| Feature tree structure | M5+ — not gated for M1 | NO (M5+) |
| Sketch geometry | M5+ — not gated for M1 | NO (M5+) |
| `Contents/DisplayLists` regenerated by SW | Verify SW regenerates after first open | YES (by absence, not equality) |
| `_MO_VERSION_NNNNN/Biography` save history | Pass-through verbatim from original | YES |

## 3. Test corpus

### Sources

The corpus has two parts:

1. **Primary (in-tree)**: SW files generated by combridge from .csx
   scripts under `tools/corpus_gen/`. These are deterministic,
   regenerable, and IP-clean (synthetic, no client data). Live at
   `test/corpus/<type>/<sw_version>/<name>.sld*`.
2. **External (referenced by path)**: your own existing real-world SW
   files referenced via absolute path in `corpus.config.json`. These
   exercise edge cases the synthetic corpus doesn't cover. Never
   copied in-tree.

### corpus.config.json format

```json
{
  "files": [
    {
      "tag": "short_identifier",
      "path": "C:\\path\\to\\file.sldprt",
      "doc_type": "part|assembly|drawing",
      "expected_format": "modern|ole2|opc",
      "sw_version_estimate": "2026",
      "license_status": "synthetic|owner",
      "notes": "What this file exercises."
    }
  ]
}
```

The `license_status` field is enforced by CI: only `synthetic` files
are checked into `test/corpus/`; `owner` files MUST be referenced by
absolute path and never copied in-tree.

### Required corpus seed (M0 deliverable)

At minimum:

- 1 part with one extrude, generated by .csx (cube_100mm_one_extrude.sldprt)
- 1 part with two configurations
- 1 assembly with two components
- 1 drawing with one sheet, one view

Each has a matching golden manifest in `test/golden/manifests/`.

### Corpus growth strategy

As each milestone progresses, the corpus grows to exercise the new
capability:

- M2: parts with each kind of property (custom-value, computed,
  attached-to-config, with non-ASCII characters)
- M3: parts/assemblies with 1, 2, 10 configurations
- M4: drawings with 1, 5, 31 sheets; views of each type
- M5: parts with each feature type covered, multiple schema versions

## 4. Layer 1 — chunk-walker correctness

**Test file**: `test/harness/test_chunk_walker.py`

**Fixtures**:

- `corpus_file` — parametrized over every file in `corpus.config.json`
- `manifest` — loads the matching golden manifest JSON

**Tests** (per fixture):

```python
def test_format_matches_manifest(corpus_file, manifest):
    doc = read_file(corpus_file)
    assert doc.format == manifest['format']

def test_doc_version_matches_manifest(corpus_file, manifest):
    doc = read_file(corpus_file)
    assert doc.doc_version == manifest['doc_version']

def test_stream_names_match_manifest(corpus_file, manifest):
    doc = read_file(corpus_file)
    expected = sorted(manifest['stream_names'])
    actual = sorted(c.stream_name for c in doc.chunks)
    assert actual == expected

def test_no_orphan_bytes(corpus_file):
    """The 'tail bytes' invariant: every byte in the file is either
    inside a Chunk's raw bytes (header + original_compressed) or
    inside a Gap.raw_bytes. Bytes can never be silently dropped."""
    data = Path(corpus_file).read_bytes()
    doc = read_file(corpus_file)
    chunk_bytes = sum(len(c.raw_header) + len(c.original_compressed) for c in doc.chunks)
    gap_bytes   = sum(len(g.raw_bytes) for g in doc.gaps)
    assert chunk_bytes + gap_bytes == len(data), \
        f"orphan bytes: file={len(data)}, accounted={chunk_bytes + gap_bytes}"
```

The `test_no_orphan_bytes` test is the LOAD-BEARING invariant. If it
ever fails, the parser is silently dropping bytes and the writer (when
it exists) cannot round-trip.

### Golden manifest format

```json
{
  "format": "modern",
  "doc_version": 15000,
  "doc_type": "part",
  "file_size_bytes": 81519,
  "rol_key": 5,
  "stream_count": 39,
  "stream_names": [
    "Contents/Config-0-PreviewPNG",
    "Contents/Config-0-ResolvedFeatures",
    "Contents/DisplayLists",
    ...
  ],
  "chunks": [
    {
      "stream_name": "Contents/DisplayLists",
      "section_type": 253,
      "file_offset": 1234,
      "compressed_size": 12345,
      "decompressed_size": 61325,
      "decompressed_sha256": "abc123..."
    },
    ...
  ],
  "gaps": [
    {"file_offset": 0, "byte_count": 64}
  ]
}
```

Generated by `tools/corpus_gen/regenerate.py`: parse the file, dump
the manifest. Manifests are checked into git as the golden truth.

When a SW SP update changes file output and a manifest mismatch
appears, the question is: did our parser break, or did SW's output
change? Re-run the generator; if the new output is semantically
correct, accept the new manifest. If not, the parser regressed.

## 5. Layer 2 — byte equality under stable mask

**Tool**: `python -m swformat.tools.echo_check <file>`

**What it does**:

1. Read the file: `doc = read_file(path)`
2. Write it: `doc.write(tmp_out_path)` (no modifications)
3. Compare: byte-diff between original and tmp_out
4. Apply the stable mask: ignore differences in known-nondeterministic regions
5. Report:
   - Bytes-different (raw)
   - Bytes-different (after mask)
   - Diff cluster locations (which streams)
   - PASS if post-mask differences are zero

**Stable mask data source**: M0.5 twin-save experiment. See
`research/empirical_findings/twin_save_baseline/log.md` (run before M1).

**Why this is DIAGNOSTIC, not a gate**: Some non-determinism is
genuinely unknowable (e.g., a CObject map's ordering depends on heap
allocation order inside SW; on a fresh save, the same model can land
in a different order). Layer 2 reveals these regions but doesn't punish
us for them.

## 6. Layer 3 — SOLIDWORKS equivalence on reopen

**Tool**: `python -m swformat.tools.echo_check --layer3 <file>`

**Requires**: combridge installed, SOLIDWORKS running, the file's
SW version matching the running SW.

**What it does** (planned for M1):

1. Read the file
2. Write it as a copy (no modifications)
3. Use combridge to launch SW (if not running) and open both files
4. Run an equivalence script via combridge that compares each row of
   the equivalence matrix from §2 above
5. Report row-by-row pass/fail

**Operator gate**: Layer 3 tests modify SW state; the test harness
prompts before opening real SW files unless `--yes` is passed.

**CI integration**: Layer 3 cannot run on a vanilla GitHub Actions
runner (no SW installed), so CI runs the pure-Python Layers 1+2 only. The
Layer-3 suite is run manually on a machine with SOLIDWORKS + combridge.

## 7. Performance benchmark (M0 deliverable)

**Tool**: `test/harness/bench_chunk_walker.py`

**Targets** (subject to revision after first measurement):

| File size | Target throughput | Why |
|---|---|---|
| 100 KB part | <50 ms | Tray apps need fast inspection |
| 1 MB part | <100 ms | Bulk-edit of 1000 files in <100s |
| 10 MB assembly | <500 ms | Bulk-edit still tractable |
| 100 MB assembly | <5 s | Acceptable for one-off |

If any target is exceeded by 2x, the next milestone should include
optimization (mmap, lazy decompression, etc.).

## 8. Fuzzing harness (M0 deliverable)

**Tool**: `test/harness/fuzz_chunk_walker.py` (atheris or hypothesis)

**Targets**:

- `iter_chunks(bytes)` — must NEVER crash on any input
- `rol_decode(name_bytes, key)` — must NEVER crash
- `read_file(path)` — must raise `FormatError` on any malformed input,
  never silently produce a corrupt Document

**Initial corpus**: a small directory of synthetic mutated SW files
(real file with random bytes corrupted at random offsets).

Fuzzing finds the inevitable parser crashes on malformed input before
they appear in production. Especially important for the bulk-edit
workflow where SWFormat might process thousands of files including
truncated, corrupted, or maliciously-modified ones.

## 9. CI configuration

`.github/workflows/ci.yml` runs on every push and pull request, on
**ubuntu-latest** (Python 3.11 and 3.13) — running on Linux is itself a
demonstration that the library needs no SOLIDWORKS and no Windows:

- A **corpus-IP guard** — fails if any `*.sld*` file is tracked in the repo
  (no real CAD files may be committed; see §"Corpus IP").
- `ruff check` — linting (advisory; the tree is currently ruff-clean).
- `mypy --strict` on the **core** (`swformat.types` + Layer 1 `chunks/` + `io/`)
  — a gate. The higher M2+/M5+ layers (`api/`, `carchive/`, `sketches/`,
  `tools/`) are intentionally permissive while they stabilise and are not
  strict-checked.
- `pytest test/harness -q` — the **gate**: the pure-Python Layer-1/2 suite.

Layer-3 (live-SOLIDWORKS reopen via combridge) does NOT run in CI — it needs
Windows + SOLIDWORKS — and the real-file Layer-2 tests skip when no local
corpus file is configured. Layer 3 is run manually with a SOLIDWORKS session.

## 10. Quarantine pattern

When a corpus file fails the parser but the failure is understood and
expected (e.g., it's an OLE2 file and we haven't built that parser
yet), it goes to `test/corpus/quarantine/<file>/` with a
`quarantine.log` explaining why and what milestone will fix it.
Quarantined files are skipped by default but can be run explicitly via
`pytest --include-quarantine`. This keeps the green-test discipline
without losing visibility into known failures.
