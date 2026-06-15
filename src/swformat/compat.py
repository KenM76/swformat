"""``swformat.compat`` — version-compatibility gate + telemetry.

SWFormat's read/write paths were reverse-engineered and (for writes) Layer-3
verified against a small set of internal SOLIDWORKS modern-format versions.
Files outside that tested envelope may parse partially or differ structurally,
so this module surfaces the file's version and **flags reads of untested
versions** with a :class:`UntestedVersionWarning` — early warning when a file
outside the verified range shows up, instead of a silent mis-read.

The internal format version is the ``_MO_VERSION_NNNNN`` stream-name prefix
(parsed by :func:`swformat.chunks.walker.doc_version`). It is NOT the marketing
year; it identifies the modern-format generation (SW 2015+). Pre-2015 OLE2 files
have no such stream → version ``None`` → ``unsupported`` (a different container
SWFormat does not handle). See ``docs/COMPATIBILITY.md`` for the full layer-by-
layer assessment.
"""
from __future__ import annotations

import warnings
from pathlib import Path

from swformat.chunks.walker import doc_version

#: Internal modern-format versions empirically exercised by the test suite and,
#: for write paths, by Layer-3 (live-SOLIDWORKS reopen) verification.
TESTED_VERSIONS = frozenset({11000, 15000, 19000})


class UntestedVersionWarning(UserWarning):
    """A file's internal format version is outside SWFormat's tested envelope."""


def version_status(version: int | None) -> str:
    """Classify a version: ``tested`` / ``untested-modern`` / ``untested-newer``
    / ``unsupported`` (None — legacy OLE2 / non-modern / version not found)."""
    if version is None:
        return "unsupported"
    if version in TESTED_VERSIONS:
        return "tested"
    if version > max(TESTED_VERSIONS):
        return "untested-newer"
    return "untested-modern"


def warn_if_untested(version: int | None) -> str:
    """Emit an :class:`UntestedVersionWarning` if ``version`` is not in the
    tested envelope. Returns the status string. Consumers can silence via the
    standard ``warnings`` filters on :class:`UntestedVersionWarning`."""
    status = version_status(version)
    if status != "tested":
        warnings.warn(
            f"SOLIDWORKS internal format version {version} ({status}) is outside "
            f"SWFormat's tested envelope {sorted(TESTED_VERSIONS)} — reads may be "
            f"incomplete and writes are not verified for this version. "
            f"See docs/COMPATIBILITY.md.",
            UntestedVersionWarning,
            stacklevel=2,
        )
    return status


def warn_streams(streams: dict[str, bytes]) -> int | None:
    """Derive the version from a streams dict, warn if untested, return it.

    Cheap telemetry seam for the read API — uses streams the caller already has,
    so it adds no extra I/O.
    """
    v = doc_version(streams)
    warn_if_untested(v)
    return v


def check_supported(path: str | Path, *, warn: bool = True) -> tuple[int | None, str]:
    """Open ``path``, return ``(version, status)``. Warns when untested unless
    ``warn=False``. Use for an explicit pre-flight check."""
    from swformat.io.reader import read_document

    v = doc_version(read_document(path).streams())
    return v, (warn_if_untested(v) if warn else version_status(v))
