# calibre-xray Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** calibre plugin that generates spoiler-staged X-Ray data (characters, locations, terms, timeline) from an EPUB via Gemini and embeds it as `xray/xray.json` into the library EPUB.

**Architecture:** Pure-stdlib core library `xray_core/` (extraction → checkpoints → Gemini → merge/staging → JSON), thin calibre glue in `calibre_plugin/`. Checkpoint algorithm and merge logic are 1:1 ports of the Lua originals in `../koreader-xray-plugin-main/xray.koplugin/` (the authoritative reference). Embedding happens at the end of the generation job into the library EPUB (no send-hook).

**Tech Stack:** Python ≥3.8 stdlib only in `xray_core/` (zipfile, html.parser, urllib.request, concurrent.futures, hashlib, re, json). pytest as dev-only test runner. calibre plugin API (`InterfaceActionBase`, `JSONConfig`, `ThreadedJob`) only inside `calibre_plugin/`.

## Global Constraints

- `xray_core/` must never import `calibre` or any third-party package (testable via plain `python3 -m pytest`).
- Spec is `docs/2026-07-09-calibre-xray-desktop-generation-design.md`; Lua reference repo: `../koreader-xray-plugin-main/xray.koplugin/`.
- Checkpoint constants (verbatim from `xray_prefetch.lua:9-11`): `MAX_CHECKPOINTS = 10`, `HARD_CAP = 12`, `MAX_INTERVAL_PCT = 15`.
- Full-text budget per segment: `120000` chars (K2, from `xray_chapteranalyzer.lua` `full_text_budget`).
- Gemini defaults (from `xray_aihelper.lua`): model `gemini-3.5-flash`, endpoint `https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent`, header `x-goog-api-key`, `generationConfig = {temperature: 0.2, maxOutputTokens: 16384, responseMimeType: "application/json"}`, for `gemini-3*` models additionally `thinkingConfig = {includeThoughts: true, thinkingLevel: "medium"}`.
- Detail caps: `normal` = Lua defaults (`char_desc 200, loc_desc 100, timeline_event 80, hist_bio 100, term_def 100`); `detailed` = (`400, 200, 150, 200, 200`). Count caps use the Lua formulas: `num_chars = min(60, max(10, 50*200//char_len))`, `num_locs = min(20, max(3, 8*100//loc_len))`, `num_hist = min(15, max(3, 8*100//hist_len))`, `num_terms = min(20, max(5, 15*100//term_len))`.
- Spoiler invariant (D4): a snapshot never contains data past its checkpoint; snapshots are cumulative (snapshot N ⊇ snapshot N−1); boundaries round down.
- Entity chronology: desktop stamps `first_pct` (checkpoint percent of first appearance) + `first_seq` (monotonic counter) instead of the device's `first_page`; the KOReader importer maps `first_pct` → page. Characters/locations sort by (`first_pct`, `first_seq`); terms alphabetical; historical figures by role-weight frequency.
- All JSON output UTF-8, `ensure_ascii=False`.
- Commit after every green task; conventional-commit messages; never commit API keys or personal test EPUBs.

---

### Task 1: Interchange schema + validator

