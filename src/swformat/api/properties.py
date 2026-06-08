"""High-level custom-property API (M2) — read/edit without SOLIDWORKS.

Ties together the layers proven in M1.5:

    read_document  →  edit docProps/custom.xml (streams.custom_props)
                   →  set_stream_payload  →  write_with_toc

``write_with_toc`` re-deflates the edited stream and fixes the central TOC's
offset pointers, so the result reopens in real SOLIDWORKS with the new
values (verified). Custom-property *value* edits, additions, and deletions
all happen INSIDE the existing ``docProps/custom.xml`` chunk, so they do not
change the file's chunk *set* — exactly the case ``write_with_toc`` handles.

Both take an optional ``config: int | None`` keyword: ``None`` targets the
document-level store (``docProps/custom.xml``); ``config=N`` targets a
configuration's store (``docProps/Config-N-Properties.xml``), via the
:func:`_props_stream` helper.

Public functions:
- :func:`read_properties` — ``{name: value}`` for a file (no SW needed),
  global or per-configuration.
- :func:`edit_properties` — apply sets/deletes and write a new file,
  global or per-configuration.
"""
from __future__ import annotations

from pathlib import Path

from swformat.io.reader import read_document
from swformat.io.writer import set_stream_payload, write_with_toc
from swformat.streams import custom_props


def _props_stream(config: int | None) -> str:
    """Stream name for the property store: global, or a configuration's.

    ``config=None`` → ``docProps/custom.xml`` (document-level / global).
    ``config=N``    → ``docProps/Config-N-Properties.xml`` (config-scoped, by
    0-based index — the index used in the stream name). Mapping a
    configuration NAME → index needs the config list (CMgrHdr2, M3); until
    then configs are addressed by index.
    """
    return custom_props.STREAM_NAME if config is None \
        else f"docProps/Config-{config}-Properties.xml"


def read_properties(path: str | Path, *, config: int | None = None) -> dict[str, str]:
    """Return custom properties as ``{name: value}`` (global, or a config's).

    ``config=None`` reads document-level props (``docProps/custom.xml``);
    ``config=N`` reads configuration N's props
    (``docProps/Config-N-Properties.xml``). Empty dict if the stream is
    absent or has no user-defined properties.
    """
    streams = read_document(path).streams()
    xml = streams.get(_props_stream(config))
    if xml is None:
        return {}
    return custom_props.list_properties(xml)


