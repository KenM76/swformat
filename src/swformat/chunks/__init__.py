"""Layer 1 — the chunk walker.

Re-exports the public walker API so callers can write
``from swformat.chunks import iter_records`` etc.
"""
from __future__ import annotations

from swformat.chunks.walker import (
    MARKER,
    detect_format,
    doc_version,
    iter_chunks,
    iter_records,
    rol_decode,
)

__all__ = [
    "MARKER",
    "detect_format",
    "doc_version",
    "iter_chunks",
    "iter_records",
    "rol_decode",
]