**Files:**
- Create: `schema/xray.schema.json`
- Create: `xray_core/__init__.py` (empty)
- Create: `xray_core/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `xray_core.schema.SCHEMA_VERSION = 1`; `validate(doc: dict) -> list[str]` returning a list of human-readable problems (empty = valid); `tests/conftest.py` fixture `minimal_doc()` used by later tasks.

`xray.schema.json` is documentation of the contract (draft-07 JSON Schema, kept in sync manually); `schema.py` is the enforced, hand-rolled validator (stdlib only — no jsonschema dependency).

Document shape (v1):

```json
{
  "schema_version": 1,
  "generator": "calibre-xray",
  "generator_version": "0.1.0",
  "detail_level": "normal",
  "language": "de",
  "book_fingerprint": {"calibre_uuid": "…", "title": "…", "authors": ["…"], "text_hash": "sha256:…"},
  "complete": true,
  "last_percent": 100,
  "book_type": "fiction",
  "timeline": [{"chapter": "…", "event": "…", "pct": 12}],
  "checkpoints": [
    {
      "percent": 12,
      "snippet_anchor": "…80-120 chars…",
      "chapter_anchor": {"toc_title": "…", "spine_index": 3},
      "snapshot": {
        "characters": [{"name": "…", "role": "…", "description": "…", "gender": "…", "occupation": "…", "aliases": ["…"], "first_pct": 12, "first_seq": 1}],
        "locations": [{"name": "…", "description": "…", "importance": "…", "aliases": [], "first_pct": 12, "first_seq": 2}],
        "terms": [{"name": "…", "aliases": [], "expanded": "…", "category": "…", "definition": "…"}],
        "historical_figures": [{"name": "…", "biography": "…", "role": "…", "importance_in_book": "…", "context_in_book": "…"}]
      }
    }
  ]
}
```

`chapter_anchor` may be `null` (densified/10%-fallback checkpoints). `snippet_anchor` is required on every checkpoint.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_schema.py
from xray_core.schema import validate, SCHEMA_VERSION

def test_minimal_valid_doc(minimal_doc):
    assert validate(minimal_doc) == []

def test_missing_snippet_anchor(minimal_doc):
    del minimal_doc["checkpoints"][0]["snippet_anchor"]
    assert any("snippet_anchor" in p for p in validate(minimal_doc))

def test_wrong_schema_version(minimal_doc):
    minimal_doc["schema_version"] = 99
    assert any("schema_version" in p for p in validate(minimal_doc))

def test_checkpoints_must_ascend(minimal_doc):
    cp = dict(minimal_doc["checkpoints"][0]); cp["percent"] = 5
    minimal_doc["checkpoints"].append(cp)
    assert any("ascend" in p for p in validate(minimal_doc))
```

`tests/conftest.py` provides `minimal_doc()` building the JSON above as a Python dict (one checkpoint, one character).

- [ ] **Step 2: Run** `python3 -m pytest tests/test_schema.py -v` → FAIL (module missing)
- [ ] **Step 3: Implement** `xray_core/schema.py`: `SCHEMA_VERSION = 1`; `validate()` checks: required top-level keys and types, `schema_version == 1`, fingerprint keys, every checkpoint has `percent` (int 1–100, strictly ascending, last == `last_percent`), non-empty `snippet_anchor`, `snapshot` with the four lists, characters/locations entries have `name`, `first_pct`, `first_seq`. Write `schema/xray.schema.json` mirroring it.
- [ ] **Step 4: Run** same command → PASS
- [ ] **Step 5: Commit** `feat: xray.json v1 schema + validator`

---

### Task 2: EPUB text extraction (stdlib)

**Files:**
- Create: `xray_core/epub.py`
- Test: `tests/test_epub.py`, helper `tests/epub_fixture.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class TocEntry: title: str; spine_index: int; offset: int   # char offset into full_text
  @dataclass
  class BookText:
      title: str; authors: list; language: str
      full_text: str            # spine-ordered plain text, "\n\n" between spine items
      spine_offsets: list       # char offset where each spine item begins
      toc: list                 # list[TocEntry]
      text_hash: str            # "sha256:<hex>" of normalized full_text
  class DrmError(Exception): ...
  def read_epub(path) -> BookText   # raises DrmError if META-INF/encryption.xml encrypts spine content
  def normalize_text(s) -> str  # collapse whitespace runs to single space, strip soft hyphens ­
  ```
- `text_hash = "sha256:" + hashlib.sha256(normalize_text(full_text).encode("utf-8")).hexdigest()` — the KOReader importer must be able to reproduce this, so `normalize_text` is part of the contract.

Implementation route: `zipfile` → `META-INF/container.xml` → OPF (`ElementTree`) → metadata (title/creator/language), spine idrefs → manifest hrefs → per-item HTML → text via `html.parser.HTMLParser` subclass (drop `<script>/<style>`, block elements emit `\n`). TOC: EPUB3 `nav` document (`epub:type="toc"`) or EPUB2 `toc.ncx`; map each TOC href to its spine item; `offset` = the spine item's start offset (fragment-level precision not needed — checkpoints use chapter *ends*).

