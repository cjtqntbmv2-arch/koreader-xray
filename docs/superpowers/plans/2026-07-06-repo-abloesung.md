# Repo-Ablösung Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Projekt vom Original-Repo lösen: eigenes öffentliches Repo `cjtqntbmv2-arch/koreader-xray`, OTA-Updater zieht von dort, alle Original-Hinweise raus bis auf eine Attribution.

**Architecture:** Bestehende Historie (nur eigene Commits, key-frei verifiziert) wird weitergeführt; ein Rebranding-Commit, dann Repo-Erstellung via `gh`, Push, Tag `26.7.4`, Workflow-Draft-Release publizieren.

**Tech Stack:** Lua 5.1 (Plugin), gh CLI, GitHub Actions (bestehender `release.yml`).

## Global Constraints

- **NIE `git add -A`, `git add .` oder `git commit -a`** — `xray.koplugin/xray_config.lua` trägt den echten User-API-Key (modified, bewusst uncommitted). Staging nur per explizitem Pfad.
- Erlaubte verbleibende Autoren-Hinweise: `LICENSE` (MIT-Pflichtvermerk „Jimmy Pautz") und die eine Credits-Zeile in `README.md`. Sonst nirgends `ultimatejimmy`/`jimmy` in getrackten Dateien.
- Nur Tag `26.7.4` pushen — Alt-Tags 26.7.2/26.7.3 bleiben lokal (gepushte Tags triggern den Release-Workflow).
- Suite-Baseline: 197 passed / 11 failed (AI-Helper ohne `SQUASHFS_ROOT`); Akzeptanz = keine NEUEN Fails.
- Tools mit `python3.12`; Syntax-Check per `luajit -bl <datei> > /dev/null`.

---

### Task 1: Rebranding-Commit

**Files:**
- Modify: `xray.koplugin/xray_updater.lua:15-16`
- Modify: `README.md` (komplett neu)
- Modify: `.agents/rules/release_notes.md:19-38`
- Modify: `run_koreader.bat:9`, `tools/wsl_test.ps1:30,44`, `tools/spec_runner.lua:1`
- Modify: `.gitignore`
- Delete (aus Index): `.leann/` (9 Dateien), `.agents/.DS_Store`

**Interfaces:**
- Produces: Updater-Konstanten `GITHUB_OWNER = "cjtqntbmv2-arch"`, `GITHUB_REPO = "koreader-xray"` — Task 2/3 erzeugen genau dieses Repo und das Release, das `_apiUrl()` abfragt.

- [ ] **Step 1: Updater-Konstanten umstellen** (`xray.koplugin/xray_updater.lua`)

```lua
local GITHUB_OWNER = "cjtqntbmv2-arch"
local GITHUB_REPO  = "koreader-xray"
```

- [ ] **Step 2: README.md komplett ersetzen** durch:

```markdown
# X-Ray Plugin for KOReader

![version](https://img.shields.io/badge/version-26.7.4-blue)
![Platform](https://img.shields.io/badge/platform-KOReader-green.svg)
![License](https://img.shields.io/badge/license-MIT-yellow.svg)

This plugin brings Kindle-style X-Ray features to KOReader. It uses AI to track characters, build plot timelines, and provide insights while you read.

## What it does

- **AI-Powered Insights**: Supports Google Gemini, OpenAI, **DeepSeek**, **Claude**, and **Custom API** providers (like OpenRouter).
- **Character Tracking**: View bios and roles. Now supports **Merging Duplicates** with AI-consolidated summaries.
- **Customizable Detail**: Choose between short or long AI descriptions to fit your preference.
- **Linked Entries**: Automatically connect related characters and locations through smart cross-referencing.
- **Plot Timeline**: Keeps track of major events chapter by chapter, strictly sorted by physical page location for accuracy.
- **Historical Context**: Pulls real-world info for historical figures and locations.
- **Mention Scanning**: Find every occurrence of a character or location throughout the book, complete with page numbers and context snippets for quick navigation.
- **Spoiler Protection**: "Spoiler-free" mode only reads up to your current page so future twists aren't ruined.
- **Auto Fetching while you read**: Automatically fetches data in the background when you get to a new chapter.
- **X-Ray Mode & Inline Fetching**: Get instant lookups by tapping the "X-Ray" button in dictionary or selection popups. If an entity is missing, the plugin can fetch it on-the-fly using AI without requiring a full book scan.
- **Silent Weekly Updates**: Automatically checks for new plugin versions in the background once a week.
- **Offline First**: You only need internet to fetch the data. After that, it's saved locally.
- **Multilingual**: Available in English, Arabic, Dutch, French, German, Hungarian, Indonesian, Italian, Polish, Brazilian Portuguese, Russian, Serbian, Simplified Chinese, Spanish, Turkish, and Ukrainian.

## Installation

1. Download `xray.koplugin.zip` from the [latest release](https://github.com/cjtqntbmv2-arch/koreader-xray/releases/latest).
2. Extract it into KOReader's `plugins/` folder, so you end up with `plugins/xray.koplugin/`.
3. Restart KOReader.

## Setup

Open a book and pick X-Ray from the reader menu. On first use the plugin walks you through storing an API key for your preferred AI provider (Gemini, OpenAI, DeepSeek, Claude, or a custom OpenAI/Anthropic-compatible endpoint). Keys are stored on-device in `xray_config.lua` and survive plugin updates.

## Credits

Based on [koreader-xray-plugin](https://github.com/ultimatejimmy/koreader-xray-plugin) by Jimmy Pautz (MIT).
```

- [ ] **Step 3: `.agents/rules/release_notes.md`** — Codeblock (Zeilen 19-38) ersetzen durch:

```
### Install

If you are on an older version that doesn't have built-in updates or you haven't tried it yet, get it here: https://github.com/cjtqntbmv2-arch/koreader-xray

Here's the direct link the releases: https://github.com/cjtqntbmv2-arch/koreader-xray/releases

### Feedback

I'm always open to feedback. If you have ideas or issues, you can let me know. GitHub is ideal so things don't get missed:

- [GitHub issue tracker](https://github.com/cjtqntbmv2-arch/koreader-xray/issues)

I will also respond on Reddit or via Reddit chats.
```

(„GitHub ideas"-Discussions-Link und „Support me"-Absatz entfallen.)

- [ ] **Step 4: `/home/jimmy` → `/home/user`** in `run_koreader.bat:9`, `tools/wsl_test.ps1:30`, `tools/wsl_test.ps1:44`, `tools/spec_runner.lua:1` (je exakt der String `/home/jimmy/squashfs-root` → `/home/user/squashfs-root`).

- [ ] **Step 5: `.leann` + `.DS_Store` aus dem Index, `.gitignore` erweitern**

```bash
git rm -r --cached .leann .agents/.DS_Store
```

`.gitignore` um diese Zeilen ergänzen:

```
.leann/
.claude/
.superpowers/
.DS_Store
```

- [ ] **Step 6: Verifizieren**

```bash
git grep -ni 'ultimatejimmy\|jimmy'          # erwartet: NUR LICENSE + README-Credits-Zeile
luajit -bl xray.koplugin/xray_updater.lua > /dev/null
luajit -bl tools/spec_runner.lua > /dev/null
luajit tools/spec_runner.lua                  # erwartet: 197 passed / 11 failed (Baseline)
git checkout -- spec/mocks/                   # Suite-Nebeneffekt (WoT-Mock) zurücksetzen
```

- [ ] **Step 7: Commit (explizite Pfade!)**

```bash
git add xray.koplugin/xray_updater.lua README.md .agents/rules/release_notes.md run_koreader.bat tools/wsl_test.ps1 tools/spec_runner.lua .gitignore
git commit -m "chore: Ablösung vom Original-Repo (Updater → cjtqntbmv2-arch/koreader-xray, README/Links neutralisiert, .leann entfernt)"
```

(Die `git rm --cached`-Löschungen sind bereits gestagt und landen im selben Commit.)

### Task 2: Repo anlegen und pushen

**Interfaces:**
- Consumes: Rebranding-Commit auf `main`.
- Produces: öffentliches Repo `cjtqntbmv2-arch/koreader-xray` mit `main`; Remote `origin`.

- [ ] **Step 1: Repo anlegen + Remote setzen + pushen**

```bash
gh repo create cjtqntbmv2-arch/koreader-xray --public --description "Kindle-style X-Ray for KOReader: AI-generated character bios, plot timeline, glossary, mention scanning, and spoiler protection."
git remote add origin https://github.com/cjtqntbmv2-arch/koreader-xray.git
git push -u origin main
```

Erwartet: Push nur von `main`, keine Tags (kein `--follow-tags`!).

### Task 3: Release 26.7.4 publizieren

**Interfaces:**
- Consumes: Repo aus Task 2; `release.yml` triggert auf jeden Tag-Push und baut `xray.koplugin.zip` als Draft.
- Produces: publiziertes Release `26.7.4` mit Asset `xray.koplugin.zip` — sichtbar für die unauthentifizierte Updater-API.

- [ ] **Step 1: Tag setzen und einzeln pushen**

```bash
git tag -a 26.7.4 -m "26.7.4"
git push origin 26.7.4
```

- [ ] **Step 2: Workflow abwarten**

```bash
gh run list --repo cjtqntbmv2-arch/koreader-xray --limit 1
```

Pollen bis Status `completed`/`success` (Workflow „Release").

- [ ] **Step 3: Draft publizieren**

```bash
gh release edit 26.7.4 --repo cjtqntbmv2-arch/koreader-xray --draft=false
```

### Task 4: Verifikation

- [ ] **Step 1: Exakt die Updater-URL prüfen**

```bash
curl -s https://api.github.com/repos/cjtqntbmv2-arch/koreader-xray/releases/latest | python3.12 -c "import json,sys; d=json.load(sys.stdin); print(d['tag_name'], [a['name'] for a in d.get('assets',[])])"
```

Erwartet: `26.7.4 ['xray.koplugin.zip']`

- [ ] **Step 2: Endzustand prüfen**

```bash
git status --short          # erwartet: nur " M xray.koplugin/xray_config.lua"
git ls-remote --tags origin # erwartet: nur 26.7.4
```
