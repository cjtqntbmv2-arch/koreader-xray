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

## Release

Versions are CalVer-ish `YY.M.PATCH` (see `version` in `xray.koplugin/_meta.lua`, e.g. `26.7.1`). `python tools/release.py <version>` bumps `_meta.lua`, commits `Release <version>`, tags with the bare version, and pushes. Any pushed tag triggers `.github/workflows/release.yml`, which zips `xray.koplugin/` and creates a **draft** GitHub release (`-beta` in the tag → prerelease). Release-notes tone rules live in `.agents/rules/release_notes.md` (no emoji, human, end-user friendly).

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
