# SWFormat Reverse-Engineering Methodology

> How we figure out the format. Disciplined, repeatable, lesson-driven.
> Every working primitive in SWFormat is the output of this process.

---

## 1. The diff-pair method

The fundamental RE primitive:

1. **Build a minimal-variation pair of files.** Two SW files that
   differ in EXACTLY one knowable way. Examples:
   - Cube_v1.sldprt vs Cube_v2.sldprt — same model, second saved 10
     seconds later with no changes. **Reveals: save-time
     non-determinism.**
   - Cube_with_prop_FOO=1.sldprt vs Cube_with_prop_FOO=2.sldprt —
     identical except the custom property `FOO`. **Reveals: where
     custom property values are encoded.**
   - Cube_one_config.sldprt vs Cube_two_configs.sldprt — same geometry,
     second has an extra empty configuration. **Reveals: how
     configurations expand CMgrHdr2.**
   - Cube_100mm.sldprt vs Cube_101mm.sldprt — same model, one
     dimension changed. **Reveals: where parameter values are
     encoded inside Contents/Definition.**

2. **Run the diff tool at all three layers:**
   ```
   python -m swformat.tools.diff_files a.sldprt b.sldprt
   ```
   Layer 1 reports chunk-level differences (which streams added /
   removed / changed size). Layer 2 reports stream-content differences
   (which streams have different decompressed payloads, and a
   hex/XML diff of the contents). Layer 0 (raw bytes) is the
   sanity check.

3. **Form a hypothesis.** "The change to FOO=1 vs FOO=2 should appear
   in `docProps/custom.xml` only, as an XML text change."

4. **Falsify the hypothesis.** Run additional diff pairs that should
   produce the SAME change set if the hypothesis is correct. Or
   modify the byte at the suspected location and confirm SW opens
   the file with the predicted change.

5. **Write the lesson.** Once the hypothesis survives 3-5
   falsification attempts, document it at
   `lessons/lesson_<YYMMDD>_<short_slug>.md`.

6. **Encode the lesson as a handler.** Implement the stream-level
   read+write in `src/swformat/streams/...`. Add a test case to
   `test/harness/`. The test case is the standing falsification — if
   it ever breaks, the lesson is wrong.

## 2. Test corpus is the lifeblood

Every diff-pair is a fixture. The corpus grows monotonically as RE
progresses. Each fixture is:

- **A .csx script** at `tools/corpus_gen/<type>/<name>.csx` — produces
  a SW file deterministically via combridge
- **An output file** at `test/corpus/<type>/<sw_version>/<name>.sldprt`
- **A golden manifest** at `test/golden/manifests/.../<name>.json`
  recording the parser's expected output for this fixture
- **A lesson** (when the fixture produces a finding) at
  `lessons/lesson_*.md`

**Why .csx generation matters:** the corpus is REGENERATABLE. When a
new SW version ships and saves files slightly differently, we re-run
the .csx scripts under that SW version, get fresh files, and update
the golden manifests. The corpus-IP problem (real work-product files
cannot be shipped publicly) is sidestepped because the committed corpus is
synthetic.

## 3. Hypothesis log

Each active RE topic gets a directory under `research/empirical_findings/`:

```
research/empirical_findings/cmgrhdr2_field_at_0x40/
├── log.md          # dated hypothesis entries
├── samples/        # the specific corpus files this investigation uses
├── diffs/          # captured diff outputs
└── scratch.py      # experimental decoders
```

`log.md` format:

```markdown
# CMgrHdr2 field at 0x40 (added in schema 0x0858)

## 2026-06-12 — initial hypothesis
The DWORD at CMgrHdr2 offset 0x40 (per the openswx version table) was
added in schema 0x0858 (SW 2014 SP3?). Diff between a SW2013-saved file
and a SW2014-saved file should reveal what it encodes.

## 2026-06-13 — falsified
Diff shows the DWORD changes between two SW2014 saves of the SAME
file. So it's not a version stamp; it's per-save data.

## 2026-06-14 — re-hypothesis
Looks like a save-counter (monotonically increasing across saves).
Need to confirm with 3 sequential saves.

## 2026-06-15 — confirmed
Three sequential saves: value increments 1, 2, 3. It's the save count.
Cross-references the file's "SaveCount" custom property when shown in
SW's File Properties dialog. **Hypothesis confirmed; ready to graduate
to lesson.**

## 2026-06-15 — graduated
Lesson written at lessons/lesson_20260615_cmgrhdr2_save_count.md
Handler implemented in src/swformat/streams/cmgrhdr.py (M3 work).
Closing this hypothesis log; it stays here for historical reference.
```

**Falsified hypotheses stay in the log** so we don't re-investigate
them in 6 months thinking we have a new idea.

## 4. The "tail bytes" discipline

When parsing any structure, **read every byte you intend to write back.**
Don't skip "obviously unused" bytes — there's no such thing in a
proprietary binary format. Either:

- The bytes have meaning → decode them
- The bytes are unused → they should be zeros; assert that and pass through
- The bytes are nondeterministic → record them in the stable mask and
  pass through

**A parser that reads structure X and only emits the fields it
understands is broken,** even if it appears to work today. The next
file with garbage in those bytes will mysteriously fail.

In SWFormat code, the convention is `tail_bytes: bytes` as the last
field of every dataclass that wraps a binary structure:

```python
@dataclass
class CMgrHdr2:
    write_count: int
    config_count: int
    # ... known fields ...
    tail_bytes: bytes   # everything we haven't decoded yet — pass through verbatim
```

`tail_bytes` shrinks as we decode more, never grows. It's the
visible measure of progress.

