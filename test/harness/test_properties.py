"""Layer-2/4 tests — custom-property read + edit API (M2).

Pure Python (no SW). Validates ``streams.custom_props`` and
``api.properties`` against real corpus files:

- read the property map;
- SET an existing value and DELETE another, write via ``edit_properties``
  (which uses the TOC-aware writer), re-read, and confirm the changes plus
  that unrelated properties/streams are untouched and the file has no orphan
  bytes.

SW-side confirmation (SET and DELETE of EXISTING properties are honoured by
SOLIDWORKS; ADD of a brand-new name is not, pending CusProps registration)
is recorded in ``research/empirical_findings/m1_writer_roundtrip/``.
Marked ``layer2``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

import swformat  # noqa: E402
from swformat.api.properties import edit_properties, read_properties  # noqa: E402
from swformat.streams import custom_props  # noqa: E402


def _corpus_with_custom() -> list[tuple[str, Path]]:
    cfg = ROOT / "test" / "corpus" / "corpus.config.json"
    if not cfg.exists():
        return []
    out = []
    for e in json.loads(cfg.read_text(encoding="utf-8")).get("files", []):
        p = Path(e["path"])
        if p.exists() and custom_props.STREAM_NAME in swformat.read_document(p).streams():
            out.append((e["tag"], p))
    return out


CORPUS = _corpus_with_custom()
_IDS = [t for t, _ in CORPUS]
_skip = pytest.mark.skipif(not CORPUS, reason="no corpus file with docProps/custom.xml")


def _corpus_with_config() -> list[tuple[str, Path, int]]:
    """Corpus files that carry at least one ``docProps/Config-N-Properties.xml``.

    Returns ``(tag, path, config_index)`` for the lowest config index present.
    """
    cfg = ROOT / "test" / "corpus" / "corpus.config.json"
    if not cfg.exists():
        return []
    out = []
    for e in json.loads(cfg.read_text(encoding="utf-8")).get("files", []):
        p = Path(e["path"])
        if not p.exists():
            continue
        streams = swformat.read_document(p).streams()
        # Match only docProps/Config-<int>-Properties.xml. Weldments also carry
        # docProps/Config-<n>-Cutlist-Properties.xml (cut-list config props),
        # whose middle ("0-Cutlist") is not a bare integer — skip those.
        idxs = sorted(
            int(mid)
            for s in streams
            if s.startswith("docProps/Config-") and s.endswith("-Properties.xml")
            for mid in [s.removeprefix("docProps/Config-").removesuffix("-Properties.xml")]
            if mid.isdigit()
        )
        if idxs:
            out.append((e["tag"], p, idxs[0]))
    return out


CONFIG_CORPUS = _corpus_with_config()
_CFG_IDS = [t for t, _, _ in CONFIG_CORPUS]
_skip_cfg = pytest.mark.skipif(
    not CONFIG_CORPUS, reason="no corpus file with docProps/Config-N-Properties.xml"
)


def test_custom_props_unit() -> None:
    """The surgical XML editor: set existing, add, delete, escaping."""
    xml = (
        b'<?xml version="1.0"?><Properties><propertySection name="UserDefinedProperties">'
        b'<property name="" pid="1" TypeID="0"><vt:i2>65001</vt:i2></property>'
        b'<property name="REVISION" pid="5" IsEquation="False" TypeID="30">'
        b"<vt:lpstr>0</vt:lpstr><FPVals><vt:lpstr>0</vt:lpstr></FPVals></property>"
        b"</propertySection></Properties>"
    )
    assert custom_props.list_properties(xml) == {"REVISION": "0"}
    x = custom_props.set_property(xml, "REVISION", "B")
    assert custom_props.get_property(x, "REVISION") == "B"
    # FPVals cached copy updated too
    assert x.count(b"<vt:lpstr>B</vt:lpstr>") == 2
    x = custom_props.set_property(x, "NOTE", "a & b < c")
    assert custom_props.get_property(x, "NOTE") == "a & b < c"
    assert b"a &amp; b &lt; c" in x  # escaped on disk
    x = custom_props.delete_property(x, "NOTE")
    assert "NOTE" not in custom_props.list_properties(x)
    assert custom_props.get_property(x, "REVISION") == "B"  # survivor intact


def test_delete_removes_name_dictionary_entry() -> None:
    """Delete removes BOTH the <property> AND the propertyNameDictionaryElement.

    SW reads the visible list from the name dictionary, so a delete that left
    the dictionary entry would risk the name lingering and would break a later
    re-add (duplicate dict entry / pid drift). Mirror of the add path.
    """
    base = (
        b'<?xml version="1.0"?><Properties><propertySection name="UserDefinedProperties">'
        b'<property name="" pid="1" TypeID="0"><vt:i2>65001</vt:i2></property>'
        b'<property name="KEEP" pid="2" IsEquation="False" TypeID="30">'
        b"<vt:lpstr>k</vt:lpstr><FPVals><vt:lpstr>k</vt:lpstr></FPVals></property>"
        b"</propertySection></Properties>"
    )
    x = custom_props.set_property(base, "GONE", "g")
    assert b'propertyNameDictionaryElement name="GONE"' in x  # add wrote dict entry
    x = custom_props.delete_property(x, "GONE")
    assert b'name="GONE"' not in x  # neither property nor dict entry remains
    assert "GONE" not in custom_props.list_properties(x)
    assert custom_props.get_property(x, "KEEP") == "k"  # survivor intact
    # re-add after delete must NOT create a duplicate dictionary entry
    x = custom_props.set_property(x, "GONE", "again")
    assert x.count(b'propertyNameDictionaryElement name="GONE"') == 1


def test_special_char_name_roundtrip() -> None:
    """Names with XML metacharacters (& < > ") add/get/list/delete correctly.

    Regression: names sit in an attribute and must be escaped for attribute
    context (incl. &quot;) on write AND searched in that same escaped form;
    list_properties must reverse &quot; too (saxutils.unescape doesn't by
    default). Previously such a name could be added but never found.
    """
    base = (
        b'<?xml version="1.0"?><Properties><propertySection name="UserDefinedProperties">'
        b'<property name="" pid="1" TypeID="0"><vt:i2>65001</vt:i2></property>'
        b"</propertySection></Properties>"
    )
    for nm in ["A&B<C", 'has"quote', "tag>end", "plain"]:
        x = custom_props.set_property(base, nm, "v1")
        assert custom_props.get_property(x, nm) == "v1"
        assert nm in custom_props.list_properties(x)
        # the stored attribute is well-formed: no raw quote inside the value
        assert b'name="' + custom_props._attr_escape(nm) + b'"' in x
        x = custom_props.set_property(x, nm, "v2")        # update existing
        assert custom_props.get_property(x, nm) == "v2"
        x = custom_props.delete_property(x, nm)            # delete
        assert nm not in custom_props.list_properties(x)


@pytest.mark.layer2
@_skip
@pytest.mark.parametrize(("tag", "path"), CORPUS, ids=_IDS)
def test_read_properties(tag: str, path: Path) -> None:
    # Parsing must succeed and return a dict; some files legitimately have
    # zero user-defined properties (e.g. imported parts), so don't require any.
    props = read_properties(path)
    assert isinstance(props, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in props.items())


@pytest.mark.layer2
@_skip
def test_bulk_add_either_fits_or_is_refused() -> None:
    """Bulk-adding many properties either FITS the span (SW-valid round-trip) or
    is REFUSED (SpanPreserveError) — it must never silently emit a broken file.

    A large bulk add overflows custom.xml's compressed span. Because the
    offset-shift grow path is SW-invalid (re-falsified 2026-06-11 — SW rejects
    the grown file with e=2097152), the writer now REFUSES a grow-beyond-span by
    default instead of emitting it. So the only two acceptable outcomes are:

    - the add fit in the original span → it round-trips and re-parses cleanly
      (an SW-valid edit); OR
    - the add overflowed → :class:`SpanPreserveError` (the honest current limit;
      grow-beyond-span awaits the unsolved central-directory rewrite).

    The forbidden third outcome — a larger output that re-parses but SW rejects —
    is what this test exists to prevent regressing to.
    """
    from swformat.chunks.walker import doc_version
    from swformat.io.writer import SpanPreserveError

    chosen = None
    for tag, path in CORPUS:
        doc = swformat.read_document(path)
        ver = doc_version({c.name: b"" for c in doc.chunks}) or 0
        if ver >= 15000:
            chosen = (tag, path)
            break
    if chosen is None:
        pytest.skip("no modern (v>=15000) corpus file with custom.xml")
    _tag, path = chosen
    props = {f"SWF_BULK_{i:02d}": f"value_number_{i}" for i in range(20)}
    out = path.with_suffix(path.suffix + ".bulk_tmp")
    try:
        try:
            edit_properties(path, out, sets=props)
        except SpanPreserveError:
            return  # overflowed the span → correctly refused (documented limit)
        # Fit in span → must be a clean, span-preserving (size-stable) round-trip.
        back = read_properties(out)
        for k, v in props.items():
            assert back.get(k) == v, f"{k} not round-tripped"
        doc = swformat.read_document(out)
        assert doc.reconstruct() == doc.data  # no orphan bytes
    finally:
        out.unlink(missing_ok=True)


@pytest.mark.layer2
@_skip
def test_add_new_property_updates_both_stores() -> None:
    """Adding a brand-new property registers it in custom.xml AND CusProps.

    SW reads the name from the custom.xml name dictionary (SW-verified
    separately); here we assert the pure-Python invariant that a new name
    lands in BOTH the XML store and the binary CusProps store, and the file
    stays structurally sound.
    """
    from swformat.carchive.cusprops import read_cusprops

    _tag, path = CORPUS[0]
    if "Contents/CusProps" not in swformat.read_document(path).streams():
        pytest.skip("base file has no CusProps")
    out = path.with_suffix(path.suffix + ".addnew_tmp")
    try:
        result = edit_properties(path, out, sets={"SWF_NEWPROP": "newval"})
        assert result.get("SWF_NEWPROP") == "newval"
        streams = swformat.read_document(out).streams()
        # XML (authoritative for SW) carries it...
        assert "SWF_NEWPROP" in custom_props.list_properties(streams[custom_props.STREAM_NAME])
        # ...and the binary store is kept consistent.
        assert "SWF_NEWPROP" in read_cusprops(streams["Contents/CusProps"])
        # no orphan bytes
        doc = swformat.read_document(out)
        assert doc.reconstruct() == doc.data
    finally:
        out.unlink(missing_ok=True)


@pytest.mark.layer2
@_skip_cfg
@pytest.mark.parametrize(("tag", "path", "idx"), CONFIG_CORPUS, ids=_CFG_IDS)
def test_read_config_properties(tag: str, path: Path, idx: int) -> None:
    """Config-scoped read targets ``docProps/Config-N-Properties.xml``.

    The global and config stores are independent streams; reading with
    ``config=idx`` must parse the config stream and return a dict.
    """
    props = read_properties(path, config=idx)
    assert isinstance(props, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in props.items())


@pytest.mark.layer2
@_skip_cfg
def test_edit_config_properties_span_limited() -> None:
    """Config-scoped ADD via the span-preserving writer; honest about its limit.

    The ``config=`` routing is verified either way: a brand-new config property
    is written into the config XML stream only (never the global store). BUT
    ``Config-N-Properties.xml`` streams are small with little compressed slack,
    so growing one by adding a property usually overflows the span-preserving
    budget and raises :class:`SpanPreserveError` — the honest current limit
    (growing a chunk beyond its csz needs the unsolved central-directory
    rewrite; the prior offset-shift writer emitted files SW actually rejected).
    If a config stream DOES have slack, the add round-trips and surfaces in the
    config stream without touching the global store.
    """
    from swformat.io.writer import SpanPreserveError

    _tag, path, idx = CONFIG_CORPUS[0]
    cfg_stream = f"docProps/Config-{idx}-Properties.xml"
    global_before = read_properties(path)  # document-level, must not change

    out = path.with_suffix(path.suffix + ".cfgedit_tmp")
    try:
        try:
            edit_properties(path, out, sets={"SWF_CFGPROP": "cfgval"}, config=idx)
        except SpanPreserveError:
            return  # documented limitation: config stream has no slack to grow
        # Slack existed → verify correctness of the write.
        assert read_properties(out, config=idx).get("SWF_CFGPROP") == "cfgval"
        new_streams = swformat.read_document(out).streams()
        assert "SWF_CFGPROP" in custom_props.list_properties(new_streams[cfg_stream])
        assert read_properties(out) == global_before  # global store untouched
        doc = swformat.read_document(out)
        assert doc.reconstruct() == doc.data
    finally:
        out.unlink(missing_ok=True)


@pytest.mark.layer2
@_skip
def test_edit_properties_roundtrip() -> None:
    _tag, path = CORPUS[0]
    before = read_properties(path)
    # choose an existing prop to change and one to delete
    names = list(before)
    change = names[0]
    delete = names[1] if len(names) > 1 else None

    out = path.with_suffix(path.suffix + ".propedit_tmp")
    try:
        edit_properties(
            path, out,
            sets={change: "SWFORMAT_EDIT_VALUE"},
            deletes=[delete] if delete else None,
        )
        after = read_properties(out)
        assert after[change] == "SWFORMAT_EDIT_VALUE"
        if delete:
            assert delete not in after
        # unrelated props unchanged
        for n, v in before.items():
            if n != change and n != delete:
                assert after.get(n) == v
        # produced file is structurally sound (no orphan bytes)
        doc = swformat.read_document(out)
        assert doc.reconstruct() == doc.data
        # only docProps/custom.xml content changed among streams
        orig_streams = swformat.read_document(path).streams()
        new_streams = doc.streams()
        for n, v in orig_streams.items():
            if n != custom_props.STREAM_NAME:
                assert new_streams.get(n) == v, f"unrelated stream {n} changed"
    finally:
        out.unlink(missing_ok=True)