- [ ] **Step 1: Write failing tests** — `tests/epub_fixture.py` exposes `build_epub(tmp_path, chapters: list[tuple[title, html_body]], toc=True, epub3=True) -> path`, building a minimal valid EPUB with `zipfile` (mimetype stored first, container.xml, content.opf, nav/ncx, one xhtml per chapter). Tests:

```python
def test_reads_spine_order_and_text(tmp_path): ...      # 3 chapters -> full_text contains all bodies in order
def test_toc_entries_have_ascending_offsets(tmp_path): ...
def test_epub2_ncx_toc(tmp_path): ...                   # epub3=False
def test_no_toc(tmp_path): ...                          # toc=False -> toc == []
def test_text_hash_stable_across_whitespace(tmp_path): ... # extra "  \n" in html -> same hash
def test_strips_tags_and_soft_hyphens(tmp_path): ...
def test_drm_raises(tmp_path): ...                      # fixture with META-INF/encryption.xml -> DrmError
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** `xray_core/epub.py` as specified
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat: stdlib EPUB text extraction with TOC + text hash`

---

### Task 3: Checkpoint planner + snippet anchors

**Files:**
- Create: `xray_core/checkpoints.py`
- Test: `tests/test_checkpoints.py`

**Interfaces:**
- Consumes: `BookText` (Task 2)
- Produces:
  ```python
  MAX_CHECKPOINTS, HARD_CAP, MAX_INTERVAL_PCT = 10, 12, 15
  def is_non_narrative(title) -> bool
  def thin_to(items, target) -> list
  @dataclass
  class Checkpoint: offset: int; percent: int; snippet_anchor: str; chapter_anchor: dict | None
  def plan_checkpoints(book: BookText) -> list[Checkpoint]
  def make_snippet_anchor(text, end_offset) -> str
  ```

Port of `computeCheckpoints` (`xray_prefetch.lua:35-102`) with char offsets instead of pages. Non-narrative patterns port (`xray_data.lua:24-31`, Lua → Python regex, matched against `title.lower().strip()`):

```python
NON_NARRATIVE = [r"^cover$", r"^title", r"^half-title", r"^copyright", r"^table of contents",
    r"^contents$", r"^dedication", r"^acknowledgment", r"^also by", r"^other books",
    r"^about the author", r"^about the", r"^epigraph$", r"^foreword$", r"^preface$",
    r"^appendix", r"^glossary", r"^index$", r"^notes$", r"^bibliography", r"^colophon",
    r"^frontispiece", r"^books by", r"^praise for", r"^reviews", r"^blurb"]
```

`thin_to` (Lua `thinTo`, 1-based → 0-based):

```python
def thin_to(items, target):
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
```

`plan_checkpoints` core (mirror the Lua flow exactly):

```python
def plan_checkpoints(book):
    total = len(book.full_text)
    narrative = sorted((e for e in book.toc
                        if 0 <= e.offset < total and not is_non_narrative(e.title)),
                       key=lambda e: e.offset)
    ends, anchors = [], {}   # anchors: end offset -> TocEntry (for chapter_anchor)
    for i, e in enumerate(narrative):
        nxt = narrative[i + 1].offset if i + 1 < len(narrative) else None
        end = (nxt - 1) if nxt is not None else total
        if 0 < end <= total and (not ends or ends[-1] != end):
            ends.append(end); anchors[end] = e
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
                parts = -(-gap // max_gap)          # ceil
                for j in range(1, parts):
                    mid = prev + gap * j // parts
                    if mid > (densified[-1] if densified else 0) and mid < p:
                        densified.append(mid)
            densified.append(p); prev = p
        ends = thin_to(densified, HARD_CAP)
    cps = []
    for i, p in enumerate(ends):
        pct = 100 if i == len(ends) - 1 else p * 100 // total
        a = anchors.get(p)
        cps.append(Checkpoint(offset=p, percent=pct,
            snippet_anchor=make_snippet_anchor(book.full_text, p),
            chapter_anchor={"toc_title": a.title, "spine_index": a.spine_index} if a else None))
    return cps
```

