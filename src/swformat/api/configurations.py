"""High-level configuration API (M3) — list and rename configurations.

Reads (and now renames) a document's configurations via the binary
``Contents/CMgrHdr2`` (see :mod:`swformat.carchive.cmgrhdr2`) without
SOLIDWORKS. The read side is SW-verified to match
``IModelDoc2.GetConfigurationNames``; the rename side is SW-verified (SW 2026 /
v19000) to reopen with no repair and report the new name — see the hypothesis
log ``research/empirical_findings/cmgrhdr2_configs/log.md`` (2026-06-09).

Public functions:
- :func:`read_configurations` — ``[name, …]`` in file order.
- :func:`configuration_count` — the number of configurations.
- :func:`rename_configuration` — rename one configuration, writing a new file.
"""
from __future__ import annotations

from pathlib import Path

from swformat.carchive import cmgrhdr2
from swformat.io.reader import read_document
from swformat.io.writer import set_stream_payload, write_with_toc

_STREAM = "Contents/CMgrHdr2"


class ConfigurationError(Exception):
    """Raised when a configuration edit cannot be applied (e.g. no CMgrHdr2,
    unknown source name, or the target name already exists)."""


def read_configurations(path: str | Path) -> list[str]:
    """Return the document's configuration names (file order); ``[]`` if none.

    ``[]`` when the file has no ``Contents/CMgrHdr2`` (e.g. some drawings) or
    the header can't be parsed. No SOLIDWORKS required.
    """
    h2 = read_document(path).streams().get(_STREAM)
    if h2 is None:
        return []
    try:
        return cmgrhdr2.read_configuration_names(h2)
    except cmgrhdr2.ConfigMgrError:
        return []


def configuration_count(path: str | Path) -> int:
    """Return the number of configurations, or ``0`` if none/unavailable."""
    h2 = read_document(path).streams().get(_STREAM)
    if h2 is None:
        return 0
    try:
        return cmgrhdr2.read_configuration_count(h2)
    except cmgrhdr2.ConfigMgrError:
        return 0


def rename_configuration(
    path: str | Path,
    old_name: str,
    new_name: str,
    out_path: str | Path,
) -> list[str]:
    """Rename configuration ``old_name`` → ``new_name``, writing to ``out_path``.

    Edits only the authoritative ``Contents/CMgrHdr2`` stream (the config-name
    list SW reads on open — see :func:`swformat.carchive.cmgrhdr2.rename_configuration`
    for why that single-stream edit is accepted by SOLIDWORKS), then re-deflates
    and fixes the central directory via :func:`write_with_toc`. Returns the new
    configuration-name list.

    The write is span-preserving when the new name fits the stream's original
    compressed span (works on ALL files, including assemblies). A LONGER name
    that overflows the span requires a grow-beyond-span write:

    - On a PART, this uses relocate-to-EOF (``write_with_toc(relocate_grow=True)``)
      — SW-verified — so config rename-to-longer works on parts.
    - On an ASSEMBLY (or any non-part), grow-beyond-span is not yet SW-valid
      (relocate is rejected by assemblies; the offset-shift path is falsified),
      so an overflowing rename raises
      :class:`swformat.io.writer.SpanPreserveError`. Same-length / fits-in-span
      renames work on assemblies too.

    :raises ConfigurationError: if the file has no ``Contents/CMgrHdr2``, if
        ``new_name`` already names a configuration (SOLIDWORKS requires unique
        configuration names), or if ``old_name`` is not present.
    """
    doc = read_document(path)
    h2 = doc.streams().get(_STREAM)
    if h2 is None:
        raise ConfigurationError(f"{path}: no {_STREAM} stream to rename in")

    existing = cmgrhdr2.read_configuration_names(h2)
    if new_name in existing:
        raise ConfigurationError(
            f"a configuration named {new_name!r} already exists "
            "(SOLIDWORKS requires unique configuration names)"
        )

    try:
        new_h2 = cmgrhdr2.rename_configuration(h2, old_name, new_name)
    except cmgrhdr2.ConfigMgrError as exc:
        raise ConfigurationError(str(exc)) from exc

    if set_stream_payload(doc, _STREAM, new_h2) == 0:
        raise ConfigurationError(f"could not locate the {_STREAM} chunk to update")
    # Grow-beyond-span (longer name) is SW-valid via relocate-to-EOF on PARTS only.
    try:
        from swformat.api.metadata import detect_doc_type
        is_part = detect_doc_type(path) == "part"
    except Exception:
        is_part = False
    write_with_toc(doc, out_path, relocate_grow=is_part)
    return cmgrhdr2.read_configuration_names(new_h2)
