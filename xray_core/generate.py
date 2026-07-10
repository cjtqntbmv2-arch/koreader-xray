"""Generation orchestrator: the hybrid parallel-extract / ordered-merge-
barrier / sequential-enrich pipeline that produces the final xray.json doc.

Three phases (see docs/2026-07-09-calibre-xray-desktop-generation-design.md
and the task-7 brief):

  A. Parallel extraction -- a ThreadPoolExecutor fetches every chunk of
     every checkpoint concurrently (rate-limited), oversized segments are
     sub-chunked, truncated responses are split-and-retried. Results are
     only COLLECTED here, keyed by (checkpoint_index, chunk_index) --
     never merged.
  B. Ordered-merge barrier (D4) -- a strictly sequential second pass merges
     the collected results into one BookState in (checkpoint index, chunk
     index) order and snapshots after each checkpoint. Because this pass
     never depends on fetch-completion order, a later checkpoint's chunk
     finishing first can never leak into an earlier snapshot.
  C. Sequential enrichment -- an optional re-synthesis pass over recurring
     characters, walked in checkpoint order; each call is bounded to that
     checkpoint's own already-covered text (D4-safe).

Stdlib-only on purpose (see xray_core/epub.py): concurrent.futures,
threading, time, json, os -- no calibre, no third-party packages.
"""

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from xray_core.checkpoints import plan_checkpoints
from xray_core.epub import BookText
from xray_core.gemini import QuotaError
from xray_core.merge import BookState, clean_response, sort_entity_list
from xray_core.prompts import build_prompt
from xray_core.schema import SCHEMA_VERSION, validate

FULL_TEXT_BUDGET = 120000
CHUNK_OVERLAP = 800
ENRICH_TOP_N = 20

_MAX_SPLIT_DEPTH = 3  # bounded truncation-retry recursion
_GENERATOR_NAME = "calibre-xray"


class RateLimiter:
    """Token-bucket pacing shared across the executor's worker threads.

    acquire() reserves the next free slot under a lock (so concurrent
    callers never double-book the same slot) and sleeps outside the lock
    (so callers don't serialize on the wait itself, only on the booking).
    """

    def __init__(self, per_minute=10):
        self._interval = 60.0 / per_minute
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def acquire(self):
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_slot)
            self._next_slot = start + self._interval
        wait = start - now
        if wait > 0:
            time.sleep(wait)


def _paragraph_spans(text):
    """[(start, end), ...] char offsets for each "\\n\\n"-separated paragraph
    in `text`, covering it completely and in order."""
    spans = []
    start = 0
    for part in text.split("\n\n"):
        end = start + len(part)
        spans.append((start, end))
        start = end + 2  # skip the "\n\n" separator
    return spans


def _chunk_segment(segment_text, budget=FULL_TEXT_BUDGET, overlap=CHUNK_OVERLAP):
    """Split into <=budget chunks at paragraph boundaries. Every chunk after
    the first is prefixed with up to `overlap` chars of the immediately
    preceding IN-SEGMENT text -- never text from before this segment starts,
    so a checkpoint boundary is never crossed (D4).

    ponytail: greedy bin-packing (each chunk grown as full as fits) rather
    than an exactly-equal-size partition -- the brief's "equal parts" is
    satisfied by the budget bound and full paragraph-aligned coverage; a
    single pathological paragraph bigger than the budget is kept whole
    (never cut mid-paragraph) rather than force-split.
    """
    if len(segment_text) <= budget:
        return [segment_text]

    spans = _paragraph_spans(segment_text)
    groups = [spans[0]]
    for p_start, p_end in spans[1:]:
        g_start, _ = groups[-1]
        if p_end - g_start <= budget:
            groups[-1] = (g_start, p_end)
        else:
            groups.append((p_start, p_end))

    chunks = [segment_text[groups[0][0]:groups[0][1]]]
    for g_start, g_end in groups[1:]:
        prefix_start = max(0, g_start - overlap)
        chunks.append(segment_text[prefix_start:g_start] + segment_text[g_start:g_end])
    return chunks