`make_snippet_anchor(text, end_offset)`: take `normalize_text(text[max(0, end_offset-400):end_offset])`; if it is empty/whitespace (textless zone), extend the window backwards in 400-char steps until text is found; cut the result at the last sentence boundary (`. ! ? …` followed by space) so the snippet ends on a sentence, then keep the final 80–120 chars (never cut inside a word: if the 120-char cut lands mid-word, advance to the next space).

- [ ] **Step 1: Write failing tests**

```python
def test_thin_to_matches_lua():          # [1..20] -> 10 items, last preserved
def test_chapter_end_anchors():          # 5 narrative chapters -> ends at chapter boundaries, last=total, chapter_anchor set
def test_non_narrative_filtered():       # "Copyright", "About the Author" excluded
def test_no_toc_falls_back_to_10pct():   # toc=[] -> 10 checkpoints at 10..100%, chapter_anchor None
def test_two_chapter_book_densified():   # 2 huge chapters -> no interval > 15% of total
def test_hard_cap_12():                  # 40 chapters -> <= 12 checkpoints
def test_last_checkpoint_is_100():
def test_snippet_anchor_sentence_cut():  # snippet 80-120 chars, ends at sentence end, normalized
def test_snippet_anchor_skips_textless():# end_offset inside whitespace run -> snippet from preceding text
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** as above
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat: checkpoint planner (Lua computeCheckpoints port) + snippet anchors`

---

### Task 4: Prompts (en/de) + placeholder substitution

