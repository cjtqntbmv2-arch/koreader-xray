from xray_core.checkpoints import (
    HARD_CAP,
    MAX_INTERVAL_PCT,
    Checkpoint,
    is_non_narrative,
    make_snippet_anchor,
    plan_checkpoints,
    thin_to,
)
from xray_core.epub import BookText, TocEntry, normalize_text


def _book(full_text, toc=()):
    """Minimal BookText for checkpoint planning -- fields plan_checkpoints
    doesn't touch (spine_offsets, text_hash) get throwaway values."""
    return BookText(
        title="T",
        authors=["A"],
        language="en",
        full_text=full_text,
        spine_offsets=[e.offset for e in toc] or [0],
        toc=list(toc),
        text_hash="sha256:" + "0" * 64,
    )


def test_thin_to_matches_lua():
    result = thin_to(list(range(1, 21)), 10)

    assert result == [2, 4, 6, 8, 10, 12, 14, 16, 18, 20]
    assert result[-1] == 20


def test_chapter_end_anchors():
    titles = ["Chapter One", "Chapter Two", "Chapter Three", "Chapter Four", "Chapter Five"]
    chapter_texts = [
        f"{t} tells its own part of the story in reasonable detail here. " * 2 for t in titles
    ]
    offsets, running = [], 0
    for c in chapter_texts:
        offsets.append(running)
        running += len(c)
    full_text = "".join(chapter_texts)
    total = len(full_text)
    toc = [TocEntry(title=t, spine_index=i, offset=o) for i, (t, o) in enumerate(zip(titles, offsets))]
    book = _book(full_text, toc)

    cps = plan_checkpoints(book)
    by_offset = {cp.offset: cp for cp in cps}

    expected_ends = offsets[1:] + [total]
    for end, title in zip(expected_ends, titles):
        assert end in by_offset, f"missing checkpoint at end of {title}"
        assert by_offset[end].chapter_anchor == {"toc_title": title, "spine_index": titles.index(title)}

    assert cps[-1].offset == total
    assert cps[-1].percent == 100
    assert [cp.offset for cp in cps] == sorted(cp.offset for cp in cps)


def test_is_non_narrative_treats_blank_title_as_non_narrative():
    # xray_data.lua:328 `if not title then return true end`
    # xray_data.lua:330 `if lower == "" then return true end`
    assert is_non_narrative(None) is True
    assert is_non_narrative("") is True
    assert is_non_narrative("   ") is True


def test_is_non_narrative_still_accepts_real_chapters():
    assert is_non_narrative("Kapitel 1") is False
    assert is_non_narrative("cover") is True


def test_non_narrative_filtered():
    assert is_non_narrative("Copyright")
    assert is_non_narrative("About the Author")
    assert not is_non_narrative("Chapter One")

    copyright_text = "All rights reserved. No part of this publication. "
    ch1_text = "Chapter one tells the beginning of a long and eventful story indeed. " * 3
    ch2_text = "Chapter two continues the tale with even more twists and turns here. " * 3
    author_text = "The author lives quietly and writes more books each year without fail. "

    offset_copyright = 0
    offset_ch1 = len(copyright_text)
    offset_ch2 = offset_ch1 + len(ch1_text)
    offset_author = offset_ch2 + len(ch2_text)
    full_text = copyright_text + ch1_text + ch2_text + author_text

    toc = [
        TocEntry(title="Copyright", spine_index=0, offset=offset_copyright),
        TocEntry(title="Chapter One", spine_index=1, offset=offset_ch1),
        TocEntry(title="Chapter Two", spine_index=2, offset=offset_ch2),
        TocEntry(title="About the Author", spine_index=3, offset=offset_author),
    ]
    book = _book(full_text, toc)

    cps = plan_checkpoints(book)
    anchored_titles = {cp.chapter_anchor["toc_title"] for cp in cps if cp.chapter_anchor}

    assert "Copyright" not in anchored_titles
    assert "About the Author" not in anchored_titles
    assert "Chapter One" in anchored_titles
    assert "Chapter Two" in anchored_titles


def test_no_toc_falls_back_to_10pct():
    full_text = "word " * 200  # exactly 1000 chars
    book = _book(full_text, toc=[])

    cps = plan_checkpoints(book)

    assert len(cps) == 10
    assert [cp.percent for cp in cps] == list(range(10, 101, 10))
    assert all(cp.chapter_anchor is None for cp in cps)
    assert cps[-1].offset == len(full_text)


