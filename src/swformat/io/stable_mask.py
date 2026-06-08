"""Stream-level non-determinism mask (M1, re-scoped from M0.5 findings).

ORIGINAL PLAN vs REALITY
------------------------
M1 originally envisaged a *positional* byte mask: "ignore these byte
ranges when byte-comparing our output to the original." **M0.5 killed
that idea**: two SW saves of an unmodified file differ in total LENGTH
(0/9 size-stable), so byte offsets do not even align between saves — a
fixed-offset mask is inapplicable, not merely leaky.

The achievable, useful form is a **stream-name allow/deny list**, applied
at Layer 2 (decompressed-stream comparison). When we compare a
SWFormat-produced file against a reference SW save:

- A difference in a **known-nondeterministic** stream (timestamps, save
  history, graphics cache, regenerated GUIDs/hashes) is EXPECTED — ignore
  it. These are listed in :data:`NONDETERMINISTIC_STREAMS` (exact names)
  and :data:`NONDETERMINISTIC_PATTERNS` (substring/glob-ish patterns for
  the families whose names embed per-file hashes).
- A difference in ANY OTHER stream is a real regression — flag it.

The lists are seeded from the M0.5 baseline (9 files, SW 2026,
`research/empirical_findings/twin_save_baseline/`): the 5 streams that
differed in 100% of files, plus the per-type length-shifters and the
classified timestamp/GUID loci. They will grow as more versions/types are
observed; treat as empirical, not exhaustive.

USAGE
-----
    from swformat.io.stable_mask import is_nondeterministic
    if payload_a != payload_b and not is_nondeterministic(name):
        flag_regression(name)
"""
from __future__ import annotations

import re

# Exact stream names empirically nondeterministic across unmodified saves
# (M0.5; SW 2026 / _MO_VERSION_19000). Grouped by cause for documentation.
NONDETERMINISTIC_STREAMS: frozenset[str] = frozenset(
    {
        # --- save-time stamps -------------------------------------------------
        "docProps/core.xml",                  # ISO save time (dcterms:modified)
        "docProps/ISolidWorksInformation.xml",  # human-readable save clock time
        "Contents/CMgrHdr2",                  # save-time epoch DWORD @~0x9b
        "Header2",                            # save-time epoch DWORD @~0xcab
        "Contents/Config-0-ModelHeader",      # save-time epoch DWORD (mirror)
        # --- save history (length-variable; the universal length-shifter) -----
        # NOTE: name embeds the doc version, e.g. _MO_VERSION_19000/Biography;
        # the version-agnostic form is matched by NONDETERMINISTIC_PATTERNS.
        # --- graphics / tessellation caches ----------------------------------
        "Contents/DisplayLists",              # graphics cache, regenerated each save
        "FaceTessellations/Directory",        # tessellation index
        # --- regenerated partition GUIDs / hashes ----------------------------
        "Contents/Config-0-GhostPartition",   # high-entropy regenerated blob
        # --- 3DExperience / cloud-exchange blobs -----------------------------
        "Contents/3DExperienceExchange2",
        "Contents/3DExperienceExchange_ConfigHeader",
        # --- PMI / mates serialization (assemblies) --------------------------
        "Contents/PMISemanticDataDB",
        "Contents/Config-0-MatesList",
        # --- geometry body reserialization (parts) ---------------------------
        "Config-0-FeatureBodies/LocalBodies",
        # --- drawing thumbnails ----------------------------------------------
        "Preview",
        "PreviewPNG",
        # --- third-party addin store -----------------------------------------
        "ThirdPtyStore/ThirdPtySTGStore",
    }
)

# Regex patterns for stream-name FAMILIES whose names vary per file (embed a
# document version number or a per-body hash) — these can't be listed exactly.
NONDETERMINISTIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^_MO_VERSION_\d+/Biography$"),     # save history (any version)
    re.compile(r"^_MO_VERSION_\d+/History$"),
    re.compile(r"^_DL_VERSION_\d+/DLUpdateStamp$"),  # display-list update stamp
    re.compile(r"^Attachments/Config-\d+_caBodyPartition_[0-9A-Fa-f]+$"),
)


def is_nondeterministic(stream_name: str) -> bool:
    """True if a difference in this stream is expected save noise, not a bug.

    Checks the exact-name set first, then the family patterns.
    """
    if stream_name in NONDETERMINISTIC_STREAMS:
        return True
    return any(p.match(stream_name) for p in NONDETERMINISTIC_PATTERNS)