**Files:**
- Create: `xray_core/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  ```python
  DETAIL_CAPS = {
    "normal":   {"char": 200, "loc": 100, "tl": 80,  "hist": 100, "term": 100},
    "detailed": {"char": 400, "loc": 200, "tl": 150, "hist": 200, "term": 200},
  }
  def build_prompt(language, detail_level, title, author, percent, segment_text, prior_names) -> tuple[str, str]
      # returns (system_instruction, user_prompt)
  ```

Source of the template texts: copy verbatim from `../koreader-xray-plugin-main/xray.koplugin/prompts/en.lua` (keys `system_instruction`, `comprehensive_xray`) and `prompts/de.lua` (same keys) into Python triple-quoted strings — do not paraphrase. Also copy the SEGMENT COMPLETENESS MODE addendum prose from `xray_aihelper.lua:1490-1498` (appended when fetching a prefetch segment) as `SEGMENT_ADDENDUM_EN` / `_DE`.

Substitution: the `%s`/`%d` positional args of `comprehensive_xray` are `(title, author, percent × ~20)` — count the `%`-specifiers in the template at import time and build the arg tuple accordingly (`(title, author) + (percent,) * (n - 2)`). Then `str.replace` the brace tags using `DETAIL_CAPS[detail_level]` and the count formulas from Global Constraints: `{MAX_CHAR_DESC}`, `{NUM_CHARS}`, `{MAX_LOC_DESC}`, `{NUM_LOCS}`, `{MAX_TIMELINE_EVENT}`, `{TIMELINE_DETAIL_GUIDANCE}`, `{TIMELINE_EXAMPLE}`, `{MAX_HIST_BIO}`, `{NUM_HIST}`, `{MAX_TERM_DEF}`, `{NUM_TERMS}`. Timeline guidance buckets (port from `createPrompt`): `tl<=50` brief / `<=80` concise single-sentence / `<=150` detailed / else rich; always append `"Write between {int(tl*0.75)} and {tl} characters"`. `prior_names` (names already known from earlier segments) are appended as a short "already known, do not re-describe unless new information appears" list — this mirrors the device's update-mode context. Finally append `segment_text`.

- [ ] **Step 1: Write failing tests**

```python
def test_no_unresolved_tags():        # build_prompt(...) contains no "{MAX_" or "%s"/"%d" leftovers
def test_detail_level_changes_caps(): # "400" appears in detailed, "200" in normal (char cap)
def test_de_prompt_is_german():       # de template used for language="de"
def test_segment_addendum_present():  # SEGMENT COMPLETENESS marker in user prompt
def test_prior_names_listed():
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** (copy templates, write substitution)
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat: en/de prompt templates + detail-level substitution`

---

### Task 5: Gemini client (stdlib urllib)

**Files:**
- Create: `xray_core/gemini.py`
- Test: `tests/test_gemini.py`

**Interfaces:**
- Consumes: `(system_instruction, user_prompt)` from Task 4
- Produces:
  ```python
  class QuotaError(Exception): ...
  class GeminiClient:
      def __init__(self, api_key, model="gemini-3.5-flash", transport=None, timeout=180): ...
      def generate(self, system_instruction, user_prompt) -> dict   # parsed + key-normalized JSON
  def parse_ai_json(text) -> dict     # fence-strip, brace-extract, fix_truncated_json, json.loads
  def fix_truncated_json(s) -> str
  def normalize_keys(obj)             # lowercase, spaces->underscores, recursive
  ```
- `transport` is a callable `(url, headers, body_bytes) -> (status_code, response_bytes)`; default uses `urllib.request`. Tests inject fakes — no network in tests, ever.

Request body exactly as `xray_aihelper.lua:288-298`: `contents=[{role:"user",parts:[{text}]}]`, `system_instruction={parts:[{text}]}`, the four `safetySettings` with `BLOCK_NONE`, `generationConfig` per Global Constraints (thinkingConfig only when model name contains `gemini-3`). Response: concatenate `candidates[0].content.parts[*].text` skipping parts with `thought: true`. Retry: on 503 retry once after `time.sleep(2)` (max 2 attempts); 429 → raise `QuotaError`; other non-200 → `RuntimeError` with status + body excerpt. `fix_truncated_json`: scan char-wise tracking string/escape state and a bracket stack; strip a trailing comma; append the closers for whatever remains on the stack.

- [ ] **Step 1: Write failing tests**

```python
def test_request_body_shape():        # capture body via fake transport; assert contents/system_instruction/safetySettings/generationConfig
def test_thinking_only_for_gemini3(): # model="gemini-2.0-flash" -> no thinkingConfig
def test_skips_thought_parts():
def test_retries_503_once_then_succeeds():
def test_429_raises_quota_error():
def test_parse_strips_markdown_fences():
def test_fix_truncated_json():        # '{"a": [1, 2' -> '{"a": [1, 2]}'
def test_normalize_keys():            # {"Full Name": 1} -> {"full_name": 1}
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** (monkeypatch `time.sleep` in the retry test)
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat: Gemini client with injectable transport + JSON repair`

---

### Task 6: Entity validation + merge/staging (Lua port)

**Files:**
- Create: `xray_core/merge.py`
- Test: `tests/test_merge.py`

**Interfaces:**
- Consumes: parsed AI dicts (Task 5)
- Produces:
  ```python
  def clean_response(raw: dict) -> dict     # port of validateAndCleanData essentials:
      # characters: keep name (fallback keys: role/full_name/formal_name), role (truncate 40),
      #   description, gender, occupation, aliases(list of str); drop nameless entries
      # locations: name, description, importance, aliases; historical_figures: name, biography,
      #   role(trunc 40), importance_in_book, context_in_book; terms: name, aliases, expanded,
      #   category, definition; timeline: chapter, event; book_type: "fiction"|"non_fiction"
  class BookState:                          # accumulating merge target
      characters, locations, terms, historical_figures, timeline: lists
      book_type: str; _seq: int
      def merge_segment(self, cleaned: dict, checkpoint_pct: int) -> None
      def snapshot(self) -> dict            # deep-copied {characters, locations, terms, historical_figures}, sorted
  def is_more_complete_name(new, old) -> bool
  def sort_entity_list(lst, kind) -> list   # character/location: (first_pct, first_seq); term: name.lower(); historical_figure: role-weight
  ```

Port rules (reference `xray_data.lua`):

`is_more_complete_name` (`xray_data.lua:184-198`):

```python
def is_more_complete_name(new, old):
    if not new or not old or len(new) <= len(old):
        return False
    nl, ol = new.lower(), old.lower()
    if re.search(r"(?<![\w])" + re.escape(ol) + r"(?![\w])", nl):
        return True
    return nl.startswith(ol) or nl.endswith(ol)
