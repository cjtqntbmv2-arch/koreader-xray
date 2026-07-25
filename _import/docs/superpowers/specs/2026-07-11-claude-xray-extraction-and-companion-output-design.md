# Spec A — Claude-backed X-Ray extraction skill + companion output (calibre-xray)

Status: draft for review · Date: 2026-07-11 · Repo: `calibre-xray`
Sibling spec: `../koreader-xray-plugin-main/docs/2026-07-11-companion-xray-import-design.md` (importer side of the companion mode).

## 1. Problem & goal

Generating `xray.json` today calls the Gemini API per chunk, which costs money per book and is rate-limited. The user already pays a Claude subscription. Goal: a repeatable one-command skill that, given an EPUB, produces the exact KOReader-format `xray.json` using **Claude subagents as the extraction backend instead of Gemini** — no API key, no per-book API cost — and delivers it in a way that does **not** modify the book file (to preserve KOReader reading statistics).

Non-goals: replacing Gemini in the calibre plugin GUI (that path keeps using Gemini); changing the `xray.json` schema; supporting non-EPUB formats.

## 2. Key insight — reuse the `--workdir` cache seam

`xray_core.generate.generate_xray` already persists each chunk result as `chunk_<cp>_<chunk>.json` in `workdir` and **resumes** from those files without any API call (`_fetch_and_persist` / the resume loop). Therefore: if we pre-populate every `chunk_<cp>_<chunk>.json` with Claude-produced extractions, `generate_xray(workdir=…)` makes **zero** network calls and only runs the deterministic Phase B merge / D4 barrier / validate. No new contract, no format risk — the same output path the Gemini pipeline uses.

The whole deterministic stack (EPUB extraction, `plan_checkpoints`, `_chunk_segment`, `normalize_text`/`text_hash`, merge, sort, `first_pct`/`first_seq` stamping, `schema.validate`, `embed_xray`) is stdlib-only and untouched.

## 3. Architecture

Three stages, glued by the skill (the orchestrator dispatches the subagents; Python does the deterministic work):

### 3a. Planner — `tools/claude_xray_plan.py`
- Reads the EPUB via `xray_core.epub.read_epub`.
- Computes the **exact same** checkpoints + chunks as `generate_xray` would, by **importing the real functions — never reimplementing them**: `from xray_core.checkpoints import plan_checkpoints`, `from xray_core.generate import _chunk_segment`, then per checkpoint slice `full_text[prev:cp.offset]` **verbatim** and `_chunk_segment(segment)`. (Grill finding P2: a reimplemented slice/budget/overlap drifts the keys by one → `generate_xray` sees a "missing" chunk and routes it to the stub client. `plan_checkpoints` and `_chunk_segment` incl. `CHUNK_OVERLAP=800` are pure/integer/stable-sorted — determinism holds only if imported.)
- Emits, per chunk, into `workdir`:
  - `chunk_<cp>_<idx>.prompt.txt` — the extraction instruction (a Claude-tuned variant of the extract prompt) + the chunk text + the reading-progress percent for that checkpoint.
  - A `manifest.json` listing every `(cp_idx, chunk_idx, percent, prompt_path, raw_path)` plus book title/authors/language, and the detail level.
- Prints the manifest path.

### 3b. Extraction — one Claude subagent per chunk (parallel, no cap)
- The skill dispatches **one subagent per chunk**, in parallel batches, each told to: read its `prompt.txt`, extract **every** character/location/term/historical-figure/timeline entry from that chunk **exhaustively**, then in the same turn **self-glean** ("re-scan: which minor/one-scene figures did you miss?") and merge, and write the result as JSON to `chunk_<cp>_<idx>.raw.json`. Full pipeline (chunk + gleaning) collapsed into a single subagent turn per chunk.
- Progress is surfaced (n/total chunks). Because every result lands as a file, the run is **resumable**: a subagent that already wrote its `raw.json` is skipped on re-run.
- Spoiler safety is structural: each subagent only ever sees its own chunk (bounded to ≤ the checkpoint), never later text.

### 3c. Assembler — `tools/claude_xray_assemble.py`
- Reads every `chunk_<cp>_<idx>.raw.json`, runs `xray_core.merge.clean_response` on each (tolerant of missing fields), and writes the cleaned dict to `chunk_<cp>_<idx>.json` — the exact shape `generate_xray`'s resume expects. (Grill P confirmed: `clean_response` emits exactly the six keys `merge_segment` reads, so this is byte-shape-identical to what `_fetch_and_persist` persists.)
- **Fail-loud pre-check (before any assembly):** for every `(cp,idx)` in the manifest, the corresponding `raw.json` must exist AND parse as JSON. Any missing OR unparseable file → abort listing the offending `(cp,idx)`. Never silently produce a partial book.
- Then calls `generate_xray(book, stub_client, language, detail, workdir=…, enrich=False, glean=False)`. **Two hard requirements from the grill:**
  - **`enrich=False` is mandatory, not optional.** `generate_xray(enrich=None)` resolves `enrich = (detail=='detailed')`, and Phase C `_enrich_checkpoint` calls `client.generate` **even with a full cache** (it is not gated on `to_submit`). At the default `--detail detailed` this would call the stub and crash every book. Passing `enrich=False` skips Phase C entirely. (For the Claude path the richer single-pass extraction replaces enrich — deliberate divergence, §4.)
  - **The stub client's `.generate` must raise a NON-`QuotaError` (e.g. `RuntimeError`).** The fetch loop catches only `QuotaError`, sets `complete=False`, and returns a *partial* doc — so a `QuotaError` stub would turn a cache gap into a silent partial book. A `RuntimeError` propagates loudly. (The §3c pre-check already guarantees no gap reaches the fetch path; the stub is belt-and-suspenders.)
