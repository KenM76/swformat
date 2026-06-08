"""Pure-Python parser for the SolidWorks 2015+ "modern" file format.

Based on the openswx (github.com/schwitters/openswx) C++20 reference
parser. Walks the `14 00 06 00 08 00` chunk format
from raw file bytes -- no SOLIDWORKS running, no DocMgr license, no
OLE.

What this gives us:
- Detect modern vs OLE2 vs ZIP/OPC SW files.
- Iterate all chunks; for each: stream name (ROL-decoded), payload
  (raw-DEFLATE decompressed if inline) or marker (no payload).
- Read every documented stream: docProps/*.xml, SheetPreviews/
  SheetNames, swXmlContents/KeyWords, Contents/CMgrHdr2, etc.

This module deliberately stays at the "raw stream" level. Higher-
level extraction (e.g. drawing-specific sheet/view structure) lives
in scan_drawing.py.

Refs: the breakthrough lesson has the format spec + an inline
Python sketch which this implements; chunk-layout offsets:

    si+0x00  val_a            4 bytes, file-specific (ignored)
    si+0x04  0x14             fixed separator byte
    si+0x05  00 06 00 08 00   5 core bytes (with 0x14 = 6-byte marker)
    si+0x0a  section_type     1 byte: 0xDF=TOC 0xFD=data 0x1C=mini
    si+0x0b  suffix           3 file-specific bytes
    si+0x0e  f1               uint32 LE -- >=65536 for inline chunks
    si+0x12  csz              uint32 LE -- compressed data size
    si+0x16  usz              uint32 LE -- uncompressed data size
    si+0x1a  nsz              uint32 LE -- stream name length
    si+0x1e  name[nsz]        ROL-encoded stream name (UTF-8 after decode)
    si+0x1e+nsz  data[csz]    raw deflate (inline chunks only)

Sanity caps (from openswx): nsz <= 512, csz <= 64 MiB.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Iterator

MARKER = b"\x14\x00\x06\x00\x08\x00"
OLE2_SIGNATURE = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
ZIP_SIGNATURE = b"\x50\x4B\x03\x04"

# header probe for modern-format detection (first 64 bytes)
_MODERN_HEAD_PROBE = b"\x14\x00\x06\x00\x08\x00"

_MAX_NAME_LEN = 512
_MAX_CSZ = 64 * 1024 * 1024


class SwxError(Exception):
    pass


def detect_format(data: bytes) -> str:
    """Return 'modern' | 'ole2' | 'zip' | 'unknown'."""
    if data[:8] == OLE2_SIGNATURE:
        return "ole2"
    if data[:4] == ZIP_SIGNATURE:
        return "zip"
    if _MODERN_HEAD_PROBE in data[:64]:
        return "modern"
    return "unknown"


def _rol_byte(b: int, k: int) -> int:
    k &= 7
    if k == 0:
        return b
    return ((b << k) | (b >> (8 - k))) & 0xFF


def _rol_decode(buf: bytes, k: int) -> str:
    return bytes(_rol_byte(b, k) for b in buf).decode("utf-8", errors="replace")


class Chunk:
    __slots__ = (
        "offset",
        "section_type",
        "f1",
        "csz",
        "usz",
        "nsz",
        "name",
        "_data_offset",
        "_raw",
    )

    def __init__(
        self,
        offset: int,
        section_type: int,
        f1: int,
        csz: int,
        usz: int,
        nsz: int,
        name: str,
        data_offset: int,
        raw: bytes,
    ):
        self.offset = offset
        self.section_type = section_type
        self.f1 = f1
        self.csz = csz
        self.usz = usz
        self.nsz = nsz
        self.name = name
        self._data_offset = data_offset
        self._raw = raw

    @property
    def is_inline(self) -> bool:
        return self.f1 >= 65536

    def payload_compressed(self) -> bytes | None:
        """Return the raw-DEFLATE-compressed payload bytes (or None)."""
        if not self.is_inline or self.csz == 0:
            return None
        end = self._data_offset + self.csz
        if end > len(self._raw):
            return None
        return self._raw[self._data_offset : end]

    def payload(self) -> bytes | None:
        """Decompressed payload (raw DEFLATE, wbits=-15). None on no-payload
        or decompression failure."""
        comp = self.payload_compressed()
        if comp is None:
            return None
        try:
            return zlib.decompressobj(-15).decompress(comp)
        except zlib.error:
            return None


def iter_chunks(data: bytes) -> Iterator[Chunk]:
    """Yield every chunk found by scanning for the 6-byte marker.

    Skips false positives (any header that fails the sanity caps,
    name decode, or printable-ASCII check) -- openswx does the same.
    """
    if len(data) < 8:
        return
    key = data[7]
    pos = 0
    n = len(data)
    while True:
        m = data.find(MARKER, pos)
        if m < 0 or m < 4:
            return
        si = m - 4
        if si + 0x1E > n:
            pos = m + 1
            continue
        section_type = data[si + 0x0A]
        f1 = struct.unpack_from("<I", data, si + 0x0E)[0]
        csz = struct.unpack_from("<I", data, si + 0x12)[0]
        usz = struct.unpack_from("<I", data, si + 0x16)[0]
        nsz = struct.unpack_from("<I", data, si + 0x1A)[0]
        if nsz > _MAX_NAME_LEN or csz > _MAX_CSZ:
            pos = m + 1
            continue
        name_start = si + 0x1E
        name_end = name_start + nsz
        if name_end > n:
            pos = m + 1
            continue
        name = _rol_decode(data[name_start:name_end], key)
        if not name or any((c < " " or c > "~") for c in name):
            pos = m + 1
            continue
        data_offset = name_end
        chunk = Chunk(si, section_type, f1, csz, usz, nsz, name, data_offset, data)
        is_inline = f1 >= 65536
        if is_inline and csz > 0:
            d_end = data_offset + csz
            if d_end <= n:
                yield chunk
                pos = d_end
                continue
        yield chunk
        pos = m + len(MARKER)


def read_file(path: str | Path) -> tuple[str, dict[str, bytes]]:
    """Read a SolidWorks file -> (format, streams).

    streams: dict[name -> decompressed bytes]. First occurrence wins
    (setdefault) to match openswx's behaviour on duplicate names.

    For non-modern formats: returns the format tag and an empty
    streams dict. (OLE2 / ZIP parsers not implemented here.)
    """
    p = Path(path)
    data = p.read_bytes()
    fmt = detect_format(data)
    if fmt != "modern":
        return fmt, {}
    streams: dict[str, bytes] = {}
    for ch in iter_chunks(data):
        if not ch.is_inline:
            continue
        payload = ch.payload()
        if payload is None:
            continue
        streams.setdefault(ch.name, payload)
    return fmt, streams


def doc_version(streams: dict[str, bytes]) -> int | None:
    """Largest `_MO_VERSION_NNNNN/...` value found across stream names
    -- the document's effective format version per openswx."""
    best = -1
    for name in streams:
        if not name.startswith("_MO_VERSION_"):
            continue
        tail = name[len("_MO_VERSION_") :]
        head = tail.split("/", 1)[0]
        try:
            v = int(head)
        except ValueError:
            continue
        if v > best:
            best = v
    return best if best >= 0 else None


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: swx_reader.py <.SLDDRW|.SLDPRT|.SLDASM>")
        sys.exit(2)
    fmt, streams = read_file(sys.argv[1])
    print(f"format: {fmt}")
    print(f"version: {doc_version(streams)}")
    print(f"streams: {len(streams)}")
    for nm in sorted(streams):
        print(f"  {len(streams[nm]):>10}  {nm}")
