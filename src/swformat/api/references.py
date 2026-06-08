"""``swformat.api.references`` — extract the external model references of a drawing.

A SOLIDWORKS drawing (`.SLDDRW`) references the part/assembly models it depicts:
each drawing view is a projection of some model, and the drawing's
``Contents/Definition`` stream records those model file paths (in the
``moExtObjectList_c`` external-object collection, in view references, and in the
BOM). This module extracts that **drawing → referenced-models** list **without
SOLIDWORKS** — purely from the decompressed CArchive stream bytes.

Why this exists (the consumer): an agent that opens and *trains on* a corpus of
drawing files needs, per drawing, the set of models it depicts — to join drawings
to their source geometry, to dedupe, to build a dependency graph, or to label
training examples. This is high-value metadata that does NOT require the full
(LOW-feasibility) content-general CArchive object-map walk: a model path is stored
as an MFC ``CStringW`` ending in a SOLIDWORKS document extension, which is an
*unambiguous* anchor (unlike the dimension/geometry byte-signatures, a
``.SLDPRT``/``.SLDASM``/``.SLDDRW`` suffix on a length-prefixed wide string does
not occur by chance), so a scan is both robust and real-file-capable.

Method (see :func:`read_referenced_models`):
  1. Decompress ``Contents/Definition`` (via the chunk walker / reader).
  2. Scan for every MFC ``CStringW`` (``FF FE FF <len> <utf16le×len>``,
     :func:`swformat.carchive.cstring.read_cstring`).
  3. Keep those whose decoded value ends (case-insensitively) in a SOLIDWORKS
     document extension AND is a plausible file path (not a sheet-format template
     token such as ``$PRP:"…"`` — those are filtered).
  4. De-duplicate (a model referenced by several views appears several times) and
     return sorted.

Scope / honesty: this returns the model paths *as stored in the drawing* (absolute
or relative, as SW wrote them — they may point at files that have since moved).
It is a SUPERSET of the ``moExtObjectList_c`` collection because the same paths
also appear in view/BOM references; that is intentional (the union of all model
references is what a consumer wants). It does not resolve or open the models, and
it does not require any per-class CArchive schema. Verified on real multi-sheet
production drawings (read-only) — e.g. one 14.5 MB drawing yields ~100 unique model
references with clean basenames.
"""
from __future__ import annotations

import re
from pathlib import Path

from swformat.carchive.cstring import read_cstring
from swformat.io.reader import read_document

# SOLIDWORKS document extensions a drawing can reference. LFP = library feature
# part; DRW included because a drawing can reference a sheet-format/another drawing.
_SW_DOC_EXTS = (".SLDPRT", ".SLDASM", ".SLDDRW", ".SLDLFP")

_DEF_STREAM = "Contents/Definition"

# The CStringW lead-in marker (see swformat.carchive.cstring): FF FE FF.
_CSTR_MARKER = b"\xff\xfe\xff"

# A sheet-format template field (e.g. `$PRP:"SW-File Name(File Name)".SLDDRW`) can
# end in a doc extension but is a TOKEN, not a path. Filter any value carrying
# template syntax — paths never contain `$PRP`, a double-quote, or a `$` sigil.
_TEMPLATE_RE = re.compile(r'\$PRP|["$]')


def _looks_like_model_path(value: str) -> bool:
    """True if ``value`` is a plausible referenced-model file path.

    Requires a SOLIDWORKS document extension and rejects sheet-format template
    tokens (``$PRP:"…"``) which can also end in an extension but are not paths.
    """
    if not value.upper().endswith(_SW_DOC_EXTS):
        return False
    if _TEMPLATE_RE.search(value):
        return False
    return True


def iter_referenced_model_strings(defn: bytes):
    """Yield every CStringW in ``defn`` (a decompressed CArchive stream) that is a
    plausible referenced-model path, in file order (duplicates included).

    Pure scan over the ``FF FE FF`` CStringW marker — no per-class schema. Robust
    on real files because a length-prefixed wide string ending in a SOLIDWORKS
    document extension is an unambiguous anchor.
    """
    start = 0
    while True:
        i = defn.find(_CSTR_MARKER, start)
        if i < 0:
            return
        start = i + 3
        decoded = read_cstring(defn, i)
        if not decoded:                       # not a valid CStringW here
            continue
        value, _ = decoded
        if value and _looks_like_model_path(value):
            yield value


def read_referenced_models(path: str | Path, *, basenames: bool = False) -> list[str]:
    """Return the external model files a drawing references, de-duplicated + sorted.

    Scans ``Contents/Definition`` for CStringW values that are SOLIDWORKS document
    paths (``.SLDPRT`` / ``.SLDASM`` / ``.SLDDRW`` / ``.SLDLFP``), filtering
    sheet-format template tokens. The result is the UNION across the
    ``moExtObjectList_c`` collection, view references, and the BOM — i.e. every
    model the drawing depicts.

    No SOLIDWORKS required; real-file capable (the suffix anchor is unambiguous, so
    this does NOT need the content-general CArchive object-map walk). Returns ``[]``
    for a non-drawing file, a file with no ``Contents/Definition``, or a drawing
    that references nothing.

    Args:
        path:       the drawing file.
        basenames:  if True, return just the file names (``Part13.SLDPRT``) rather
                    than the full stored paths — handy for joining/labelling when
                    the absolute paths have drifted.

    Returns:
        Sorted, de-duplicated list of model paths (or basenames).
    """
    defn = read_document(path).streams().get(_DEF_STREAM)
    if not defn:
        return []
    seen: set[str] = set()
    for value in iter_referenced_model_strings(defn):
        item = PurePathBasename(value) if basenames else value
        seen.add(item)
    return sorted(seen)


def PurePathBasename(value: str) -> str:
    """Return the file-name component of a stored model path, handling BOTH Windows
    (``\\``) and POSIX (``/``) separators regardless of the host OS (SW stores
    Windows paths; this code may run anywhere). Defined explicitly rather than via
    ``os.path.basename`` so the split is separator-correct off-Windows too."""
    tail = value.replace("\\", "/").rsplit("/", 1)[-1]
    return tail