def _split_in_half_at_paragraph(text):
    """Split `text` into two halves at the paragraph boundary closest to the
    midpoint (falls back to a hard char split if there's no boundary at
    all -- a single giant paragraph)."""
    mid = len(text) // 2
    idx = text.rfind("\n\n", 0, mid)
    if idx == -1:
        idx = text.find("\n\n", mid)
    if idx == -1:
        return text[:mid], text[mid:]
    return text[:idx], text[idx + 2:]


def _union_cleaned(a, b):
    """Union two clean_response()-shaped dicts. Just concatenates the entity
    lists rather than deduplicating here -- the (checkpoint, chunk)-indexed
    result this produces still goes through BookState.merge_segment() in
    Phase B, whose existing dedup already handles duplicate names within one
    incoming batch correctly (see merge.py)."""
    merged = {
        key: (a.get(key) or []) + (b.get(key) or [])
        for key in ("characters", "locations", "historical_figures", "terms", "timeline")
    }
    merged["book_type"] = b.get("book_type") or a.get("book_type") or "fiction"
    return merged


def _fetch_with_retry(client, rate_limiter, language, detail_level, title, author,
                       percent, chunk_text, depth=0):
    """Fetch one chunk; on truncation, split in half at a paragraph boundary
    and re-fetch each half (bounded recursion depth), unioning the cleaned
    results. Never accepts a truncated response as final while another split
    is still allowed."""
    rate_limiter.acquire()
    system, user = build_prompt(
        language, detail_level, title, author, percent, chunk_text, mode="extract"
    )
    result = client.generate(system, user)
    if not result.truncated or depth >= _MAX_SPLIT_DEPTH:
        return clean_response(result.data, language)

    first_half, second_half = _split_in_half_at_paragraph(chunk_text)
    left = _fetch_with_retry(client, rate_limiter, language, detail_level, title,
                              author, percent, first_half, depth + 1)
    right = _fetch_with_retry(client, rate_limiter, language, detail_level, title,
                               author, percent, second_half, depth + 1)
    return _union_cleaned(left, right)


_MAX_PATH_COMPONENT = 32


def _sanitize_path_component(value):
    """Collapse anything outside [a-z0-9_-] to '_', then cap the length.
    language/detail_level reach _chunk_path as free-form argparse text
    (language has no `choices=`) and land directly in a filename below --
    this makes path traversal (e.g. --language ../../etc) structurally
    impossible rather than merely unlikely. The cap keeps a pathological
    --language from pushing the filename past the OS limit, where the
    open() in _fetch_and_persist would raise OSError mid-run."""
    return re.sub(r"[^a-z0-9_-]", "_", str(value).lower())[:_MAX_PATH_COMPONENT]


def _chunk_path(workdir, cp_idx, chunk_idx, language, detail_level):
    # The cached file holds the OUTPUT of clean_response(): already-cleaned
    # prose bound to one language, fetched under a prompt whose character
    # caps were set by detail_level (xray_core/prompts.py). Keying the
    # filename on both means a resume after either changes simply misses
    # the cache instead of silently serving stale-language/stale-length
    # content into the new run.
    lang = _sanitize_path_component(language)
    detail = _sanitize_path_component(detail_level)
    return os.path.join(workdir, f"chunk_{cp_idx}_{chunk_idx}_{lang}_{detail}.json")


def _fetch_and_persist(client, rate_limiter, workdir, cp_idx, chunk_idx, language,
                        detail_level, title, author, percent, chunk_text):
    cleaned = _fetch_with_retry(
        client, rate_limiter, language, detail_level, title, author, percent, chunk_text
    )
    if workdir:
        os.makedirs(workdir, exist_ok=True)
        final_path = _chunk_path(workdir, cp_idx, chunk_idx, language, detail_level)
        tmp_path = final_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cleaned, f)
        os.replace(tmp_path, final_path)  # atomic -- a mid-write crash never leaves a corrupt final file
    return cleaned