- Writes the deliverables (§5).

With every chunk cached, `to_submit` is empty, `executor.submit` is never called, and `client` is untouched in Phase A; with `enrich=False` it is untouched end-to-end (grill-confirmed).

## 4. Detail level, gleaning, reproducibility
- **Detail:** per-run argument (`--detail normal|detailed`, default `detailed`). Controls the caps passed to the prompt and whether the enrich-equivalent richer descriptions are requested. (Phase C enrich in `generate_xray` is Gemini-oriented re-synthesis; for the Claude path the richer single-pass extraction covers it — enrich is left OFF for the Claude skill to avoid a second backend round; documented as a deliberate divergence.)
- **Gleaning:** performed inside each chunk's subagent turn (self-review), not as a separate dispatch. A single merged JSON has one `timeline` array, so `_union_glean`'s "don't double the timeline" invariant is satisfied by construction (grill-confirmed).
- **Anti-outside-knowledge clauses are MANDATORY in the Claude prompt.** Structural D4 only bounds the input *text*; a prompt that drops the "use ONLY the provided text — no training/sequel/series/author knowledge; historical figures excepted for biography/role" clauses (`xray_core/prompts.py` SYSTEM + the STRICT KNOWLEDGE lines) can leak known-book facts the chunk never contained, which `schema.validate` cannot detect. The Claude-tuned prompt must keep these clauses even while dropping the Gemini-3.x-specific framing.
- **Reproducibility:** the `workdir` cache freezes the first extraction. Any re-run with the same `workdir` reloads the cached `raw.json`/`chunk.json` and produces a **byte-identical** `xray.json`. The first extraction has normal LLM run-to-run variance (true of Gemini too); the deterministic structure (anchors, `text_hash`, ordering, stamping) is identical every time regardless.

## 5. Deliverables & UX
Skill invocation: `/xray <path-to-epub> [--detail normal|detailed]`. Outputs into a **separate output dir** (default `<epub_dir>/xray_out/`, never overwriting the source):
- **`<original-basename>.epub.xray.json`** — the **companion file** (preferred, stats-safe). The name is the book's on-device path **with `.xray.json` appended** (append-based, NOT extension-substituted). This is the **shared cross-repo contract**: the importer (Spec B) derives the exact same name as `book_path .. ".xray.json"`. Append-form is case-proof (`Book.EPUB` works) and format-agnostic — chosen over `gsub("%.epub$", …)` which is case-sensitive and would misfire (grill P2, Spec B). Both specs MUST use this identical derivation.
- `<original-basename>.epub` — embedded copy with `xray/xray.json`, **byte-identical original filename** (drop-in replace for embed-before-first-read of new books). Original EPUB untouched (`embed_xray` reads source, writes a new file).
- `xray.json` — raw doc for inspection. Byte-identical to the document embedded/companion-written.
Recommendation surfaced to the user: companion file for already-read books; embedded copy for new books before first read.

## 6. Testing (no network)
- Planner: chunk keys/percents match what `generate_xray` computes for the same book (assert against imported `plan_checkpoints` + `_chunk_segment`, not a reimplementation).
- Assembler: given fake `raw.json` files, `clean_response` → cache files → `generate_xray` yields a `schema.validate`-clean doc.
- **Stub-untouched at `--detail detailed`:** run the assembler with a stub whose `.generate` raises `RuntimeError`; assert it completes without the stub ever being called (guards the `enrich=False` requirement — a regression that lets Phase C run fails here).
- **Fail-loud:** a missing OR unparseable `raw.json` raises a clear error naming the offending `(cp,idx)` — no partial doc, no network path.
- Reproducibility: two assembler runs over the same cache produce identical `xray.json` bytes.
- Deliverables: companion file named `<...>.epub.xray.json` (append-form) + embedded copy (original name) written; source EPUB byte-unchanged.
- One runnable end-to-end test on a tiny synthetic EPUB (existing `tests/epub_fixture.py`) with canned `raw.json`.

## 7. Risks / open questions
- **Big books = many subagents** (Fire and Blood ≈ 77). Accepted (parallel, no cap, resumable); progress shown.
- **Claude prompt tuning** may differ from the Gemini prompt (Gemini-3.x-specific phrasing is irrelevant to Claude). The planner uses a Claude-tuned extract prompt derived from `xray_core.prompts` content but without the Gemini-only framing.
- **`generate_xray` client coupling — resolved by the grill:** Phase A makes no fetch when the cache is complete (`to_submit` empty); the ONLY residual client use is Phase C enrich, neutralized by the mandatory `enrich=False` (§3c). The `ThreadPoolExecutor` is still constructed but never `submit`ted — harmless.
- **Manifest `percent` is cosmetic (robustness note):** `first_pct` stamping uses `cp.percent` recomputed inside `generate_xray`, not anything from the cache/prompt files, so a wrong percent in a `prompt.txt` cannot corrupt D4 stamping.
- **Detail/enrich divergence** from the Gemini path is intentional; flagged for the plan.
- Two-repo: the companion *output* here is inert until the sibling importer change ships; until then, the embedded copy is the working delivery.
