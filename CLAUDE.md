# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A KOReader plugin (Lua 5.1 / LuaJIT) that brings Kindle-style X-Ray to e-readers: AI-generated character bios, plot timeline, glossary, mention scanning, and spoiler protection. Supports Gemini, OpenAI, DeepSeek, Claude, and custom OpenAI/Anthropic-compatible endpoints. The deployable artifact is the `xray.koplugin/` directory — it gets copied verbatim into KOReader's `plugins/` folder. There is no build step.

Target devices include very old hardware (e.g. Kindle Paperwhite 1 from 2012), so the code defends against old KOReader versions everywhere: requires are `pcall`-wrapped with old/new module-path fallbacks (e.g. `ui/elements/reader_menu_order` vs `apps/reader/modules/readermenuorder`). Preserve these shims; don't assume recent KOReader APIs.

## Commands

```bash
# Unit tests (custom busted-compatible runner, no busted install needed)
luajit tools/spec_runner.lua

# Full green run requires KOReader's bundled libs (provides `json` etc.).
# Without it, ~11 AI-helper specs fail with nil `generationConfig`/`response_format`:
SQUASHFS_ROOT=/path/to/extracted-koreader-squashfs-root luajit tools/spec_runner.lua

# Lua syntax check (needs `pip install luaparser`)
python3 tools/check_syntax.py xray.koplugin

# Verify .po files are in sync with source
python3 tools/check_translations.py
```