def test_two_chapter_book_densified():
    ch1 = "The first movement of the story unfolds slowly across a great many pages. " * 6
    ch2 = "The second movement resolves every thread left dangling from before now. " * 6
    full_text = ch1 + ch2
    toc = [
        TocEntry(title="Part One", spine_index=0, offset=0),
        TocEntry(title="Part Two", spine_index=1, offset=len(ch1)),
    ]
    book = _book(full_text, toc)

    cps = plan_checkpoints(book)

    total = len(full_text)
    max_gap = max(1, total * MAX_INTERVAL_PCT // 100)
    prev = 0
    for cp in cps:
        assert cp.offset - prev <= max_gap
        prev = cp.offset
    assert cps[-1].offset == total


def test_hard_cap_12():
    # Uneven fixture: one huge chapter + 30 tiny ones. thin_to(ends, 10) keeps
    # a point right after the huge chapter, so the gap back to offset 0 is far
    # above MAX_INTERVAL_PCT and densify inserts several midpoints, pushing
    # the pre-clamp count to 16. Only an uneven book like this makes the
    # final thin_to(densified, HARD_CAP) clamp do real work (16 -> 12); the
    # old evenly-sized fixture thinned straight to <=10 and never re-grew
    # past HARD_CAP, so that clamp ran as a no-op and couldn't catch a break.
    n = 31
    titles = [f"Chapter {i}" for i in range(1, n + 1)]
    chapter_texts = []
    for idx, t in enumerate(titles, start=1):
        body_len = 3000 if idx == 1 else 0  # chapter 1 huge, rest tiny
        chapter_texts.append(f"{t}. " + ("x" * body_len))
    offsets, running = [], 0
    for c in chapter_texts:
        offsets.append(running)
        running += len(c)
    full_text = "".join(chapter_texts)
    toc = [TocEntry(title=t, spine_index=i, offset=o) for i, (t, o) in enumerate(zip(titles, offsets))]
    book = _book(full_text, toc)

    cps = plan_checkpoints(book)

    assert len(cps) == HARD_CAP
    assert cps[-1].offset == len(full_text)
    assert cps[-1].percent == 100


def test_last_checkpoint_is_100():
    ch1 = "Chapter text that runs long enough to be realistic and useful for testing. " * 4
    ch2 = "More chapter text that continues on for quite some additional length here. " * 4
    ch3 = "Final chapter text that wraps everything up nicely at the very end of it. " * 4
    full_text = ch1 + ch2 + ch3
    toc = [
        TocEntry(title="Chapter A", spine_index=0, offset=0),
        TocEntry(title="Chapter B", spine_index=1, offset=len(ch1)),
        TocEntry(title="Chapter C", spine_index=2, offset=len(ch1) + len(ch2)),
    ]
    book = _book(full_text, toc)

    cps = plan_checkpoints(book)

    assert cps[-1].percent == 100
    assert cps[-1].offset == len(full_text)


def test_no_duplicate_percents_short_interior_chapter():
    """Chapter B is a short interior chapter (50 chars) sandwiched between
    two long ones, well under 1% of the 10_000-char total. Pre-fix, the
    end-of-A offset (5000, exactly 50%) and end-of-B offset (5050) floor-
    divide to the SAME percent (50): `plan_checkpoints` yielded
    [..., 50, 50, ...], which schema.validate() rejects (percent must
    strictly ascend) -- crashing generate_xray only after the whole API
    budget for the run was already spent. Verified pre-fix to collide via a
    throwaway repro against git HEAD's checkpoints.py before this fix landed."""
    len_a, len_b = 5000, 50
    text_a = "Chapter A. " + "a" * (len_a - len("Chapter A. "))
    text_b = "Chapter B. " + "b" * (len_b - len("Chapter B. "))
    total_target = 10000
    len_c = total_target - len_a - len_b
    text_c = "Chapter C. " + "c" * (len_c - len("Chapter C. "))
    full_text = text_a + text_b + text_c
    assert len(full_text) == total_target

    toc = [
        TocEntry(title="Chapter A", spine_index=0, offset=0),
        TocEntry(title="Chapter B", spine_index=1, offset=len_a),
        TocEntry(title="Chapter C", spine_index=2, offset=len_a + len_b),
    ]
    book = _book(full_text, toc)

    cps = plan_checkpoints(book)
    percents = [cp.percent for cp in cps]

    assert all(a < b for a, b in zip(percents, percents[1:])), (
        f"percents must strictly ascend, got {percents}"
    )
    assert len(percents) == len(set(percents))
    assert cps[-1].percent == 100
    assert cps[-1].offset == total_target


def test_snippet_anchor_sentence_cut():
    filler = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 5
    ending = "The hero finally arrived home after a very long journey through the mountains."
    text = filler + ending + " SPOILER TEXT THAT MUST NOT APPEAR IN THE SNIPPET."
    end_offset = len(filler + ending)  # right after ending's final "."

    snippet = make_snippet_anchor(text, end_offset)

    assert 80 <= len(snippet) <= 120
    assert snippet.endswith(".")
    assert snippet == normalize_text(snippet)
    assert "SPOILER" not in snippet


def test_snippet_anchor_skips_textless():
    body = "Chapter text ends with a clean sentence right here."
    gap = "\n\n\n   \n\n"  # whitespace run between chapters
    text = body + gap + "Next chapter starts."
    end_offset = len(body) + len(gap) - 2  # lands inside the whitespace run

    snippet = make_snippet_anchor(text, end_offset)

    assert snippet != ""
    assert snippet.endswith(".")
    assert "Next chapter" not in snippet


def test_snippet_anchor_grows_until_unique():
    tail = (
        "the closing line of this passage repeats verbatim across two very "
        "different scenes in the story without a single character changing here."
    )
    context_1 = "NORTHERNVILLAGEMARKER "
    context_2 = "SOUTHERNCAVERNMARKER "
    filler = "filler word here and there padding the middle section out nicely. " * 5
    text = context_1 + tail + " " + filler + context_2 + tail
    end_1 = len(context_1 + tail)
    end_2 = len(text)

    snippet_1 = make_snippet_anchor(text, end_1)
    snippet_2 = make_snippet_anchor(text, end_2)

    normalized_full = normalize_text(text)
    assert snippet_1 != snippet_2
    assert normalized_full.count(snippet_1) == 1
    assert normalized_full.count(snippet_2) == 1
    # both had to grow past the shared tail (which alone is not unique) to get here
    assert len(snippet_1) > 120
    assert len(snippet_2) > 120


def test_snippet_anchor_empty_when_no_text():
    text = " " * 20 + "Real content starts only after all this leading whitespace."
    end_offset = 10  # still inside the leading whitespace run

    assert make_snippet_anchor(text, end_offset) == ""


def test_snippet_anchor_short_text_returned_as_is():
    text = "Short book, tiny text, nothing much happens here at all."
    assert len(text) < 80

    snippet = make_snippet_anchor(text, len(text))

    assert snippet != ""
    assert snippet == normalize_text(text)


def test_snippet_anchor_no_sentence_punctuation():
    text = (
        "the wind moved slowly across the empty field and nothing else stirred "
        "that whole long afternoon while the old house waited quietly for someone"
    )
    assert not any(c in ".!?…" for c in text)

    snippet = make_snippet_anchor(text, len(text))

    assert snippet != ""
    assert snippet in normalize_text(text)


def test_sub_one_percent_boundary_never_yields_percent_zero():
    """Two tiny front chapters put a boundary below 1% of the book. percent=0
    fails schema.validate(), and generate_xray validates only after the whole
    API budget is spent -- so the floor has to happen here, not there. The
    coalescing pass absorbs the duplicate this can create."""
    toc = [
        TocEntry(title="Kapitel 1", offset=200, spine_index=0),
        TocEntry(title="Kapitel 2", offset=500, spine_index=1),
        TocEntry(title="Kapitel 3", offset=60000, spine_index=2),
        TocEntry(title="Kapitel 4", offset=130000, spine_index=3),
    ]
    book = BookText(
        title="T", authors=["A"], language="de", full_text="x" * 200000,
        spine_offsets=[0, 200, 500, 60000, 130000], toc=toc,
        text_hash="sha256:" + "0" * 64,
    )

    percents = [cp.percent for cp in plan_checkpoints(book)]

    assert 0 not in percents
    assert percents == sorted(set(percents))  # strictly ascending, no duplicates
    assert percents[-1] == 100
