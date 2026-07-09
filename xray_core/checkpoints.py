"""Checkpoint planning: spoiler-stage boundaries for a book.

Port of KOReader Lua's `computeCheckpoints` (`xray_prefetch.lua:35-102`),
using char offsets into `BookText.full_text` instead of page numbers. Each
`Checkpoint.offset` is the EXCLUSIVE end of the span it covers, so
`full_text[prev_offset:offset]` is exactly the text available up to that
checkpoint -- no off-by-one.

Stdlib-only on purpose (see `xray_core/epub.py`).
"""

import re
from dataclasses import dataclass

from xray_core.epub import BookText, normalize_text

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
    t = (title or "").lower().strip()
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
    snippet_anchor: str
    chapter_anchor: dict | None


def plan_checkpoints(book: BookText) -> list[Checkpoint]:
    total = len(book.full_text)
    narrative = sorted(
        (e for e in book.toc if 0 <= e.offset < total and not is_non_narrative(e.title)),
        key=lambda e: e.offset,
    )
    ends, anchors = [], {}  # anchors: end offset -> TocEntry (for chapter_anchor)
    for i, e in enumerate(narrative):
        nxt = narrative[i + 1].offset if i + 1 < len(narrative) else None
        end = nxt if nxt is not None else total
        if 0 < end <= total and (not ends or ends[-1] != end):
            ends.append(end)
            anchors[end] = e
    if not ends or ends[-1] != total:
        ends.append(total)
    if len(ends) < 2:
        ends = []
        for pct in range(10, 101, 10):
            p = max(1, total * pct // 100)
            if not ends or ends[-1] != p:
                ends.append(p)
        ends[-1] = total
        anchors = {}
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
        pct = 100 if i == len(ends) - 1 else p * 100 // total
        a = anchors.get(p)
        cps.append(Checkpoint(
            offset=p,
            percent=pct,
            snippet_anchor=make_snippet_anchor(book.full_text, p),
            chapter_anchor={"toc_title": a.title, "spine_index": a.spine_index} if a else None,
        ))
    return cps


_SENTENCE_END_CHARS = ".!?…"
_WINDOW = 400
_BASE_SNIPPET_LEN = 120
_GROWTH_CAP = 300  # ponytail: fixed ceiling: if a repeated phrase's unique
# context is farther back than this, the snippet stays non-unique; raise if
# real books hit it (device-side anchor would just be best-effort then).
_GROWTH_STEP = 40


def _cut_trailing_partial_sentence(chunk: str) -> str:
    """Discard a trailing sentence fragment so the chunk ends cleanly.

    If `chunk` already ends with sentence punctuation, it's already clean
    (the common case: normalize_text stripped any trailing whitespace).
    Otherwise, cut back to the last "punct + space" found inside it.
    """
    if not chunk or chunk[-1] in _SENTENCE_END_CHARS:
        return chunk
    best = -1
    for i in range(len(chunk) - 1):
        if chunk[i] in _SENTENCE_END_CHARS and chunk[i + 1] == " ":
            best = i
    return chunk[: best + 1] if best != -1 else chunk


def _tail_word_safe(chunk: str, length: int) -> str:
    """Last `length` chars of `chunk`, never starting mid-word."""
    if length >= len(chunk):
        return chunk
    tail = chunk[-length:]
    if chunk[-length - 1] != " " and tail[0] != " ":
        sp = tail.find(" ")
        tail = tail[sp + 1:] if sp != -1 else ""
    return tail.lstrip()


def make_snippet_anchor(text: str, end_offset: int) -> str:
    """A short, unique-in-`text` snippet ending at (at or before) `end_offset`.

    This is the device-side search marker for a checkpoint: its end is the
    spoiler boundary, so it must occur exactly once in the book -- never cut
    into the following (not-yet-revealed) text.
    """
    window = _WINDOW
    start = max(0, end_offset - window)
    chunk = normalize_text(text[start:end_offset])
    while not chunk and start > 0:
        window += _WINDOW
        start = max(0, end_offset - window)
        chunk = normalize_text(text[start:end_offset])
    if not chunk:
        return ""  # textless zone (e.g. image-only front matter): no anchor

    chunk = _cut_trailing_partial_sentence(chunk)

    length = min(_BASE_SNIPPET_LEN, len(chunk))
    snippet = _tail_word_safe(chunk, length)
    normalized_full = normalize_text(text)
    cap = min(_GROWTH_CAP, len(chunk))
    while normalized_full.count(snippet) > 1 and length < cap:
        length = min(length + _GROWTH_STEP, cap)
        snippet = _tail_word_safe(chunk, length)
    return snippet