def _completed_prefix_len(chunks_per_cp, results):
    """How many checkpoints, counted contiguously from 0, have every one of
    their chunks present in `results`. Phase B can only merge a contiguous
    run starting at checkpoint 0 -- a gap (e.g. from a QuotaError) stops it,
    regardless of what completed after the gap."""
    count = 0
    for cp_idx, chunk_list in enumerate(chunks_per_cp):
        if all((cp_idx, n) in results for n in range(len(chunk_list))):
            count += 1
        else:
            break
    return count


def _enrich_checkpoint(client, rate_limiter, language, detail_level, title, author,
                        checkpoints_out, cp, i, segment_text):
    """Phase C step for checkpoint i>=2: re-synthesize descriptions for up to
    ENRICH_TOP_N recurring (longest-running) characters already known as of
    checkpoint i, using only text already covered by checkpoint i (D4-safe).

    Patches ONLY the `description` field, in place, on checkpoint i's own
    already-frozen snapshot (built by Phase B). Never adds/removes entities
    and never re-derives the snapshot from live `BookState` -- by the time
    Phase C runs, that state is the FULLY-accumulated end-of-book state, so
    re-snapshotting it (the pre-fix bug) would leak every later checkpoint's
    entities backward into checkpoint i (a D4 spoiler leak).
    """
    frozen_characters = checkpoints_out[i]["snapshot"]["characters"]
    candidates = sort_entity_list(frozen_characters, "character")[:ENRICH_TOP_N]
    if not candidates:
        return

    prior_names = [(c["name"], c.get("description", "")) for c in candidates]
    rate_limiter.acquire()
    system, user = build_prompt(
        language, detail_level, title, author, cp.percent, segment_text,
        prior_names=prior_names, mode="enrich",
    )
    result = client.generate(system, user)
    cleaned = clean_response(result.data, language)

    by_lower_name = {c["name"].lower(): c for c in frozen_characters if c.get("name")}
    for updated in cleaned.get("characters") or []:
        target = by_lower_name.get((updated.get("name") or "").lower())
        description = updated.get("description") or ""
        if target is not None and description:
            target["description"] = description


def _generator_version():
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        # VERSION not packaged, or unreadable -- e.g. inside a real (unextracted)
        # calibre plugin zip, "parent.parent" lands on the zip file itself, and
        # reading "<zip>/VERSION" raises NotADirectoryError, not FileNotFoundError.
        # Catch OSError broadly so every such case falls back rather than crashes.
        return "0.1.0"