- The spec list is **hardcoded** in `tools/spec_runner.lua` — a new `spec/*_spec.lua` file must be added there or it silently never runs. To run a single spec, temporarily trim that list (the runner has no filter flag), or use `busted spec/foo_spec.lua` if busted is installed.
- Windows/WSL (upstream author's setup): `powershell -ExecutionPolicy Bypass -File tools/wsl_test.ps1` runs the whole pipeline — syntax check → translation check → tests under KOReader's bundled luajit → rsync into the WSL KOReader install (preserving user `xray_config.lua` via `tools/merge_config.py`) → restart KOReader. `-Watch` re-runs on file changes; `$env:KOREADER_START_CMD` overrides the restart command.

## Release & versioning workflow

The remote is `origin` = github.com/cjtqntbmv2-arch/koreader-xray (public). The in-app OTA updater (`xray_updater.lua`) reads `releases/latest` of exactly this repo, so a published release there is what ships to devices.

Versions are CalVer-ish `YY.M.PATCH` (see `version` in `xray.koplugin/_meta.lua`, e.g. `26.7.4`).

**Version bump (routine, after release-worthy changes):** update `_meta.lua` and the version badge in `README.md` to the same value, commit locally (`chore: bump version to X.Y.Z`). **Do NOT tag and do NOT push** — bumps stay local until a release is explicitly requested.

**Release (ONLY on explicit user instruction, never proactively):**

1. Working tree must be committed and the stage empty — `release.py` commits whatever happens to be staged. **Never stage `xray.koplugin/xray_config.lua`** (carries the user's real API key locally); never use `git add -A`/`git add .`/`git commit -a` in this repo.
2. Make sure `_meta.lua` and the README badge already carry the target version (commit that first if not).
3. `python3.12 tools/release.py <version>` — stages only `_meta.lua`, commits `Release <version>` if needed, tags with the bare version, and pushes `HEAD` + that one tag to `origin`.
4. The pushed tag triggers `.github/workflows/release.yml`: zips `xray.koplugin/` into `xray.koplugin.zip` and creates a **draft** release (`-beta` in the tag → prerelease). Wait for the run: `gh run list --limit 1`.
5. Drafts are invisible to the updater API — publish with `gh release edit <version> --repo cjtqntbmv2-arch/koreader-xray --draft=false`.
6. Verify the device view: `gh api repos/cjtqntbmv2-arch/koreader-xray/releases/latest` must show the new tag and the `xray.koplugin.zip` asset.

**Tag rules:** every pushed tag triggers the release workflow — never `git push --tags` or `--follow-tags`; push tags only individually and deliberately. Old local tags (26.7.2, 26.7.3) must never be pushed. Never force-push or overwrite existing tags.

Release-notes tone rules live in `.agents/rules/release_notes.md` (no emoji, human, end-user friendly).

## Architecture: one plugin object, many mixins

`main.lua` defines `XRayPlugin` (a KOReader `WidgetContainer`). Every other `xray_*.lua` module returns a plain table of methods that `safeRequireMixin()` merges onto `XRayPlugin` (main.lua:29-48). Consequences:

- All modules share one `self` — a method in `xray_fetch.lua` calls UI code as `self:showSomething()` even though that lives in `xray_ui.lua`. To find a method's definition, grep across all modules.
- Method names must be unique across all mixin files; a collision silently overwrites.
- Adding functionality = adding a method to the topically right module file, not a new class.

Module map (by responsibility, not exhaustive):

- `main.lua` — lifecycle and KOReader integration: menu registration, Dispatcher gesture actions, event handlers (`onReaderReady`, `onPageUpdate` for auto-fetch on chapter change, `onNetworkConnected`, `onDictButtonsReady` which injects the X-Ray button into dictionary/selection popups).
- `xray_ui.lua` (~4600 lines, the bulk) — all menus, dialogs, entry views.
- `xray_aihelper.lua` — builds/parses provider-specific requests (Gemini `generationConfig`/thinking, OpenAI `response_format`/reasoning effort, Claude thinking blocks, custom endpoints with format auto-detection). Provider quirks live here.
- `xray_fetch.lua` — fetch orchestration and networking; `xray_chapteranalyzer.lua` — which entities appear in the current chapter/page; `xray_data.lua` — data processing; `xray_mentions.lua` — mention scanning; `xray_lookupmanager.lua` — text-selection lookups; `xray_seriesmanager.lua` — standalone series-recap logic.
- `xray_cachemanager.lua` — persistence: per-book X-Ray data is stored in the book's `.sdr` sidecar dir (`DocSettings:getSidecarDir`). Offline-first: fetch once, read from cache after.
- `xray_updater.lua` — OTA plugin updates; deliberately preserves the user-edited `xray_config.lua` (API keys). Don't rename `xray_config.lua` keys — user configs in the wild depend on them.
- `localization_xray.lua` — runtime `.po` loader; strings are used as `self.loc:t("key")`.
- `prompts/<lang>.lua` — AI prompt templates per language; `languages/<lang>.po` — UI translations.

## X-Ray data model: complete, offline-first, spoiler-staged

The character / location / glossary lists are meant to be a **complete, one-time-per-book, fully offline reference**, built by the checkpoint-prefetch — not by the single "fetch now". Treat this as the guiding intent when touching entity extraction:

- **Prefetch is the completeness path** (established in the 26.7.3 design, decision E3). Two triggers, same work: auto-on-WiFi (`maybeStartAutoPrefetch`) and manual "prepare for offline" (`startOfflinePrefetch`). The single "fetch now" stays intentionally top-N — do **not** bolt a multipass/topup loop onto it.
- **Spoiler-staged snapshots:** ~10–12 checkpoints (`xray_prefetch.lua`), each a %-capped snapshot in the book's `.sdr`. A snapshot never holds data past its checkpoint %; reading at X% shows the ≤X% snapshot. The cost is paid once per book; afterwards reads are local/offline and tapping to look something up never triggers an API call.
- **Completeness needs full text, not samples — for capable providers.** For Gemini and other large-context providers, segment fetches send the **full chapter text** of the covered region (context is ~1M tokens; input is not the bottleneck — JSON **output** is). Cover dense spans in output-bounded sub-chunks that merge into the one checkpoint snapshot. Small/unknown-output models keep the START/MID/END sampling + caps. Never regress those sampling shims.
- **Late single-entity adds** (word lookup) merge and `propagateEntityForward` into later snapshots, spoiler-safe (26.7.8) — a missing name must never require wiping the cache.
- **Order character/location lists by first appearance (chronological), not by recency** — the recent reading window must not dominate list order. The glossary/terms list is ordered **alphabetically** (you look a term up by name).
- **Intent: distant content deserves fuller reminders than recently-read content** (you forget the old). Today this is partly emergent — the per-checkpoint merge re-enriches long-running entities' descriptions. An explicit distance-scaled length rule is a deferred refinement (see the spec's §10); don't assume it exists.
- **Completeness applies to all three lists** (characters, locations, terms) — not just characters. Any per-segment "extract every X" instruction must name all three.

Implementation detail for the current push lives in `docs/superpowers/specs/2026-07-09-xray-full-text-entity-coverage-design.md`.

## Localization workflow (mandatory)

- `en.po` is the master. After adding/removing/changing any `loc:t("key")` usage in Lua, run `python3 tools/sync_translations.py` to propagate keys to all `.po` files.
- Prompt changes go into `prompts/en.lua` first, then audit/translate the other languages with `python3 tools/translate_all.py --audit <lang>` / `--translate <lang> "<Language Name>"` (translate mode needs `GEMINI_API_KEY`). Placeholders (`%s`, `%d`, `%1$s`) and braced tags (`{MAX_CHAR_DESC}`) and JSON keys must stay identical across languages — mismatches crash string formatting at runtime.
- New language: add it to both `LANGUAGE_NAMES` tables in `xray_ui.lua` (`showLanguageSelection`, `suggestBookLanguage`) and to the `supported` table in `xray_aihelper.lua`.

## Testing conventions

`spec/spec_helper.lua` fakes the whole KOReader environment via `package.loaded[...]` (device, uimanager, widgets, docsettings, lfs, logger) and records widgets in `_G.ui_tracker` (`shown`, `last_shown`, `closed`) so specs can assert UI behavior. Mock book/series data lives under `spec/mocks/`. Specs are written in busted syntax, but the custom runner only implements the subset of `assert.*` defined in `tools/spec_runner.lua` — stick to those matchers.

## Repo conventions

- `.agents/rules/` holds pre-existing agent rules (general, localization, release notes); the important content is folded into this file.
- Don't change the menu structure or core behavior unless the task asks for it; match the existing Lua style.
- New features and logic changes need specs in `spec/` (registered in the runner's list) and a full test run before claiming done.
