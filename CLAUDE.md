# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## What this is

X-Ray for KOReader, in two halves that meet in one file:

- **Desktop generation** — Claude subagents read an EPUB and produce `xray.json`:
  characters, locations, terms, historical figures, a timeline, optional recaps
  and a relationship net, staged into checkpoints so nothing past the reader's
  position is ever in the snapshot they see. Driven by the `xray` skill
  (`.claude/skills/xray/SKILL.md`), the `tools/claude_xray_*.py` scripts and the
  stdlib-only library `xray_core/`.
- **Device plugin** — `xray.koplugin/`, seven Lua files that *read that file and
  show it*. No AI, no API key, no network except the OTA updater, no writing to
  the book. Deployed by copying the directory into KOReader's `plugins/`; there
  is no build step.

The contract between them is `schema_version: 2` (`xray_core/schema.py`).

There is no Gemini path any more. It was removed on 2026-07-25 along with the
whole on-device generation stack (six mixins, prompts, cache manager, prefetch,
mention scanning — all gone; 21 specs deleted with them). Comments that mention
Gemini are narrating that history or citing its prompt-structuring guidance.
Anything you find that still claims a provider menu or an on-device fetch is
stale: **`README.md` and `docs/on-device-debugging.md` both are, as of
2026-08-02**, and are worth reading only as a record of what was.

## Commands

```bash
python3 -m pytest tests/                 # desktop suite, ~0.4s, no network, no calibre
luajit tools/spec_runner.lua             # device specs
python3 tools/check_syntax.py xray.koplugin   # Lua parse check (needs `pip install luaparser`)
python3 tools/build_calibre_plugin.py    # -> dist/xray-generator-<VERSION>.zip
```

Both suites must be green before anything is called done. `SQUASHFS_ROOT` is no
longer needed for the specs — `spec/spec_helper.lua` mocks enough of KOReader on
its own; the variable only steers `tools/wsl_test.ps1`'s "run against a real
KOReader luajit" path.

## Generating X-Ray data

Invoke the `xray` skill; it owns the details. The shape:

1. `python3 -m tools.claude_xray_plan "<EPUB>" --workdir DIR --detail detailed`
   — chunks the book, writes one prompt file per chunk plus `manifest.json`.
2. Extraction is not a script: dispatch subagents (model `sonnet`, 3–5 chunks
   each, waves of 8–12). Each reads its prompt file and writes raw JSON. The
   batching is measured, not taste — see the skill.
3. `python3 -m tools.claude_xray_assemble "<EPUB>" --workdir DIR --out DIR`
   — cleans, merges in book order, validates, writes `xray.json` and
   `<book>.epub.xray.json` (identical bytes, two names).
4. Optional: `claude_xray_recap.py` and `claude_xray_relations.py`, each a
   `plan` → subagents → `fold` pair.

Delivery is either the calibre plugin's "Embed X-Ray" action (writes
`xray/xray.json` into the EPUB) or copying `<book>.epub.xray.json` next to the
book over USB — the fast path for testing, since it leaves the EPUB untouched
and the reading statistics with it.

## Spoiler staging — the one invariant

A snapshot never contains anything past its own checkpoint. Everything else in
this repo is negotiable; this is not.

- Desktop: chunks are fetched independently and only ever **collected**; a
  strictly sequential pass merges them in index order into a `BookState` and
  freezes `snapshot()` after each checkpoint. Ordering the merge — not the
  fetching — is what makes the guarantee hold. The `test_d4_*` family in
  `tests/test_e2e.py` carries it as assertions.
- Device: `XRayDoc.selectCheckpoint(doc, pct)` (`xray_doc.lua:349`) is the
  single place a reading position becomes a snapshot index. It takes the
  highest checkpoint whose `percent + MARGIN` the reader has passed, and returns
  `nil` rather than falling back to the earliest. Every call site routes through
  it; `xray_ui.lua` re-derives nothing.

Ordering rules that the UI depends on: characters and locations chronological
by first appearance, terms alphabetical, historical figures by role weight.

## Non-obvious things

