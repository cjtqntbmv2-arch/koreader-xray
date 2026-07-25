"""Generation orchestrator: ordered merge of pre-extracted chunks into the
final xray.json doc.

Extraction itself happens outside this module -- the Claude skill writes one
cleaned JSON file per chunk into the workdir (see tools/claude_xray_plan.py
and tools/claude_xray_assemble.py). What lives here is the part that carries
the D4 spoiler guarantee:

  Ordered-merge barrier -- a strictly sequential pass merges the cached chunk
  results into one BookState in (checkpoint index, chunk index) order and
  freezes a snapshot after each checkpoint. Because that order is the book's
  own and never the order results happened to arrive in, a later checkpoint's
  chunk can never leak into an earlier snapshot.

Until 2026-07-25 this module also drove a Gemini client directly: a parallel
rate-limited fetch phase (A) and a sequential description-enrichment phase (C).
Both went with the Gemini path. The assembler had already been running phase B
alone, handing in a stub client that refused every call -- so removing them
changes no output, only the amount of code that can go wrong.

Stdlib-only on purpose (see xray_core/epub.py).
"""

import json
import os
import re
from pathlib import Path

from xray_core.checkpoints import plan_checkpoints
from xray_core.epub import BookText
from xray_core.merge import BookState, clean_response
from xray_core.schema import SCHEMA_VERSION, validate

# ~8k tokens/chunk (moderate). Research: multi-entity recall degrades badly on
# huge chunks (~2x more entities from small ones), so the extraction unit is
# kept small; per-checkpoint segments still bound spoilers (D4).
FULL_TEXT_BUDGET = 32000
CHUNK_OVERLAP = 800

_GENERATOR_NAME = "calibre-xray"


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


_MAX_PATH_COMPONENT = 32


def _sanitize_path_component(value):
    """Collapse anything outside [a-z0-9_-] to '_', then cap the length.
    language/detail_level reach _chunk_path as free-form text and land
    directly in a filename below -- this makes path traversal (e.g.
    --language ../../etc) structurally impossible rather than merely
    unlikely. The cap keeps a pathological --language from pushing the
    filename past the OS limit, where open() would raise OSError mid-run."""
    return re.sub(r"[^a-z0-9_-]", "_", str(value).lower())[:_MAX_PATH_COMPONENT]


def _chunk_path(workdir, cp_idx, chunk_idx, language, detail_level):
    # The cached file holds the OUTPUT of clean_response(): already-cleaned
    # prose bound to one language, extracted under a prompt whose character
    # caps were set by detail_level (xray_core/prompts.py). Keying the
    # filename on both means a rerun after either changes simply misses
    # the cache instead of silently serving stale-language/stale-length
    # content into the new run.
    lang = _sanitize_path_component(language)
    detail = _sanitize_path_component(detail_level)
    return os.path.join(workdir, f"chunk_{cp_idx}_{chunk_idx}_{lang}_{detail}.json")


def _generator_version():
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        # VERSION not packaged, or unreadable -- e.g. inside a real (unextracted)
        # calibre plugin zip, "parent.parent" lands on the zip file itself, and
        # reading "<zip>/VERSION" raises NotADirectoryError, not FileNotFoundError.
        # Catch OSError broadly so every such case falls back rather than crashes.
        # "unknown" rather than a plausible-looking number: a wrong version in a
        # generated document is worse than an obviously missing one.
        return "unknown"


def chunk_plan(book: BookText):
    """[(checkpoint, [chunk_text, ...]), ...] -- the extraction unit list.

    The single definition of how a book is cut up, shared by the planner
    (which writes one prompt per chunk) and by generate_xray (which reads one
    result per chunk back). If these two ever disagreed, every chunk would
    miss its cache file.
    """
    cps = plan_checkpoints(book)
    plan, prev_offset = [], 0
    for cp in cps:
        plan.append((cp, _chunk_segment(book.full_text[prev_offset:cp.offset])))
        prev_offset = cp.offset
    return plan


def generate_xray(book: BookText, language, detail_level, workdir,
                   calibre_uuid=None) -> dict:
    """Merge the cached chunk extractions in `workdir` into a validated doc.

    Every chunk of every checkpoint must be present; a missing one raises
    rather than quietly producing a doc that covers less of the book than it
    claims. tools/claude_xray_assemble.py pre-checks the same thing against
    its manifest and fails earlier with a per-chunk list -- this is the
    backstop for any other caller.
    """
    plan = chunk_plan(book)

    results, missing = {}, []
    for cp_idx, (_cp, chunk_list) in enumerate(plan):
        for chunk_idx in range(len(chunk_list)):
            path = _chunk_path(workdir, cp_idx, chunk_idx, language, detail_level)
            if not os.path.exists(path):
                missing.append(os.path.basename(path))
                continue
            with open(path, "r", encoding="utf-8") as f:
                # Re-clean on load: a workdir written by an older build carries
                # whatever clean_response guaranteed back then, and
                # merge_segment trusts its input. clean_response is idempotent
                # on its own output (every field it emits is the canonical head
                # of its own fallback chain), so this costs nothing and stops a
                # stale cache from reviving a fixed bug on a rerun.
                results[(cp_idx, chunk_idx)] = clean_response(json.load(f), language)
    if missing:
        raise ValueError(
            f"{len(missing)} chunk result(s) missing from {workdir!r}: "
            + ", ".join(missing[:10])
            + (" ..." if len(missing) > 10 else "")
        )

    # Ordered-merge barrier (D4): strictly sequential, in (checkpoint, chunk)
    # index order, which is the book's own order.
    state = BookState(language)
    checkpoints_out = []
    for cp_idx, (cp, chunk_list) in enumerate(plan):
        for chunk_idx in range(len(chunk_list)):
            state.merge_segment(results[(cp_idx, chunk_idx)], cp.percent)
        checkpoints_out.append({"percent": cp.percent, "snapshot": state.snapshot()})

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
        # Always complete: a missing chunk raised above. The pair is kept in
        # the schema because the device shows it, and because a future
        # deliberate partial mode would need somewhere to say so.
        "complete": True,
        "last_percent": checkpoints_out[-1]["percent"] if checkpoints_out else 0,
        "book_type": state.book_type,
        "timeline": state.timeline,
        "checkpoints": checkpoints_out,
    }

    problems = validate(doc)
    if problems:
        raise ValueError("generated xray.json failed validation: " + "; ".join(problems))

    return doc
