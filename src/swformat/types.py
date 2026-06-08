"""Core data shapes for the SWFormat chunk layer (Layer 1).

This module defines the dataclasses that the chunk walker
(`swformat.chunks.walker`) produces and that the writer
(`swformat.io.writer`, M1) consumes. They are deliberately small,
explicit, and round-trip-faithful.

WHY THESE TYPES EXIST
---------------------
A SOLIDWORKS "modern" file (2015+) is a flat sequence of length-prefixed,
raw-DEFLATE-compressed *chunks* separated by the 6-byte marker
``14 00 06 00 08 00``, with non-chunk regions (the leading file header,
inter-chunk padding, and a trailing table-of-contents region) in between.
To read→modify→write such a file *without losing a single byte*, we model
the file as an ordered list of two record kinds:

- :class:`Chunk` — a recognised, validated chunk with a decoded stream
  name and (for inline chunks) a compressed payload.
- :class:`Gap`   — any run of bytes that is **not** part of a recognised
  chunk: the leading header, padding, the trailing TOC, etc.

THE TWO LOAD-BEARING INVARIANTS
-------------------------------
1. **No orphan bytes.** Concatenating the byte spans of every record in
   :attr:`Document.items`, in file order, reproduces the original file
   *exactly*. There is no byte of the input that is not owned by exactly
   one record. This is what makes a faithful writer possible and is
   enforced by ``test_no_orphan_bytes`` (Layer 1).

2. **Lazy round-trip.** A :class:`Chunk` carries its *original* header
   bytes and *original* compressed payload verbatim. Unless a consumer
   explicitly sets :attr:`Chunk.modified_payload`, the writer re-emits
   those original bytes byte-for-byte — it never re-deflates. This
   sidesteps DEFLATE's encoder non-determinism (the same logical bytes
   compress to different output across zlib versions / levels), which
   would otherwise make byte-equal round-trip impossible.

See ``docs/ARCHITECTURE.md`` for the five-layer model and
``docs/REVERSE_ENGINEERING.md`` §4 for the tail-bytes discipline.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Format detection result type
# ---------------------------------------------------------------------------
# 'modern' : the 2015+ chunk format this package decodes (the focus of M0-M5)
# 'ole2'   : pre-2015 OLE2 compound-document format (not decoded here)
# 'opc'    : OPC/ZIP container (rare; some SW-adjacent files) — not decoded
# 'unknown': none of the signatures matched
FileFormat = Literal["modern", "ole2", "opc", "unknown"]


@dataclass(slots=True)
class Chunk:
    """One recognised chunk in a modern SOLIDWORKS file.

    A chunk's on-disk layout (offsets relative to ``offset``, the chunk
    start ``si``; see ``docs`` and the openswx breakthrough lesson):

        +0x00  val_a            uint32 LE, file-specific (not interpreted)
        +0x04  14 00 06 00 08 00   the 6-byte marker
        +0x0a  section_type     1 byte: 0xDF=TOC 0xFD=data 0x1C=mini
        +0x0b  suffix           3 file-specific bytes
        +0x0e  f1               uint32 LE; >=65536 ("inline") => payload follows
        +0x12  csz              uint32 LE; compressed payload size
        +0x16  usz              uint32 LE; uncompressed payload size
        +0x1a  nsz              uint32 LE; stream-name length
        +0x1e  name[nsz]        ROL-encoded stream name (UTF-8 after decode)
        +0x1e+nsz  data[csz]    raw-DEFLATE payload (inline chunks only)

    The chunk *owns* the byte span ``[offset, offset + len(self))``. For
    an inline data chunk that is ``header_bytes + original_compressed``;
    for a non-inline / marker chunk it is just ``header_bytes`` (no
    payload). Reconstructing that span verbatim is the lazy round-trip.

    Attributes:
        offset: File offset of the chunk start (``si`` = marker pos - 4).
        section_type: The byte at +0x0a. Known: 0xDF TOC, 0xFD data,
            0x1C "mini" (purpose not fully understood — openswx flags it).
        f1: The uint32 at +0x0e. ``f1 >= 65536`` marks an inline chunk
            that carries a compressed payload.
        csz: Compressed payload size (bytes of raw DEFLATE).
        usz: Declared uncompressed size. Empirically equals the actual
            inflated length for every inline chunk across the corpus
            (156/156), so it is a reliable integrity signal — but
            :meth:`decompressed` does NOT currently enforce it (it relies on
            zlib's own error detection), to avoid silently dropping streams
            on an untested SW version whose usz semantics might differ. A
            caller wanting strict validation can compare
            ``len(chunk.decompressed()) == chunk.usz`` itself.
        nsz: Stream-name length in bytes.
        name: ROL-decoded UTF-8 stream name (e.g. ``"docProps/custom.xml"``).
        header_bytes: The verbatim bytes ``[offset, data_offset)`` — the
            fixed header plus the encoded name. Re-emitted as-is on write.
        original_compressed: The verbatim raw-DEFLATE payload bytes, or
            ``b""`` for a non-inline / zero-length chunk. Re-emitted as-is
            on write unless ``modified_payload`` is set.
        modified_payload: If not ``None``, the consumer has changed this
            stream's *logical* (decompressed) content; the writer must
            re-deflate this and rebuild ``csz``/``usz`` in the header.
            ``None`` (the default) means "unchanged — write verbatim".
    """

    offset: int
    section_type: int
    f1: int
    csz: int
    usz: int
    nsz: int
    name: str
    header_bytes: bytes
    original_compressed: bytes
    modified_payload: bytes | None = None

    @property
    def is_inline(self) -> bool:
        """True if this chunk carries an inline compressed payload."""
        return self.f1 >= 65536 and self.csz > 0

    @property
    def data_offset(self) -> int:
        """File offset where the payload begins (== end of header_bytes)."""
        return self.offset + len(self.header_bytes)

    @property
    def end(self) -> int:
        """File offset one past the last byte this chunk owns."""
        return self.data_offset + len(self.original_compressed)

    def __len__(self) -> int:
        """Number of bytes this chunk owns on disk (header + payload)."""
        return len(self.header_bytes) + len(self.original_compressed)

    def raw_bytes(self) -> bytes:
        """The verbatim on-disk span of this chunk (for lazy round-trip)."""
        return self.header_bytes + self.original_compressed

    def decompressed(self) -> bytes | None:
        """Inflate the payload (raw DEFLATE, ``wbits=-15``).

        Returns ``None`` for non-inline chunks or on any inflate error
        (a corrupt or misidentified chunk). Callers that need the logical
        stream content use this; callers doing lazy round-trip never do.

        Does not enforce ``len(result) == usz`` (see the ``usz`` attribute
        note); zlib's error detection is the guard against corrupt payloads.
        """
        if not self.is_inline:
            return None
        try:
            return zlib.decompressobj(-15).decompress(self.original_compressed)
        except zlib.error:
            return None


@dataclass(slots=True)
class Gap:
    """A run of bytes not owned by any recognised chunk.

    Gaps capture the leading file header (including the ROL key at byte
    7), inter-chunk padding, and the trailing TOC region. They are always
    written back verbatim — we do not (yet) interpret their contents at
    Layer 1. The Gap record is what makes the no-orphan-bytes invariant
    hold by construction.
    """

    offset: int
    raw_bytes: bytes

    @property
    def end(self) -> int:
        return self.offset + len(self.raw_bytes)

    def __len__(self) -> int:
        return len(self.raw_bytes)


# A file is an ordered sequence of these two record kinds.
Record = Chunk | Gap


@dataclass(slots=True)
class Document:
    """A parsed modern SOLIDWORKS file: ordered records + conveniences.

    Attributes:
        fmt: Detected format. Only ``"modern"`` files have a populated
            ``items`` list; other formats parse to an empty document.
        data: The original file bytes (kept so the writer and the
            no-orphan-bytes check can compare against ground truth).
        items: Ordered list of :class:`Chunk` / :class:`Gap` covering
            every byte of ``data`` exactly once, in file order.
    """

    fmt: FileFormat
    data: bytes
    items: list[Record] = field(default_factory=list)

    @property
    def chunks(self) -> list[Chunk]:
        """All Chunk records in file order."""
        return [it for it in self.items if isinstance(it, Chunk)]

    @property
    def gaps(self) -> list[Gap]:
        """All Gap records in file order."""
        return [it for it in self.items if isinstance(it, Gap)]

    def streams(self) -> dict[str, bytes]:
        """Map stream-name -> decompressed bytes (first occurrence wins).

        Mirrors the imported reader's ``read_file`` semantics so existing
        callers (proof_of_life, etc.) keep working. Only inline chunks
        that inflate successfully contribute.
        """
        out: dict[str, bytes] = {}
        for ch in self.chunks:
            if not ch.is_inline:
                continue
            payload = ch.decompressed()
            if payload is None:
                continue
            out.setdefault(ch.name, payload)
        return out

    def locate(self, offset: int) -> Record | None:
        """Return the record (Chunk or Gap) owning ``offset``, or None.

        Used to map a raw byte offset (e.g. a twin-save diff position) back
        to the stream/region it falls in. Records are contiguous and in
        file order, so the first containing record is the answer.
        """
        for it in self.items:
            if it.offset <= offset < it.end:
                return it
        return None

    def reconstruct(self) -> bytes:
        """Reassemble the file from the records (lazy round-trip).

        For an *unmodified* document this MUST equal :attr:`data` exactly
        — that equality is the no-orphan-bytes invariant. Used by the
        Layer-1 test and by M0.5's lazy-roundtrip simulation. The M1
        writer is a superset of this that also honours
        :attr:`Chunk.modified_payload`.
        """
        return b"".join(
            it.raw_bytes() if isinstance(it, Chunk) else it.raw_bytes
            for it in self.items
        )