## 5. Falsification before commit

Every finding goes through 3 falsification attempts before becoming a
lesson:

1. **Cross-version**: does the finding hold on files from SW 2020,
   2024, 2026? If only on 2026, it's a version-specific quirk that
   needs to be flagged in the lesson.
2. **Cross-type**: does the finding hold on parts AND assemblies AND
   drawings (where the relevant stream exists)?
3. **Edge case**: does the finding hold on files with 1 configuration,
   100 configurations, configurations with non-ASCII names, etc.?

A finding that survives all three is robust enough to encode as a
handler. A finding that breaks any of them gets a tighter lesson
("works only for X under Y").

## 6. The Schwitters `openswx` reference

`openswx` is the primary prior art. SWFormat uses it as:

- **Algorithm reference under MIT license**: we port algorithms
  (chunk walker, ROL codec, CMgrHdr2 partial decoder) to Python.
  Attribution at `research/openswx_notes/README.md`.
- **NOT a runtime dependency**: SWFormat has no C++ dependency.
- **NOT clean-room**: this is a stronger standard than license
  compatibility; we'd need a spec-writer and an isolated
  implementer. We don't claim that. We claim license-compatible
  port.

When porting from openswx:

1. Read the relevant openswx source file
2. Write a one-paragraph description of what the algorithm does in
   `research/openswx_notes/<file>.md`
3. Implement in Python using that description as the spec
4. Cross-check the output against openswx's output on the same input
   file (when openswx builds — it doesn't always)

## 7. CArchive RE methodology (M5+)

CArchive is harder than chunk-format RE because:

- The wire format encodes **class names and schema versions** — those
  tell you WHAT type of object follows, but not how to decode it
- Each class's `Serialize()` method is SOLIDWORKS-proprietary
- Object references via the CObject map create graphs, not trees
- Geometry payloads are Parasolid (or similar) binary

The methodology for a single CArchive class (e.g. `featureType_c`):

1. **Find files with N=1, 2, 3 features.** Diff them. The
   `Contents/Definition` stream should grow predictably.
2. **Locate the feature-tree-array header.** Should be at a stable
   offset relative to the document header.
3. **Read the first feature's tag.** CArchive tags are documented:
   `0xfffe` = NULL ref, `0x8000`-flagged = schema number follows,
   otherwise it's an object reference index.
4. **Read the class name string** (CString format: length byte / 0xFF
   prefix / Unicode tag).
5. **Diff the per-feature bytes** between "extrude(100mm)" and
   "extrude(101mm)". The difference reveals where the dimension
   parameter lives.
6. **Hypothesis log → falsification → lesson → handler.**

This is the M5 work. Realistic timeline: weeks per class. Many feature
types.

## 8. When to stop

Some bytes are not worth decoding:

- `Contents/DisplayLists` is the graphics cache. SW regenerates it on
  every save. **Treat as OpaqueStream forever.** Possibly strip on
  write (let SW rebuild).
- `_MO_VERSION_NNNNN/Biography` is the save history. Version is in
  the stream NAME; the payload bytes carry timestamps and incremental
  history. **Pass through verbatim.**
- `ThirdPtyStore/*` is third-party addin data (Toolbox, custom
  property tools, etc.). **Never decode.** These streams may contain
  data the third party considers proprietary; safer to treat as
  opaque.

The OpaqueStream pattern is the explicit acknowledgement that "we
chose not to look inside this." That's a valid permanent disposition,
not a temporary stub.

## 9. Failure-mode catalog

When the parser produces unexpected output, the catalog of plausible
causes:

| Symptom | Likely cause |
|---|---|
| Chunk walker reports zero chunks on a file SW opens fine | Not modern format (likely OLE2 pre-2015); call `detect_format()` first |
| Stream name decodes to gibberish | ROL key is wrong; verify `file[7]` is being used as key |
| Decompressed bytes empty / very short for a chunk that should have content | `f1 < 65536` so it's a TOC entry not an inline chunk; the actual payload is elsewhere |
| Inter-chunk gap of unexpected size | Padding before next chunk; preserve in `Gap.raw_bytes` |
| Stream content is XML but starts with garbage byte | ~1-byte prefix before XML; strip leading non-`<` byte before XML parse (documented quirk on some `swXmlContents/KeyWords` and `ISolidWorksInformation.xml`) |
| CMgrHdr2 field at offset X has unexpected value | Schema version may not have introduced the field yet; check the version-threshold table |

## 10. The lesson template

Every finding produces a lesson at
`lessons/lesson_<YYMMDD>_<slug>.md` with frontmatter:

```yaml
---
date: 2026-MM-DD
category: format-spec | quirk | workflow | api-usage | crash
severity: high | medium | low
subject: swformat
api_symbols: [list, of, format, elements, touched]
keywords: [searchable, terms]
streams_touched: [Contents/CMgrHdr2, ...]
schema_versions_validated: [2070, 2136, ...]
sw_versions_tested: [2020, 2024, 2026]
related_lessons:
  - lessons/lesson_20260521_openswx_modern_format_breakthrough.md
  - lessons/lesson_<prereq>.md
---
```

Body sections:

- **Context** — what we were trying to do
- **What we found** — the finding, with byte ranges, hex dumps,
  version stamps
- **How we verified** — the diff pairs, the falsification attempts
- **Implementation** — file path of the handler / parser that encodes
  this finding
- **Limits** — what the finding does NOT cover (which versions, which
  edge cases)
- **References** — openswx file(s), Microsoft docs, related lessons

The lesson is the single source of truth. If the code drifts from the
lesson, the LESSON is right and the code is wrong (or the lesson needs
update with a dated footer).
