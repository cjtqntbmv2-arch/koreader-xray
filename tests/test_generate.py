"""Tests for the generation orchestrator: the ordered-merge D4 barrier and
the chunk cache it reads.

Extraction moved out of this module (the Claude skill writes one JSON file per
chunk), so the tests that covered the parallel fetch, truncation-split,
gleaning, quota handling and description enrichment went with it. What is left
is what generate_xray still does -- and what still has to be true.

Note on ordering: the "a late chunk finishing early must not leak into an
earlier snapshot" hazard is gone by construction, because there is no fetch
order any more -- the merge is a plain nested loop over (checkpoint, chunk)
indices. The observable D4 property is asserted directly instead, in
test_d4_no_future_entities.
"""
import json
import os

import pytest
from conftest import write_chunk_cache

from xray_core.epub import BookText, TocEntry
from xray_core.generate import _chunk_path, chunk_plan, generate_xray
from xray_core.schema import validate


def _book(full_text, toc=()):
    """Minimal BookText -- fields plan_checkpoints/generate_xray don't touch
    (spine_offsets) get a throwaway value. Empty toc -> the checkpoint
    planner's fixed 10%-grid fallback (exactly 10 checkpoints, no
    densification surprises); this is used by most tests below for
    predictability."""
    return BookText(
        title="Test Book",
        authors=["Author One"],
        language="en",
        full_text=full_text,
        spine_offsets=[e.offset for e in toc] or [0],
        toc=list(toc),
        text_hash="sha256:" + "0" * 64,
    )


def _two_chapter_book(ch1_text, ch2_text):
    toc = [
        TocEntry(title="Chapter One", spine_index=0, offset=0),
        TocEntry(title="Chapter Two", spine_index=1, offset=len(ch1_text)),
    ]
    return _book(ch1_text + ch2_text, toc)


def _filler_book(reps=400):
    # Paragraph-separated on purpose: _chunk_segment only splits at "\n\n",
    # and deliberately keeps a single oversized paragraph whole.
    full_text = "Filler prose continues along nicely for quite some time in this book.\n\n" * reps
    return _book(full_text, toc=[])


def _run(book, workdir, responses=(), language="en", detail_level="normal"):
    write_chunk_cache(book, str(workdir), language, detail_level, responses)
    return generate_xray(book, language, detail_level, str(workdir))


# ---------------------------------------------------------------------------
# End-to-end + D4
# ---------------------------------------------------------------------------


def test_end_to_end_two_checkpoints(tmp_path):
    ch1 = "Alice walks through the CH1MARKER village at dawn, greeting everyone she meets today. " * 5
    ch2 = "Bob arrives at the CH2MARKER harbor just as the tide turns for the evening light. " * 5
    book = _two_chapter_book(ch1, ch2)

    doc = _run(book, tmp_path, [
        ("CH2MARKER", {"characters": [{"name": "Bob"}]}),
        ("CH1MARKER", {"characters": [{"name": "Alice"}]}),
    ])

    assert validate(doc) == []
    assert doc["complete"] is True
    names_last = {c["name"] for c in doc["checkpoints"][-1]["snapshot"]["characters"]}
    names_first = {c["name"] for c in doc["checkpoints"][0]["snapshot"]["characters"]}
    assert names_last >= {"Alice", "Bob"}
    assert "Bob" not in names_first  # snapshot 2 accumulates snapshot 1's entities


def test_d4_no_future_entities(tmp_path):
    ch1 = "Alice explores the CH1MARKER ruins alone, searching for something long lost today. " * 5
    ch2 = "Bob discovers the CH2MARKER treasure hidden beneath the old stone archway nearby. " * 5
    book = _two_chapter_book(ch1, ch2)

    doc = _run(book, tmp_path, [
        ("CH2MARKER", {"characters": [{"name": "Bob"}]}),
        ("CH1MARKER", {"characters": [{"name": "Alice"}]}),
    ])

    checkpoints = doc["checkpoints"]
    first_with_bob = next(
        (cp for cp in checkpoints
         if any(c["name"] == "Bob" for c in cp["snapshot"]["characters"])),
        None,
    )
    assert first_with_bob is not None
    earlier = checkpoints[: checkpoints.index(first_with_bob)]
    assert all(
        not any(c["name"] == "Bob" for c in cp["snapshot"]["characters"])
        for cp in earlier
    )
    bob = next(c for c in first_with_bob["snapshot"]["characters"] if c["name"] == "Bob")
    assert bob["first_pct"] == first_with_bob["percent"]


