"""M3 (read side) tests — configuration listing from Contents/CMgrHdr2.

Pure Python (no SW). Validates `api.configurations` / `carchive.cmgrhdr2`
against the registered corpus. Known config sets were SW-verified
(`GetConfigurationNames`) and recorded in
`research/empirical_findings/cmgrhdr2_configs/`. Marked `layer2`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

import swformat  # noqa: E402
from swformat.api.configurations import configuration_count, read_configurations  # noqa: E402

# Config sets confirmed against SOLIDWORKS GetConfigurationNames.
_KNOWN: dict[str, list[str]] = {
    "delete_part_smallest": ["Default"],
    "washer_simple": ["Default", "PreviewCfg"],
    "delete_assembly": ["Default"],
}


def _corpus() -> list[tuple[str, Path]]:
    cfg = ROOT / "test" / "corpus" / "corpus.config.json"
    if not cfg.exists():
        return []
    out = []
    for e in json.loads(cfg.read_text(encoding="utf-8")).get("files", []):
        p = Path(e["path"])
        if p.exists():
            out.append((e["tag"], p))
    return out


CORPUS = _corpus()
_IDS = [t for t, _ in CORPUS]
_skip = pytest.mark.skipif(not CORPUS, reason="no corpus files present")


@pytest.mark.layer2
@_skip
@pytest.mark.parametrize(("tag", "path"), CORPUS, ids=_IDS)
def test_read_configurations_invariants(tag: str, path: Path) -> None:
    """Returns a list[str]; count matches the name list for these files."""
    names = read_configurations(path)
    assert isinstance(names, list)
    assert all(isinstance(n, str) and n for n in names)
    assert len(names) == len(set(names)), "duplicate config names"
    # For files with a CMgrHdr2, the count equals the recovered name list here.
    if "Contents/CMgrHdr2" in swformat.read_document(path).streams():
        assert len(names) == configuration_count(path)


@pytest.mark.layer2
@_skip
@pytest.mark.parametrize(("tag", "path"), CORPUS, ids=_IDS)
def test_known_configuration_names(tag: str, path: Path) -> None:
    """Config names match the SW-verified expectation for known corpus files."""
    if tag not in _KNOWN:
        pytest.skip(f"no known config set for {tag}")
    assert read_configurations(path) == _KNOWN[tag]


@pytest.mark.layer2
def test_configurations_absent_stream_returns_empty(tmp_path: Path) -> None:
    """No CMgrHdr2 / unreadable → empty list + zero count, no exception."""
    from swformat.api.configurations import read_configurations as rc

    # A non-modern blob has no streams → empty.
    f = tmp_path / "notmodern.sldprt"
    f.write_bytes(b"not a solidworks file" * 8)
    assert rc(f) == []
    assert configuration_count(f) == 0


# --------------------------------------------------------------------------- #
# M3 modify side — rename (SW-verified on parts + assemblies, v19000; see
# research/empirical_findings/cmgrhdr2_configs/log.md 2026-06-09).
# --------------------------------------------------------------------------- #

def _multi_config_corpus() -> tuple[str, Path] | None:
    """First registered corpus file that has >= 2 configurations, else None."""
    for tag, path in CORPUS:
        if len(read_configurations(path)) >= 2:
            return tag, path
    return None


_MULTI = _multi_config_corpus()
_skip_multi = pytest.mark.skipif(_MULTI is None, reason="no >=2-config corpus file")


@pytest.mark.layer1
@_skip_multi
def test_rename_handler_same_length_is_reversible() -> None:
    """L1: a same-length rename on the raw CMgrHdr2 bytes is byte-reversible and
    updates exactly the target name (others unchanged)."""
    from swformat.carchive import cmgrhdr2

    _tag, path = _MULTI
    h2 = swformat.read_document(path).streams()["Contents/CMgrHdr2"]
    names = cmgrhdr2.read_configuration_names(h2)
    target = names[-1]
    repl = ("X" + target[1:]) if target[0] != "X" else ("Y" + target[1:])
    assert len(repl) == len(target) and repl not in names

    renamed = cmgrhdr2.rename_configuration(h2, target, repl)
    got = cmgrhdr2.read_configuration_names(renamed)
    assert got == [repl if n == target else n for n in names]
    # same length → stream length unchanged, and reversible to the exact bytes.
    assert len(renamed) == len(h2)
    assert cmgrhdr2.rename_configuration(renamed, repl, target) == h2


@pytest.mark.layer1
@_skip_multi
def test_rename_handler_length_changing() -> None:
    """L1: a longer name grows the stream and updates the name list."""
    from swformat.carchive import cmgrhdr2

    _tag, path = _MULTI
    h2 = swformat.read_document(path).streams()["Contents/CMgrHdr2"]
    names = cmgrhdr2.read_configuration_names(h2)
    target = names[-1]
    longer = target + "_RENAMED_LONGER"
    renamed = cmgrhdr2.rename_configuration(h2, target, longer)
    assert cmgrhdr2.read_configuration_names(renamed) == [
        longer if n == target else n for n in names
    ]
    assert len(renamed) > len(h2)


@pytest.mark.layer1
@_skip_multi
def test_rename_handler_unknown_name_raises() -> None:
    """L1: renaming a configuration that doesn't exist raises ConfigMgrError."""
    from swformat.carchive import cmgrhdr2

    _tag, path = _MULTI
    h2 = swformat.read_document(path).streams()["Contents/CMgrHdr2"]
    with pytest.raises(cmgrhdr2.ConfigMgrError):
        cmgrhdr2.rename_configuration(h2, "NoSuchConfigXYZ", "Whatever")


