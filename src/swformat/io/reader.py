"""File entry points: bytes/path -> :class:`~swformat.types.Document`.

Two functions:

- :func:`read_document` — the productionised reader. Returns a full
  :class:`Document` (ordered chunk+gap records, byte-exact) for modern
  files, or an empty document carrying the detected format for everything
  else.
- :func:`read_file` — backward-compatible shim returning
  ``(format, {stream_name: decompressed_bytes})``, matching the imported
  ``_imported_swx_reader_v0.read_file`` so existing callers
  (``proof_of_life.py``, the twin-save template) keep working unchanged.
"""
from __future__ import annotations

from pathlib import Path

from swformat.chunks.walker import detect_format, iter_records
from swformat.types import Document


def read_document_bytes(data: bytes) -> Document:
    """Parse an in-memory SOLIDWORKS file image into a :class:`Document`.

    Same semantics as :func:`read_document` but takes the raw bytes directly,
    so callers that already hold the bytes (e.g. the TOC writer re-parsing its
    own freshly serialized output) need not round-trip through a temp file.
    """
    fmt = detect_format(data)
    if fmt != "modern":
        # Non-modern: keep bytes faithful (single gap) but decode nothing.
        from swformat.types import Gap

        return Document(fmt=fmt, data=data, items=[Gap(0, data)] if data else [])
    return Document(fmt=fmt, data=data, items=list(iter_records(data)))


def read_document(path: str | Path) -> Document:
    """Parse a SOLIDWORKS file into a :class:`Document`.

    For ``modern`` files the document's ``items`` cover every byte
    (chunks + gaps). For ``ole2``/``opc``/``unknown`` files the document
    carries the detected format and the raw bytes as a single gap (so
    ``reconstruct()`` still round-trips), but no chunks are decoded.
    """
    return read_document_bytes(Path(path).read_bytes())


def read_file(path: str | Path) -> tuple[str, dict[str, bytes]]:
    """Backward-compatible reader: ``(format, streams_dict)``.

    ``streams_dict`` maps stream name -> decompressed bytes, first
    occurrence winning (matching the imported reader). Non-modern files
    return ``(fmt, {})``.
    """
    doc = read_document(path)
    if doc.fmt != "modern":
        return doc.fmt, {}
    return doc.fmt, doc.streams()