def test_snapshot_is_a_copy_not_a_live_view(tmp_path):
    """Every checkpoint freezes its own snapshot. If snapshot() handed out the
    live BookState lists instead of a deep copy, the last merge would be
    visible in every earlier checkpoint -- the original spoiler-leak bug."""
    ch1 = "Alice walks the CH1MARKER road with little to say about it today at all. " * 5
    ch2 = "Bob waits at the CH2MARKER gate for a message that never actually arrives. " * 5
    book = _two_chapter_book(ch1, ch2)

    doc = _run(book, tmp_path, [
        ("CH1MARKER", {"characters": [{"name": "Alice", "description": "early"}]}),
        ("CH2MARKER", {"characters": [{"name": "Alice", "description": "late"}]}),
    ])

    descriptions = [
        next(c["description"] for c in cp["snapshot"]["characters"] if c["name"] == "Alice")
        for cp in doc["checkpoints"]
        if any(c["name"] == "Alice" for c in cp["snapshot"]["characters"])
    ]
    assert descriptions[0] == "early"
    assert descriptions[-1] == "late"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def test_oversized_segment_is_subchunked(tmp_path):
    """A segment past FULL_TEXT_BUDGET becomes several chunks, and every one
    of them is read back -- if the planner and the reader disagreed on the
    split, generate_xray would raise on a missing chunk."""
    # 10 checkpoints over ~420k chars -> ~42k per segment, past the 32k budget
    book = _filler_book(reps=6000)
    plan = chunk_plan(book)

    assert any(len(chunk_list) > 1 for _cp, chunk_list in plan), "fixture too small to split"

    doc = _run(book, tmp_path)
    assert validate(doc) == []


# ---------------------------------------------------------------------------
# The chunk cache
# ---------------------------------------------------------------------------


def test_missing_chunk_raises_and_names_the_file(tmp_path):
    book = _two_chapter_book("Alice walks on and on through a long first chapter here. " * 5,
                             "Bob waits patiently through an equally long second chapter. " * 5)
    write_chunk_cache(book, str(tmp_path), "en", "normal")
    victim = _chunk_path(str(tmp_path), 0, 0, "en", "normal")
    os.remove(victim)

    with pytest.raises(ValueError) as excinfo:
        generate_xray(book, "en", "normal", str(tmp_path))

    assert os.path.basename(victim) in str(excinfo.value)


@pytest.mark.parametrize("changed", [
    {"language": "de"},
    {"detail_level": "detailed"},
])
def test_cache_is_keyed_by_language_and_detail(tmp_path, changed):
    """A cache file holds already-cleaned, language-bound prose written under
    one detail level's character caps. Reading it back under different
    settings would mix languages or mis-sized prose into a document that
    declares only one -- so it must MISS, loudly, not silently reuse."""
    book = _two_chapter_book("Alice walks on and on through a long first chapter here. " * 5,
                             "Bob waits patiently through an equally long second chapter. " * 5)
    write_chunk_cache(book, str(tmp_path), "en", "normal")

    kwargs = {"language": "en", "detail_level": "normal", **changed}
    with pytest.raises(ValueError):
        generate_xray(book, kwargs["language"], kwargs["detail_level"], str(tmp_path))


def test_chunk_path_sanitizes_malicious_language(tmp_path):
    path = _chunk_path(str(tmp_path), 0, 0, "../../etc/passwd", "normal")

    assert os.path.dirname(os.path.abspath(path)) == os.path.abspath(str(tmp_path))
    assert ".." not in os.path.basename(path)
    assert "/" not in os.path.basename(path)


def test_chunk_path_caps_a_pathological_language(tmp_path):
    path = _chunk_path(str(tmp_path), 0, 0, "a" * 500, "normal")

    assert len(os.path.basename(path)) < 120  # far below any OS filename limit


def test_stale_cache_is_recleaned_on_load(tmp_path):
    """A workdir written by an older build carries whatever clean_response
    guaranteed back then, and the merge trusts its input. Re-cleaning on load
    is what stops a since-fixed bug from coming back through the cache: here
    the cached chunk uses the alternative `place`/`desc` keys, which only
    clean_response knows how to fold into name/description."""
    book = _two_chapter_book("Alice walks on and on through a long first chapter here. " * 5,
                             "Bob waits patiently through an equally long second chapter. " * 5)
    write_chunk_cache(book, str(tmp_path), "en", "normal")
    with open(_chunk_path(str(tmp_path), 0, 0, "en", "normal"), "w", encoding="utf-8") as f:
        json.dump({"locations": [{"place": "Harborside", "desc": "the old docks"}]}, f)

    doc = generate_xray(book, "en", "normal", str(tmp_path))

    locations = doc["checkpoints"][-1]["snapshot"]["locations"]
    assert any(loc["name"] == "Harborside" and loc.get("description") == "the old docks"
               for loc in locations)


def test_snapshots_carry_localized_name_placeholders(tmp_path):
    """A nameless entity keeps a placeholder name (unlike the empty-field
    divergence for role/description): BookState._merge must never let two
    nameless entries collide into one, and the placeholder follows the
    document's language."""
    ch1 = "Alice walks the CH1MARKER road with a stranger who never gives a name. " * 5
    ch2 = "Bob waits at the CH2MARKER gate beside another figure in the rain. " * 5
    book = _two_chapter_book(ch1, ch2)
    nameless = [("CH1MARKER", {"characters": [{"description": "a figure in the rain"}]})]

    en = _run(book, tmp_path / "en", nameless, language="en")
    de = _run(book, tmp_path / "de", nameless, language="de")

    def _first_name(doc):
        for cp in doc["checkpoints"]:
            for c in cp["snapshot"]["characters"]:
                return c["name"]
        return None

    assert _first_name(en)
    assert _first_name(de)
    assert _first_name(en) != _first_name(de)
