# Version compatibility

How likely is SWFormat compatible with SOLIDWORKS versions other than the ones it
was reverse-engineered against — older releases, and future ones? This document
gives the honest, layer-by-layer assessment, the empirical basis for it, the
design features that make failures *graceful*, and the built-in **version gate**
that flags files outside the tested envelope.

## TL;DR
- **Reads are broadly safe; writes are version-pinned.** The XML side-channel
  readers are the most robust part (decade-stable schemas); binary CArchive
  decode/encode is the most exposed to version change (but fails safe).
- **Hard floor ≈ SOLIDWORKS 2015.** Pre-2015 files use a different container
  (OLE2) SWFormat does not handle.
- **Tested envelope:** internal format versions **11000, 15000, 19000** (the
  `_MO_VERSION_NNNNN` prefix; 19000 = SW 2026). Everything else is engineering
  judgment, not measured — and `swformat.compat` will **warn** when you read a
  file outside this set.

## The two format eras
The internal version number (`_MO_VERSION_NNNNN`, not the marketing year)
identifies the modern-format generation:
- **Pre-2015 — OLE2 / structured storage (legacy).** A completely different
  container. `detect_format()` returns `"ole2"` and there is **no handler**.
  Backward compatibility hard-stops here.
- **2015+ — the "modern" chunk format.** Marker `14 00 06 00 08 00`, ROL-encoded
  stream names (key = `data[7]`), raw-DEFLATE (`wbits=-15`) payloads. This family
  has held for ~11 years (2015→2026) — everything below assumes it.

## Tested envelope (empirical basis)
| Internal version | Source file | What was exercised |
|---|---|---|
| `19000` (SW 2026) | most fixtures + all Layer-3 reopens | reads + writes, SW-verified |
| `15000` | washer fixture | offset-shift grow accepted; reads |
| `11000` | DELETE.SLDPRT | span-preserve only (offset-shift **rejected** — 6× self-pointer directory) |

Three points, mostly SW 2026. Claims for any other version below are **inference
from format stability + the openswx prior art (which targets 2015+)** — not
verified. Converting them to facts is the multi-version-corpus task at the bottom.

## Layer-by-layer assessment

### Backward (SW 2015 → 2025, modern format)
| Layer | Likelihood | Rationale |
|---|---|---|
| Chunk walker (L0/L1) | **High** | Same container marker/codec across 2015+ (openswx-documented). |
| **XML side-channel reads** (COMPINSTANCETREE, core.xml/app.xml, MATERIALTREE, Features, cut-list XML) | **Very high** | Frozen, decade-old schemas (`sw2003/schema`, materials `version="2008.03"`); readers extract by attribute name and degrade to empty. The most version-robust layer. |
| Binary CArchive reads (CusProps, CMgrHdr2, Definition) | **Moderate–high, fail-safe** | Schema-gated; a schema mismatch raises rather than mis-reads. |
| Writes | **Mixed** | Span-preservation is structure-free → robust. Grow/offset-shift is version-gated (v15000+, measured); on older versions it degrades to `SpanPreserveError`, never corruption. |
| Pre-2015 OLE2 | **None** | Unsupported container. |

### Forward (SW 2027+)
| Layer | Likelihood survives | Risk |
|---|---|---|
| XML reads | **Very high** | SW evolves these schemas additively; unknown attributes/streams are ignored. Only a rename/removal would bite (historically rare). |
| Chunk walker | **High** | Survives unless a format-generation change (like 2015's OLE2→modern); `detect_format()` would catch that, not crash. |
| Binary CArchive reads + binary writes | **At risk** | A class-schema bump (new/reordered fields) would break these — but **fails safe** (schema-gating raises; span-preserve doesn't touch structure). Expect to re-RE the binary layer per major release. |

## Why failures stay graceful (not catastrophic)
These invariants are the reason a wrong-version file degrades instead of corrupts:
- **OpaqueStream pass-through + tail-bytes invariant** — anything unrecognized is
  preserved verbatim, never dropped.
- **Lazy round-trip** — unmodified chunks are re-emitted byte-for-byte, so a
  reopen is equivalent regardless of version.
- **Schema-gating on CArchive** — fail-closed (raise) over silent mis-read.
- **Span-preservation default** — no structural offset assumptions.
- **"re-parse ≠ SW-valid" + Layer-3** — no write path ships without a live-SW
  reopen, catching versions that re-parse but aren't valid.

## The version gate (telemetry)
`swformat.compat` surfaces the version and flags reads outside the tested set:
- `TESTED_VERSIONS = {11000, 15000, 19000}`.
- `version_status(v) -> "tested" | "untested-modern" | "untested-newer" | "unsupported"`.
- The shipped read APIs (`read_component_tree`, `read_part_config_tree`,
  `read_doc_metadata`, `read_materials`, `read_cutlist_xml`) call
  `compat.warn_streams(...)`, emitting an `UntestedVersionWarning` when a file is
  outside the envelope (silence via the standard `warnings` filters).
- CLI: **`swf-version FILE`** → `version: NNNNN  status: ...`.
- Programmatic pre-flight: `compat.check_supported(path) -> (version, status)`.

## Establishing the envelope empirically (recommended)
Build a **multi-version corpus**: save the same handful of parts / assemblies /
drawings in each licensable SOLIDWORKS release (e.g. 2018/2020/2022/2024/2026),
register them in `test/corpus/corpus.config.json` (`license_status: synthetic`
where applicable), and run the read suite across all. That turns the High/Moderate
judgments above into a measured envelope and is the home for the project's living
schema-version table. Until then, trust the `swf-version` gate to flag anything
outside `{11000, 15000, 19000}`.
