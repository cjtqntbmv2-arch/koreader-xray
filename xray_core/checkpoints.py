"""Checkpoint planning: spoiler-stage boundaries for a book.

Port of KOReader Lua's `computeCheckpoints` (`xray_prefetch.lua:computeCheckpoints`),
using char offsets into `BookText.full_text` instead of page numbers. Each
`Checkpoint.offset` is the EXCLUSIVE end of the span it covers, so
`full_text[prev_offset:offset]` is exactly the text available up to that
checkpoint -- no off-by-one.

A checkpoint carries only offset and percent. The three-stage anchor chain
(text snippet -> TOC entry -> percent) was dropped in schema v2: the device
now compares the reading position on the text axis directly against percent,
which needs no per-checkpoint marker.

Stdlib-only on purpose (see `xray_core/epub.py`).
"""

import re
from dataclasses import dataclass

from xray_core.epub import BookText

MAX_CHECKPOINTS, HARD_CAP, MAX_INTERVAL_PCT = 10, 12, 15

# Front/back matter that isn't part of the narrative -- excluded from
# chapter-boundary checkpoints. Matched against title.lower().strip().
NON_NARRATIVE = [
    r"^cover$", r"^title", r"^half-title", r"^copyright", r"^table of contents",
    r"^contents$", r"^dedication", r"^acknowledgment", r"^also by", r"^other books",
    r"^about the author", r"^about the", r"^epigraph$", r"^foreword$", r"^preface$",
    r"^appendix", r"^glossary", r"^index$", r"^notes$", r"^bibliography", r"^colophon",
    r"^frontispiece", r"^books by", r"^praise for", r"^reviews", r"^blurb",
]
_NON_NARRATIVE_RE = [re.compile(p) for p in NON_NARRATIVE]


def is_non_narrative(title) -> bool:
    """Port of `isNonNarrativeChapter` (`xray_data.lua:327-335`).

    A missing or blank title counts as non-narrative -- Lua's
    `if not title then return true end` plus `if lower == "" then return
    true end`. Both callers share that rule on the device too: chapter-
    boundary selection (`xray_prefetch.lua:46`) and the timeline filter
    (`xray_fetch.lua:534`), which is why the guard lives here and not in
    either caller.
    """
    t = (title or "").lower().strip()
    if not t:
        return True
    return any(p.match(t) for p in _NON_NARRATIVE_RE)


def thin_to(items, target):
    """Lua `thinTo` (1-based -> 0-based): stride-sample down to `target`
    items, always keeping the last one."""
    if len(items) <= target:
        return list(items)
    out, step = [], len(items) / target
    for i in range(1, target + 1):
        out.append(items[int(i * step + 0.5) - 1])
    out[-1] = items[-1]
    deduped = []
    for p in out:
        if not deduped or deduped[-1] != p:
            deduped.append(p)
    return deduped


@dataclass
class Checkpoint:
    offset: int
    percent: int


def plan_checkpoints(book: BookText) -> list[Checkpoint]:
    total = len(book.full_text)
    narrative = sorted(
        (e for e in book.toc if 0 <= e.offset < total and not is_non_narrative(e.title)),
        key=lambda e: e.offset,
    )
    ends = []
    for i, e in enumerate(narrative):
        nxt = narrative[i + 1].offset if i + 1 < len(narrative) else None
        end = nxt if nxt is not None else total
        if 0 < end <= total and (not ends or ends[-1] != end):
            ends.append(end)
    if not ends or ends[-1] != total:
        ends.append(total)
    if len(ends) < 2:
        ends = []
        for pct in range(10, 101, 10):
            p = max(1, total * pct // 100)
            if not ends or ends[-1] != p:
                ends.append(p)
        ends[-1] = total
    else:
        ends = thin_to(ends, MAX_CHECKPOINTS)
        max_gap = max(1, total * MAX_INTERVAL_PCT // 100)
        densified, prev = [], 0
        for p in ends:
            gap = p - prev
            if gap > max_gap:
                parts = -(-gap // max_gap)  # ceil
                for j in range(1, parts):
                    mid = prev + gap * j // parts
                    if mid > (densified[-1] if densified else 0) and mid < p:
                        densified.append(mid)
            densified.append(p)
            prev = p
        ends = thin_to(densified, HARD_CAP)

    cps = []
    for i, p in enumerate(ends):
        # Round UP, not down. percent is the only thing the device compares the
        # reading position against (the snippet/TOC anchors are gone), and it
        # must never CLAIM LESS coverage than the snapshot actually has: a
        # checkpoint whose text runs to 4.92% but reports 4 would be activated
        # by a reader at 4%, showing entities from text they have not read.
        # Measured on a real book, flooring gave away almost a full point --
        # most of the device-side safety margin -- before device pagination
        # error even entered the picture.
        #
        # Ceiling also makes the old floor-to-1 clamp unnecessary: for any
        # p >= 1 the ceiling is already >= 1, so percent=0 (rejected by
        # schema.validate(), and only noticed after a full generation run)
        # can no longer be produced. Duplicates are absorbed by the
        # coalescing pass below.
        pct = 100 if i == len(ends) - 1 else min(100, -(-p * 100 // total))
        cps.append(Checkpoint(offset=p, percent=pct))

    # Coalesce checkpoints that land on the same integer percent (e.g. two
    # chapter boundaries <1% of the book apart): percent is a non-decreasing
    # function of offset over a strictly-increasing `ends`, so duplicates are
    # always a consecutive run. Keep the LAST (largest-offset) checkpoint of
    # each run -- coverage stays gapless and the forced-100 final checkpoint
    # is never dropped. schema.validate() requires strictly-ascending
    # percent; without this, generate_xray raises ValueError only after the
    # whole API budget for the run is already spent.
    coalesced: list[Checkpoint] = []
    for cp in cps:
        if coalesced and coalesced[-1].percent == cp.percent:
            coalesced[-1] = cp
        else:
            coalesced.append(cp)
    return coalesced