**Merging prose.** Descriptions, definitions and biographies keep the
**longest** non-empty value across checkpoints, not the newest
(`BookState._merge`, `xray_core/merge.py`) — a late segment that mentions a
character in passing must not replace a dense earlier description with one thin
sentence. Short labels (`role`) stay newest-wins. Other deliberate divergences
from the old Lua are commented at each site in `merge.py`; they are decisions,
not drift.

**Prompts are per language and must stay that way.** `xray_core/prompts.py`
holds en/de. A German prompt with an English instruction attached to one field
comes back with that field in English — this actually happened to
`timeline[].event`. Critical rule 3 of the German prompt now demands German
values explicitly; keep it there.

**`historical_figures` means real people.** Presidents, authors, generals a book
refers to — never in-world ancestors, however historical they feel inside the
story. A secondary-world fantasy correctly yields an empty list. The cap is
`min(15, max(3, 800 // hist_cap))`, so `detailed` asks for 3 and `normal` for 8.

**`schema.py` is the contract, not `schema/xray.schema.json`.** The two are
hand-synced copies, and draft-07 cannot express the cross-field rules that
matter (checkpoint chronology, `timeline[].pct >= 1`). Bumping the schema is a
two-sided event: validator, JSON copy, and the device's `SUPPORTED_SCHEMA` gate
(`xray_doc.lua:34`).

**`xray_core/` is stdlib-only and never imports calibre.** Everything
calibre-specific lives in `calibre_plugin/`, which today only embeds — it has no
API key, no job, no configuration (`is_customizable()` returns `False`).
`build_calibre_plugin.py` flattens `calibre_plugin/*` to the zip root with
`xray_core/` and `VERSION` as siblings; `VERSION` must stay a root sibling
because `generate.py` reads it as `../VERSION`.

**BusyBox `unzip` is load-bearing on device.** `unzip -d <dir>` does *not*
create `<dir>` on BusyBox (Kobo, Kindle) — always `mkdir -p` first; there is no
`-t`, so integrity is checked by hand-parsing zip magic bytes. This path has no
off-device test: pytest and the specs run under Info-ZIP, which masks the
difference. Anything touching `os.execute("unzip …")` gets verified on real
hardware, and KOReader caches plugin code — a full restart is needed to load an
edited `.lua`.

**The spec list in `tools/spec_runner.lua` is hardcoded** (~line 147). A new
`spec/*_spec.lua` that is not listed there silently never runs.

**`_` is the i18n function** (`xray_i18n.lua`), required as
`local _ = require("xray_i18n")`. Never use `_` as a throwaway variable anywhere
in the plugin — it shadows the function and has crashed a real run. Only
`languages/de.po` exists; English is the source string.

## Version and release

One version in four places, stamped by one tool:

```bash
python3 tools/release.py <version>
```

It writes `VERSION`, `xray.koplugin/_meta.lua`, `calibre_plugin/__init__.py` and
the `README.md` badge, commits, tags, and pushes `HEAD` plus that one tag to
`origin` (github.com/cjtqntbmv2-arch/koreader-xray).

**Only ever on explicit instruction — never proactively.** A routine version
bump after a release-worthy change means editing those four files and committing
locally; tagging and pushing wait.

Every pushed tag triggers `.github/workflows/release.yml`, which runs both
suites, zips `xray.koplugin/` and the calibre plugin, and creates a **draft**
release (`-beta` in the tag → prerelease). Drafts are invisible to the device
updater, so publishing is a separate step:

```bash
gh release edit <version> --repo cjtqntbmv2-arch/koreader-xray --draft=false
```

Then verify what the device will actually see:
`gh api repos/cjtqntbmv2-arch/koreader-xray/releases/latest`.

Never `git push --tags` or `--follow-tags` — every tag that reaches the remote
starts a release. Push tags individually and deliberately, never force-push, and
never overwrite an existing one. Release-notes tone lives in
`.agents/rules/release_notes.md`: no emoji, human, written for a reader.

## Working here

- New features and logic changes need specs (`spec/`, registered in the runner)
  or tests (`tests/`), and a full run of both suites before "done".
- Match the surrounding Lua and Python style. Don't reshape the menu or core
  behaviour unless that is the task.
- Plans and designs live in `docs/` and `docs/plans/`; the current architecture
  came out of `docs/plans/2026-07-25-xray-neuausrichtung.md`.
