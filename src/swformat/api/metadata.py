"""``swformat.api.metadata`` — one-call structured metadata for a drawing.

Bundles the individual real-file readers (sheets, referenced models, tables/BOM,
text annotations) into a single JSON-serialisable dict, so a corpus/training
pipeline can extract everything it needs from a `.SLDDRW` in one call **without
SOLIDWORKS**. This is the convenience capstone over:

- :func:`swformat.api.sheets.read_sheets` / ``sheet_count`` — sheet names + count.
- :func:`swformat.api.references.read_referenced_models` — the part/assembly models
  the drawing depicts.
- :func:`swformat.api.tables.read_tables` — BOM / Revision / … tables as rows.
- :func:`swformat.api.annotations.read_annotation_text` — notes / title-block text.

(Per-sheet rendered PNG previews are large binaries, extracted to files via
:func:`swformat.api.sheets.extract_sheet_previews` — referenced here only by count,
not inlined.)

Every value is plain JSON types (str / int / list / dict), so
``json.dumps(read_drawing_metadata(path))`` just works. Each section degrades to
empty/zero independently (a part, or a drawing missing one stream, still returns a
well-formed dict). No SOLIDWORKS required; real-file capable.
"""
from __future__ import annotations

from pathlib import Path

from swformat.api.annotations import read_annotation_text
from swformat.api.dimensions import read_dimension_values
from swformat.api.keywords import keyword_index_counts
from swformat.api.references import read_referenced_models
from swformat.api.sheets import (
    _sheet_image_streams,
    read_sheet_formats,
    read_sheets,
    sheet_count,
)
from swformat.api.tables import read_tables
from swformat.api.views import read_views
from swformat.io.reader import read_document

_EXT_DOC_TYPE = {".sldprt": "part", ".sldasm": "assembly", ".slddrw": "drawing"}


def detect_doc_type(path: str | Path, streams: dict | None = None) -> str:
    """Return ``"part"`` / ``"assembly"`` / ``"drawing"`` / ``"unknown"``.

    Primary signal is the file EXTENSION (reliable for SOLIDWORKS files). Falls back
    to a content check when the extension is unhelpful: ``Contents/Definition`` (the
    drawing CArchive) => drawing; a ``swXmlContents/MATERIALTREE`` or
    ``Contents/Config-0-ModelHeader`` => part/assembly (defaults to part, since the
    two are not cheaply distinguishable by content)."""
    ext = Path(path).suffix.lower()
    if ext in _EXT_DOC_TYPE:
        return _EXT_DOC_TYPE[ext]
    if streams is None:
        streams = read_document(path).streams()
    if "Contents/Definition" in streams:
        return "drawing"
    if "swXmlContents/MATERIALTREE" in streams or "Contents/Config-0-ModelHeader" in streams:
        return "part"
    return "unknown"


