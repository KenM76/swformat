"""SWFormat — pure-Python read/modify/write for SOLIDWORKS native files.

Reverse-engineering effort targeting .sldprt/.sldasm/.slddrw without a
SOLIDWORKS install. See ``docs/ARCHITECTURE.md`` for the five-layer model
(bytes -> chunks -> streams -> CArchive -> API) and ``docs/ROADMAP.md``
for milestone status.

PUBLIC API (M0)
---------------
- :func:`read_document` — parse a file into a byte-exact
  :class:`Document` (ordered chunk + gap records).
- :func:`read_document_bytes` — same, from an in-memory byte image
  (no temp file; used by the TOC writer's re-parse).
- :func:`read_file` — legacy ``(format, {stream: bytes})`` shim.
- :func:`detect_format`, :func:`iter_records`, :func:`iter_chunks`,
  :func:`doc_version`, :func:`rol_decode` — Layer-1 primitives.
- :class:`Document`, :class:`Chunk`, :class:`Gap` — the core data shapes.

The standalone module ``swformat._imported_swx_reader_v0`` is preserved
verbatim as the historical openswx-derived reference and is NOT the
maintained code path.
"""
from __future__ import annotations

from swformat.chunks.walker import (
    detect_format,
    doc_version,
    iter_chunks,
    iter_records,
    rol_decode,
)
from swformat.io.reader import read_document, read_document_bytes, read_file
from swformat.types import Chunk, Document, Gap

__all__ = [
    "Chunk",
    "Document",
    "Gap",
    "detect_format",
    "doc_version",
    "iter_chunks",
    "iter_records",
    "read_document",
    "read_document_bytes",
    "read_file",
    "rol_decode",
]
