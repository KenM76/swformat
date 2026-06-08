"""Tests for ``swformat.api.references`` — drawing → referenced-models extraction.

Two tiers (the project's "committable-synthetic first" convention):

1. **Synthetic byte-layout tests** (always run, no fixtures): build a blob of
   MFC ``CStringW`` values by hand (model paths + a sheet-format template token +
   a non-path) and assert the scan keeps only the model paths, filters the
   template token, de-dupes, and computes basenames separator-correctly.
2. **Fixture cross-check** (skip if absent): on the SW-generated synthetic
   ``drawing_partview`` (a drawing with a view of ``weldment_min``), assert the
   referenced-models list contains that part. The fixture is not git-tracked, so
   the test skips cleanly in CI.

Pure Python, no SOLIDWORKS. See :mod:`swformat.api.references`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from swformat.api.references import (  # noqa: E402
    PurePathBasename,
    _looks_like_model_path,
    iter_referenced_model_strings,
    read_referenced_models,
)
from swformat.carchive.cstring import encode_cstring  # noqa: E402


def test_looks_like_model_path() -> None:
    assert _looks_like_model_path("Part13.SLDPRT")
    assert _looks_like_model_path(r"C:\models\Sub.SLDASM")
    assert _looks_like_model_path("a.sldprt")            # case-insensitive
    assert _looks_like_model_path("x.SLDDRW")
    assert not _looks_like_model_path("notes.txt")       # wrong extension
    assert not _looks_like_model_path("")                # empty
    # sheet-format template token that ends in an extension but is NOT a path
    assert not _looks_like_model_path('$PRP:"SW-File Name(File Name)".SLDDRW')


def test_basename_separator_correct() -> None:
    # Windows separators must split even off-Windows (SW stores Windows paths).
    assert PurePathBasename(r"C:\a\b\Part13.SLDPRT") == "Part13.SLDPRT"
    assert PurePathBasename("sub/dir/Widget.SLDASM") == "Widget.SLDASM"
    assert PurePathBasename("Bare.SLDPRT") == "Bare.SLDPRT"


def test_scan_extracts_model_paths_and_filters() -> None:
    blob = (b"xx" + encode_cstring(r"C:\models\Part13.SLDPRT")
            + b"yy" + encode_cstring("sub\\Widget.SLDASM")
            + b"zz" + encode_cstring('$PRP:"x".SLDDRW')       # template token -> filtered
            + encode_cstring("notapath.txt"))                  # wrong ext -> filtered
    got = list(iter_referenced_model_strings(blob))
    assert got == [r"C:\models\Part13.SLDPRT", "sub\\Widget.SLDASM"]


def test_read_referenced_models_dedup_sorted(tmp_path: Path, monkeypatch) -> None:
    # A model referenced twice (e.g. by two views) appears once; result is sorted.
    blob = (encode_cstring(r"C:\m\Beta.SLDPRT")
            + encode_cstring(r"C:\m\Alpha.SLDASM")
            + encode_cstring(r"C:\m\Beta.SLDPRT"))   # duplicate
    # drive read_referenced_models via a fake document exposing the Definition.
    import swformat.api.references as refs

    class _Doc:
        def streams(self):
            return {"Contents/Definition": blob}

    monkeypatch.setattr(refs, "read_document", lambda _p: _Doc())
    out = read_referenced_models("anything")
    assert out == [r"C:\m\Alpha.SLDASM", r"C:\m\Beta.SLDPRT"]
    base = read_referenced_models("anything", basenames=True)
    assert base == ["Alpha.SLDASM", "Beta.SLDPRT"]


def test_no_definition_returns_empty(monkeypatch) -> None:
    import swformat.api.references as refs

    class _Doc:
        def streams(self):
            return {}                                  # no Contents/Definition

    monkeypatch.setattr(refs, "read_document", lambda _p: _Doc())
    assert read_referenced_models("anything") == []


def test_drawing_partview_fixture_references_weldment_min() -> None:
    """Cross-check on the synthetic drawing_partview (a view of weldment_min);
    skips if the (gitignored, regenerable) fixture is absent."""
    path = ROOT / "research" / "empirical_findings" / "m4_drawing_sheets" / "drawing_partview.SLDDRW"
    if not path.exists():
        pytest.skip("drawing_partview fixture not present (regen via gen_drawing_partview.csx)")
    refs = read_referenced_models(path, basenames=True)
    assert any(r.lower() == "weldment_min.sldprt" for r in refs), refs
