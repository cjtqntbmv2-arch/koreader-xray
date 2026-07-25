# Grill findings — calibre-xray plan (2026-07-09)

Five parallel adversarial subagents (Sonnet) reviewed the plan + design spec + Lua original.
Clustered by decision. Severity: 🔴 blocker · 🟠 important · 🟡 minor.

## A. Book-identity gate (text_hash) — 🔴
- Exact `text_hash` equality as import gate is **DOA**. Python `re \s` collapses NBSP (U+00A0),
  Lua `%s` (ASCII) does not → any book with one non-breaking space hashes differently.
  Plus structural extraction differences (footnotes, ruby, block/inline) between calibre
  stdlib and crengine. Design is self-contradictory: fuzzy 3-tier anchors but exact-hash identity.
- `calibre_uuid` is unverifiable on-device (no calibre there); only title/authors + a coarse
  signal are checkable.
- **Fix:** text_hash advisory only; gate = schema_version + structural sanity + case-insensitive
  title/author + bucketed char-count (±2%). Catches "wrong/re-converted book" without false refusals.

## B. Generation strategy: concurrency vs enrichment — 🔴 + 🟠
- **D4 blocker:** Task 7 fetches all chunks across all checkpoints concurrently (max_workers=3)
  but "merge in order" has no ordering mechanism. Network latency lets a later checkpoint finish
  first → future entities merge into BookState before the earlier `snapshot()` → spoiler leak.
  Synchronous fake-client tests cannot catch this.
- **Enrichment loss:** `prior_names` is always empty (Task 7), so no checkpoint carries forward
  accumulated entities. The device does AI-driven re-synthesis (MERGE MODE, `xray_aihelper.lua:1301-1353`,
  "Initial Introduction | Latest Status" anchoring). Independent-chunk fetch + mechanical merge
  drops the progressive re-enrichment that is a core design value.
- Sub-chunk boundaries have no overlap → context-blind cuts exactly on dense (verbose) segments.

## C. Output completeness — 🔴
- `maxOutputTokens=16384` shared with thinking budget; a detailed full-book segment can exceed it.
  Task 5 never checks `finishReason` → a `MAX_TOKENS` truncation is salvaged by `fix_truncated_json`
  into valid-but-incomplete JSON, silently accepted as a full segment. Completeness is the whole point.
- **Fix:** check `finishReason`; on MAX_TOKENS split the chunk and re-fetch (or mark checkpoint incomplete).

## D. Delivery survivability — 🟠
- Embedding `xray/xray.json` without an OPF manifest entry: calibre's **Convert Book** (and
  auto-convert-on-send) rebuilds the EPUB from the manifest → the unlisted entry is silently dropped.
- KOReader has **no** code today to read one arbitrary zip entry; the "reuse Updater path" claim is
  false. `unzip -p` on BusyBox/Kindle is unverified (same risk class as the 26.7.6 `unzip -t` bug).
- **Fix options:** add a manifest entry (survives convert, touches OPF) **or** document "regenerate
  after reconvert" as a known limit. Verify BusyBox `unzip -p` on-device or extract-to-temp.

## E. Device-side importer feasibility — 🔴 (planning gap)
- The entire consuming side is 3 prose sentences; the anchor mechanism is unproven.
- `findAllText` (`xray_mentions.lua:371`) returns only `{start,end}` xpointers — **no page/percent**,
  capped at 500 hits; existing `findText` usage only ever takes `results[1]`. No precedent for
  "collect all occurrences + rank by percent."
- calibre char-% and device page-% are **different units** (design admits this) — the "nearest to
  percent" tie-break compares them directly (unsound) and has **no spoiler-safe bias** (a repeated
  phrase before the true cutoff can anchor a later-inclusive snapshot too early → leak).
- Up to `HARD_CAP=12` full-book text scans back-to-back at import, no cooperative yield → likely
  **UI freeze on 2012 Kindle** (the target hardware; device already coroutine-yields for one such scan).
- **Implication:** the generator's output contract depends on what the device can actually consume.

## F. Porting-fidelity bugs (plan vs Lua) — 🟠/🟡 (mostly mechanical plan fixes)
1. Merge field-fill matches neither Lua path: Lua overwrites `role` even to blank (`xray_fetch.lua:587`),
   takes **newest** non-empty description not longest (`:589-590`); `deduplicateByName` never re-merges
   role/desc/gender at all. Plan invents "keep longest + fill empty".
2. "Drop nameless entries" **inverts** Lua, which keeps them with a placeholder name
   (`xray_aihelper.lua:2015`). Plan's fallback-key list is also wrong (`role` isn't a name key;
   `full_formal_name`/`Name` omitted).
3. thinkingConfig: Lua gates on `reasoning_effort` being set (`xray_aihelper.lua:254`); a fresh config
   sends **none** even for gemini-3.5-flash. Plan sends it unconditionally → behavior + cost change.
4. `detailed` char cap = 400 matches no Lua tier; real presets are 80/200/350/500 (`xray_ui.lua:2604`)
   → detailed = **350**. Plan also collapses Lua's 4-tier × 5-field system into one 2-value enum
   (unflagged feature reduction).
