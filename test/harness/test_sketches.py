"""M5.3 (read side) tests — drawing sketch-geometry decode from ``sgSketch``.

Two tiers, mirroring the project's "committable-synthetic first" convention:

1. **Synthetic byte-layout tests** (always run, no fixtures): construct an
   ``sgSketch`` blob by hand from the documented layout
   (``[name][u32 pcount][80 B header][pcount × 142 B points][entity fields]``)
   and assert the decoder recovers the point count, the f64 coordinate triples,
   and the (marker, code) → kind classification. These pin the decoder contract
   without depending on any generated/staged file.
2. **Fixture cross-checks** (skip if absent): decode the generated single-
   primitive ``geom_*`` drawings and assert the validated kinds/coords. These
   files are SW-generated synthetic fixtures (no client data) but are not
   git-tracked, so the tests skip cleanly in CI.

Marked layer2 (pure Python, no SW). See :mod:`swformat.api.sketches`.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from swformat.api.sketches import (  # noqa: E402
    _DIM_VALUE_SIG,
    _REL_ANCHOR,
    POINT_STRIDE,
    Sketch,
    SketchEntity,
    _dimensions_in_region,
    _relation_handles,
    _relations_in_region,
    enumerate_sketches,
    find_sketch_offsets,
    read_sketch_at,
    read_sketch_dimensions,
    read_sketch_relations,
    read_sketches,
)


def _build_point(x: float, y: float, z: float, *, marker: int | None = None,
                 code: int | None = None) -> bytes:
    """One 142-byte point record: f64 (x,y,z) at +0; optionally a u8 marker at
    +24 and a u32 code at +72 (the entity fields the decoder reads off the
    *first* point's record). Non-first points pass marker/code = None."""
    rec = bytearray(POINT_STRIDE)
    struct.pack_into("<ddd", rec, 0, x, y, z)
    if marker is not None:
        rec[24] = marker
    if code is not None:
        struct.pack_into("<I", rec, 72, code)
    return bytes(rec)


def _build_sgsketch(points: list[tuple[float, float, float]], marker: int,
                    code: int, *, prefix: bytes = b"\xde\xad") -> bytes:
    """Assemble a minimal def-blob containing one ``sgSketch`` with ``points``.

    Layout matches :mod:`swformat.api.sketches`: name, then body = u32 count +
    an 80-byte header (count lives in its first 4 bytes), then the 142-byte
    point records (the first carries the marker/code entity fields)."""
    header = bytearray(80)
    struct.pack_into("<I", header, 0, len(points))
    body = bytes(header)
    for i, (x, y, z) in enumerate(points):
        if i == 0:
            body += _build_point(x, y, z, marker=marker, code=code)
        else:
            body += _build_point(x, y, z)
    return prefix + b"sgSketch" + body


def test_decode_line_synthetic() -> None:
    pts = [(0.1234, 0.1357, 0.0), (0.2468, 0.1809, 0.0)]
    blob = _build_sgsketch(pts, marker=0, code=2)
    offs = find_sketch_offsets(blob)
    assert offs == [blob.find(b"sgSketch")]
    sk = read_sketch_at(blob, offs[0])
    assert isinstance(sk, Sketch)
    assert sk.kind == "line"
    assert sk.point_count == 2
    assert sk.points == pts
    assert sk.description == "line (2 points)"


def test_z_is_hardcoded_not_read_from_structure() -> None:
    """Drawing sketches are 2-D: the decoder reads (x, y) and reports z = 0.0,
    and must NOT read point+16 as a z coordinate. Build a point whose +16..+23
    bytes are a non-zero f64 (simulating the structural data that actually lives
    there) and assert the decoded z is still 0.0 (and x, y are correct).
    Regression for the original `<ddd` over-read."""
    rec = bytearray(POINT_STRIDE)
    struct.pack_into("<dd", rec, 0, 0.5, 0.25)        # real x, y
    struct.pack_into("<d", rec, 16, 123456.789)        # garbage in the +16 'z' slot
    rec[24] = 0                                         # line marker
    struct.pack_into("<I", rec, 72, 2)                 # line code
    rec2 = bytearray(POINT_STRIDE)
    struct.pack_into("<dd", rec2, 0, 0.75, 0.125)
    struct.pack_into("<d", rec2, 16, -987654.321)
    header = bytearray(80)
    struct.pack_into("<I", header, 0, 2)
    blob = b"\x00" + b"sgSketch" + bytes(header) + bytes(rec) + bytes(rec2)
    sk = read_sketch_at(blob, find_sketch_offsets(blob)[0])
    assert sk.kind == "line"
    assert sk.points == [(0.5, 0.25, 0.0), (0.75, 0.125, 0.0)]  # z fixed at 0.0


def test_decode_circle_synthetic() -> None:
    pts = [(0.15, 0.12, 0.0), (0.1814, 0.12, 0.0)]
    blob = _build_sgsketch(pts, marker=1, code=1)
    sk = read_sketch_at(blob, find_sketch_offsets(blob)[0])
    assert sk.kind == "circle"          # (1,1) + 2 points
    assert sk.points == pts


def test_decode_arc_synthetic() -> None:
    pts = [(0.16, 0.11875, 0.0), (0.11, 0.13, 0.0), (0.21, 0.13, 0.0)]
    blob = _build_sgsketch(pts, marker=1, code=1)
    sk = read_sketch_at(blob, find_sketch_offsets(blob)[0])
    assert sk.kind == "arc"             # (1,1) + 3 points
    assert sk.point_count == 3
    assert sk.points == pts


def test_decode_empty_sketch() -> None:
    blob = _build_sgsketch([], marker=0, code=0)
    sk = read_sketch_at(blob, find_sketch_offsets(blob)[0])
    assert sk.point_count == 0
    assert sk.kind == "empty"
    assert sk.points == []
    assert sk.description == "empty (no entities)"


def test_decode_unknown_kind() -> None:
    """An unrecognised (marker, code) yields a diagnostic kind, never a crash."""
    blob = _build_sgsketch([(1.0, 2.0, 0.0)], marker=7, code=9)
    sk = read_sketch_at(blob, find_sketch_offsets(blob)[0])
    assert sk.kind == "unknown(marker=7,code=9)"


def test_find_multiple_sketches() -> None:
    a = _build_sgsketch([(1.0, 1.0, 0.0), (2.0, 2.0, 0.0)], marker=0, code=2, prefix=b"\x00")
    b = _build_sgsketch([(3.0, 3.0, 0.0), (4.0, 4.0, 0.0)], marker=1, code=1, prefix=b"\x11")
    blob = a + b
    offs = find_sketch_offsets(blob)
    assert len(offs) == 2
    assert read_sketch_at(blob, offs[0]).kind == "line"
    assert read_sketch_at(blob, offs[1]).kind == "circle"


def test_no_sketch_returns_empty() -> None:
    assert find_sketch_offsets(b"no sketch here at all") == []


def test_false_positive_marker_hit_does_not_crash() -> None:
    """A stray b"sgSketch" with a garbage u32 count (false-positive marker hit on
    a non-drawing stream) must NOT crash or loop — it decodes to kind 'invalid'.
    Regression for the unguarded struct.unpack_from in the first cut."""
    blob = b"\x00\x10junk" + b"sgSketch" + (0x41414141).to_bytes(4, "little") + b"\x00" * 40
    off = find_sketch_offsets(blob)[0]
    sk = read_sketch_at(blob, off)
    assert sk.kind == "invalid"
    assert sk.point_count == 0
    assert sk.points == []
    assert "invalid" in sk.description


def test_truncated_header_is_invalid() -> None:
    """b"sgSketch" at the very end (count u32 doesn't fit) → invalid, no crash."""
    blob = b"padding" + b"sgSketch" + b"\x01"  # only 1 of 4 count bytes
    sk = read_sketch_at(blob, find_sketch_offsets(blob)[0])
    assert sk.kind == "invalid"


def test_read_sketches_drops_invalid(tmp_path: Path) -> None:
    """read_sketches filters invalid hits: a blob with one real line sketch plus a
    trailing false-positive marker yields exactly the one valid sketch.

    Exercises the read_sketch_at + filter path directly (no file I/O) by reusing
    the in-memory decode, mirroring what read_sketches does over a def stream."""
    real = _build_sgsketch([(1.0, 2.0, 0.0), (3.0, 4.0, 0.0)], marker=0, code=2,
                           prefix=b"\x00")
    blob = real + b"sgSketch" + (0xDEADBEEF).to_bytes(4, "little") + b"\x00" * 8
    offs = find_sketch_offsets(blob)
    assert len(offs) == 2  # the real one + the false-positive marker
    decoded = [read_sketch_at(blob, o) for o in offs]
    kept = [s for s in decoded if s.kind != "invalid"]
    assert len(kept) == 1
    assert kept[0].kind == "line"


# --- fixture cross-checks (skip if the generated geom_* drawings are absent) --
_GEOM_DIR = ROOT / "research" / "empirical_findings" / "definition_decode"
_GEOM_CASES = {
    "geom_line": ("line", 2),
    "geom_circle": ("circle", 2),
    "geom_arc": ("arc", 3),
}


@pytest.mark.layer2
@pytest.mark.parametrize("name,expected", _GEOM_CASES.items())
def test_geom_fixture_decodes(name: str, expected: tuple[str, int]) -> None:
    from swformat.api.sketches import read_sketches

    path = _GEOM_DIR / f"{name}.SLDDRW"
    if not path.exists():
        pytest.skip(f"generated fixture not present: {path.name}")
    sketches = read_sketches(path)
    assert sketches, f"{name}: expected at least one sgSketch"
    kind, pcount = expected
    sk = sketches[0]
    assert sk.kind == kind
    assert sk.point_count == pcount
    assert len(sk.points) == pcount
    assert all(len(p) == 3 for p in sk.points)


# --- entity-array decode (multi-entity, indexed binding) ---------------------
# Synthetic builder for the VALIDATED entity-array layout (2026-06-10 corpus):
#   line  (92 B): u16@+16 = 0xbff0 (tag); start idx u16@+2, end idx u16@+4
#   circle(112 B): u32@+30 = 1; center idx u16@+34; perimeter idx u16@+16
#   arc   (112 B): u32@+30 = 0xffffffff; center u16@+34; e1 u16@+14; e2 u16@+16
#   terminal record: 0x8008 at record +size-4
_LINE_SIZE, _CURVE_SIZE = 92, 112


def _line_rec(start: int, end: int) -> bytearray:
    r = bytearray(_LINE_SIZE)
    struct.pack_into("<H", r, 16, 0xBFF0)            # line tag
    struct.pack_into("<H", r, 2, start)
    struct.pack_into("<H", r, 4, end)
    return r


def _circle_rec(center: int, perim: int) -> bytearray:
    r = bytearray(_CURVE_SIZE)
    struct.pack_into("<I", r, 30, 1)                 # closed flag → circle
    struct.pack_into("<H", r, 34, center)
    struct.pack_into("<H", r, 16, perim)
    return r


def _arc_rec(center: int, e1: int, e2: int) -> bytearray:
    r = bytearray(_CURVE_SIZE)
    struct.pack_into("<I", r, 30, 0xFFFFFFFF)        # open flag → arc
    struct.pack_into("<H", r, 34, center)
    struct.pack_into("<H", r, 14, e1)
    struct.pack_into("<H", r, 16, e2)
    return r


def _build_entity_sketch(points: list[tuple[float, float]], records: list[bytearray]) -> bytes:
    """Assemble an sgSketch blob with a real point array AND an entity array.
    Marks the last record terminal (0x8008 at +size-4)."""
    header = bytearray(80)
    struct.pack_into("<I", header, 0, len(points))
    body = bytes(header)
    for (x, y) in points:
        rec = bytearray(POINT_STRIDE)
        struct.pack_into("<dd", rec, 0, x, y)
        body += bytes(rec)
    ent = bytearray()
    for j, r in enumerate(records):
        r = bytearray(r)
        if j == len(records) - 1:
            r[len(r) - 4: len(r) - 2] = b"\x08\x80"  # terminal marker
        ent += r
    return b"\x00" + b"sgSketch" + body + bytes(ent)


def test_entity_line_indexed() -> None:
    pts = [(0.10, 0.20), (0.30, 0.40)]
    blob = _build_entity_sketch(pts, [_line_rec(0, 1)])
    sk = read_sketch_at(blob, find_sketch_offsets(blob)[0])
    assert len(sk.entities) == 1
    e = sk.entities[0]
    assert isinstance(e, SketchEntity)
    assert e.kind == "line"
    assert e.point_indices == [0, 1]
    assert e.points == [(0.1, 0.2, 0.0), (0.3, 0.4, 0.0)]
    assert sk.description == "1 entity: line"


def test_entity_circle_center_perimeter() -> None:
    pts = [(0.50, 0.50), (0.55, 0.50)]   # center idx 0, perimeter idx 1 (r=0.05)
    blob = _build_entity_sketch(pts, [_circle_rec(center=0, perim=1)])
    e = read_sketch_at(blob, find_sketch_offsets(blob)[0]).entities[0]
    assert e.kind == "circle"
    assert e.point_indices == [0, 1]
    assert e.points[0] == (0.5, 0.5, 0.0)            # center
    assert e.points[1] == (0.55, 0.5, 0.0)           # perimeter


def test_entity_arc_center_two_endpoints() -> None:
    pts = [(0.3, 0.3), (0.2, 0.4), (0.4, 0.4)]       # center 0, e1 1, e2 2
    blob = _build_entity_sketch(pts, [_arc_rec(center=0, e1=1, e2=2)])
    e = read_sketch_at(blob, find_sketch_offsets(blob)[0]).entities[0]
    assert e.kind == "arc"
    assert e.point_indices == [0, 1, 2]
    assert e.points == [(0.3, 0.3, 0.0), (0.2, 0.4, 0.0), (0.4, 0.4, 0.0)]


def test_entity_mixed_line_circle() -> None:
    pts = [(0.1, 0.1), (0.2, 0.1), (0.5, 0.5), (0.55, 0.5)]
    blob = _build_entity_sketch(pts, [_line_rec(0, 1), _circle_rec(center=2, perim=3)])
    sk = read_sketch_at(blob, find_sketch_offsets(blob)[0])
    assert [e.kind for e in sk.entities] == ["line", "circle"]
    assert sk.entities[0].points == [(0.1, 0.1, 0.0), (0.2, 0.1, 0.0)]
    assert sk.entities[1].point_indices == [2, 3]
    assert sk.description == "2 entities: line, circle"


def test_entity_rectangle_wrapping_indices() -> None:
    """The indexed-binding falsifier in synthetic form: 4 connected lines over 4
    SHARED corner points with indices (0,1)(1,2)(2,3)(3,0) — the wrap to 0 is
    impossible under positional binding."""
    pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    recs = [_line_rec(0, 1), _line_rec(1, 2), _line_rec(2, 3), _line_rec(3, 0)]
    sk = read_sketch_at(_build_entity_sketch(pts, recs),
                        find_sketch_offsets(_build_entity_sketch(pts, recs))[0])
    assert [e.point_indices for e in sk.entities] == [[0, 1], [1, 2], [2, 3], [3, 0]]
    # the closing edge binds corner 3 → corner 0
    assert sk.entities[3].points == [(0.0, 1.0, 0.0), (0.0, 0.0, 0.0)]


def test_entity_array_absent_yields_no_entities() -> None:
    """A legacy-style blob with NO entity array (old synthetic builder) must yield
    entities == [] (defensive), not a crash or garbage — read_sketch_at then
    reports the first-order kind."""
    blob = _build_sgsketch([(0.1, 0.2, 0.0), (0.3, 0.4, 0.0)], marker=0, code=2)
    sk = read_sketch_at(blob, find_sketch_offsets(blob)[0])
    assert sk.entities == []
    assert sk.kind == "line"                         # falls back to first-order


def test_entity_out_of_range_index_is_rejected() -> None:
    """An entity referencing a point index beyond the point array → entities == []
    (untrusted), never an IndexError."""
    pts = [(0.1, 0.2), (0.3, 0.4)]
    blob = _build_entity_sketch(pts, [_line_rec(0, 99)])   # 99 >= 2 points
    sk = read_sketch_at(blob, find_sketch_offsets(blob)[0])
    assert sk.entities == []


def test_read_sketch_entities_flattens(tmp_path: Path) -> None:
    """read_sketch_entities is exercised on the geom_mixed fixture if present
    (line+circle → 2 entities); skips cleanly when the fixture is absent."""
    from swformat.api.sketches import read_sketch_entities

    path = ROOT / "research" / "empirical_findings" / "sketch_relations" / "geom_mixed.SLDDRW"
    if not path.exists():
        pytest.skip("geom_mixed fixture not present")
    ents = read_sketch_entities(path)
    assert [e.kind for e in ents] == ["line", "circle"]


# --- MULTI-INSTANCE enumeration (M5.7) ---------------------------------------

def test_enumerate_finds_literal_and_interned() -> None:
    """A drawing interns the sgSketch class: the 2nd+ instances are CLASS_REF
    back-ref tags (0x80NN) + the same body, not literal 'sgSketch' strings.
    enumerate_sketches must find BOTH a literal sketch AND an interned one."""
    pts = [(0.1, 0.1), (0.2, 0.1)]
    lit = _build_entity_sketch(pts, [_line_rec(0, 1)])    # b"\x00sgSketch" + body
    body = lit[9:]                                        # strip prefix + name (9 B)
    interned = b"\x76\x80" + body                         # CLASS_REF 0x8076 + body
    blob = lit + b"\x00" * 4 + interned
    sks = enumerate_sketches(blob)
    assert len(sks) == 2
    assert all(s.entities and s.entities[0].kind == "line" for s in sks)
    assert [s.points for s in sks] == [[(0.1, 0.1, 0.0), (0.2, 0.1, 0.0)]] * 2


def test_enumerate_rejects_interned_garbage() -> None:
    """A high-bit u16 tag NOT followed by a valid sketch body must be ignored
    (the body pre-check + entity requirement), so no phantom sketch appears."""
    pts = [(0.1, 0.1), (0.2, 0.1)]
    lit = _build_entity_sketch(pts, [_line_rec(0, 1)])
    blob = lit + b"\x80\x80" + b"\x05\x00\x00\x00" + b"\xff" * 200   # bogus tag + junk
    sks = enumerate_sketches(blob)
    assert len(sks) == 1                                  # only the real literal


def test_two_sketch_fixture_enumerates() -> None:
    """Cross-check the multi-instance enumerator on the SW-generated 2-sheet
    fixture (a line on each sheet → 2 sketch instances); skips if absent."""
    path = ROOT / "research" / "empirical_findings" / "definition_decode" / "two_sketch.SLDDRW"
    if not path.exists():
        pytest.skip("two_sketch fixture not present")
    sks = read_sketches(path)
    assert len(sks) == 2
    assert sorted(p[1] for s in sks for p in s.points) == [0.1, 0.1, 0.2, 0.2]


# --- SPLINE record (M5.6) ----------------------------------------------------

def _spline_rec(indices: list[int]) -> bytearray:
    """Build a synthetic spline entity record matching the M5.6 layout the
    decoder reads: a leading block (control-point placeholder; byte +16 stays
    0x0000 so it is NOT mistaken for the 0xBFF0 line tag), the
    ``modifSplineList_c`` class string, the u16 fit-point count, the
    ``sgPointHandle`` class string, then the handle list (first fit point 10 B
    = idx+ffff+0000; each subsequent 12 B = backref+idx+ffff+0000). The
    control-point/knot block is not modelled (the decoder doesn't read it)."""
    r = bytearray(20)                                # leading block; +16 == 0x0000
    r += b"\x01\x00\x11\x00" + b"modifSplineList_c"  # len-prefixed class string
    r += struct.pack("<H", len(indices))             # fit-point count
    r += b"\x01\x00\x0d\x00" + b"sgPointHandle"
    for k, ix in enumerate(indices):
        if k == 0:
            r += struct.pack("<H", ix) + b"\xff\xff\xff\xff\x00\x00\x00\x00"
        else:                                         # backref value is arbitrary
            r += b"\x71\x80" + struct.pack("<H", ix) + b"\xff\xff\xff\xff\x00\x00\x00\x00"
    return r


def test_entity_spline_indexed() -> None:
    """A 3-fit-point spline binds its 3 point-array indices in order."""
    pts = [(0.10, 0.10), (0.15, 0.13), (0.20, 0.10)]
    blob = _build_entity_sketch(pts, [_spline_rec([0, 1, 2])])
    sk = read_sketch_at(blob, find_sketch_offsets(blob)[0])
    assert len(sk.entities) == 1
    e = sk.entities[0]
    assert e.kind == "spline"
    assert e.point_indices == [0, 1, 2]
    assert e.points == [(0.1, 0.1, 0.0), (0.15, 0.13, 0.0), (0.2, 0.1, 0.0)]
    assert sk.description == "1 entity: spline"


def test_entity_spline_count_scales() -> None:
    """The fit-point count + handle list scale (5 points → indices 0..4)."""
    pts = [(i / 10, 0.1) for i in range(5)]
    blob = _build_entity_sketch(pts, [_spline_rec([0, 1, 2, 3, 4])])
    e = read_sketch_at(blob, find_sketch_offsets(blob)[0]).entities[0]
    assert e.kind == "spline"
    assert e.point_indices == [0, 1, 2, 3, 4]


def test_entity_line_then_spline_coexist() -> None:
    """The key mixed case: a fixed-positional LINE record and the variable
    class-tagged SPLINE record coexist in one entity array; point indices are
    global+sequential (line→0,1; spline→2,3,4). Previously this yielded []."""
    pts = [(0.05, 0.05), (0.25, 0.05),               # line endpoints (idx 0,1)
           (0.10, 0.10), (0.15, 0.13), (0.20, 0.10)]  # spline fit points (idx 2,3,4)
    blob = _build_entity_sketch(pts, [_line_rec(0, 1), _spline_rec([2, 3, 4])])
    sk = read_sketch_at(blob, find_sketch_offsets(blob)[0])
    assert [e.kind for e in sk.entities] == ["line", "spline"]
    assert sk.entities[0].point_indices == [0, 1]
    assert sk.entities[1].point_indices == [2, 3, 4]
    assert sk.description == "2 entities: line, spline"


def test_entity_spline_out_of_range_index_rejected() -> None:
    """A spline fit-point index beyond the point array → entities == [] (the
    spline decoder returns None → defensive empty), never an IndexError."""
    pts = [(0.1, 0.1), (0.2, 0.1)]                   # only 2 points
    blob = _build_entity_sketch(pts, [_spline_rec([0, 1, 9])])  # 9 out of range
    assert read_sketch_at(blob, find_sketch_offsets(blob)[0]).entities == []


def test_spline_fixture_decodes() -> None:
    """Cross-check against the SW-generated spline fixtures if present; skips
    cleanly in CI where the (synthetic, non-git-tracked) fixtures are absent."""
    base = ROOT / "research" / "empirical_findings" / "sketch_splines"
    path = base / "sp_line_then_spline.SLDDRW"
    if not path.exists():
        pytest.skip("generated spline fixture not present")
    from swformat.api.sketches import read_sketch_entities
    ents = read_sketch_entities(path)
    assert [e.kind for e in ents] == ["line", "spline"]
    assert ents[1].point_indices == [2, 3, 4]


# --- SKETCH RELATIONS (constraints, M5.4 read side) --------------------------

def test_relations_region_reads_types() -> None:
    """Two relation records (HORIZONTAL=4, PARALLEL=7), each ``<type u32><anchor>``
    — both are found and named. The anchor (not the longer signature) is used so
    interned-handle records are not undercounted."""
    buf = (b"XX" + struct.pack("<I", 4) + _REL_ANCHOR + b"handle-junk"
           + struct.pack("<I", 7) + _REL_ANCHOR + b"tail")
    rels = _relations_in_region(buf, 0, len(buf))
    assert [(r.type_id, r.type_name) for r in rels] == [(4, "HORIZONTAL"), (7, "PARALLEL")]


def test_relations_implausible_type_skipped() -> None:
    """An anchor preceded by an out-of-range u32 (not a swConstraintType_e) is a
    chance match and must be skipped, not fabricated into a relation."""
    buf = b"\x00\x00\x00\x00" + struct.pack("<I", 0xDEADBEEF) + _REL_ANCHOR + b"xx"
    assert _relations_in_region(buf, 0, len(buf)) == []


def test_relations_unmapped_type_named_generically() -> None:
    """A plausible-but-unmapped type id is still reported, named ``type(N)``."""
    buf = b"ZZZZ" + struct.pack("<I", 13) + _REL_ANCHOR   # 13 = ATINTERSECT (mapped)
    buf2 = b"ZZZZ" + struct.pack("<I", 60) + _REL_ANCHOR  # 60 = plausible, unmapped here
    assert _relations_in_region(buf, 0, len(buf))[0].type_name == "ATINTERSECT"
    assert _relations_in_region(buf2, 0, len(buf2))[0].type_name == "type(60)"


def test_relations_region_empty_when_no_anchor() -> None:
    assert _relations_in_region(b"no relations here" * 4, 0, 68) == []


def _rel_full_handle(cls: bytes, idx: int) -> bytes:
    """A FULL relation handle: ff ff 01 00 | u16 len | class | u16 idx | trailer."""
    return (b"\xff\xff\x01\x00" + struct.pack("<H", len(cls)) + cls
            + struct.pack("<H", idx) + b"\xff\xff\xff\xff\x00\x00\x00\x00")


def _rel_interned_handle(idx: int, backref: int = 0x806F) -> bytes:
    """An INTERNED relation handle: u16 back-ref (high bit set) | u16 idx | trailer."""
    return struct.pack("<HH", backref, idx) + b"\xff\xff\xff\xff\x00\x00\x00\x00"


def test_relation_entity_binding_full_handle() -> None:
    """A HORIZONTAL relation's single full handle binds one entity index."""
    buf = (b"ZZZZ" + struct.pack("<I", 4) + _REL_ANCHOR
           + _rel_full_handle(b"sgLineHandle", 1) + b"\x00\x00\x00\x00")
    r = _relations_in_region(buf, 0, len(buf))[0]
    assert (r.type_name, r.entity_indices) == ("HORIZONTAL", [1])


def test_relation_entity_binding_multi_handle() -> None:
    """A PARALLEL relation chains a full handle + an interned back-ref handle →
    two entity indices (the parallel fixture's [1, 0])."""
    buf = (b"ZZZZ" + struct.pack("<I", 7) + _REL_ANCHOR
           + _rel_full_handle(b"sgLineHandle", 1) + _rel_interned_handle(0)
           + b"\x00\x00\x00\x00")
    r = _relations_in_region(buf, 0, len(buf))[0]
    assert (r.type_name, r.entity_indices) == ("PARALLEL", [1, 0])


def test_relation_handles_stop_at_padding() -> None:
    """The greedy handle walk stops at non-handle bytes (zeros), not over-reads."""
    buf = _rel_full_handle(b"sgArcHandle", 0) + b"\x00" * 16
    assert _relation_handles(buf, 0, len(buf)) == [0]


@pytest.mark.parametrize("name,expected", [
    ("rel_base", []),
    ("rel_horiz1", [("HORIZONTAL", [0])]),
    ("rel_horiz2", [("HORIZONTAL", [0]), ("HORIZONTAL", [1])]),
    ("rel_parallel", [("PARALLEL", [1, 0])]),
    ("rel_fix_circle", [("FIXED", [0])]),
])
def test_relation_fixture_decodes(name: str, expected: list) -> None:
    """Cross-check read_sketch_relations on the SW-generated relation fixtures;
    skips cleanly when absent. Asserts both TYPE and ENTITY binding. rel_horiz2
    (2 relations sharing the sgLineHandle class) confirms the anchor counts the
    interned 2nd relation, and that its handle binds the 2nd line (index 1)."""
    path = ROOT / "research" / "empirical_findings" / "sketch_relations" / f"{name}.SLDDRW"
    if not path.exists():
        pytest.skip(f"generated relation fixture not present: {name}")
    assert [(r.type_name, r.entity_indices) for r in read_sketch_relations(path)] == expected


# --- SKETCH DIMENSIONS (driving dimensions, M5.5 read side) ------------------

def _dim_cluster(value: float, display_tag: bytes) -> bytes:
    """Build a synthetic dimension cluster: the param object's interning-immune
    VALUE SIGNATURE immediately before the value f64 (the anchor the reader uses),
    then a display-class string (the kind)."""
    rec = bytearray(b"moLengthParameter_c")
    rec += b"\x00" * 8                                # some param fields
    rec += _DIM_VALUE_SIG                             # the interning-immune anchor
    rec += struct.pack("<d", value)                  # value at signature + 24
    rec += b"\x00" * 8
    rec += display_tag
    return bytes(rec)


def test_dimensions_region_distance_and_value() -> None:
    buf = b"prefix" + _dim_cluster(0.10, b"moDisplayDistanceDim_c") + b"tail"
    dims = _dimensions_in_region(buf, 0, len(buf))
    assert [(d.kind, d.value) for d in dims] == [("distance", 0.1)]


def test_dimensions_region_radial_stores_diameter() -> None:
    buf = b"x" + _dim_cluster(0.08, b"moDisplayRadialDim_c")
    dims = _dimensions_in_region(buf, 0, len(buf))
    assert dims[0].kind == "radial"
    assert dims[0].value == 0.08


def test_dimensions_two_clusters_dont_crossclassify() -> None:
    buf = (b"a" + _dim_cluster(0.10, b"moDisplayDistanceDim_c")
           + b"b" + _dim_cluster(0.08, b"moDisplayRadialDim_c"))
    dims = _dimensions_in_region(buf, 0, len(buf))
    assert [(d.kind, d.value) for d in dims] == [("distance", 0.1), ("radial", 0.08)]


def test_dimensions_no_param_is_empty() -> None:
    assert _dimensions_in_region(b"no dimensions here at all" * 3, 0, 75) == []


def test_dimensions_multi_instance_value_only() -> None:
    """The signature anchor finds BOTH a full-form dim (kind via the display
    string) AND an interned one (signature + value, no display string → value
    only, kind 'unknown') — the multi-instance behaviour."""
    full = _dim_cluster(0.10, b"moDisplayDistanceDim_c")
    interned = _DIM_VALUE_SIG + struct.pack("<d", 0.25)   # signature + value, no display
    buf = b"a" + full + b"b" * 8 + interned + b"tail"
    dims = _dimensions_in_region(buf, 0, len(buf))
    assert [(d.kind, d.value) for d in dims] == [("distance", 0.1), ("unknown", 0.25)]


def test_two_dim_fixture_enumerates() -> None:
    """Cross-check on the SW-generated 2-sheet 2-dimension fixture: both
    dimensions found (was 1 before the signature anchor); skips if absent."""
    path = ROOT / "research" / "empirical_findings" / "sketch_dimensions" / "two_dim.SLDDRW"
    if not path.exists():
        pytest.skip("two_dim fixture not present")
    dims = read_sketch_dimensions(path)
    assert len(dims) == 2
    assert all(abs(d.value - 0.1) < 1e-6 for d in dims)


def test_ndim7_dimension_count_oracle_xfail() -> None:
    """REGRESSION ORACLE for the dimension-reader under-count (2026-06-10 22:45).

    ``ndim7.SLDDRW`` is a synthetic 2-sheet fixture with a SW-AUTHORITATIVE count
    of 7 display dimensions (ALPHA 4 + BETA 3, confirmed via per-sheet
    ``IView.GetDimensionCount2``). The byte-anchored reader currently returns only
    2 — ``_DIM_VALUE_SIG`` matches just a sub-form of dimension serialization
    (proof the reader is NOT a reliable enumerator; see the docstring on
    ``read_sketch_dimensions`` + ``sketch_dimensions/log.md``).

    This test asserts the CORRECT answer (7) and is expected to FAIL until the
    CArchive class table (keystone) lands and lets the reader walk true dimension
    objects. When it starts passing, the dimension reader has been fixed — remove
    the ``xfail``. Skips if the (gitignored, regenerable via gen_ndim.csx) fixture
    is absent."""
    path = ROOT / "research" / "empirical_findings" / "sketch_dimensions" / "ndim7.SLDDRW"
    if not path.exists():
        pytest.skip("ndim7 fixture not present (regenerate via scripts/gen_ndim.csx)")
    dims = read_sketch_dimensions(path)
    if len(dims) != 7:
        pytest.xfail(f"known under-count: reader finds {len(dims)} of 7 "
                     f"(needs the CArchive class table; see sketch_dimensions/log.md)")
    assert len(dims) == 7   # reached only once the keystone fixes the reader


def test_move_sketch_point_roundtrip(tmp_path: Path) -> None:
    """MODIFY: move a sketch point and write back; the output re-decodes with the
    moved coordinate (read→modify→write). Uses geom_line if present; skips else.
    SW-verifies separately (clean reopen) — see verify_moved_point.csx."""
    from swformat.api.sketches import move_sketch_point
    src = ROOT / "research" / "empirical_findings" / "definition_decode" / "geom_line.SLDDRW"
    if not src.exists():
        pytest.skip("geom_line fixture not present")
    out = tmp_path / "moved.SLDDRW"
    pts = move_sketch_point(src, out, point_index=1, x=0.25, y=0.15)
    assert pts[1] == (0.25, 0.15, 0.0)
    # point 0 unchanged; re-read independently confirms persistence
    assert read_sketches(out)[0].points[1] == (0.25, 0.15, 0.0)


def test_move_sketch_point_by_sketch_index(tmp_path: Path) -> None:
    """MODIFY: ``sketch_index`` reaches a NON-first sketch — including an interned
    (CLASS_REF) one — on the SW-generated 2-sheet fixture. Editing sketch[1]'s
    point persists on re-decode and leaves sketch[0] untouched. Skips if absent."""
    from swformat.api.sketches import move_sketch_point
    src = ROOT / "research" / "empirical_findings" / "definition_decode" / "two_sketch.SLDDRW"
    if not src.exists():
        pytest.skip("two_sketch fixture not present")
    before = read_sketches(src)
    assert len(before) == 2
    s0_before = before[0].points
    out = tmp_path / "moved1.SLDDRW"
    pts = move_sketch_point(src, out, point_index=0, x=0.42, y=0.37, sketch_index=1)
    assert pts[0] == (0.42, 0.37, 0.0)
    after = read_sketches(out)
    assert after[1].points[0] == (0.42, 0.37, 0.0)   # edit persisted on sketch[1]
    assert after[0].points == s0_before              # sketch[0] untouched


def test_move_dimension_text_roundtrip(tmp_path: Path) -> None:
    """MODIFY: move a dimension's text placement and write back; the output
    re-decodes with the moved placement (annotation-only edit). Uses dim_len if
    present; skips else. SW-verified separately (clean reopen)."""
    from swformat.api.sketches import move_dimension_text
    src = ROOT / "research" / "empirical_findings" / "sketch_dimensions" / "dim_len.SLDDRW"
    if not src.exists():
        pytest.skip("dim_len fixture not present")
    out = tmp_path / "textmoved.SLDDRW"
    xy = move_dimension_text(src, out, 0.30, 0.25)
    assert xy == (0.30, 0.25)
    assert read_sketch_dimensions(out)[0].text_xy == (0.30, 0.25)


def test_set_dimension_value_roundtrip(tmp_path: Path) -> None:
    """MODIFY: set a dimension value and write back; the output re-decodes with
    the new value (read→modify→write). SW-verified separately that SW re-solves
    the driving dimension (resizes geometry: 0.10→0.15 moved the line endpoint)."""
    from swformat.api.sketches import set_dimension_value
    src = ROOT / "research" / "empirical_findings" / "sketch_dimensions" / "dim_len.SLDDRW"
    if not src.exists():
        pytest.skip("dim_len fixture not present")
    out = tmp_path / "valset.SLDDRW"
    v = set_dimension_value(src, out, 0.15)
    assert v == 0.15
    assert read_sketch_dimensions(out)[0].value == 0.15


def test_set_dimension_value_by_index(tmp_path: Path) -> None:
    """set_dimension_value(dim_index=N) targets the N-th dimension — including an
    INTERNED one (the value anchor is interning-immune). Uses the 2-dimension
    two_dim fixture; skips if absent."""
    from swformat.api.sketches import set_dimension_value
    src = ROOT / "research" / "empirical_findings" / "sketch_dimensions" / "two_dim.SLDDRW"
    if not src.exists():
        pytest.skip("two_dim fixture not present")
    out = tmp_path / "valset2.SLDDRW"
    set_dimension_value(src, out, 0.22, dim_index=1)   # the 2nd (interned) dim
    vals = sorted(d.value for d in read_sketch_dimensions(out))
    assert vals == [0.1, 0.22]                         # 1st unchanged, 2nd edited


@pytest.mark.parametrize("name,expected", [
    ("dim_base", []),
    ("dim_len", [("distance", 0.1, [0, 1], (0.15, 0.13))]),
    ("dim_len2", [("distance", 0.1, [0, 1], (0.15, 0.16))]),
    ("dim_len_long", [("distance", 0.15, [0, 1], (0.175, 0.13))]),
    ("dim_radius", [("radial", 0.08, [0], (0.2, 0.2))]),
    ("dim_circle_base", []),
    ("dim_angle_base", []),
    ("dim_angle", [("angular", 1.012197, [0, 1], (0.16, 0.12))]),
    # A diameter dim on a sketch circle decodes as radial (same display class),
    # value = diameter (0.08), bound to the arc entity [0].
    ("dim_diam", [("radial", 0.08, [0], (0.22, 0.22))]),
])
def test_dimension_fixture_decodes(name: str, expected: list) -> None:
    """Cross-check read_sketch_dimensions on the SW-generated dimension fixtures;
    skips cleanly when absent. Asserts kind, value, refs AND text placement.
    Radial confirms the stored value is the diameter and that the null
    sgEntHandle is filtered (→ [0], the arc); distance binds the line's two
    endpoint points ([0, 1]); dim_len vs dim_len2 confirms the placement Y
    tracks (0.13 → 0.16) at the same display-anchored offset."""
    path = ROOT / "research" / "empirical_findings" / "sketch_dimensions" / f"{name}.SLDDRW"
    if not path.exists():
        pytest.skip(f"generated dimension fixture not present: {name}")
    got = [(d.kind, d.value, d.refs, d.text_xy) for d in read_sketch_dimensions(path)]
    assert got == expected