```

`merge_segment` per entity kind (dedup logic = `deduplicateByName`, `xray_data.lua:223-289`):
1. Build `seen = {name.lower(): entity}` and `alias_map = {alias.lower(): entity}` over existing entities.
2. Incoming entity collides if `name.lower()` matches an existing name **or** a registered alias. **No first-name fuzzy matching** (deliberate — dynasty books reuse first names).
3. On collision: if `is_more_complete_name(new, old)` → promote (old name moves into aliases, name replaced); merge incoming aliases case-insensitively (skip alias == own name); keep the longer description; fill empty fields (role/gender/occupation/importance) from the incoming entity.
4. New entity: append; for characters/locations stamp `first_pct = checkpoint_pct`, `self._seq += 1`, `first_seq = self._seq` (idempotent — never restamp).
5. Timeline: append events with `pct = checkpoint_pct`; drop events whose `chapter` is non-narrative (`is_non_narrative`, mirrors `xray_fetch.lua:534`).
6. Terms/historical figures: same dedup, no first-stamping.

`sort_entity_list` role weights for historical figures (port `sortDataByFrequency` weights only, no text-frequency — desktop has no cheap frequency source and the importer re-sorts anyway): `protagonist=100, main/lead/hero/detective=90, deuteragonist=80, major/antagonist/villain/primary=70, secondary/supporting=30, minor/background=5, else 15` (substring match on lowercased role).

`snapshot()` returns a `copy.deepcopy` of the four sorted lists — later mutation of `BookState` must not leak into earlier snapshots (that would violate D4).

- [ ] **Step 1: Write failing tests**

```python
def test_clean_drops_nameless_and_truncates_role():
def test_new_entities_stamped_first_pct_and_seq():
def test_stamp_idempotent_across_segments():      # same char in seg 1 and 2 -> keeps first_pct of seg 1
def test_alias_collision_merges():                # "Fitz" alias of "FitzChivalry" -> no duplicate
def test_name_promotion():                        # "Kvothe" then "Kvothe Kingkiller" -> promoted, old name in aliases
def test_no_first_name_fuzzy_match():             # "Robert Baratheon" and "Robert Arryn" stay separate
def test_snapshot_is_deep_copy():                 # mutate state after snapshot -> snapshot unchanged
def test_sort_characters_chronological_terms_alpha():
def test_non_narrative_timeline_events_dropped():
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** as specified
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat: entity cleaning + merge/staging (Lua deduplicateByName port)`

---

### Task 7: Generation orchestrator + resume

**Files:**
- Create: `xray_core/generate.py`
- Test: `tests/test_generate.py`

**Interfaces:**
- Consumes: everything above
- Produces:
  ```python
  FULL_TEXT_BUDGET = 120000
  def generate_xray(book: BookText, client, language, detail_level,
                    calibre_uuid=None, progress_cb=None, workdir=None) -> dict  # the xray.json doc
  ```

Flow:
1. `cps = plan_checkpoints(book)`; segments: segment *i* covers `full_text[cps[i-1].offset : cps[i].offset]` (segment 1 starts at 0).
2. Sub-chunk any segment longer than `FULL_TEXT_BUDGET` into equal parts ≤ budget (split at paragraph boundaries near the cut).
3. Fetch all chunks concurrently with `concurrent.futures.ThreadPoolExecutor(max_workers=3)`; each chunk → `build_prompt(...)` (percent = its checkpoint's percent; `prior_names` empty — chunks are independent, dedup happens at merge) → `client.generate` → `clean_response`.
4. Merge **strictly in checkpoint order** into one `BookState`; after merging all chunks of checkpoint *i*, emit `snapshot()` → `checkpoints[i].snapshot`. (Fetch is parallel, merge is sequential — that preserves `first_pct` chronology.)
5. Resume: if `workdir` given, each chunk's cleaned response is dumped to `workdir/chunk_<cp>_<n>.json` after fetch and loaded instead of fetched on re-run. On `QuotaError`/network failure mid-run: build the document from completed checkpoints only, set `complete=False`, `last_percent = last finished checkpoint`.
6. Assemble doc: fingerprint (`calibre_uuid`, `book.title`, `book.authors`, `book.text_hash`), `generator_version` read from `VERSION` file, timeline top-level, `validate()` it; raise on problems.
7. `progress_cb(done_chunks, total_chunks)` if given (calibre job UI hooks in here).

- [ ] **Step 1: Write failing tests** — fake client returning canned per-segment responses keyed by which chunk text it receives:

```python
def test_end_to_end_two_checkpoints():   # 2 chapters -> doc validates, snapshot 2 superset of snapshot 1
def test_d4_no_future_entities():        # entity only in seg 2 -> absent from snapshot 1, first_pct == cp2.percent
def test_oversized_segment_subchunked(): # segment > budget -> client called >1x for that checkpoint
def test_quota_failure_partial_doc():    # client raises QuotaError on cp 2 -> complete=False, last_percent=cp1
def test_resume_skips_fetched_chunks(tmp_path):  # run 1 fails at cp2, run 2 with same workdir refetches only cp2
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** as specified
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat: generation orchestrator (parallel fetch, ordered merge, resume)`

---

### Task 8: CLI entry point + EPUB embedding

**Files:**
- Create: `xray_core/__main__.py`
- Create: `xray_core/embed.py`
- Test: `tests/test_embed.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  ```python
  # embed.py
  def embed_xray(epub_path, doc: dict, out_path) -> None   # writes epub copy with xray/xray.json
  def read_embedded(epub_path) -> dict | None
  ```