5. `prior_names` dead feature (see B).
6. Missing prompt blocks: CHARACTER COMPLETENESS / NAME DISAMBIGUATION (`xray_aihelper.lua:1480-1489`)
   — the prompt-side rule underwriting "no first-name fuzzy match" — and `context_footer`.
7. Terms merge: Lua **overwrites** aliases/expanded on exact-name hit (`xray_fetch.lua:736-737`);
   plan **unions** them. Divergence (arguably better, but not faithful).
8. historical_figures order drops the text-frequency tiebreaker (`xray_data.lua:74-111`); mitigation
   ("importer re-sorts") leans on the unbuilt device side.
9. `is_more_complete_name` word-boundary: Lua `%f[%w]` (ASCII) vs Python `(?<!\w)` (Unicode) —
   diverges for accented German names / leading-trailing punctuation.
10. Off-by-one: `end = nxt-1` (Lua page-closed) reused as Python exclusive slice bound → drops each
    chapter's true last char (1 char/boundary, conservative, harmless).
11. Percent float vs int floor-division — negligible.
12. `%%` escape handling in the "%-specifier counting" substitution is unspecified (`prompts/en.lua:51`
    "%d%%") — could misalign args.

## G. Test / invariant coverage — 🟠
- `timeline` is top-level, never nested in a snapshot → Task 10's D4 sweep structurally cannot check
  timeline events for premature exposure. Add a pct-based timeline check.
- The real spoiler gate (device-side anchor mapping) has no invariant tests even in future work.
- Intra-checkpoint sub-chunk merge order unspecified → nondeterminism if two chunks conflict.
- Pretraining spoilers: larger desktop models + detailed mode may surface known-book facts from the
  model's own knowledge even from an early segment. Add a "use only provided text" prompt constraint.

## Decisions from grilling (user, 2026-07-09)

- **Sequencing:** build calibre side AND run a device-side feasibility spike in parallel;
  verify device APIs against koreader.rocks/doc (2 subagents dispatched).
- **Generation strategy = Hybrid:** parallel extraction + strict ordered (barrier) merge
  (fixes D4 deterministically), THEN a sequential enrichment pass that re-synthesizes
  long-running entities' descriptions using accumulated context visible ≤ that checkpoint
  (stays D4-safe). Reshapes Task 7 into two phases.
- **Identity gate = title+author + schema_version + structural sanity;** text_hash stored but
  ADVISORY only (never a refusal reason). Drop exact-hash gate.
- **Detail caps:** normal = Lua default (200/100/80/100/100); detailed = Lua very-detailed
  (char 500 + max per field). Fix the bogus 400 constant.
- **Still open (pending doc results):** delivery vector (OPF manifest entry vs documented
  "regenerate after reconvert" limitation) — depends on whether device can read a zip entry.

### Plan corrections to apply (no further input needed)
- Output ceiling: check `finishReason`; on MAX_TOKENS split-and-refetch (C).
- thinkingConfig: gate behind a config flag defaulting OFF (Lua parity, lowers ceiling pressure) (F3).
- Merge: faithful-ish (newest non-empty desc, accumulate aliases); enrichment pass owns description quality (F1).
- Keep nameless entities with placeholder name; fix fallback-key list (F2).
- Add missing prompt blocks: CHARACTER COMPLETENESS / NAME DISAMBIGUATION + context_footer (F6).
- Terms merge: keep union (deliberate improvement over Lua overwrite) (F7).
- is_more_complete_name: keep Unicode boundaries (better for de) (F9).
- Fix off-by-one slice / drop vestigial -1 (F10); handle `%%` in specifier count (F12).
- Timeline: keep top-level + pct; ADD pct≤checkpoint sweep check to Task 10 (G).
- Add "use only information present in the provided text" prompt constraint (pretraining spoilers) (G).
- 429 exponential backoff + shared rate limiter; explicit Future.cancel (3.8-safe) (H).
- add_format: validate temp EPUB (zipfile.testzip/read_epub) before replace (H).
- embed_xray: `zin.read(item)` not filename; preserve `item.compress_type` (H).
- Intra-checkpoint sub-chunk merge in fixed index order (determinism) (G).
- Anchor multi-match: spoiler-safe bias (never place a snapshot boundary earlier than true cutoff) (E/D4).

## H. calibre-side safety — 🟠/🟡
- `add_format(replace=True)` overwrites the library EPUB with no backup/validation → an `embed_xray`
  bug becomes permanent data loss. Validate the temp EPUB (`zipfile.testzip()`/re-`read_epub`) first.
- `embed_xray` uses `zin.read(item.filename)` → duplicate-named zip entries corrupt; use `zin.read(item)`.
- Blanket recompression ignores `item.compress_type`; preserve it.
- No 429 backoff (Task 5) → one quota hit aborts the run; resume can re-trigger immediately.
  Add exponential backoff + a shared rate limiter; `cancel_futures` is 3.9+ (floor is 3.8).
