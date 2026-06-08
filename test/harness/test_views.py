"""Tests for ``swformat.api.views`` — per-view model/sheet inventory."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from swformat.api.views import (  # noqa: E402
    DrawingView,
    parse_view_list,
    read_views,
)

_VAL = ("3:@@EXPLODE@0@" r"C:\eng\example-assembly.SLDASM" "@Drawing View13@Sheet-1"
        "@EXPLODE@0@" r"C:\eng\example-assembly.SLDASM" "@Drawing View14@Sheet-1"
        "@Default@0@" r"sub\example-part.SLDPRT" "@Drawing View162@18")


def test_parse_view_list() -> None:
    views = parse_view_list(_VAL)
    assert len(views) == 3
    assert views[0] == DrawingView("Drawing View13", r"C:\eng\example-assembly.SLDASM", "Sheet-1")
    assert views[2].name == "Drawing View162" and views[2].sheet == "18"
    assert views[2].model_basename == "example-part.SLDPRT"


def test_parse_empty() -> None:
    assert parse_view_list("") == []
    assert parse_view_list("no models here") == []


def test_read_views_from_xml(monkeypatch) -> None:
    import swformat.api.views as v
    xml = (b'<Properties xmlns="x"><propertySection>'
           b'<customProperty name="SW-Config-Model-View-Sheet-List">'
           b'<lpwstr>' + _VAL.encode("ascii") + b'</lpwstr>'
           b'</customProperty></propertySection></Properties>')

    class _Doc:
        def streams(self):
            return {"docProps/ISolidWorksInformation.xml": xml}

    monkeypatch.setattr(v, "read_document", lambda _p: _Doc())
    views = read_views("d")
    assert [x.name for x in views] == ["Drawing View13", "Drawing View14", "Drawing View162"]


def test_no_stream(monkeypatch) -> None:
    import swformat.api.views as v
    monkeypatch.setattr(v, "read_document", lambda _p: type("D", (), {"streams": lambda s: {}})())
    assert read_views("d") == []


def test_real_drawing_views_if_present() -> None:
    path = (ROOT / "research" / "empirical_findings" / "twin_save_baseline" /
            "samples" / "drw_10" / "example-drawing.SLDDRW")
    if not path.exists():
        pytest.skip("real drawing fixture not present")
    views = read_views(path)
    assert len(views) >= 10
    assert all(v.name and v.model and v.sheet for v in views)
