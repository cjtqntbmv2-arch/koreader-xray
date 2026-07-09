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
    n = 40
    chapter_len = 25
    titles = [f"Chapter {i}" for i in range(1, n + 1)]
    chapter_texts = []
    for t in titles:
        pad = "x" * max(0, chapter_len - len(t) - 2)
        chapter_texts.append(f"{t}. {pad}")
    offsets, running = [], 0
    for c in chapter_texts:
        offsets.append(running)
        running += len(c)
    full_text = "".join(chapter_texts)
    toc = [TocEntry(title=t, spine_index=i, offset=o) for i, (t, o) in enumerate(zip(titles, offsets))]
    book = _book(full_text, toc)

    cps = plan_checkpoints(book)

    assert len(cps) <= HARD_CAP
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