- CLI: `python3 -m xray_core BOOK.epub --api-key KEY [--model M] [--language de] [--detail normal|detailed] [--json-out xray.json] [--embed] [--workdir DIR]` — the calibre-free path for development and power users; also the seam the calibre job calls.

`embed_xray` (order-preserving zip rewrite; `mimetype` stays first and STORED):

```python
def embed_xray(epub_path, doc, out_path):
    payload = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    with zipfile.ZipFile(epub_path) as zin, zipfile.ZipFile(out_path, "w") as zout:
        for item in zin.infolist():
            if item.filename == "xray/xray.json":
                continue
            comp = zipfile.ZIP_STORED if item.filename == "mimetype" else zipfile.ZIP_DEFLATED
            zout.writestr(item, zin.read(item.filename), comp)
        zout.writestr("xray/xray.json", payload, zipfile.ZIP_DEFLATED)
```

- [ ] **Step 1: Write failing tests**

```python
def test_embed_roundtrip(tmp_path):        # embed -> read_embedded == doc
def test_mimetype_first_and_stored(tmp_path):
def test_reembed_replaces_old(tmp_path):   # embed twice -> exactly one xray/xray.json, newest content
def test_cli_json_out(tmp_path, monkeypatch):  # fake transport via env-injected... -> use --transport-fixture flag reading canned responses from a dir (test-only flag)
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement**; CLI uses `argparse`, prints progress to stderr, exits 2 on partial (`complete=False`)
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat: CLI + EPUB embedding`

---

### Task 9: calibre plugin glue

**Files:**
- Create: `calibre_plugin/__init__.py`, `calibre_plugin/ui.py`, `calibre_plugin/config.py`, `calibre_plugin/plugin-import-name-xray_generator.txt` (empty marker file, calibre convention)
- Create: `tools/build_plugin.py`
- Test: manual (calibre GUI); no pytest here — everything testable lives in `xray_core`

**Interfaces:**
- Consumes: `generate_xray`, `embed_xray`, `read_epub`
- Produces: installable calibre plugin zip

`__init__.py`:

