"""High-level drawing-sketch geometry API (M5.3, read side) — decode the
``sgSketch`` point/entity arrays out of a drawing's ``Contents/Definition``
CArchive **without SOLIDWORKS**.

------------------------------------------------------------------------------
WHY THIS EXISTS
------------------------------------------------------------------------------
A SOLIDWORKS drawing sheet carries its 2-D sketch geometry (the lines, circles
and arcs you draw on the sheet) inside the drawing's ``Contents/Definition``
MFC-CArchive blob, in a structure tagged by the class name ``sgSketch``. Unlike
the property streams (which are index-objects pointing into an object map),
``sgSketch`` stores its geometry **inline**: a fixed-shape header, then a flat
array of *point* records, then a flat array of *entity* records. Decoding it
needs no object-map walk — just the byte layout established by the 2026-06-10
diff-pair campaign (documented in the project's reverse-engineering notes).

This module is the read-only Layer-4 wrapper over that validated decoder. The
prototype it was promoted from is
``research/empirical_findings/definition_decode/read_sketch.py``; the decode is
ground-truth-validated against the single-primitive ``geom_*`` fixtures
(``geom_line``, ``geom_circle``, ``geom_arc``, ``geom_2line``).

------------------------------------------------------------------------------
BYTE LAYOUT (sgSketch body) — enough to reconstruct this decoder
------------------------------------------------------------------------------
Let ``sg`` be the offset of the literal ASCII bytes ``b"sgSketch"`` in the
decompressed ``Contents/Definition`` stream. The class *body* begins right after
that 8-byte name::

    body = sg + 8

    body + 0                : u32  point_count          (number of point records)
    body + 0 .. +80         : header region (80 B total, INCLUDES the u32 count)
    body + 80               : POINT ARRAY start
        point[i]            : 142-byte record, stride POINT_STRIDE = 142
            point[i] + 0    : f64 x        (the coordinate payload is 2-D: x, y ONLY)
            point[i] + 8    : f64 y
            point[i] + 16.. : STRUCTURE (not a z coordinate — see note below);
                              includes the entity marker/code the classifier reads.
            (first point's x therefore sits at body + 80 = body + PCOUNT_TO_FIRSTPOINT)

    Z NOTE: drawing sheet sketches are 2-D (planar) — there is NO stored z. An
    earlier cut unpacked a 3rd f64 at point+16 as "z"; that field is structural
    (its bytes are e.g. `00 00 01 00 00 00 00 00` = 0x10000, a denormal that
    rounds to ~0, NOT a clean f64 0.0 which would be 8 zero bytes). It only
    *looked* like z=0 by rounding. The decoder therefore reads (x, y) and reports
    z = 0.0 explicitly (correct for 2-D drawing geometry, and robust — it no
    longer depends on those structural bytes staying tiny).
    body + 80 + n*142       : ENTITY ARRAY start (n = point_count)
        per-entity type fields, read relative to the first point region:
            base = body + 80
            base + 24       : u8  marker        (ENTITY_MARKER_OFF = 24)
            base + 72       : u32 code

Entity-type key (marker, code) + point_count → kind:

    LINE   : marker 0, code 2,  2 points (the two endpoints)
    CIRCLE : marker 1, code 1,  2 points (center + a perimeter point)
    ARC    : marker 1, code 1,  3 points (center + two endpoints)

(circle vs arc is disambiguated by point_count: 2 → circle, 3 → arc.)

The empty-sketch overhead (no entities) is 317 B; ``point_count == 0`` then.

------------------------------------------------------------------------------
SCOPE / HONESTY
------------------------------------------------------------------------------
* The point-array decode (x, y as f64; z fixed at 0.0 for 2-D drawing geometry —
  see the Z NOTE above) and the single-primitive classification are VALIDATED on
  the ``geom_*`` fixtures.
* Multi-entity point→entity *binding* is EXPLICITLY INDEXED, not positional —
  PROVEN by the ``geom_rect`` fixture (4 connected lines, 4 SHARED corner points,
  line records referencing point indices (0,1)(1,2)(2,3)(3,0) — the wrap to 0
  closes the loop and is impossible under positional binding). A line entity
  record stores its start/end point index as a u16 at record +2 / +4. Connected
  geometry SHARES points, so ``point_count`` is the number of UNIQUE points
  (NOT endpoints×entities) and the entity count must be found by walking the
  entity array (terminated by ``0x8008``), not derived from ``point_count``.
* CURRENT SURFACE: :func:`read_sketches` returns the raw point array plus the
  first-order ``kind``, AND iterates the entity array into per-entity
  ``SketchEntity`` objects (line/circle/arc/spline) with point-index→coordinate
  binding. The raw ``kind`` field still reflects only the first entity (it is the
  legacy first-order classifier); the authoritative per-entity view is
  ``Sketch.entities`` / :func:`read_sketch_entities`.
* SPLINE support (M5.6): the spline record is variable-size and class-string-
  tagged (``modifSplineList_c``), unlike the fixed positional line/circle/arc
  records. The decoder emits a ``"spline"`` entity bound to its fit-point indices
  (verified on sp_3pt/4pt/5pt + a mixed line+spline fixture). KNOWN LIMITATION:
  the control-point/knot block inside the record is not decoded, so the record's
  total size is unknown — the walker emits the spline then STOPS (a sketch with
  entities AFTER a spline is truncated at the spline). See the M5.6 entry in
  research/empirical_findings/sketch_splines/log.md.
* Read-only. No SW required. There is no sketch *writer* (that is far-future M5+
  byte-synthesis work).

* RELATIONS (M5.4 read side): geometric constraints (horizontal, parallel,
  fixed, …) are stored OUT-OF-LINE in a block between the last entity record and
  the entity list's ``0x8008`` terminator. :func:`read_sketch_relations` reports
  each relation's TYPE (``swConstraintType_e``) by anchoring on the constant
  schema-marker+sentinel run every relation record carries. Entity binding
  (which entities a relation joins) is a follow-up — see :class:`SketchRelation`
  and the M5.4 implementation note in the definition_decode hypothesis log.

Public surface:
- :class:`Sketch`                 — one decoded ``sgSketch`` occurrence (raw point
                                    array + first-order ``kind`` + decoded ``entities``).
- :class:`SketchEntity`           — one line/circle/arc/spline with point-resolved coords.
- :class:`SketchRelation`         — one geometric constraint (type + name).
- :func:`find_sketch_offsets`     — locate every ``sgSketch`` in a def blob.
- :func:`read_sketch_at`          — decode one occurrence at a known offset.
- :func:`read_sketches`           — decode all sketches for a drawing file path.
- :func:`read_sketch_entities`    — all per-entity geometry across a drawing.
- :func:`read_sketch_relations`   — all geometric constraints across a drawing.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from swformat.io.reader import read_document
from swformat.io.writer import set_stream_payload, write_with_toc

_DEF_STREAM = "Contents/Definition"

# --- validated byte-layout constants (see module docstring) ------------------
POINT_STRIDE = 142            # bytes per point record
PCOUNT_TO_FIRSTPOINT = 80     # sgSketch-body offset of the first point's x (== header size)
ENTITY_MARKER_OFF = 24        # u8 marker, relative to the first-point region base
_CODE_OFF = 72                # u32 entity code, relative to the same base

# (marker, code) → coarse kind; circle/arc split by point_count downstream.
_TYPE = {(0, 2): "line", (1, 1): "circle_or_arc"}

# --- ENTITY ARRAY layout (validated 2026-06-10 on the SW-generated corpus) ----
# The entity array follows the point array (body + 80 + point_count*142): a flat
# concatenation of per-entity records terminated by 0x8008 in the LAST record's
# tail. Per-entity point→entity binding is by explicit point INDEX (proven by the
# rectangle fixture: line indices (0,1)(1,2)(2,3)(3,0) — the wrap closes the loop).
_ENT_LINE_SIZE = 92            # line entity record size
_ENT_CURVE_SIZE = 112          # circle/arc entity record size
_ENT_TAG_OFF = 16              # offset of the line-vs-curve discriminator u16
_ENT_LINE_TAG = 0xBFF0         # u16 @ +16 of every LINE record (-1.0 f64 hi word);
#                                curves put a (small) point index there instead.
_ENT_LINE_START = 2            # line: u16 start-point index @ +2
_ENT_LINE_END = 4             #       u16 end-point index   @ +4
_ENT_CURVE_FLAG = 30          # curve: u32 closed/open flag @ +30 (1=circle, -1=arc)
_ENT_CURVE_CENTER = 34        # curve: u16 center-point index @ +34
_ENT_CURVE_MID = 14          # arc: u16 first-endpoint index @ +14
_ENT_CURVE_LAST = 16          # circle perimeter / arc 2nd-endpoint index @ +16
_ENT_TERMINATOR = b"\x08\x80"  # marks the last entity (at record +size-4)
_ENT_MAX = 100000              # hard loop guard

# --- SPLINE record (M5.6, validated 2026-06-10 on sp_3pt/4pt/5pt + mixed) -----
# Unlike line/circle/arc (fixed-size records discriminated positionally by the
# u16 @ +16 tag), a spline is a VARIABLE-size record carrying an explicit class
# string ``modifSplineList_c``. Layout (relative to the class string):
#     modifSplineList_c            (u16 len 17 + ASCII)
#     +0 after string : u16 fit-point COUNT
#     ... sgPointHandle (u16 len 13 + ASCII) ...
#     after sgPointHandle string:
#         u16 index[0]  | ff ff ff ff | 00 00 00 00            (first fit point, 10 B)
#         then (count-1) ×: u16 backref(<0x8000 set>) | u16 index[k] | ff ff ff ff | 00 00 00 00  (12 B)
# The fit points are entries in the 142-B point array (GLOBAL sequential indices;
# a line+spline sketch gives the line points 0,1 and the spline 2,3,4). The
# back-ref VALUE is intern-table-dependent (do NOT hardcode it); only its slot
# position is fixed, so indices are read structurally by stride.
#
# KNOWN LIMITATION: the control-point / weight / knot-vector f64 block inside the
# record is NOT yet decoded, so the spline record's total SIZE is unknown — the
# walker emits the spline entity (kind + fit-point indices) then STOPS (cannot
# step to a following record). In practice the spline is created last; a sketch
# with entities AFTER a spline is truncated at the spline. See the M5.6 entry in
# research/empirical_findings/sketch_splines/log.md.
_ENT_SPLINE_TAG = b"modifSplineList_c"   # class string identifying a spline record
_ENT_SPLINE_PTHANDLE = b"sgPointHandle"  # binding-handle class inside a spline
_ENT_SPLINE_SCAN = 96                    # bytes after record start to find the tag
_ENT_SPLINE_FIRST_STRIDE = 10            # first fit-point handle: idx(2)+ffff(4)+0000(4)
_ENT_SPLINE_REPEAT_STRIDE = 12           # subsequent: backref(2)+idx(2)+ffff(4)+0000(4)

# --- SKETCH RELATIONS (constraints) — M5.4 decode, read side ------------------
# Relations live OUT-OF-LINE in a block between the last entity record and the
# entity list's 0x8008 terminator (see definition_decode/log.md, M5.4). Each
# relation record is:
#     00 | type:u32 | 02 00 00 00 | 00 00 fe ff 00 00 | <handle list> | pad
# The 10-byte run ``02 00 00 00 00 00 fe ff 00 00`` (the constant schema marker
# + the 0xFFFE sentinels) is present in EVERY relation record — both the full-
# handle form and the interned-handle (0x8000-tagged back-ref) form — so it is
# the reliable per-relation ANCHOR; the ``type:u32`` (swConstraintType_e) is the
# 4 bytes immediately BEFORE it. (An earlier longer signature that also required
# the trailing ``ff ff 01 00`` UNDERCOUNTED: a 2nd relation sharing a handle
# class writes a back-ref there, not ``ff ff`` — see the 2026-06-10 implementation
# note in the log. The short anchor counts rel_horiz2's two relations correctly.)
# ENTITY BINDING (which entities each relation joins) needs the full-vs-interned
# handle-list parse and is a FOLLOW-UP; this surface reports relation TYPE+COUNT,
# validated on rel_horiz1/2 (HORIZONTAL ×1/×2), rel_parallel (PARALLEL),
# rel_fix_circle (FIXED), with zero false positives on pure geometry / dimension
# / spline fixtures.
_REL_ANCHOR = b"\x02\x00\x00\x00\x00\x00\xfe\xff\x00\x00"
_REL_TYPE_MIN, _REL_TYPE_MAX = 1, 90     # plausible swConstraintType_e range (max known 84)
# HANDLE LIST (after the anchor) — one handle per bound entity, each ending in
# an 8-byte trailer (ff ff ff ff 00 00 00 00). Two forms:
#   FULL     : ff ff | 01 00 | u16 len | class name | u16 ENTITY index | <8-B trailer>
#   INTERNED : <u16 back-ref, high bit set> | u16 ENTITY index | <8-B trailer>
# The first handle of a relation written before its handle-class is interned is
# FULL; later relations sharing the class use the INTERNED back-ref. A multi-
# entity relation (e.g. PARALLEL) chains additional handles after the first.
# The ENTITY index is the entity-array index (proven M5.4: line[1] → 1, not its
# point index 2). Parsing is bounded by the next relation anchor.
_REL_H_FULL = 0xFFFF                      # u16 marking a full (string) handle
_REL_H_TRAILER = 8                        # ff ff ff ff 00 00 00 00 after each handle's index
_REL_H_MAX = 64                           # hard guard on handles per relation
# swConstraintType_e int → name (verified subset from swconst_enums.txt; 4/7/17
# cross-checked against live SW-generated fixtures).
_CONSTRAINT_TYPES = {
    1: "DISTANCE", 2: "ANGLE", 3: "RADIUS", 4: "HORIZONTAL", 5: "VERTICAL",
    6: "TANGENT", 7: "PARALLEL", 8: "PERPENDICULAR", 9: "COINCIDENT",
    10: "CONCENTRIC", 11: "SYMMETRIC", 12: "ATMIDDLE", 13: "ATINTERSECT",
    14: "SAMELENGTH", 15: "DIAMETER", 17: "FIXED", 27: "COLINEAR",
    28: "CORADIAL", 32: "USEEDGE", 40: "ATPIERCE", 42: "MERGEPOINTS",
    44: "ARCLENGTH",
}

# --- SKETCH DIMENSIONS (driving dimensions) — M5.5 decode, read side ----------
# A driving dimension is a cluster of CArchive objects (see the graduated lesson
# lesson_20260610_sgsketch_drawing_dimension_encoding.md): a geometry-kind object
# (sgPntPntDist / sgCircleDim …), a ``moLengthParameter_c`` that HOLDS THE VALUE,
# a handle list, and a display object whose class names the dimension KIND. We
# anchor on ``moLengthParameter_c`` — exactly ONE per dimension, and absent from
# pure-geometry / relation / spline sketches — read the value f64 at a fixed
# offset into it, and classify by the nearest following display-class string.
# Value semantics: a RADIAL dimension stores the DIAMETER (verified: r=0.04 →
# 0.08), not the radius. Value is reported as the stored f64; the caller
# interprets per ``kind``. Entity binding is a follow-up (as for relations).
# The value-holding parameter object; exactly ONE per dimension, and the CLASS
# depends on the dimension type — moLengthParameter_c (distance/radial; value in
# meters) vs moAngleParameter_c (angular; value in RADIANS). Anchor on either.
_DIM_PARAMS = (b"moLengthParameter_c", b"moAngleParameter_c")
_DIM_VALUE_AFTER_PARAM = 32              # f64 value offset, relative to the param-class-string END
#   (so it's the same constant despite the class names differing in length:
#    moLengthParameter_c is 19 B → value @ start+51; moAngleParameter_c is 18 B
#    → value @ start+50; both = string_end + 32.)
_DIM_DISPLAY = {                         # display-class string → dimension kind
    b"moDisplayDistanceDim_c": "distance",
    b"moDisplayRadialDim_c": "radial",   # value is the DIAMETER. NOTE: a DIAMETER
    #   dimension on a sketch circle ALSO uses this class (verified: AddDiameter-
    #   Dimension → moDisplayRadialDim_c, value=diameter, byte-identical to a
    #   radius dim in the display region) — the R-vs-⌀ display is a flag, not the
    #   class. So both radius and diameter dims decode as kind="radial".
    b"moDisplayAngularDim_c": "angular",
    # The two below are SPECULATIVE (not observed on sketch dims; kept defensively
    # — they may surface for model/non-sketch dimensions). A sketch-circle
    # diameter dim does NOT use moDisplayDiameterDim_c (see the radial note above).
    b"moDisplayDiameterDim_c": "diameter",
    b"moDisplayLinearDim_c": "linear",
}
_DIM_DISPLAY_SCAN = 600                  # bytes after the param to find the display class
# Handle-class strings that begin a dimension's handle list; the list starts
# _DIM_HANDLE_BACKUP bytes before the first such string (over ``ff ff 01 00`` +
# the u16 length). Indices >= _DIM_REF_MAX are treated as null/garbage (e.g. a
# radial dimension's auxiliary ``sgEntHandle`` carries a 0xFFFE-ish null index).
_DIM_HANDLE_CLASSES = (b"sgPointHandle", b"sgLineHandle", b"sgArcHandle")
_DIM_HANDLE_BACKUP = 6
_DIM_REF_MAX = 4096
# Dimension TEXT-PLACEMENT 2-D point, as two f64 at a fixed offset past the END
# of the display-class string (so it's the same constant for distance vs radial,
# whose class names differ in length). Verified: distance @ disp+442/+450 (string
# len 22), radial @ disp+440/+448 (string len 20) → both = (string_end)+420/+428.
_DIM_PLACE_X_OFF = 420                    # f64 X, relative to display-class string END
_DIM_PLACE_Y_OFF = 428                    # f64 Y, relative to display-class string END
# INTERNING-IMMUNE value anchor (M5.7): this 24-byte run inside the parameter
# object immediately PRECEDES the value f64 — identical for the full-form AND the
# interned (CLASS_REF) form, and for distance / radial / angular dims (verified:
# 1 hit per dim with the right value on every fixture; 2 hits = both instances on
# the 2-dimension two_dim fixture; zero false positives on geometry/relation/
# spline files). So anchoring on it ENUMERATES every dimension (incl. interned),
# giving the value; kind/refs/placement still need the display/handle class
# strings (present only for the full-form instance).
_DIM_VALUE_SIG = bytes.fromhex("31000000000000000040ffffffff00000000fffeff000000")


@dataclass
class SketchEntity:
    """One geometric entity within a sketch, with its points resolved to coords.

    Attributes:
        kind:           ``"line"``, ``"circle"``, ``"arc"`` or ``"spline"``.
        point_indices:  the entity's point-array indices, in role order —
                        line: [start, end]; circle: [center, perimeter];
                        arc: [center, endpoint1, endpoint2];
                        spline: the fit-point indices, in order (the user's
                        through-points; the computed control points/knots are
                        NOT decoded — see the spline note in :func:`_decode_entities`).
        points:         the resolved ``(x, y, 0.0)`` coordinate triples for those
                        indices (same order as ``point_indices``).
    """

    kind: str
    point_indices: list[int]
    points: list[tuple[float, float, float]] = field(default_factory=list)


@dataclass
class SketchRelation:
    """One sketch relation (geometric constraint) decoded from the out-of-line
    relation block of a sketch.

    Attributes:
        type_id:    the raw ``swConstraintType_e`` integer (e.g. 4 = HORIZONTAL,
                    7 = PARALLEL, 17 = FIXED).
        type_name:  the constraint's name (``"HORIZONTAL"`` …) or ``"type(N)"``
                    if the id is outside the mapped subset.

    Attributes:
        type_id:        the raw ``swConstraintType_e`` integer.
        type_name:      the constraint's name or ``"type(N)"``.
        entity_indices: the ENTITY-array indices of the entities the relation
                        joins, in handle order (e.g. a HORIZONTAL on the 2nd
                        line → ``[1]``; a PARALLEL on two lines → ``[1, 0]``).
                        Decoded from the relation's ``sgLineHandle`` /
                        ``sgArcHandle`` handle list (full + interned/back-ref
                        forms). Empty if the handle list didn't parse. (These
                        are entity-array indices for the line/curve constraints
                        tested; point-level constraints — e.g. COINCIDENT on
                        endpoints — are not yet exercised.)
    """

    type_id: int
    type_name: str
    entity_indices: list[int] = field(default_factory=list)


@dataclass
class SketchDimension:
    """One driving dimension decoded from a sketch's dimension cluster.

    Attributes:
        kind:   ``"distance"``, ``"radial"`` (value = diameter), ``"angular"``,
                ``"diameter"``, ``"linear"`` or ``"unknown"`` — from the display
                object's class name.
        value:  the stored value f64 (rounded to 6 dp), or ``None`` if it
                couldn't be read. UNITS depend on ``kind``: distance/radial are
                in METERS (and a radial dim stores the DIAMETER, not the
                radius); an angular dim is in RADIANS.
        refs:   the point/entity indices the dimension references, parsed from
                its handle list (e.g. a distance dim → the two endpoint POINT
                indices ``[0, 1]``; a radial dim → the arc ENTITY index ``[0]``).
                NOTE the index space depends on the handle class —
                ``sgPointHandle`` → point-array index, ``sgArcHandle`` /
                ``sgLineHandle`` → entity-array index; null/auxiliary handles
                (e.g. a radial's ``sgEntHandle``) are filtered out. Empty if the
                handle list didn't parse.
        text_xy: the dimension TEXT placement ``(x, y)`` in sheet space (meters,
                rounded to 6 dp), or ``None`` if it couldn't be read. Stored as a
                2-D f64 point at a fixed offset past the display-class string."""

    kind: str
    value: float | None
    refs: list[int] = field(default_factory=list)
    text_xy: tuple[float, float] | None = None


@dataclass
class Sketch:
    """One decoded ``sgSketch`` occurrence from a drawing's ``Contents/Definition``.

    Attributes:
        offset:       byte offset of the ``b"sgSketch"`` name in the def stream.
        point_count:  number of point records (0 = empty sketch, no entities).
        points:       list of ``(x, y, z)`` f64 triples (rounded to 6 dp), in
                      file/array order. These are the raw geometry coordinates.
        kind:         first-order entity classification — one of ``"line"``,
                      ``"circle"``, ``"arc"``, ``"empty"``,
                      ``"unknown(marker=..,code=..)"`` or ``"invalid"`` (the
                      latter when the bytes at this offset do not decode to a
                      plausible sgSketch — e.g. a false-positive name-marker hit;
                      :func:`read_sketches` drops these).
        body_offset:  byte offset of the sketch BODY (the u32 point_count), i.e.
                      where the point array header begins. For a literal
                      (NEW_CLASS) instance this is ``offset + 8`` (past the
                      ``b"sgSketch"`` name); for an interned (CLASS_REF back-ref)
                      instance ``offset`` is the 2-byte tag, so the body sits at
                      ``offset + 2``. Consumers that need to locate a specific
                      sketch's point array for a MODIFY (e.g.
                      :func:`move_sketch_point` by ``sketch_index``) use this
                      rather than re-deriving the body location from ``offset``,
                      which differs by instance form.
    """

    offset: int
    point_count: int
    points: list[tuple[float, float, float]] = field(default_factory=list)
    kind: str = "empty"
    entities: list[SketchEntity] = field(default_factory=list)
    body_offset: int = 0

    @property
    def description(self) -> str:
        """Human one-liner. With decoded entities, summarises them (e.g.
        ``"2 entities: line, circle"``); otherwise falls back to the first-order
        ``kind`` (e.g. ``"line (2 points)"`` / ``"empty (no entities)"``)."""
        if self.kind == "invalid":
            return "invalid (not a decodable sgSketch)"
        if self.point_count == 0:
            return "empty (no entities)"
        if self.entities:
            kinds = ", ".join(e.kind for e in self.entities)
            return f"{len(self.entities)} entit{'y' if len(self.entities) == 1 else 'ies'}: {kinds}"
        return f"{self.kind} ({self.point_count} points)"


def find_sketch_offsets(defn: bytes) -> list[int]:
    """Return the offsets of every ``b"sgSketch"`` occurrence in ``defn``.

    A multi-sheet drawing has one ``sgSketch`` per sheet (plus any block/view
    sketches); a single-sheet ``geom_*`` fixture has exactly one. Returns ``[]``
    when none are present (e.g. a non-drawing file).
    """
    offsets: list[int] = []
    start = 0
    while True:
        i = defn.find(b"sgSketch", start)
        if i < 0:
            return offsets
        offsets.append(i)
        start = i + 8


def read_sketch_at(defn: bytes, sg_offset: int) -> Sketch:
    """Decode the single ``sgSketch`` whose name starts at ``sg_offset``.

    Reads the u32 ``point_count`` at ``body+0`` then the ``point_count`` f64
    ``(x,y,z)`` triples at stride :data:`POINT_STRIDE` from ``body+80``, and
    classifies the entity from the ``(marker, code)`` fields. Coordinates are
    rounded to 6 decimal places (matching the validated prototype output).

    Defensive contract (never raises on adversarial input): ``b"sgSketch"`` is
    only an 8-byte ASCII name and can occur by chance in a non-drawing stream
    (a false-positive marker hit — the project mandates sanity caps on exactly
    this, see the chunk-walker's ``nsz``/``csz`` caps). If the header doesn't
    fit, or the declared ``point_count`` is larger than the number of 142-byte
    point records the remaining buffer can physically hold, the bytes are not a
    real sgSketch: returns ``Sketch(kind="invalid")`` rather than crashing with
    ``struct.error`` or looping on a garbage count. :func:`read_sketches` drops
    ``invalid`` results so a stray marker hit never pollutes a file's sketch list.
    """
    body = sg_offset + 8
    if body + 4 > len(defn):                       # header u32 doesn't even fit
        return Sketch(offset=sg_offset, point_count=0, kind="invalid", body_offset=body)
    pcount = struct.unpack_from("<I", defn, body)[0]

    base = body + PCOUNT_TO_FIRSTPOINT
    # Sanity cap: the point array (pcount × 142 B) must fit in the bytes after the
    # header. A garbage count from a false-positive hit fails this and is rejected.
    max_pts = (len(defn) - base) // POINT_STRIDE if len(defn) > base else 0
    if pcount > max_pts:
        return Sketch(offset=sg_offset, point_count=0, kind="invalid", body_offset=body)
    if pcount == 0:
        return Sketch(offset=sg_offset, point_count=0, points=[], kind="empty", body_offset=body)

    pts: list[tuple[float, float, float]] = []
    for i in range(pcount):
        o = base + i * POINT_STRIDE
        # Coordinate payload is 2-D (x, y); z is fixed at 0.0 for planar drawing
        # geometry. point+16 is STRUCTURE, not z (see the Z NOTE in the module doc).
        x, y = struct.unpack_from("<dd", defn, o)
        pts.append((round(x, 6), round(y, 6), 0.0))

    marker = defn[base + ENTITY_MARKER_OFF]
    code = struct.unpack_from("<I", defn, base + _CODE_OFF)[0]
    kind = _TYPE.get((marker, code), f"unknown(marker={marker},code={code})")
    if kind == "circle_or_arc":
        # Validated split: 2 points → circle (center + perimeter),
        # 3 points → arc (center + two endpoints).
        kind = "circle" if pcount == 2 else ("arc" if pcount == 3 else f"curve({pcount}pts)")

    entities = _decode_entities(defn, base + pcount * POINT_STRIDE, pts)
    return Sketch(offset=sg_offset, point_count=pcount, points=pts, kind=kind,
                  entities=entities, body_offset=body)


def _decode_entities(
    defn: bytes, ent_start: int, points: list[tuple[float, float, float]]
) -> list[SketchEntity]:
    """Walk the entity array at ``ent_start`` → list of :class:`SketchEntity`.

    Iterates the flat per-entity records (line 92 B / curve 112 B), reading each
    entity's type and its point-array indices, and resolves those indices to the
    already-decoded ``points``. Stops at the terminal record (``0x8008`` at
    record ``+size-4``).

    Defensive: returns ``[]`` rather than raising/guessing if the array does not
    cleanly parse — a record that would read out of bounds, an out-of-range point
    index, or no terminal reached before the buffer/guard ends. (The legacy
    synthetic test blobs have no entity array, so ``ent_start`` lands at EOF and
    this returns ``[]`` — read_sketch_at then reports the first-order ``kind``.)

    KNOWN INCOMPLETENESS (dimensioned drawing SHEET sketches): a sheet sketch that
    carries dimensions does NOT terminate its entity array with the ``0x8008``
    marker the standalone ``geom_*`` sketches use — the per-dimension object
    CLUSTER (``sgPntPntDist`` / ``moLengthParameter_c`` / ``moDisplay*Dim_c`` …)
    begins immediately after the last line record, and the final line record is
    SHORTER than the 92-B norm (the array span is ``92·nlines − 24``; see
    ``research/empirical_findings/definition_decode/log.md`` steps 9-11). With no
    ``0x8008`` reached, this walk over-reads into the cluster and returns ``[]`` —
    so ``read_sketch_at`` reports the points + first-order ``kind`` but an EMPTY
    ``entities`` list for such sketches. The entities ARE present and individually
    decodable (the line tags + indices read correctly); only the array-END signal
    differs. A robust fix needs the exact shorter-last-record format OR to treat
    the first CArchive object tag (``0xFFFF`` NEW_CLASS / ``0x80NN`` CLASS_REF =
    the dimension cluster) after a run of valid line records as the array end.
    Deferred (finicky last-record sizing; the conservative ``[]`` is correct, just
    incomplete) rather than risk the standalone-sketch path that relies on
    ``0x8008``.
    """
    n = len(points)
    pos = ent_start
    out: list[SketchEntity] = []
    for _ in range(_ENT_MAX):
        if pos + 18 > len(defn):                    # can't even read the +16 tag
            return []
        is_line = struct.unpack_from("<H", defn, pos + _ENT_TAG_OFF)[0] == _ENT_LINE_TAG
        if not is_line:
            # A spline is a variable-size, class-string-tagged record (not a
            # fixed line/curve). Detect it before the curve interpretation: if
            # the spline tag sits just after this record start, decode its
            # fit-point binding and STOP (its full size is not yet known).
            sp = defn.find(_ENT_SPLINE_TAG, pos, pos + _ENT_SPLINE_SCAN)
            if sp >= 0:
                spline = _decode_spline_record(defn, sp, points)
                if spline is None:                  # malformed → untrusted
                    return []
                out.append(spline)
                return out                           # cannot size the record to continue
        size = _ENT_LINE_SIZE if is_line else _ENT_CURVE_SIZE
        if pos + size > len(defn):                  # record would run past EOF
            return []
        if is_line:
            idx = [struct.unpack_from("<H", defn, pos + _ENT_LINE_START)[0],
                   struct.unpack_from("<H", defn, pos + _ENT_LINE_END)[0]]
            kind = "line"
        else:
            flag = struct.unpack_from("<I", defn, pos + _ENT_CURVE_FLAG)[0]
            center = struct.unpack_from("<H", defn, pos + _ENT_CURVE_CENTER)[0]
            last = struct.unpack_from("<H", defn, pos + _ENT_CURVE_LAST)[0]
            if flag == 1:
                kind, idx = "circle", [center, last]
            elif flag == 0xFFFFFFFF:
                mid = struct.unpack_from("<H", defn, pos + _ENT_CURVE_MID)[0]
                kind, idx = "arc", [center, mid, last]
            else:
                return []                            # unknown curve flag → untrusted
        if any(i >= n for i in idx):                 # index out of range → untrusted
            return []
        out.append(SketchEntity(kind=kind, point_indices=idx,
                                points=[points[i] for i in idx]))
        terminal = defn[pos + size - 4: pos + size - 2] == _ENT_TERMINATOR
        pos += size
        if terminal:
            return out
    return []                                        # no terminal within guard → untrusted


def _decode_spline_record(
    defn: bytes, tag_offset: int, points: list[tuple[float, float, float]]
) -> SketchEntity | None:
    """Decode the spline record whose ``modifSplineList_c`` class string starts at
    ``tag_offset`` → a ``kind="spline"`` :class:`SketchEntity` bound to its
    fit-point indices, or ``None`` if the bytes don't decode to a plausible spline.

    Reads the u16 fit-point count right after the class string, then walks the
    ``sgPointHandle`` handle list to recover each fit point's GLOBAL point-array
    index (first handle 10 B = idx+ffff+0000; each subsequent 12 B =
    backref+idx+ffff+0000 — the back-ref VALUE is intern-dependent so only its
    slot is used, not its value). Resolves indices to coordinates.

    Defensive: returns ``None`` (caller then yields ``[]``) if the count is
    implausible, the handle list runs past EOF, the recovered index list is the
    wrong length, or any index is out of range — so a stray/garbled match never
    fabricates an entity. The control-point/knot block is intentionally not read.
    """
    n = len(points)
    cnt_off = tag_offset + len(_ENT_SPLINE_TAG)
    if cnt_off + 2 > len(defn):
        return None
    count = struct.unpack_from("<H", defn, cnt_off)[0]
    if count == 0 or count > n:                      # need 1..n fit points
        return None
    ph = defn.find(_ENT_SPLINE_PTHANDLE, cnt_off, cnt_off + _ENT_SPLINE_SCAN)
    if ph < 0:
        return None
    p = ph + len(_ENT_SPLINE_PTHANDLE)               # first handle starts here
    idx: list[int] = []
    for k in range(count):
        # first fit point: idx u16 immediately; subsequent: skip the 2-B backref.
        ipos = p if k == 0 else p + 2
        if ipos + 2 > len(defn):
            return None
        idx.append(struct.unpack_from("<H", defn, ipos)[0])
        p += _ENT_SPLINE_FIRST_STRIDE if k == 0 else _ENT_SPLINE_REPEAT_STRIDE
    if any(i >= n for i in idx):
        return None
    return SketchEntity(kind="spline", point_indices=idx,
                        points=[points[i] for i in idx])


# --- MULTI-INSTANCE enumeration (M5.7) ---------------------------------------
# A drawing has one sgSketch per sheet (+ any block/view sketches), but MFC
# CArchive INTERNS the class name: only the FIRST sgSketch is written in full
# (``ff ff 01 00 08 00 "sgSketch"``); every later instance is a CLASS_REF back-ref
# tag — a u16 with the high bit set (0x80NN, NN = the sgSketch class slot) —
# directly followed by the SAME sketch body. So a literal-string search finds
# ONLY the first sketch (the cause of the documented single-instance limitation).
# To enumerate ALL instances we ALSO scan for high-bit u16 tags and accept a
# candidate only if the body at tag+2 decodes to a valid sketch with >=1 entity
# (reusing read_sketch_at's defensive validation) — the same byte-anchored idea
# that makes the relations reader generalize. A cheap structural pre-check keeps
# the scan fast (~1-3 s on a multi-MB Definition). Verified on real multi-sheet
# drawings (13 and 34 sketch instances, vs 1 by string search). See the
# 2026-06-10 "BREAKTHROUGH" entry in definition_decode/log.md.
_SK_CLASSREF_HI = 0x80          # high byte of a CLASS_REF u16 has the high bit set
_SK_PCOUNT_MAX = 1000           # sanity cap on point count during enumeration
_SK_BODY_MIN = 84               # min body bytes (pcount u32 + 80-B header)


def _sketch_body_looks_valid(defn: bytes, body: int) -> bool:
    """Cheap structural pre-check that bytes at ``body`` plausibly begin a sketch
    body — rejects the vast majority of CLASS_REF candidates before the (more
    expensive) full decode. Checks a sane point count, a finite in-range first
    point, and a valid FIRST entity tag (line ``0xBFF0`` / curve flag / spline)."""
    n = len(defn)
    if body + _SK_BODY_MIN > n:
        return False
    pc = struct.unpack_from("<I", defn, body)[0]
    if pc < 1 or pc > _SK_PCOUNT_MAX:
        return False
    base = body + PCOUNT_TO_FIRSTPOINT
    ent = base + pc * POINT_STRIDE
    if ent + 34 > n:                                # enough for the line tag (+16) / curve flag (+30)
        return False
    x, y = struct.unpack_from("<dd", defn, base)
    if not (x == x and y == y and -100.0 < x < 100.0 and -100.0 < y < 100.0):
        return False
    if struct.unpack_from("<H", defn, ent + _ENT_TAG_OFF)[0] == _ENT_LINE_TAG:
        return True
    if struct.unpack_from("<I", defn, ent + _ENT_CURVE_FLAG)[0] in (1, 0xFFFFFFFF):
        return True
    return defn.find(_ENT_SPLINE_TAG, ent, min(ent + 96, n)) >= 0


def enumerate_sketches(defn: bytes) -> list[Sketch]:
    """Return EVERY sgSketch instance in a Definition blob — the full-form
    (literal NEW_CLASS) instances AND the interned (CLASS_REF back-ref) ones —
    in file order. This is the multi-instance surface that works on real
    multi-sheet drawings (where all but the first sketch are interned).

    Literal instances decode as before. Interned instances are found by scanning
    for high-bit u16 tags whose body decodes to a valid sketch with >=1 entity
    (cheap pre-check first). Deduped by body offset. Empty/invalid candidates are
    not emitted (an empty interned sketch has no entity to anchor on).
    """
    seen: set[int] = set()
    out: list[Sketch] = []
    for off in find_sketch_offsets(defn):                  # full-form (NEW_CLASS)
        body = off + 8
        if body in seen:
            continue
        s = read_sketch_at(defn, off)
        if s.kind != "invalid":
            seen.add(body)
            out.append(s)
    n = len(defn)
    for o in range(n - 2):                                  # interned (CLASS_REF)
        if defn[o + 1] < _SK_CLASSREF_HI or defn[o + 1] == 0xFF:
            continue
        body = o + 2
        if body in seen or not _sketch_body_looks_valid(defn, body):
            continue
        s = read_sketch_at(defn, o - 6)                     # body = (o-6)+8 = o+2
        if s.kind != "invalid" and s.entities:
            s.offset = o                                    # report the tag offset
            seen.add(body)
            out.append(s)
    out.sort(key=lambda s: s.offset)
    return out


def read_sketches(path: str | Path) -> list[Sketch]:
    """Return every decoded :class:`Sketch` in a drawing file, in file order.

    Reads ``Contents/Definition`` and enumerates EVERY ``sgSketch`` instance —
    both the full-form (literal) and the interned (CArchive CLASS_REF back-ref)
    ones — via :func:`enumerate_sketches`, so it finds all sheets' sketches on a
    real multi-sheet drawing (not just the first; see the M5.7 multi-instance
    note). Returns ``[]`` for a non-drawing file or one with no
    ``Contents/Definition`` / no sketches. No SOLIDWORKS required.
    """
    defn = read_document(path).streams().get(_DEF_STREAM)
    if not defn:
        return []
    return enumerate_sketches(defn)


def read_sketch_entities(path: str | Path) -> list[SketchEntity]:
    """Return every :class:`SketchEntity` across all sketches in a drawing, in
    order — the per-entity geometry (line / circle / arc) with point coordinates
    resolved via explicit point-index binding. Empty if none / not a drawing.
    No SOLIDWORKS required.

    This is the multi-entity surface: unlike :func:`read_sketches` (raw point
    array + first-order ``kind``), it segments each sketch into its individual
    entities and binds each entity to its own points (so a line+circle sheet
    yields a ``line`` entity and a ``circle`` entity, each with its own coords).
    """
    return [e for sk in read_sketches(path) for e in sk.entities]


def _relations_in_region(defn: bytes, start: int, end: int) -> list[SketchRelation]:
    """Scan ``defn[start:end]`` for relation records by the :data:`_REL_ANCHOR`
    and return one :class:`SketchRelation` per match.

    The type is the u32 in the 4 bytes before each anchor; matches whose type is
    outside the plausible ``swConstraintType_e`` range are skipped (defensive —
    so a chance anchor in unrelated bytes doesn't fabricate a relation). Each
    relation's bound-entity indices are parsed from its handle list (bounded by
    the next anchor). The region is normally one ``sgSketch``'s span (offset →
    next ``sgSketch``).
    """
    # First collect the anchors (so each relation's handle list can be bounded
    # by the NEXT anchor — handles never cross into the next relation record).
    anchors: list[tuple[int, int]] = []              # (anchor_offset, type_id)
    i = start
    while True:
        j = defn.find(_REL_ANCHOR, i, end)
        if j < 0:
            break
        if j >= start + 4:
            t = struct.unpack_from("<I", defn, j - 4)[0]
            if _REL_TYPE_MIN <= t <= _REL_TYPE_MAX:
                anchors.append((j, t))
        i = j + len(_REL_ANCHOR)

    out: list[SketchRelation] = []
    for k, (j, t) in enumerate(anchors):
        bound = anchors[k + 1][0] if k + 1 < len(anchors) else end
        out.append(SketchRelation(
            type_id=t, type_name=_CONSTRAINT_TYPES.get(t, f"type({t})"),
            entity_indices=_relation_handles(defn, j + len(_REL_ANCHOR), bound)))
    return out


def _relation_handles(defn: bytes, start: int, bound: int) -> list[int]:
    """Greedily parse a relation's handle list in ``defn[start:bound]`` → the
    ENTITY-array indices it binds, in handle order.

    Reads handles until the byte pattern stops looking like one (zeros / the
    list's tail), guarded by :data:`_REL_H_MAX`. Each handle is FULL
    (``ff ff 01 00`` + u16 len + class name + u16 index) or INTERNED (a u16
    back-ref with the high bit set + u16 index), each followed by an 8-byte
    trailer. Defensive: stops on any out-of-bounds read (returns what parsed).
    """
    idxs: list[int] = []
    p = start
    for _ in range(_REL_H_MAX):
        if p + 4 > bound:
            break
        tag = struct.unpack_from("<H", defn, p)[0]
        if tag == _REL_H_FULL:                       # full (string) handle
            p += 4                                   # skip ff ff + 01 00
            if p + 2 > bound:
                break
            ln = struct.unpack_from("<H", defn, p)[0]
            p += 2
            if p + ln + 2 > bound:
                break
            p += ln                                  # skip the class name
            idxs.append(struct.unpack_from("<H", defn, p)[0])
            p += 2 + _REL_H_TRAILER
        elif 0x8000 <= tag <= 0xFFFE:                # interned back-ref handle
            p += 2
            if p + 2 > bound:
                break
            idxs.append(struct.unpack_from("<H", defn, p)[0])
            p += 2 + _REL_H_TRAILER
        else:                                        # not a handle → end of list
            break
    return idxs


def move_sketch_point(path: str | Path, out_path: str | Path,
                      point_index: int, x: float, y: float,
                      sketch_index: int = 0) -> list:
    """MODIFY: move point ``point_index`` of sketch ``sketch_index`` to ``(x, y)``
    and write the file to ``out_path`` (read → modify → write).

    Edits the two f64 coordinates IN PLACE in ``Contents/Definition`` (a
    same-length 16-byte replacement — the entity records reference points by
    INDEX, not coordinate, so only the point array carries the geometry), then
    re-deflates the stream and fixes the central directory via
    :func:`write_with_toc` (span-preserving — no offset shift, since the edit
    doesn't change any length). Returns the decoded point list of the EDITED
    sketch in the output.

    ``sketch_index`` selects which sketch to edit among ALL instances in file
    order (the same ordering :func:`read_sketches` reports), so it reaches the
    interned (CLASS_REF) sketches on a real multi-sheet drawing too — not just
    the first/literal one. The point array is located via the chosen sketch's
    :attr:`Sketch.body_offset`, which already accounts for the literal-vs-interned
    body-location difference (``offset+8`` vs ``offset+2``).

    Defensive: raises ``ValueError`` if the file has no ``sgSketch``,
    ``sketch_index`` is out of range, or ``point_index`` is out of range for that
    sketch. Intended for a free sketch point — moving a constrained point may be
    re-solved by SOLIDWORKS on reopen.
    """
    doc = read_document(path)
    defn = doc.streams().get(_DEF_STREAM)
    if not defn:
        raise ValueError(f"{path}: no Contents/Definition (not a drawing?)")
    sketches = enumerate_sketches(defn)
    if not sketches:
        raise ValueError(f"{path}: no sgSketch in Contents/Definition")
    if not 0 <= sketch_index < len(sketches):
        raise ValueError(
            f"sketch_index {sketch_index} out of range (0..{len(sketches) - 1})")
    sk = sketches[sketch_index]
    body = sk.body_offset
    pcount = struct.unpack_from("<I", defn, body)[0]
    if not 0 <= point_index < pcount:
        raise ValueError(f"point_index {point_index} out of range (0..{pcount - 1})")
    off = body + PCOUNT_TO_FIRSTPOINT + point_index * POINT_STRIDE
    edited = bytearray(defn)
    struct.pack_into("<dd", edited, off, float(x), float(y))
    set_stream_payload(doc, _DEF_STREAM, bytes(edited))
    write_with_toc(doc, out_path)
    out = read_sketches(out_path)
    return out[sketch_index].points if len(out) > sketch_index else []


def _nth_offset(defn: bytes, needle: bytes, n: int) -> int:
    """Return the byte offset of the ``n``-th (0-based) occurrence of ``needle``
    in ``defn``, or -1 if there are fewer than ``n+1`` occurrences."""
    i = pos = -1
    for _ in range(n + 1):
        pos = defn.find(needle, i + 1)
        if pos < 0:
            return -1
        i = pos
    return pos


def set_dimension_value(path: str | Path, out_path: str | Path,
                        value: float, dim_index: int = 0) -> float | None:
    """MODIFY: set the ``dim_index``-th dimension's VALUE to ``value`` and write
    to ``out_path`` (read → modify → write). This RESIZES the dimensioned
    geometry.

    Edits the value f64 in place (at the interning-immune :data:`_DIM_VALUE_SIG`
    + 24 anchor — the ``dim_index``-th occurrence) and writes span-preserving via
    :func:`write_with_toc`.

    SW-verified (SW 2026): reopening the edited drawing accepts the new value AND
    re-solves the DRIVING dimension — e.g. setting a 0.10 m line-length dimension
    to 0.15 m makes SOLIDWORKS move the line's endpoint so the line is 0.15 m
    long (the dimension drives the geometry). Units are the stored value's units
    (meters for distance/radial, radians for angular). Raises ``ValueError`` if
    fewer than ``dim_index+1`` dimensions are present.

    ``dim_index`` SELECTION CAVEAT: the index counts :data:`_DIM_VALUE_SIG`
    occurrences, which is reliable only on the synthetic few-dimension fixtures.
    On a **real multi-sheet drawing that anchor over-matches** (see
    :func:`read_sketch_dimensions` and
    ``research/empirical_findings/sketch_dimensions/log.md`` 2026-06-10 22:25), so
    a ``dim_index`` there does NOT correspond 1:1 to a SOLIDWORKS dimension —
    do not rely on it to pick a specific dimension on real files until the
    CArchive class table lands. The EDIT mechanism (value f64 at the anchor) is
    correct; only the per-instance SELECTION is unreliable on real files."""
    doc = read_document(path)
    defn = doc.streams().get(_DEF_STREAM)
    if not defn:
        raise ValueError(f"{path}: no Contents/Definition (not a drawing?)")
    sig = _nth_offset(defn, _DIM_VALUE_SIG, dim_index)
    if sig < 0:
        raise ValueError(f"{path}: dimension index {dim_index} not present")
    vo = sig + len(_DIM_VALUE_SIG)
    if vo + 8 > len(defn):
        raise ValueError(f"{path}: value offset past end of stream")
    edited = bytearray(defn)
    struct.pack_into("<d", edited, vo, float(value))
    set_stream_payload(doc, _DEF_STREAM, bytes(edited))
    write_with_toc(doc, out_path)
    return float(value)


def move_dimension_text(path: str | Path, out_path: str | Path,
                        x: float, y: float) -> tuple[float, float] | None:
    """MODIFY: move the FIRST (full-form) dimension's TEXT to ``(x, y)`` in sheet
    space and write to ``out_path`` (read → modify → write).

    Edits the two placement f64 at the fixed offset past the display-class string
    (:data:`_DIM_PLACE_X_OFF`/`_DIM_PLACE_Y_OFF`) — an annotation-only change (no
    geometry/constraint impact, so SOLIDWORKS won't re-solve), written
    span-preserving via :func:`write_with_toc`. Returns the output's decoded
    ``text_xy``. Raises ``ValueError`` if no full-form dimension (display-class
    string) is present. Targets the full-form instance; interned dimensions'
    placement strings are back-refs (see the M5.7 interning note)."""
    doc = read_document(path)
    defn = doc.streams().get(_DEF_STREAM)
    if not defn:
        raise ValueError(f"{path}: no Contents/Definition (not a drawing?)")
    disp, disp_tag = -1, None
    for tag in _DIM_DISPLAY:
        dpos = defn.find(tag)
        if dpos >= 0 and (disp < 0 or dpos < disp):
            disp, disp_tag = dpos, tag
    if disp < 0:
        raise ValueError(f"{path}: no full-form dimension (display class) to move")
    base = disp + len(disp_tag)
    xo, yo = base + _DIM_PLACE_X_OFF, base + _DIM_PLACE_Y_OFF
    if yo + 8 > len(defn):
        raise ValueError(f"{path}: placement offset past end of stream")
    edited = bytearray(defn)
    struct.pack_into("<d", edited, xo, float(x))
    struct.pack_into("<d", edited, yo, float(y))
    set_stream_payload(doc, _DEF_STREAM, bytes(edited))
    write_with_toc(doc, out_path)
    out = read_sketch_dimensions(out_path)
    return out[0].text_xy if out else None


def read_sketch_relations(path: str | Path) -> list[SketchRelation]:
    """Return every :class:`SketchRelation` (geometric constraint) across all
    sketches in a drawing, in file order. Empty if none / not a drawing. No
    SOLIDWORKS required.

    Each ``sgSketch``'s span (its name offset up to the next ``sgSketch``) is
    scanned for the relation-record anchor; the relation TYPE is decoded.
    Reports the constraint types present (and their count) — e.g. a sketch with
    two horizontal lines made horizontal yields two ``HORIZONTAL`` relations.
    Entity binding is a follow-up (see :class:`SketchRelation`).
    """
    defn = read_document(path).streams().get(_DEF_STREAM)
    if not defn:
        return []
    offs = find_sketch_offsets(defn)
    out: list[SketchRelation] = []
    for k, sg in enumerate(offs):
        end = offs[k + 1] if k + 1 < len(offs) else len(defn)
        out.extend(_relations_in_region(defn, sg, end))
    return out


def _dimensions_in_region(defn: bytes, start: int, end: int) -> list[SketchDimension]:
    """Scan ``defn[start:end]`` for dimensions by the interning-immune
    :data:`_DIM_VALUE_SIG` anchor — one hit per dimension, full-form AND interned
    — and return one :class:`SketchDimension` each.

    The value f64 is at ``signature + 24`` (works for every instance, including
    interned). The kind/refs/placement are then located via the display- and
    handle-class STRINGS after the value — present for the full-form instance, so
    interned instances get the VALUE but ``kind="unknown"``, ``refs=[]``,
    ``text_xy=None``. (The class strings are interned to a back-ref for the 2nd+
    instance; resolving them would need the CArchive class table.)
    """
    out: list[SketchDimension] = []
    i = start
    while True:
        j = defn.find(_DIM_VALUE_SIG, i, end)
        if j < 0:
            return out
        i = j + 1
        vo = j + len(_DIM_VALUE_SIG)
        value: float | None = None
        if vo + 8 <= len(defn):
            v = struct.unpack_from("<d", defn, vo)[0]
            if v == v and v not in (float("inf"), float("-inf")):
                value = round(v, 6)
        bound = min(end, vo + _DIM_DISPLAY_SCAN)
        kind, best, disp_tag = "unknown", bound, None
        for tag, kn in _DIM_DISPLAY.items():
            d = defn.find(tag, vo, bound)
            if 0 <= d < best:
                best, kind, disp_tag = d, kn, tag
        refs = _dimension_refs(defn, vo, best)            # handle list sits after the value
        text_xy = _dimension_placement(defn, best, disp_tag) if disp_tag else None
        out.append(SketchDimension(kind=kind, value=value, refs=refs, text_xy=text_xy))


def _dimension_placement(defn: bytes, disp: int, disp_tag: bytes
                         ) -> tuple[float, float] | None:
    """Read the dimension TEXT placement (x, y) — two f64 at a fixed offset past
    the END of the display-class string at ``disp`` (same constant for distance
    vs radial because it's anchored to the string end). Returns ``None`` if the
    offsets run past EOF or read non-finite values (defensive)."""
    base = disp + len(disp_tag)
    xo, yo = base + _DIM_PLACE_X_OFF, base + _DIM_PLACE_Y_OFF
    if yo + 8 > len(defn):
        return None
    x = struct.unpack_from("<d", defn, xo)[0]
    y = struct.unpack_from("<d", defn, yo)[0]
    if x != x or y != y or float("inf") in (abs(x), abs(y)):
        return None
    return (round(x, 6), round(y, 6))


def _dimension_refs(defn: bytes, param: int, bound: int) -> list[int]:
    """Parse the point/entity indices a dimension references from its handle list.

    The list lives between ``moLengthParameter_c`` (``param``) and the display
    class (``bound``). We find the first handle-class string in that window, back
    up to the handle-list start (``ff ff 01 00`` + u16 len), reuse the relation
    handle walk, and drop null/auxiliary indices (``>= _DIM_REF_MAX`` — e.g. a
    radial dim's ``sgEntHandle`` null). Returns ``[]`` if no handle class found.
    """
    first = -1
    for cls in _DIM_HANDLE_CLASSES:
        d = defn.find(cls, param, bound)
        if d >= 0 and (first < 0 or d < first):
            first = d
    if first < 0:
        return []
    idxs = _relation_handles(defn, first - _DIM_HANDLE_BACKUP, bound)
    return [i for i in idxs if i < _DIM_REF_MAX]


def read_sketch_dimensions(path: str | Path) -> list[SketchDimension]:
    """Return every :class:`SketchDimension` (driving dimension) across all
    sketches in a drawing, in file order. Empty if none / not a drawing. No
    SOLIDWORKS required.

    Reports each dimension's KIND (distance/radial/…) and its stored VALUE
    (radial dims store the diameter), plus entity refs and text placement for
    the full-form (literal) instance.

    SCOPE / RELIABILITY (important — the count is NOT trustworthy): the
    per-dimension anchor is the 24-byte :data:`_DIM_VALUE_SIG`, which is
    **validated only on the synthetic 1–2 dimension fixtures** (``dim_len`` /
    ``dim_diam`` / ``dim_angle`` / ``two_dim``, where each value was cross-checked
    against the known SW input). It is **NOT a reliable dimension enumerator** —
    proven UNRELIABLE IN BOTH DIRECTIONS by a controlled ground-truth experiment
    (``research/empirical_findings/sketch_dimensions/log.md`` 2026-06-10 22:45):

    * UNDER-counts — on a synthetic 2-sheet fixture with **7** SW-created
      dimensions (SW-authoritative ``IView.GetDimensionCount2``: ALPHA 4 + BETA 3),
      this reader returns only **2**. The signature matches just a SUB-FORM of
      dimension serialization; the other 5 dimensions serialize without it.
    * OVER-counts — on a 14.5 MB production ``Definition`` it returns **2016**
      hits (no structural-validation gate, unlike :func:`enumerate_sketches`),
      far above the plausible true count.

    Additionally, because the display/param class strings are interned (written
    once), every instance past the first comes back ``kind="unknown"``,
    ``refs=[]``, ``text_xy=None``. **Do not use this result as a dimension count
    or enumeration.** Correct per-instance dimension counting + attribution
    requires walking the CArchive object map (each dimension is a
    ``moDisplay*Dim_c`` object) — the parked "keystone" / moDrSheet
    content-general parse. The ``ndim7`` fixture is the regression oracle: when
    the class table lands, this must return 7 for it.
    """
    defn = read_document(path).streams().get(_DEF_STREAM)
    if not defn:
        return []
    offs = find_sketch_offsets(defn)
    out: list[SketchDimension] = []
    for k, sg in enumerate(offs):
        end = offs[k + 1] if k + 1 < len(offs) else len(defn)
        out.extend(_dimensions_in_region(defn, sg, end))
    return out