@pytest.mark.layer2
@_skip_multi
def test_rename_write_roundtrip_same_length(tmp_path: Path) -> None:
    """L2: API rename (same length → span-preserving) writes a file the reader
    reads back with the new name. Works on all versions."""
    from swformat.api.configurations import rename_configuration

    _tag, path = _MULTI
    names = read_configurations(path)
    target = names[-1]
    repl = ("X" + target[1:]) if target[0] != "X" else ("Y" + target[1:])
    out = tmp_path / f"renamed{path.suffix}"
    result = rename_configuration(path, target, repl, out)
    expected = [repl if n == target else n for n in names]
    assert result == expected
    assert read_configurations(out) == expected
    # the other streams are untouched: config COUNT is preserved.
    assert configuration_count(out) == len(names)


@pytest.mark.layer2
@_skip_multi
def test_rename_write_duplicate_name_rejected(tmp_path: Path) -> None:
    """L2: renaming to an already-existing config name is rejected (SW requires
    unique configuration names)."""
    from swformat.api.configurations import ConfigurationError, rename_configuration

    _tag, path = _MULTI
    names = read_configurations(path)
    out = tmp_path / f"dup{path.suffix}"
    with pytest.raises(ConfigurationError):
        rename_configuration(path, names[-1], names[0], out)


@pytest.mark.layer2
@_skip_multi
def test_rename_write_grow_modern(tmp_path: Path) -> None:
    """L2: a length-INCREASING rename round-trips through the writer.

    On a PART, a name that overflows CMgrHdr2's span is written via relocate-to-
    EOF (SW-verified) so the rename succeeds. On a non-part (assembly) where the
    grow can't be relocated, ``rename_configuration`` raises ``SpanPreserveError``
    and we skip (grow-beyond-span on asm/drw is not yet SW-valid)."""
    from swformat.api.configurations import rename_configuration
    from swformat.io.writer import SpanPreserveError

    _tag, path = _MULTI
    names = read_configurations(path)
    target = names[-1]
    longer = target + "_GROWN"
    out = tmp_path / f"grown{path.suffix}"
    try:
        result = rename_configuration(path, target, longer, out)
    except SpanPreserveError:
        pytest.skip("grow-beyond-span not relocatable here (non-part / old layout)")
    assert read_configurations(out) == result == [
        longer if n == target else n for n in names
    ]
    import swformat
    d = swformat.read_document(out)
    assert d.reconstruct() == d.data                 # no orphan bytes