```python
from calibre.customize import InterfaceActionBase

class XRayGeneratorPlugin(InterfaceActionBase):
    name = "X-Ray Generator"
    description = "Generate spoiler-staged X-Ray data via Gemini and embed it into the EPUB"
    supported_platforms = ["windows", "osx", "linux"]
    author = "Daniel Niehof"
    version = (0, 1, 0)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = "calibre_plugin.ui:XRayGeneratorAction"

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugin.config import ConfigWidget
        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()
```

`config.py`: `JSONConfig("plugins/xray_generator")` with defaults `{"api_key": "", "model": "gemini-3.5-flash", "language": "de", "detail_level": "normal"}`; `ConfigWidget(QWidget)` with QLineEdit (api_key, password echo mode), QComboBox language (en/de), QComboBox detail, QLineEdit model.

`ui.py`: `InterfaceAction` named `"X-Ray Generator"`; `genesis()` sets menu action → `generate_selected()`: for each selected book id with an EPUB format: `db.format_abspath(book_id, "EPUB")` → dispatch a `ThreadedJob` running `read_epub` → `generate_xray(..., calibre_uuid=db.field_for("uuid", book_id), progress_cb=job.set_progress, workdir=<plugin tmp>)` → `embed_xray` to a temp file → on the GUI thread `db.add_format(book_id, "EPUB", tmp_path, replace=True)`. Errors → `error_dialog`; partial (`complete=False`) → warning dialog "prepared up to X% — run again to resume". Books without EPUB format are skipped with a summary dialog.

Packaging: the plugin zip must contain `calibre_plugin/*` at the zip **root** plus the whole `xray_core/` package and `VERSION` (read by `generate_xray`). `tools/build_plugin.py` builds `dist/xray-generator-<VERSION>.zip` accordingly (rewriting `calibre_plugin/...` paths to the root).

- [ ] **Step 1: Implement** the three modules + build script
- [ ] **Step 2: Build & install:** `python3 tools/build_plugin.py && calibre-customize -a dist/xray-generator-*.zip` (or `calibre-customize -b .` during iteration)
- [ ] **Step 3: Manual smoke test:** `calibre-debug -g` → configure API key → select a DRM-free EPUB → Generate X-Ray → verify job completes, then `python3 -c "from xray_core.embed import read_embedded; print(read_embedded('<library epub path>')['checkpoints'][0]['percent'])"`
- [ ] **Step 4: Commit** `feat: calibre InterfaceAction plugin (generate + embed job)`

---

### Task 10: End-to-end golden test + D4 invariant suite

**Files:**
- Create: `tests/test_e2e.py`, `tests/golden/xray_golden.json`, `tests/cassettes/` (canned cleaned-response JSONs)

**Interfaces:** consumes the full pipeline via the same fixture-transport used in Task 8.

- [ ] **Step 1: Write the test:** build a 6-chapter fixture EPUB (reuse `epub_fixture.py`) with known characters appearing in specific chapters; canned Gemini responses per chunk; run `generate_xray`; assert:
  - result equals `tests/golden/xray_golden.json` (first run writes it; committed thereafter),
  - D4 sweep: for every checkpoint N, every entity of snapshot N exists in snapshot N+1 (cumulative), and no entity has `first_pct > percent` of its snapshot,
  - `validate(doc) == []`, snippet anchors each occur exactly once in `full_text`.
- [ ] **Step 2: Run** `python3 -m pytest -v` (full suite) → all PASS
- [ ] **Step 3: Commit** `test: e2e golden run + D4 invariant sweep`
- [ ] **Step 4:** Bump nothing — version stays 0.1.0 until the KOReader importer exists and the pair is verified end-to-end on a real book.

---

## Deferred (explicitly out of scope for this plan)

- KOReader-side importer → separate plan in the KOReader repo, written once real `xray.json` fixtures exist (Task 10 output).
- Providers beyond Gemini; author-info / series-recap / find-duplicates prompt paths; embed-on-send hook (superseded by embed-at-generation); localization of the plugin GUI beyond de/en strings.