def edit_properties(
    in_path: str | Path,
    out_path: str | Path,
    *,
    sets: dict[str, str] | None = None,
    deletes: list[str] | None = None,
    config: int | None = None,
) -> dict[str, str]:
    """Apply property edits to ``in_path`` and write the result to ``out_path``.

    ``sets`` maps name→value (each updated if present, else added).
    ``deletes`` is a list of names to remove. Returns the resulting
    ``{name: value}`` map. The output reopens in SOLIDWORKS with the changes
    (TOC offsets are fixed by ``write_with_toc``).

    ``config=None`` edits document-level (global) properties in
    ``docProps/custom.xml``. ``config=N`` edits configuration N's properties
    in ``docProps/Config-N-Properties.xml`` (0-based index; SW surfaces these
    in the config-specific tab of the Summary Information dialog). Verified:
    adding a config-scoped name via the same name-dictionary mechanism makes
    SW show it for that configuration on reopen.

    The binary ``Contents/CusProps`` consistency pass only applies to
    document-level edits — config-scoped props live solely in their XML
    stream, so there is no parallel binary store to keep in sync.

    Raises :class:`~swformat.streams.custom_props.CustomPropsError` if the
    target property stream is absent.
    """
    stream_name = _props_stream(config)
    doc = read_document(in_path)
    streams = doc.streams()
    xml = streams.get(stream_name)
    if xml is None:
        raise custom_props.CustomPropsError(
            f"{in_path} has no {stream_name} stream to edit"
        )
    before = set(custom_props.list_properties(xml))

    # The property XML stream is authoritative for what SW surfaces (it reads
    # the name dictionary). set_property registers brand-new names there;
    # deletes remove them.
    for name, value in (sets or {}).items():
        xml = custom_props.set_property(xml, name, value)
    for name in deletes or []:
        xml = custom_props.delete_property(xml, name)
    n = set_stream_payload(doc, stream_name, xml)
    if n == 0:
        raise custom_props.CustomPropsError(
            f"could not locate the {stream_name} chunk to update"
        )

    # Keep the binary Contents/CusProps store CONSISTENT for brand-new names
    # (SW writes both; display works from custom.xml alone, but a stale binary
    # store would mislead other readers / a later SW re-save). Best-effort:
    # only adds (not deletes/edits, where SW reads custom.xml for the value),
    # and only for DOCUMENT-LEVEL props — config-scoped props have no binary
    # CusProps counterpart.
    new_names = [(k, v) for k, v in (sets or {}).items() if k not in before]
    cusprops = streams.get("Contents/CusProps")
    cus_modified = False
    if config is None and new_names and cusprops is not None:
        try:
            from swformat.carchive.cusprops import add_text_properties
            set_stream_payload(doc, "Contents/CusProps",
                               add_text_properties(cusprops, new_names))
            cus_modified = True
        except Exception:
            pass

    # Write strategy. Span-preservation handles every edit that still fits the
    # original compressed span (value changes, deletes, adds with slack). For a
    # grow-beyond-span (e.g. adding a property to a tight stream), PARTS can use
    # relocate-to-EOF (SW-verified); assemblies/drawings cannot, so they fall
    # back to the honest SpanPreserveError. The CusProps mirror is OPTIONAL
    # (custom.xml is authoritative for SW), so if a grow can't be written, drop
    # the mirror and write the authoritative XML edit alone rather than fail.
    from swformat.io.writer import SpanPreserveError

    # Gate relocate-grow to parts (asm/drw reject a relocated stream). Detect by
    # document type from the input path (lazy import avoids a cycle).
    try:
        from swformat.api.metadata import detect_doc_type
        is_part = detect_doc_type(in_path) == "part"
    except Exception:
        is_part = False

    try:
        write_with_toc(doc, out_path, relocate_grow=is_part)
    except SpanPreserveError:
        if not cus_modified:
            raise  # the authoritative XML stream itself overflowed — propagate
        for ch in doc.chunks:
            if ch.name == "Contents/CusProps":
                ch.modified_payload = None  # drop the best-effort binary mirror
        write_with_toc(doc, out_path, relocate_grow=is_part)
    return custom_props.list_properties(xml)


def read_cutlist_properties(path: str | Path) -> dict[str, str]:
    """Return ``{name: resolved_value}`` for cut-list / weldment properties.

    Thin wrapper over :func:`swformat.carchive.cusprops.read_cutlist_props`
    reading ``Contents/CusProps``. ``{}`` if the file has no cut-list container.
    No SOLIDWORKS required.
    """
    from swformat.carchive.cusprops import read_cutlist_props
    cus = read_document(path).streams().get("Contents/CusProps")
    return read_cutlist_props(cus) if cus else {}


def edit_cutlist_value(
    path: str | Path,
    name: str,
    new_value: str,
    out_path: str | Path,
) -> str:
    """Set a USER-TEXT cut-list property's value, writing to ``out_path``.

    Edits the authoritative element value (plus the record-core value/resolved)
    in the binary ``Contents/CusProps`` cut-list container, then re-deflates and
    fixes the central directory via :func:`write_with_toc`. Returns ``new_value``.

    Only manually-entered (non-formula) cut-list properties are editable;
    formula-linked / system-defined ones (e.g. MATERIAL, QUANTITY) are recomputed
    by SOLIDWORKS and are rejected. SW-verified (SW 2026 / v19000): the reopened
    file's cut-list folder reports the new value.

    :raises swformat.carchive.cusprops.CustomPropsError: if the file has no
        ``Contents/CusProps``, ``name`` is absent, or ``name`` is a
        formula-linked / system-defined cut-list property.
    """
    from swformat.carchive.cusprops import CustomPropsError, set_cutlist_value
    doc = read_document(path)
    cus = doc.streams().get("Contents/CusProps")
    if cus is None:
        raise CustomPropsError(f"{path}: no Contents/CusProps (no cut-list store)")
    new_cus = set_cutlist_value(cus, name, new_value)
    if set_stream_payload(doc, "Contents/CusProps", new_cus) == 0:
        raise CustomPropsError("could not locate the Contents/CusProps chunk to update")
    write_with_toc(doc, out_path)
    return new_value
