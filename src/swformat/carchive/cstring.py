"""MFC CArchive CString primitive (the Unicode form SOLIDWORKS uses).

Empirically (see ``research/empirical_findings/cusprops_carchive/log.md``),
strings in the SW CArchive streams are written as:

    FF FE FF  <len:u8>  <UTF-16LE bytes × len>

- ``FF`` is MFC's "length doesn't fit in a byte, a tag follows" marker.
- ``FE FF`` is the little-endian ``0xFFFE`` Unicode-string tag.
- ``<len:u8>`` is the **UTF-16 code-unit count** — i.e. the number of
  ``wchar_t`` in MFC's ``CStringW``, which is ``len(utf16le_bytes) // 2``, NOT
  the Python ``str`` length. For Basic-Multilingual-Plane text the two are
  equal, but an astral character (emoji, CJK Ext-B, …) is one code point yet
  **two** UTF-16 code units (a surrogate pair), so the distinction matters.
  This codepath covers counts < 255 (all custom-property names/values seen);
  the ``0xFF`` escape to a wider count is not yet needed.

This module is read-only for now; the writer (:func:`encode_cstring`) is
provided too since it is trivial and symmetric, for the future M5.1 writer.
"""
from __future__ import annotations

CSTRING_PREFIX = b"\xff\xfe\xff"


def read_cstring(data: bytes, offset: int) -> tuple[str, int] | None:
    """Read a CString at ``offset``. Return ``(text, next_offset)`` or None.

    None if the bytes at ``offset`` are not a CString prefix (or are
    truncated). ``next_offset`` is the position immediately after the string.
    """
    if data[offset : offset + 3] != CSTRING_PREFIX or offset + 4 > len(data):
        return None
    nchars = data[offset + 3]
    start = offset + 4
    end = start + nchars * 2
    if end > len(data):
        return None
    try:
        text = data[start:end].decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    return text, end


def encode_cstring(text: str) -> bytes:
    """Encode ``text`` as a CString. Inverse of :func:`read_cstring`.

    The length field is the UTF-16 **code-unit** count (``len(enc) // 2``), to
    match MFC's ``CStringW`` and :func:`read_cstring` (which reads ``len * 2``
    bytes). Using the Python ``str`` length here would corrupt any string
    containing an astral character (one code point = two UTF-16 code units):
    too few bytes would be written for the declared length and the round-trip
    would break. Raises :class:`ValueError` for >= 255 code units (the wider
    ``0xFF``-escaped length form is not yet implemented).
    """
    enc = text.encode("utf-16-le")
    n_codeunits = len(enc) // 2
    if n_codeunits >= 0xFF:
        raise ValueError("CString >= 255 UTF-16 code units not yet supported")
    return CSTRING_PREFIX + bytes([n_codeunits]) + enc
