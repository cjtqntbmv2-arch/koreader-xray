# Claude-backed X-Ray Extraction Skill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a skill + two Python tools that turn an EPUB into the KOReader-format `xray.json` using Claude subagents as the extraction backend (no Gemini API, no per-book API cost), delivered so the book file need not be modified.

**Architecture:** The repo's `xray_core.generate.generate_xray` already persists each chunk's extraction to `workdir/chunk_<cp>_<chunk>.json` and **resumes** from those files with zero network calls. We exploit that: a **planner** computes the exact same chunks and writes one prompt file per chunk; the orchestrating **skill** dispatches one Claude subagent per chunk to produce that chunk's extraction JSON; an **assembler** cleans those into the cache format and runs `generate_xray` (with the network path disabled) to merge, validate, and emit the deliverables. All deterministic work reuses existing `xray_core` code unchanged.

**Tech Stack:** Python 3 (stdlib + `xray_core`), pytest. No new third-party dependencies. The orchestration layer is a Claude Code skill (Markdown) that calls the two Python tools and dispatches subagents.

## Global Constraints

- `xray_core/` never imports from `calibre`; it is stdlib-only and pytest runs without calibre. The new tools live in `tools/` and may import `xray_core` + stdlib only — **no third-party packages, no `calibre` import.**
- **Do not change `schema/xray.schema.json` or `xray_core/schema.py`.** This feature produces the existing schema v1 document; it is NOT a schema change. (A schema change would be a two-repo event; this isn't one.)
- **Spoiler invariant D4:** a checkpoint snapshot must never contain data past its checkpoint. This is enforced by existing `xray_core` merge/stamping code and `schema.validate`; the plan must not weaken it. The Claude extraction prompt MUST keep the "use ONLY the provided text — no training/sequel/series/author knowledge (real historical figures excepted for biography/role)" clauses, because structural D4 bounds only the input text, not fabricated descriptions.
- API keys are never committed. This feature uses no API key.
- Repo is local (no remote); version lives in `VERSION` + the README badge, SemVer. **Do not bump the version** in this plan (the generator+importer pair is not yet end-to-end verified; version stays `0.1.0`).
- Test command: `python3 -m pytest tests/`. Every task is TDD: write the failing test, watch it fail, implement minimally, watch it pass, commit.

---

## Orientation (read before Task 1)

Key existing code this plan builds on (all in `/Users/dniehof/Programming/Programme/calibre-xray`):

- `xray_core/epub.py` — `read_epub(path) -> BookText`. `BookText` is a dataclass with fields `title: str`, `authors: list[str]`, `language: str`, `full_text: str`, `spine_offsets`, `toc`, `text_hash: str`.
- `xray_core/checkpoints.py` — `plan_checkpoints(book) -> list[Checkpoint]`. Each `Checkpoint` has `.offset: int` (char offset into `full_text`), `.percent: int`, `.snippet_anchor`, `.chapter_anchor`. Pure/deterministic.
- `xray_core/generate.py`:
  - `FULL_TEXT_BUDGET = 32000`, `CHUNK_OVERLAP = 800`.
  - `_chunk_segment(segment_text, budget=FULL_TEXT_BUDGET, overlap=CHUNK_OVERLAP) -> list[str]` — splits a checkpoint segment into ≤budget chunks at paragraph boundaries, prefixing each chunk after the first with up to `overlap` chars of preceding in-segment text. Pure/deterministic.
  - `_chunk_path(workdir, cp_idx, chunk_idx) -> str` — returns `os.path.join(workdir, f"chunk_{cp_idx}_{chunk_idx}.json")`.
  - `generate_xray(book, client, language, detail_level, calibre_uuid=None, progress_cb=None, workdir=None, max_workers=3, enrich=None, glean=True) -> dict` — the orchestrator. **Resume behavior:** for each `(cp_idx, chunk_idx)` it computes, if `workdir/chunk_<cp>_<chunk>.json` exists it loads it and does NOT fetch. If ALL chunks exist, no fetch future is submitted and `client` is never touched in Phase A. `enrich` defaults to `detail_level == "detailed"`; **Phase C enrich calls `client.generate` even with a full cache** unless `enrich=False`. Only `QuotaError` is caught in the fetch loop (a `QuotaError` from the client → partial doc with `complete=False`, no crash); any other exception propagates.
- `xray_core/merge.py` — `clean_response(raw: dict) -> dict` returns exactly the keys `characters`, `locations`, `historical_figures`, `terms`, `timeline`, `book_type` (tolerant of missing input fields). This is the exact shape the resume path loads and `merge_segment` consumes.
- `xray_core/prompts.py` — `build_prompt(language, detail_level, title, author, percent, segment_text, prior_names=None, mode="extract") -> (system_instruction, user_prompt)`. In `mode="extract"` it produces a chunk-first prompt (`BOOK TEXT CONTEXT:\n<chunk>\n\n---\n\n<instructions incl. the anti-outside-knowledge + SEGMENT COMPLETENESS clauses>`). `DETAIL_CAPS` is a dict keyed `"normal"`/`"detailed"`.
- `xray_core/embed.py` — `embed_xray(source_epub_path, doc, out_path)` reads `source_epub_path`, writes a NEW EPUB at `out_path` containing `xray/xray.json` = `doc`. Source is not modified.
- `xray_core/schema.py` — `validate(doc) -> list[str]` (empty list = valid).
- Tests live in `tests/`. `tests/epub_fixture.py` builds tiny synthetic EPUBs for tests (inspect it before Task 4). `tests/conftest.py` exists.

Reproducibility note to preserve: given a fixed set of `chunk_*.json`, `generate_xray` is deterministic (stable sorts, `text_hash` from the book, `generator_version` from `VERSION`, no RNG/wall-clock in the doc). So re-running the assembler over the same cache yields byte-identical `xray.json`.

### Test fixture — use this exact helper in ALL test files

The real EPUB fixture is `tests/epub_fixture.py:build_epub` — **NOT** `make_epub`. Its signature is `build_epub(tmp_path, chapters, toc=True, epub3=True, ...)` where `chapters` is a `list[(title, html_body)]`, and **title/authors/language are module constants** (`_TITLE = "Test Book"`, `_AUTHORS = ("Jane Author",)`, `_LANGUAGE = "en"`) that CANNOT be set per call. `read_epub` collapses HTML whitespace, so bodies must be HTML and paragraph structure is not preserved (irrelevant here — chunk boundaries come from `_chunk_segment` on the concatenated text). Import it the way the existing suite does: `from epub_fixture import build_epub` (works because `python3 -m pytest` puts `tests/` on `sys.path`; always run tests with `python3 -m pytest`, from the repo root).

Put this helper at the top of each test file (or a shared `tests/_xray_helpers.py` imported by all three):

```python
from epub_fixture import build_epub  # NOT tests.epub_fixture

def make_book_epub(tmp_path, body_html):
    # build_epub hardcodes title="Test Book", authors=("Jane Author",), language="en".
    # Do NOT assert other title/authors/language values -- they cannot be set here.
    return build_epub(tmp_path, chapters=[("Chapter", body_html)])
```

All test snippets below call `make_book_epub(tmp_path, "<p>...</p>")` and, where they need `read_epub`, call it on that path. Assertions only ever use the hardcoded constants (`title == "Test Book"`, `language == "en"`).

---

## Task 1: Planner — compute chunks and emit per-chunk prompt files + manifest

**Files:**
- Create: `tools/claude_xray_plan.py`
- Test: `tests/test_claude_xray_plan.py`

**Note on the extraction prompt (deliberate, not a spec miss):** the spec says "Claude-tuned prompt without Gemini framing." This plan instead **reuses `build_prompt(mode="extract")` unchanged** + appends `SELF_GLEAN_LINE`. Rationale: it guarantees the mandatory anti-outside-knowledge / D4 clauses stay intact and avoids maintaining a parallel prompt; the residual Gemini-3.x phrasing (e.g. "no code fences") is harmless to a Claude subagent. If you later want a forked Claude prompt, that is a follow-up, not this plan.

**Interfaces:**
- Consumes (imported, NEVER reimplemented): `xray_core.epub.read_epub`, `xray_core.checkpoints.plan_checkpoints`, `xray_core.generate._chunk_segment`, `xray_core.prompts.build_prompt`.
- Produces:
  - `plan_chunks(book) -> list[dict]` where each dict is `{"cp_idx": int, "chunk_idx": int, "percent": int, "text": str}`. (No `detail_level` param — chunking is detail-independent.) The `(cp_idx, chunk_idx)` pairs and `text` values MUST equal what `generate_xray` computes internally for the same book (same `plan_checkpoints` + per-checkpoint `full_text[prev:cp.offset]` slice + `_chunk_segment`).
  - `SELF_GLEAN_LINE: str` — an extra instruction appended to each prompt telling the extractor to re-scan for missed minor characters.
  - `write_plan(epub_path, detail_level, workdir) -> str` — writes `workdir/chunk_<cp>_<idx>.prompt.txt` for every chunk and `workdir/manifest.json`, returns the manifest path. Manifest shape:
    ```json
    {
      "book": {"title": "...", "authors": ["..."], "language": "en", "text_hash": "sha256:..."},
      "detail_level": "detailed",
      "chunks": [
        {"cp_idx": 0, "chunk_idx": 0, "percent": 10,
         "prompt_file": "chunk_0_0.prompt.txt", "raw_file": "chunk_0_0.raw.json"}
      ]
    }
    ```
  - CLI: `python3 -m tools.claude_xray_plan <epub> --workdir DIR [--detail normal|detailed] [--language L]` prints the manifest path.

- [ ] **Step 1: Write the failing test — chunk keys/percents/text match generate_xray's chunking**

```python
# tests/test_claude_xray_plan.py
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
    body = "<p>" + " ".join(f"Word{i}" for i in range(50000)) + "</p>"
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_claude_xray_plan.py::test_plan_chunks_match_generate_xray_chunking -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.claude_xray_plan'` (or ImportError).

- [ ] **Step 3: Write minimal implementation of `plan_chunks` + `SELF_GLEAN_LINE`**

```python
# tools/claude_xray_plan.py
"""Planner for the Claude-backed X-Ray extraction skill.

Computes the SAME chunks generate_xray computes (by importing its functions,
never reimplementing), and emits one prompt file per chunk plus a manifest.
Stdlib + xray_core only.
"""
import argparse
import json
import os

from xray_core.checkpoints import plan_checkpoints
from xray_core.epub import read_epub
from xray_core.generate import _chunk_segment
from xray_core.prompts import build_prompt

SELF_GLEAN_LINE = (
    "\n\nAFTER you have listed the entities, RE-SCAN the BOOK TEXT CONTEXT once "
    "more specifically for any character who speaks or acts but that you did not "
    "yet list -- especially minor and single-scene figures -- and ADD them. Do "
    "not omit anyone. Then output the final combined JSON object only."
)


def plan_chunks(book):
    cps = plan_checkpoints(book)
    chunks = []
    prev = 0
    for cp_idx, cp in enumerate(cps):
        segment = book.full_text[prev:cp.offset]
        for chunk_idx, text in enumerate(_chunk_segment(segment)):
            chunks.append({
                "cp_idx": cp_idx, "chunk_idx": chunk_idx,
                "percent": cp.percent, "text": text,
            })
        prev = cp.offset
    return chunks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_claude_xray_plan.py::test_plan_chunks_match_generate_xray_chunking -v`
Expected: PASS.

- [ ] **Step 5: Write the failing test — prompt files + manifest are written and well-formed**

```python
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
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python3 -m pytest tests/test_claude_xray_plan.py::test_write_plan_emits_prompts_and_manifest -v`
Expected: FAIL with `AttributeError`/`ImportError` on `write_plan`.

- [ ] **Step 7: Implement `write_plan` + CLI**

```python
def write_plan(epub_path, detail_level, workdir):
    book = read_epub(epub_path)
    os.makedirs(workdir, exist_ok=True)
    author = ", ".join(book.authors)
    chunks_meta = []
    for c in plan_chunks(book):
        cp_idx, chunk_idx, percent = c["cp_idx"], c["chunk_idx"], c["percent"]
        system, user = build_prompt(
            book.language, detail_level, book.title, author, percent, c["text"], mode="extract"
        )
        prompt_text = system + "\n\n" + user + SELF_GLEAN_LINE
        prompt_file = f"chunk_{cp_idx}_{chunk_idx}.prompt.txt"
        raw_file = f"chunk_{cp_idx}_{chunk_idx}.raw.json"
        with open(os.path.join(workdir, prompt_file), "w", encoding="utf-8") as f:
            f.write(prompt_text)
        chunks_meta.append({
            "cp_idx": cp_idx, "chunk_idx": chunk_idx, "percent": percent,
            "prompt_file": prompt_file, "raw_file": raw_file,
        })
    manifest = {
        "book": {"title": book.title, "authors": book.authors,
                 "language": book.language, "text_hash": book.text_hash},
        "detail_level": detail_level,
        "chunks": chunks_meta,
    }
    manifest_path = os.path.join(workdir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest_path


def main(argv=None):
    p = argparse.ArgumentParser(prog="claude_xray_plan")
    p.add_argument("book")
    p.add_argument("--workdir", required=True)
    p.add_argument("--detail", choices=["normal", "detailed"], default="detailed")
    args = p.parse_args(argv)
    manifest_path = write_plan(args.book, args.detail, args.workdir)
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

(No `--language` flag: the prompt language comes from `book.language`. Adding an override would be a dead option today — YAGNI.)

- [ ] **Step 8: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_claude_xray_plan.py -v`
Expected: PASS (both tests).

- [ ] **Step 9: Commit**

```bash
git add tools/claude_xray_plan.py tests/test_claude_xray_plan.py
git commit -m "feat(tools): planner emits per-chunk prompts + manifest for Claude extraction"
```

---

## Task 2: Assembler — clean subagent outputs into the cache and run generate_xray

**Files:**
- Create: `tools/claude_xray_assemble.py`
- Test: `tests/test_claude_xray_assemble.py`

**Interfaces:**
- Consumes: the `workdir` populated by Task 1 (`manifest.json` + one `chunk_<cp>_<idx>.raw.json` per chunk, produced by subagents), and `xray_core.merge.clean_response`, `xray_core.generate.generate_xray`, `xray_core.generate._chunk_path`, `xray_core.epub.read_epub`, `xray_core.embed.embed_xray`.
- Produces: `assemble(epub_path, workdir, out_dir) -> dict` (the validated xray doc), and writes three deliverables into `out_dir`:
  - `<basename>.xray.json` — companion (name = `os.path.basename(epub_path) + ".xray.json"`, e.g. `Book.epub.xray.json`; append-form, the cross-repo contract with the importer).
  - `<basename>` — embedded EPUB copy (identical original filename), via `embed_xray`.
  - `xray.json` — the raw doc for inspection.
  - A stub client `_NoNetworkClient` whose `.generate` raises `RuntimeError` (NOT `QuotaError`).
- CLI: `python3 -m tools.claude_xray_assemble <epub> --workdir DIR --out DIR`.

- [ ] **Step 1: Write the failing test — assemble produces a valid doc + deliverables from canned raw.json, no network**

```python
# tests/test_claude_xray_assemble.py
import json
import os

import pytest

from epub_fixture import build_epub  # NOT tests.epub_fixture
from xray_core.schema import validate
from tools.claude_xray_plan import write_plan
from tools.claude_xray_assemble import assemble


def _prepare(tmp_path):
    body = "<p>" + " ".join(f"AliceBob{i}" for i in range(40000)) + "</p>"
    epub = build_epub(tmp_path, chapters=[("Chapter", body)])  # title/authors/language are fixture constants
    workdir = str(tmp_path / "work")
    manifest_path = write_plan(epub, "detailed", workdir)
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    # Simulate subagents: write a raw.json per chunk.
    for ch in manifest["chunks"]:
        raw = {"book_type": "fiction",
               "characters": [{"name": "Alice", "description": "A person."}],
               "locations": [], "historical_figures": [], "terms": [], "timeline": []}
        with open(os.path.join(workdir, ch["raw_file"]), "w", encoding="utf-8") as f:
            json.dump(raw, f)
    return epub, workdir


def test_assemble_produces_valid_doc_and_deliverables(tmp_path):
    epub, workdir = _prepare(tmp_path)
    out = str(tmp_path / "out")
    src_before = open(epub, "rb").read()

    doc = assemble(epub, workdir, out)

    assert validate(doc) == []
    assert any(c["name"] == "Alice" for c in doc["checkpoints"][-1]["snapshot"]["characters"])
    base = os.path.basename(epub)
    assert os.path.exists(os.path.join(out, base + ".xray.json"))   # companion (append-form)
    assert os.path.exists(os.path.join(out, base))                  # embedded copy (same name)
    assert os.path.exists(os.path.join(out, "xray.json"))           # raw
    # source EPUB untouched
    assert open(epub, "rb").read() == src_before
    # companion == raw doc bytes
    assert open(os.path.join(out, base + ".xray.json"), encoding="utf-8").read() == \
           open(os.path.join(out, "xray.json"), encoding="utf-8").read()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_claude_xray_assemble.py::test_assemble_produces_valid_doc_and_deliverables -v`
Expected: FAIL (ImportError on `tools.claude_xray_assemble`).

- [ ] **Step 3: Implement the assembler**

```python
# tools/claude_xray_assemble.py
"""Assembler for the Claude-backed X-Ray extraction skill.

Reads subagent-produced chunk_<cp>_<idx>.raw.json, cleans them into the
generate_xray resume cache, then runs generate_xray with the network path
disabled (enrich=False, glean=False, stub client) and writes deliverables.
Stdlib + xray_core only.
"""
import argparse
import json
import os

from xray_core.embed import embed_xray
from xray_core.epub import read_epub
from xray_core.generate import _chunk_path, generate_xray
from xray_core.merge import clean_response


class _NoNetworkClient:
    """A full cache means generate_xray never fetches. If a chunk is missing,
    this raises a NON-QuotaError so the gap surfaces loudly instead of being
    swallowed into a partial doc (only QuotaError yields a partial result)."""

    def generate(self, *args, **kwargs):
        raise RuntimeError(
            "claude_xray_assemble: generate_xray tried to hit the network -- "
            "a chunk cache entry is missing. This is a bug; run the planner + "
            "all subagents first."
        )


def _load_manifest(workdir):
    with open(os.path.join(workdir, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def _precheck(workdir, manifest):
    """Every chunk's raw.json must exist AND parse. Fail loud listing offenders."""
    problems = []
    for ch in manifest["chunks"]:
        path = os.path.join(workdir, ch["raw_file"])
        key = f'({ch["cp_idx"]},{ch["chunk_idx"]})'
        if not os.path.exists(path):
            problems.append(f"{key} missing {ch['raw_file']}")
            continue
        try:
            with open(path, encoding="utf-8") as f:
                json.load(f)
        except (ValueError, OSError) as e:
            problems.append(f"{key} unparseable {ch['raw_file']}: {e}")
    if problems:
        raise SystemExit("assemble aborted -- incomplete/invalid chunk cache:\n  " +
                         "\n  ".join(problems))


def assemble(epub_path, workdir, out_dir):
    book = read_epub(epub_path)
    manifest = _load_manifest(workdir)
    detail = manifest["detail_level"]
    _precheck(workdir, manifest)

    # raw.json -> clean_response -> chunk_<cp>_<idx>.json (the resume cache shape)
    for ch in manifest["chunks"]:
        with open(os.path.join(workdir, ch["raw_file"]), encoding="utf-8") as f:
            raw = json.load(f)
        cleaned = clean_response(raw)
        with open(_chunk_path(workdir, ch["cp_idx"], ch["chunk_idx"]), "w", encoding="utf-8") as f:
            json.dump(cleaned, f)

    # enrich=False is MANDATORY: Phase C would call client.generate even with a
    # full cache (it is not gated on to_submit) and crash the stub. glean=False
    # is irrelevant with a full cache but set for clarity.
    doc = generate_xray(book, _NoNetworkClient(), book.language, detail,
                        workdir=workdir, enrich=False, glean=False)

    os.makedirs(out_dir, exist_ok=True)
    base = os.path.basename(epub_path)
    raw_json = os.path.join(out_dir, "xray.json")
    with open(raw_json, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    # companion: byte-identical to xray.json, append-form name (cross-repo contract)
    companion = os.path.join(out_dir, base + ".xray.json")
    with open(companion, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    # embedded copy: identical original filename, source untouched
    embed_xray(epub_path, doc, os.path.join(out_dir, base))
    return doc


def main(argv=None):
    p = argparse.ArgumentParser(prog="claude_xray_assemble")
    p.add_argument("book")
    p.add_argument("--workdir", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    assemble(args.book, args.workdir, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_claude_xray_assemble.py::test_assemble_produces_valid_doc_and_deliverables -v`
Expected: PASS.

- [ ] **Step 5: Write the failing test — fail-loud on a missing chunk, and the stub is never called at --detail detailed**

```python
def test_assemble_fails_loud_on_missing_chunk(tmp_path):
    epub, workdir = _prepare(tmp_path)
    # remove one raw.json
    manifest = json.load(open(os.path.join(workdir, "manifest.json"), encoding="utf-8"))
    os.remove(os.path.join(workdir, manifest["chunks"][0]["raw_file"]))
    with pytest.raises(SystemExit) as ei:
        assemble(epub, workdir, str(tmp_path / "out2"))
    assert "missing" in str(ei.value)


def test_assemble_never_hits_network_at_detailed(tmp_path):
    # A complete cache at detail=detailed must NOT invoke the stub client
    # (guards the mandatory enrich=False). If enrich ran, _NoNetworkClient
    # would raise RuntimeError and this test would error.
    epub, workdir = _prepare(tmp_path)  # detail=detailed
    doc = assemble(epub, workdir, str(tmp_path / "out3"))
    assert doc["complete"] is True
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_claude_xray_assemble.py -v`
Expected: PASS (all three). If `test_assemble_never_hits_network_at_detailed` errors with the `_NoNetworkClient` RuntimeError, the `enrich=False` argument is missing — fix the `generate_xray` call.

- [ ] **Step 7: Write the failing test — reproducibility (same cache → identical bytes)**

```python
def test_assemble_is_reproducible(tmp_path):
    epub, workdir = _prepare(tmp_path)
    d1 = assemble(epub, workdir, str(tmp_path / "a"))
    d2 = assemble(epub, workdir, str(tmp_path / "b"))
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)
    assert open(tmp_path / "a" / "xray.json").read() == open(tmp_path / "b" / "xray.json").read()
```

- [ ] **Step 8: Run test to verify it passes** (no new impl needed; determinism already holds)

Run: `python3 -m pytest tests/test_claude_xray_assemble.py::test_assemble_is_reproducible -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add tools/claude_xray_assemble.py tests/test_claude_xray_assemble.py
git commit -m "feat(tools): assembler builds xray.json + deliverables from subagent chunk outputs"
```

---

## Task 3: The orchestration skill

**Files:**
- Create: `.claude/skills/xray/SKILL.md` (or the repo's established skill location — check `.claude/` first; if the repo has no skills dir yet, create `.claude/skills/xray/SKILL.md`)
- Create: `docs/xray-skill-usage.md` (short human doc)

**Interfaces:**
- Consumes: `tools/claude_xray_plan.py` (Task 1), `tools/claude_xray_assemble.py` (Task 2).
- Produces: a skill that, given an EPUB path, runs planner → dispatches one subagent per chunk → runs assembler.

This task has no unit test (it is orchestration prose executed by Claude). Its "test" is the Task 4 end-to-end run done manually once. Keep the SKILL.md precise and self-contained.

- [ ] **Step 1: Write `SKILL.md`**

Content (adapt front-matter to this repo's skill format):

```markdown
---
name: xray
description: Generate KOReader X-Ray data (xray.json) from an EPUB using Claude subagents as the extraction backend (no Gemini API). Use when the user gives an EPUB and wants embedded/companion X-Ray output.
---

# xray — Claude-backed X-Ray generation

Given an EPUB path (and optional `--detail normal|detailed`, default `detailed`):

1. **Plan.** Run:
   `python3 -m tools.claude_xray_plan "<EPUB>" --workdir "<WORKDIR>" --detail <detail>`
   Read the printed `manifest.json`. It lists every chunk as `{cp_idx, chunk_idx, percent, prompt_file, raw_file}`.

2. **Extract (one subagent per chunk, in parallel batches, no cap).** Dispatch with the **Agent/Task tool** (one subagent per chunk). Send them in waves of ~8–12 concurrent subagents (a realistic batch size — a 77-chunk book is ~7 waves), each processing one chunk. For each chunk whose `raw_file` does not yet exist in `<WORKDIR>` (resume-safe), the subagent is told to:
   - Read `<WORKDIR>/<prompt_file>` (it contains the full extraction instruction + that chunk's text).
   - Follow it exactly: extract EVERY character/location/term/historical-figure/timeline entry present in the chunk, then self-glean (re-scan for missed minor figures), using ONLY the provided text.
   - Write the resulting JSON object (only the JSON, matching the schema described in the prompt) to `<WORKDIR>/<raw_file>`.
   Show progress (n/total). Because each result is a file, re-running skips finished chunks.

3. **Assemble.** Run:
   `python3 -m tools.claude_xray_assemble "<EPUB>" --workdir "<WORKDIR>" --out "<OUTDIR>"`
   This cleans the raw outputs into the resume cache, runs the deterministic merge/validate, and writes `<OUTDIR>/<book>.epub.xray.json` (companion), `<OUTDIR>/<book>.epub` (embedded copy), and `<OUTDIR>/xray.json`.

4. **Report** the three output paths and tell the user: use the companion `.xray.json` (drop next to the book on the device) to preserve reading statistics on already-read books; use the embedded copy for new books before first read. The original EPUB is never modified.

Constraints: never modify the source EPUB; if the assembler aborts listing missing/invalid chunks, re-dispatch subagents for exactly those `(cp,idx)` and re-run the assembler.
```

- [ ] **Step 2: Write `docs/xray-skill-usage.md`** — a 15–20 line human summary of the three steps + the stats-preservation guidance above.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/xray/SKILL.md docs/xray-skill-usage.md
git commit -m "feat(skill): xray orchestration skill (plan -> subagent extract -> assemble)"
```

---

## Task 4: End-to-end test on a synthetic EPUB

**Files:**
- Test: `tests/test_claude_xray_e2e.py`

**Interfaces:** Consumes Tasks 1 + 2 and `tests/epub_fixture.py`. Uses `build_epub` per the Orientation "Test fixture" note (title/authors/language are fixed constants; bodies are HTML).

- [ ] **Step 1: Write the failing end-to-end test**

```python
# tests/test_claude_xray_e2e.py
import json
import os

from epub_fixture import build_epub  # NOT tests.epub_fixture
from xray_core.schema import validate
from tools.claude_xray_plan import write_plan
from tools.claude_xray_assemble import assemble


def test_plan_extract_assemble_end_to_end(tmp_path):
    body = "<p>" + " ".join(
        f"Chapter{i} Alice meets Bob and Carol in Eldras filler filler filler" for i in range(6000)
    ) + "</p>"
    epub = build_epub(tmp_path, chapters=[("Chapter", body)])  # title="Test Book" (fixture constant)
    workdir = str(tmp_path / "work")
    manifest = json.load(open(write_plan(epub, "detailed", workdir), encoding="utf-8"))

    # Stand-in for real subagents: deterministic canned extraction per chunk.
    for ch in manifest["chunks"]:
        with open(os.path.join(workdir, ch["raw_file"]), "w", encoding="utf-8") as f:
            json.dump({"book_type": "fiction",
                       "characters": [{"name": "Alice", "description": "Protagonist."},
                                      {"name": "Bob", "description": "Friend."}],
                       "locations": [{"name": "Eldras", "description": "A town."}],
                       "historical_figures": [], "terms": [], "timeline": []}, f)

    doc = assemble(epub, workdir, str(tmp_path / "out"))
    assert validate(doc) == []
    names = {c["name"] for c in doc["checkpoints"][-1]["snapshot"]["characters"]}
    assert {"Alice", "Bob"} <= names
    # D4: no character stamped past its checkpoint
    for cp in doc["checkpoints"]:
        for c in cp["snapshot"]["characters"]:
            assert c["first_pct"] <= cp["percent"]
```

- [ ] **Step 2: Run it, verify it fails, then passes once Tasks 1–2 are in** (it should pass immediately if Tasks 1–2 are complete; if it fails, fix the fixture API usage).

Run: `python3 -m pytest tests/test_claude_xray_e2e.py -v`
Expected: PASS.

- [ ] **Step 3: Run the whole suite**

Run: `python3 -m pytest tests/`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_claude_xray_e2e.py
git commit -m "test(e2e): plan -> canned extract -> assemble yields valid, D4-safe xray.json"
```

---

## Done criteria
- `python3 -m pytest tests/` green.
- `tools/claude_xray_plan.py` + `tools/claude_xray_assemble.py` exist, import (never reimplement) the `xray_core` chunking functions, and never import `calibre` or third-party packages.
- The skill runs plan → subagent extract → assemble and emits companion (`<book>.epub.xray.json`), embedded copy (`<book>.epub`), and `xray.json`, leaving the source EPUB byte-unchanged.
- Version NOT bumped (stays `0.1.0`).
