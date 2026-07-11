import json
import os

from epub_fixture import build_epub  # NOT tests.epub_fixture; run via `python3 -m pytest`
from xray_core.checkpoints import plan_checkpoints
from xray_core.epub import read_epub
from xray_core.generate import _chunk_segment
from tools.claude_xray_plan import plan_chunks, write_plan, SELF_GLEAN_LINE


def make_book_epub(tmp_path, body_html):
    # build_epub hardcodes title="Test Book", authors=("Jane Author",), language="en".
    return build_epub(tmp_path, chapters=[("Chapter", body_html)])


def _book(tmp_path):
    # Big enough to force >1 chunk in at least one checkpoint segment (>32k budget).
    # NOTE: _chunk_segment only splits at "\n\n"-separated paragraph boundaries
    # (see xray_core/generate.py:_paragraph_spans), so paragraphs must be
    # blank-line-separated in the source markup -- a single giant <p> (or
    # concatenated <p> tags with no gap) is kept whole no matter its size.
    paras = [
        "<p>" + " ".join(f"Word{j}" for j in range(i, i + 500)) + "</p>"
        for i in range(0, 50000, 500)
    ]
    body = "\n\n".join(paras)
    epub = make_book_epub(tmp_path, body)
    return read_epub(epub), epub


def test_plan_chunks_match_generate_xray_chunking(tmp_path):
    book, _ = _book(tmp_path)

    # Recompute the ground truth exactly as generate_xray does.
    cps = plan_checkpoints(book)
    expected = []
    prev = 0
    for cp_idx, cp in enumerate(cps):
        segment = book.full_text[prev:cp.offset]
        for chunk_idx, text in enumerate(_chunk_segment(segment)):
            expected.append((cp_idx, chunk_idx, cp.percent, text))
        prev = cp.offset

    got = [(c["cp_idx"], c["chunk_idx"], c["percent"], c["text"]) for c in plan_chunks(book)]
    assert got == expected
    # sanity: at least one checkpoint produced more than one chunk
    assert any(c["chunk_idx"] > 0 for c in plan_chunks(book))


def test_write_plan_emits_prompts_and_manifest(tmp_path):
    book, epub = _book(tmp_path)
    workdir = str(tmp_path / "work")
    manifest_path = write_plan(epub, "detailed", workdir)

    manifest = json.load(open(manifest_path, encoding="utf-8"))
    assert manifest["detail_level"] == "detailed"
    assert manifest["book"]["title"] == "Test Book"
    assert manifest["book"]["text_hash"] == book.text_hash
    assert len(manifest["chunks"]) == len(plan_chunks(book))

    first = manifest["chunks"][0]
    prompt = open(os.path.join(workdir, first["prompt_file"]), encoding="utf-8").read()
    # Prompt carries the chunk text, the anti-outside-knowledge clause, and the self-glean line.
    assert "BOOK TEXT CONTEXT" in prompt
    assert "no training" in prompt.lower() or "own knowledge" in prompt.lower()
    assert "RE-SCAN" in prompt
    # raw_file names line up with generate_xray's chunk keys.
    assert first["prompt_file"] == "chunk_0_0.prompt.txt"
    assert first["raw_file"] == "chunk_0_0.raw.json"