def read_drawing_metadata(path: str | Path, *, include_table_rows: bool = True) -> dict:
    """Return a JSON-serialisable metadata dict for a drawing.

    Keys:
        ``file``               — the file name (basename).
        ``sheet_count``        — number of sheets.
        ``sheet_names``        — list of sheet names (file order).
        ``sheet_formats``      — per-sheet ``{name, width, height, scale}`` (paper
                                 size in metres + scale ratio like ``"1:32"``).
        ``sheet_preview_count``— number of ``Images/Sheet_N`` PNG previews available
                                 (extract them with ``extract_sheet_previews``).
        ``referenced_models``  — sorted unique model paths the drawing depicts.
        ``views``              — per-view inventory: ``{view, model, sheet}`` records
                                 (which view projects which model on which sheet).
        ``tables``             — list of ``{type, name, num_rows, num_cols[, rows]}``
                                 (rows included unless ``include_table_rows=False``).
        ``bom_count``          — number of BOM tables (convenience).
        ``annotation_text``    — de-duplicated notes / title-block / callout strings.
        ``dimensions``         — displayed dimension values ``{name, value}`` (the
                                 reliable real-file dimension reader, via KeyWords).
        ``keyword_index_counts``— ``{entity_type: count}`` overview of the full
                                 keyword index (dimensions/notes/views/features/…).

    Args:
        path:               the drawing file.
        include_table_rows: if False, omit the (potentially large) ``rows`` arrays
                            from ``tables`` — keep just per-table summaries.

    All sections degrade independently to empty/zero for a non-drawing or a drawing
    missing a given stream. No SOLIDWORKS required.
    """
    path = Path(path)
    streams = read_document(path).streams()

    tables = read_tables(path)
    tables_out = []
    for t in tables:
        entry = {"type": t.table_type, "name": t.name,
                 "num_rows": t.num_rows, "num_cols": t.num_cols}
        if include_table_rows:
            entry["rows"] = t.rows
        tables_out.append(entry)

    return {
        "file": path.name,
        "doc_type": detect_doc_type(path, streams),
        "sheet_count": sheet_count(path),
        "sheet_names": read_sheets(path),
        "sheet_formats": [{"name": f.name, "width": f.width, "height": f.height,
                           "scale": f.scale} for f in read_sheet_formats(path)],
        "sheet_preview_count": len(_sheet_image_streams(streams)),
        "referenced_models": read_referenced_models(path),
        "views": [{"view": v.name, "model": v.model, "sheet": v.sheet}
                  for v in read_views(path)],
        "tables": tables_out,
        "bom_count": sum(1 for t in tables if t.table_type.upper() == "BOM"),
        "annotation_text": read_annotation_text(path),
        "dimensions": [{"name": d.name, "value": d.value}
                       for d in read_dimension_values(path)],
        "keyword_index_counts": keyword_index_counts(path),
    }


def _safe_properties(path: str | Path) -> dict:
    """``read_properties`` but never raising (returns ``{}`` on any failure) — a
    non-part/odd file should degrade, not crash a corpus scan."""
    from swformat.api.properties import read_properties
    try:
        props = read_properties(path)
        return props if isinstance(props, dict) else {}
    except Exception:
        return {}


def _safe_configurations(path: str | Path) -> list[str]:
    from swformat.api.configurations import read_configurations
    try:
        cfgs = read_configurations(path)
        return list(cfgs) if cfgs else []
    except Exception:
        return []


def read_model_metadata(path: str | Path) -> dict:
    """Return a JSON-serialisable metadata dict for a PART or ASSEMBLY model.

    The model analogue of :func:`read_drawing_metadata`. Composes the readers that
    apply to a part/assembly:

        ``file``                — the file name.
        ``doc_type``            — ``"part"`` / ``"assembly"`` (or detected).
        ``properties``          — custom properties ``{name: value}`` (PARTNO,
                                  DESCRIPTION, MATERIAL, WEIGHT, REVISION, …) from
                                  ``Contents/CusProps`` — clean, resolved values.
        ``material``            — convenience: the ``MATERIAL`` property (or ``""``).
        ``configurations``      — configuration names (e.g. ``["Default"]``).
        ``dimensions``          — the model's feature dimension values
                                  ``{name, value}`` (via the KeyWords index).
        ``keyword_index_counts``— ``{entity_type: count}`` (Feature / Sketch / … ).

    Each section degrades independently to empty. No SOLIDWORKS required; real-file
    capable (verified on real production parts).
    """
    path = Path(path)
    streams = read_document(path).streams()
    props = _safe_properties(path)
    return {
        "file": path.name,
        "doc_type": detect_doc_type(path, streams),
        "properties": props,
        "material": props.get("MATERIAL", ""),
        "configurations": _safe_configurations(path),
        "dimensions": [{"name": d.name, "value": d.value}
                       for d in read_dimension_values(path)],
        "keyword_index_counts": keyword_index_counts(path),
    }


def read_metadata(path: str | Path, **kwargs) -> dict:
    """Return metadata for ANY SOLIDWORKS file — dispatches on document type:
    a drawing → :func:`read_drawing_metadata`; a part/assembly →
    :func:`read_model_metadata`. The result always carries a ``doc_type`` key.

    The single entry point for a mixed corpus (drawings + parts + assemblies):
    ``read_metadata(p)`` just works regardless of file kind. ``include_table_rows``
    is forwarded to the drawing reader.
    """
    if detect_doc_type(path) == "drawing":
        return read_drawing_metadata(path, **kwargs)
    return read_model_metadata(path)
