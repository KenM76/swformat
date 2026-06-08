"""Layer-spanning I/O: file entry points (reader + writer)."""
from __future__ import annotations

from swformat.io.reader import read_document, read_file
from swformat.io.writer import (
    force_redeflate_all,
    serialize,
    serialize_with_toc,
    set_stream_payload,
    write,
    write_with_toc,
)

__all__ = [
    "force_redeflate_all",
    "read_document",
    "read_file",
    "serialize",
    "serialize_with_toc",
    "set_stream_payload",
    "write",
    "write_with_toc",
]
