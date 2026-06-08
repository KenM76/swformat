"""M0.5 twin-save baseline — orchestrator + analyzer.

See ``research/empirical_findings/twin_save_baseline/log.md`` for the
experiment design and findings. This module automates the three phases:

    python test/harness/twin_save_baseline.py acquire  --session pid:NNNN
    python test/harness/twin_save_baseline.py twinsave --session pid:NNNN
    python test/harness/twin_save_baseline.py analyze

PURPOSE
-------
Quantify SOLIDWORKS save non-determinism so M1's pass criteria are chosen
from data, not guesses. For each corpus file we save it twice (10 s apart,
no edits) and measure how much — and where — the two saves differ.

WHY THIS IS A SCRIPT, NOT A PYTEST TEST
---------------------------------------
``acquire``/``twinsave`` drive a live SOLIDWORKS via combridge and need a
``--session pid:NNNN`` selector (the user runs several SW sessions in
parallel; we must pin to the dedicated empty one and NEVER use the MRU
default). ``analyze`` is pure-Python over the produced files. Keeping it
out of the pytest path (filename doesn't match ``test_*``) avoids firing
SW automation during ``pytest``.

METHODOLOGY NOTES (learned the hard way — see the log)
------------------------------------------------------
- **Same-filename twins.** SW embeds the saved base name in many streams,
  so saving twin A and twin B to *different* names injects "A"/"B" diffs
  everywhere. We save BOTH to one fixed path and copy the bytes out
  between saves; diff(A,B) is then pure non-determinism.
- **SaveAs Copy.** ``swSaveAsOptions_Copy`` saves a copy without changing
  the open doc's path/dirty state, so both twins derive from one pristine
  in-memory model.
- **Pack-and-Go needs SetDocumentSaveToNames.** ``SavePackAndGo`` silently
  writes nothing (returns null) unless ``SetDocumentSaveToNames`` is
  called before ``SetSaveToName2``/``SavePackAndGo``.
- **Isolation.** Assemblies/drawings are Pack-and-Go'd (flattened) into
  their own folder so they open without the W: source tree. Real client
  files (TS- prefix) are IP — samples/ is gitignored; nothing is committed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

import swformat  # noqa: E402
from swformat.tools.diff_files import byte_diff_clusters, locate_clusters  # noqa: E402

# --- paths / config --------------------------------------------------------
EXP = ROOT / "research" / "empirical_findings" / "twin_save_baseline"
SAMPLES = EXP / "samples"
SCRATCH = EXP / "scratch"
REPORT_JSON = EXP / "twin_save_report.json"
DEFAULT_COMBRIDGE = Path("combridge.exe")  # on PATH, or pass --combridge

# Source root for the local corpus. Point this at a folder of YOUR OWN
# legitimately-obtained SOLIDWORKS files; the acquired corpus is local-only and
# never committed (see docs/LEGAL.md corpus-IP rules). Placeholder shown.
WSRC = Path(r"C:\path\to\your\solidworks\files")

# doc_type -> (swDocumentTypes_e value, file extension)
DOCTYPE = {"part": (1, ".SLDPRT"), "assembly": (2, ".SLDASM"), "drawing": (3, ".SLDDRW")}

# The corpus shape used for the M0.5 twin-save baseline: a mix of parts (copy)
# + assemblies/drawings (pack-and-go). Fill in your OWN file names below;
# the example names are generic placeholders, not distributed files.
# acquire: "copy" for standalone parts, "packgo" for ref-bearing docs.
CORPUS = [
    {"tag": "part_nut",   "doc": "part",     "acquire": "copy",   "src": "example-hardware.SLDPRT"},
    {"tag": "part_large", "doc": "part",     "acquire": "copy",   "src": "example-part-a.SLDPRT"},
    {"tag": "part_a",     "doc": "part",     "acquire": "copy",   "src": "example-part-b.SLDPRT"},
    {"tag": "part_b",     "doc": "part",     "acquire": "copy",   "src": "example-part-c.SLDPRT"},
    {"tag": "asm_70",     "doc": "assembly", "acquire": "packgo", "src": "example-assembly-1.SLDASM"},
    {"tag": "asm_60",     "doc": "assembly", "acquire": "packgo", "src": "example-assembly-2.SLDASM"},
    {"tag": "asm_20",     "doc": "assembly", "acquire": "packgo", "src": "example-assembly-3.SLDASM"},
    {"tag": "drw_qc",     "doc": "drawing",  "acquire": "packgo", "src": "example-drawing-1.SLDDRW"},
    {"tag": "drw_60",     "doc": "drawing",  "acquire": "packgo", "src": "example-drawing-2.SLDDRW"},
    {"tag": "drw_10",     "doc": "drawing",  "acquire": "packgo", "src": "example-drawing-3.SLDDRW"},
]

# --- per-entry path helpers ------------------------------------------------
def sample_dir(tag: str) -> Path:
    return SAMPLES / tag

def src_in_samples(entry: dict) -> Path:
    """The acquired source file inside samples/ (keeps the original basename)."""
    return sample_dir(entry["tag"]) / entry["src"]

def twin_paths(entry: dict) -> tuple[Path, Path, Path]:
    _, ext = DOCTYPE[entry["doc"]]
    d = sample_dir(entry["tag"])
    return d / f"twin{ext}", d / f"twinA{ext}", d / f"twinB{ext}"

# --- csx templates (placeholder substitution; NO f-strings, C# uses {}) ----
_PACKGO_CSX = r"""// generated by twin_save_baseline.py — pack-and-go __TAG__ into isolated folder
string src  = @"__SRC__";
string dest = @"__DEST__";
const int swDocType = __DOCTYPE__;
const int swOpenDocOptions_Silent = 1;
if (!Directory.Exists(dest)) Directory.CreateDirectory(dest);
int errs = 0, warns = 0;
IModelDoc2 doc = swApp.OpenDoc6(src, swDocType, swOpenDocOptions_Silent, "", ref errs, ref warns);
if (doc is null) { Console.Error.WriteLine($"OpenDoc6 failed errs={errs} warns={warns}"); return 2; }
ModelDocExtension ext = doc.Extension;
PackAndGo pg = (PackAndGo)ext.GetPackAndGo();
pg.IncludeDrawings = false;
pg.IncludeSimulationResults = false;
pg.IncludeToolboxComponents = false;
object namesObj;
pg.GetDocumentNames(out namesObj);
object[] names = namesObj as object[];
pg.SetDocumentSaveToNames(names);
pg.SetSaveToName2(true, dest);
pg.FlattenToSingleFolder = true;
object ret = ext.SavePackAndGo(pg);
int[] statuses = ret as int[];
int ok = 0; if (statuses != null) foreach (int s in statuses) if (s == 0) ok++;
Console.WriteLine($"packed {(statuses?.Length ?? -1)} docs, {ok} ok -> {dest}");
swApp.CloseDoc(doc.GetTitle());
return 0;
"""

_TWINSAVE_CSX = r"""// generated by twin_save_baseline.py — twin-save __TAG__ (same-name twins)
string src   = @"__SRC__";
string fixedPath = @"__FIXED__";
string twinA = @"__TWINA__";
string twinB = @"__TWINB__";
const int swDocType = __DOCTYPE__;
const int swOpenDocOptions_Silent = 1;
const int swSaveAsCurrentVersion = 0;
int saveOpts = 1 | 2; // swSaveAsOptions_Silent | swSaveAsOptions_Copy
int errs = 0, warns = 0;
IModelDoc2 doc = swApp.OpenDoc6(src, swDocType, swOpenDocOptions_Silent, "", ref errs, ref warns);
if (doc is null) { Console.Error.WriteLine($"OpenDoc6 failed errs={errs} warns={warns}"); return 2; }
Console.WriteLine($"opened: {doc.GetTitle()} (errs={errs} warns={warns})");
int e1 = 0, w1 = 0;
bool okA = doc.Extension.SaveAs(fixedPath, swSaveAsCurrentVersion, saveOpts, null, ref e1, ref w1);
File.Copy(fixedPath, twinA, true);
Console.WriteLine($"saveA ok={okA} e={e1} w={w1}");
System.Threading.Thread.Sleep(10000);
int e2 = 0, w2 = 0;
bool okB = doc.Extension.SaveAs(fixedPath, swSaveAsCurrentVersion, saveOpts, null, ref e2, ref w2);
File.Copy(fixedPath, twinB, true);
Console.WriteLine($"saveB ok={okB} e={e2} w={w2}");
swApp.CloseDoc(doc.GetTitle());
if (File.Exists(fixedPath)) File.Delete(fixedPath);
Console.WriteLine("closed");
return (okA && okB) ? 0 : 4;
"""


def _render(template: str, **kw: str) -> str:
    out = template
    for k, v in kw.items():
        out = out.replace(f"__{k}__", v)
    return out


def _run_combridge(combridge: Path, session: str, csx: Path, timeout: int = 300) -> int:
    cmd = [str(combridge), "solidworks", "--session", session, "run-script", str(csx), "-"]
    print(f"  $ combridge … run-script {csx.name}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    for line in (proc.stdout or "").splitlines():
        print(f"    | {line}")
    if proc.returncode != 0:
        for line in (proc.stderr or "").splitlines():
            print(f"    !! {line}")
    return proc.returncode


# --- phase: acquire --------------------------------------------------------
def cmd_acquire(args: argparse.Namespace) -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    for entry in _selected(args):
        tag = entry["tag"]
        d = sample_dir(tag)
        d.mkdir(parents=True, exist_ok=True)
        src = WSRC / entry["src"]
        if not src.exists():
            print(f"[{tag}] SKIP — source not found: {src}")
            continue
        if entry["acquire"] == "copy":
            dst = src_in_samples(entry)
            shutil.copy2(src, dst)
            print(f"[{tag}] copied -> {dst.name} ({dst.stat().st_size:,} B)")
        else:  # packgo
            doctype, _ = DOCTYPE[entry["doc"]]
            csx = SCRATCH / f"packgo_{tag}.csx"
            csx.write_text(
                _render(_PACKGO_CSX, TAG=tag, SRC=str(src), DEST=str(d) + "\\",
                        DOCTYPE=str(doctype)),
                encoding="utf-8",
            )
            print(f"[{tag}] pack-and-go {entry['src']} …")
            rc = _run_combridge(args.combridge, args.session, csx, timeout=args.timeout)
            got = src_in_samples(entry)
            ok = got.exists()
            print(f"[{tag}] rc={rc} source-present={ok}"
                  + (f" ({got.stat().st_size:,} B)" if ok else f"  MISSING {got.name}"))
    return 0


# --- phase: twinsave -------------------------------------------------------
def cmd_twinsave(args: argparse.Namespace) -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    for entry in _selected(args):
        tag = entry["tag"]
        src = src_in_samples(entry)
        if not src.exists():
            print(f"[{tag}] SKIP — acquire first (no {src.name})")
            continue
        doctype, _ = DOCTYPE[entry["doc"]]
        fixed, twinA, twinB = twin_paths(entry)
        csx = SCRATCH / f"twinsave_{tag}.csx"
        csx.write_text(
            _render(_TWINSAVE_CSX, TAG=tag, SRC=str(src), FIXED=str(fixed),
                    TWINA=str(twinA), TWINB=str(twinB), DOCTYPE=str(doctype)),
            encoding="utf-8",
        )
        print(f"[{tag}] twin-save …")
        rc = _run_combridge(args.combridge, args.session, csx, timeout=args.timeout)
        ok = twinA.exists() and twinB.exists()
        if ok:
            print(f"[{tag}] rc={rc}  A={twinA.stat().st_size:,}  B={twinB.stat().st_size:,}")
        else:
            print(f"[{tag}] rc={rc}  TWINS MISSING")
    return 0


# --- phase: analyze --------------------------------------------------------
def _analyze_pair(entry: dict) -> dict | None:
    _, twinA, twinB = twin_paths(entry)
    if not (twinA.exists() and twinB.exists()):
        return None
    da = swformat.read_document(twinA)
    db = swformat.read_document(twinB)
    sa, sb = da.streams(), db.streams()
    common = set(sa) & set(sb)
    differing = sorted(n for n in common if sa[n] != sb[n])
    # length deltas per differing stream
    len_deltas = {n: (len(sa[n]), len(sb[n])) for n in differing}
    length_unstable = [n for n in differing if len(sa[n]) != len(sb[n])]

    result = {
        "tag": entry["tag"], "doc": entry["doc"],
        "size_a": len(da.data), "size_b": len(db.data),
        "same_size": len(da.data) == len(db.data),
        "recon_a_ok": da.reconstruct() == da.data,
        "recon_b_ok": db.reconstruct() == db.data,
        "n_streams_common": len(common),
        "n_streams_identical": len(common) - len(differing),
        "n_streams_differing": len(differing),
        "differing_streams": differing,
        "length_unstable_streams": length_unstable,
        "len_deltas": len_deltas,
    }
    # Layer-0 byte entropy only meaningful when sizes match.
    if result["same_size"]:
        clusters = byte_diff_clusters(da.data, db.data)
        total = sum(c.length for c in clusters)
        result["byte_diff_total"] = total
        result["byte_diff_pct"] = 100.0 * total / len(da.data) if da.data else 0.0
        result["n_clusters"] = len(clusters)
        result["cluster_streams"] = sorted({lbl for _, lbl in locate_clusters(da, clusters)})
    return result


def cmd_analyze(args: argparse.Namespace) -> int:
    results = []
    for entry in _selected(args):
        r = _analyze_pair(entry)
        if r is None:
            print(f"[{entry['tag']}] no twins — skip")
            continue
        results.append(r)

    if not results:
        print("No analyzable pairs. Run acquire + twinsave first.")
        return 1

    # Aggregate: per-stream differ frequency across files where the stream exists.
    differ_count: dict[str, int] = defaultdict(int)
    exist_count: dict[str, int] = defaultdict(int)
    for r in results:
        _, twinA, _ = twin_paths(next(e for e in CORPUS if e["tag"] == r["tag"]))
        present = set(swformat.read_document(twinA).streams())
        for n in present:
            exist_count[n] += 1
        for n in r["differing_streams"]:
            differ_count[n] += 1

    always_differ = sorted(n for n in differ_count if differ_count[n] == exist_count[n])
    never_differ = sorted(n for n in exist_count if differ_count.get(n, 0) == 0)

    report = {
        "files": results,
        "aggregate": {
            "n_files": len(results),
            "all_recon_ok": all(r["recon_a_ok"] and r["recon_b_ok"] for r in results),
            "n_size_stable": sum(1 for r in results if r["same_size"]),
            "always_differ_streams": always_differ,
            "stream_differ_frequency": dict(sorted(
                differ_count.items(), key=lambda kv: -kv[1])),
            "n_never_differ_streams": len(never_differ),
        },
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Console summary.
    print("\n=== TWIN-SAVE BASELINE — SUMMARY ===")
    print(f"{'tag':<12}{'doc':<10}{'dSize':>8}  {'same':<5}{'recon':<6}"
          f"{'diff/comm':>11}  lenUnstable")
    for r in results:
        szdelta = r["size_b"] - r["size_a"]
        recon = "ok" if (r["recon_a_ok"] and r["recon_b_ok"]) else "FAIL"
        print(f"{r['tag']:<12}{r['doc']:<10}{szdelta:>8}  "
              f"{r['same_size']!s:<5}{recon:<6}"
              f"{r['n_streams_differing']}/{r['n_streams_common']:<8}  "
              f"{len(r['length_unstable_streams'])}")
    agg = report["aggregate"]
    print(f"\nfiles analyzed: {agg['n_files']}   size-stable: {agg['n_size_stable']}/{agg['n_files']}"
          f"   all reconstruct() ok: {agg['all_recon_ok']}")
    print(f"streams that ALWAYS differ (every file where present): {agg['always_differ_streams']}")
    print(f"\nfull report -> {REPORT_JSON}")
    return 0


def _selected(args: argparse.Namespace) -> list[dict]:
    if getattr(args, "only", None):
        only = set(args.only.split(","))
        return [e for e in CORPUS if e["tag"] in only]
    return CORPUS


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="M0.5 twin-save baseline orchestrator/analyzer")
    sub = p.add_subparsers(dest="phase", required=True)
    for name in ("acquire", "twinsave"):
        sp = sub.add_parser(name)
        sp.add_argument("--session", required=True, help="combridge session, e.g. pid:16804")
        sp.add_argument("--combridge", type=Path, default=DEFAULT_COMBRIDGE)
        sp.add_argument("--timeout", type=int, default=300)
        sp.add_argument("--only", help="comma-separated tags to limit to")
    sp = sub.add_parser("analyze")
    sp.add_argument("--only", help="comma-separated tags to limit to")

    args = p.parse_args(argv)
    return {"acquire": cmd_acquire, "twinsave": cmd_twinsave, "analyze": cmd_analyze}[args.phase](args)


if __name__ == "__main__":
    sys.exit(main())