def generate_xray(book: BookText, client, language, detail_level,
                   calibre_uuid=None, progress_cb=None, workdir=None,
                   max_workers=3, enrich=None) -> dict:
    if enrich is None:
        enrich = detail_level == "detailed"

    cps = plan_checkpoints(book)
    author_str = ", ".join(book.authors)

    # Phase A setup: per-checkpoint segments (gapless, non-overlapping,
    # union == whole book), sub-chunked at the budget.
    segments = []
    chunks_per_cp = []
    prev_offset = 0
    for cp in cps:
        segment_text = book.full_text[prev_offset:cp.offset]
        segments.append(segment_text)
        chunks_per_cp.append(_chunk_segment(segment_text))
        prev_offset = cp.offset

    total = sum(len(c) for c in chunks_per_cp)
    if enrich:
        total += max(0, len(cps) - 2)
    done = 0

    rate_limiter = RateLimiter()
    results = {}  # (cp_idx, chunk_idx) -> cleaned dict

    # Resume: anything already on disk is loaded synchronously up front, no
    # thread / rate-limit slot / API call spent on it.
    to_submit = []
    for cp_idx, (cp, chunk_list) in enumerate(zip(cps, chunks_per_cp)):
        for chunk_idx, chunk_text in enumerate(chunk_list):
            cached = None
            if workdir:
                path = _chunk_path(workdir, cp_idx, chunk_idx, language, detail_level)
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        # Re-clean on load: a workdir written by an older build
                        # carries whatever clean_response guaranteed back then,
                        # and merge_segment trusts its input. clean_response is
                        # idempotent on its own output (every field it emits is
                        # the canonical head of its own fallback chain), so this
                        # costs nothing and stops a stale cache from reviving a
                        # fixed bug on resume.
                        cached = clean_response(json.load(f), language)
            if cached is not None:
                results[(cp_idx, chunk_idx)] = cached
                done += 1
                if progress_cb:
                    progress_cb(done, total)
            else:
                to_submit.append((cp_idx, chunk_idx, cp.percent, chunk_text))

    quota_hit = False
    pending = set()
    executor = ThreadPoolExecutor(max_workers=max_workers)
    try:
        future_to_key = {
            executor.submit(
                _fetch_and_persist, client, rate_limiter, workdir, cp_idx, chunk_idx,
                language, detail_level, book.title, author_str, percent, chunk_text,
            ): (cp_idx, chunk_idx)
            for cp_idx, chunk_idx, percent, chunk_text in to_submit
        }
        pending = set(future_to_key)
        for fut in as_completed(list(pending)):
            pending.discard(fut)
            cp_idx, chunk_idx = future_to_key[fut]
            try:
                cleaned = fut.result()
            except QuotaError:
                quota_hit = True
                break
            results[(cp_idx, chunk_idx)] = cleaned
            done += 1
            if progress_cb:
                progress_cb(done, total)
    finally:
        if quota_hit:
            # Best-effort: cancel() only succeeds for futures the executor
            # hasn't started yet (3.8-safe -- shutdown(cancel_futures=) is
            # 3.9+). Already-running fetches simply finish and are ignored.
            for fut in pending:
                fut.cancel()
        executor.shutdown(wait=True)

    complete_count = _completed_prefix_len(chunks_per_cp, results)
    complete = complete_count == len(cps) and not quota_hit

    # Phase B: ordered-merge barrier -- strictly sequential, (checkpoint,
    # chunk) index order, regardless of fetch-completion order.
    state = BookState()
    checkpoints_out = []
    for cp_idx in range(complete_count):
        cp = cps[cp_idx]
        for chunk_idx in range(len(chunks_per_cp[cp_idx])):
            state.merge_segment(results[(cp_idx, chunk_idx)], cp.percent)
        checkpoints_out.append({
            "percent": cp.percent,
            "snippet_anchor": cp.snippet_anchor,
            "chapter_anchor": cp.chapter_anchor,
            "snapshot": state.snapshot(),
        })

    # Phase C: sequential enrichment (device MERGE-MODE parity), D4-safe --
    # each call is bounded to that checkpoint's own already-covered text.
    if enrich and not quota_hit:
        for i in range(2, len(checkpoints_out)):
            _enrich_checkpoint(
                client, rate_limiter, language, detail_level, book.title, author_str,
                checkpoints_out, cps[i], i, segments[i],
            )
            done += 1
            if progress_cb:
                progress_cb(done, total)

    doc = {
        "schema_version": SCHEMA_VERSION,
        "generator": _GENERATOR_NAME,
        "generator_version": _generator_version(),
        "detail_level": detail_level,
        "language": language,
        "book_fingerprint": {
            "calibre_uuid": calibre_uuid or "",
            "title": book.title,
            "authors": book.authors,
            "text_hash": book.text_hash,
        },
        "complete": complete,
        "last_percent": cps[complete_count - 1].percent if complete_count else 0,
        "book_type": state.book_type,
        "timeline": state.timeline,
        "checkpoints": checkpoints_out,
    }

    problems = validate(doc)
    if problems:
        raise ValueError("generated xray.json failed validation: " + "; ".join(problems))

    return doc
