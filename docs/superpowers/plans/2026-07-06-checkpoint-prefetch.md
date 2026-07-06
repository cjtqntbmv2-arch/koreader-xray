# Checkpoint-Prefetch (Spoilerfreier Offline-Modus) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bei WLAN das ganze Buch in spoiler-gestaffelten Checkpoints vorab analysieren; offline zeigt die UI immer nur den Datenstand des letzten Checkpoints ≤ Leseposition.

**Architecture:** Neues Mixin `xray_prefetch.lua` orchestriert eine sequenzielle Checkpoint-Schleife um das bestehende `continueWithFetch` (providerneutral, erbt Retry/Trunkierungs-Reparatur). Pro Checkpoint wird der Entity-Stand (`characters`, `locations`, `terms`, `historical_figures`) als eigene Sidecar-Datei `xray_snapshot_NN.lua` persistiert; die Timeline bleibt einmalig im Haupt-Cache und wird bei der Anzeige über Seitenanker gefiltert. Eine positionsbasierte Snapshot-Auflösung tauscht die `self.*`-Entity-Listen beim Überschreiten von Checkpoint-Grenzen aus — online wie offline.

**Tech Stack:** Lua 5.1 / LuaJIT, KOReader-Plugin-API (WidgetContainer, UIManager, DocSettings, NetworkMgr), Custom-Spec-Runner (busted-kompatible Syntax).

## Kontext für Implementierer (Codebase-Grundwissen)

- **Ein Plugin-Objekt, viele Mixins:** `main.lua` definiert `XRayPlugin`; jedes `xray_*.lua` liefert eine Methodentabelle, die via `safeRequireMixin()` (main.lua:35-48) auf `XRayPlugin` gemerged wird. Alle Module teilen **ein** `self`. Methodennamen müssen über ALLE Mixin-Dateien eindeutig sein (Kollision = stilles Überschreiben).
- **Alte Geräte:** requires sind `pcall`-gewrappt mit Fallbacks — dieses Muster beibehalten. Kein `lfs` voraussetzen (kann `nil` sein); Datei-Existenz via `io.open` prüfen.
- **Tests:** `luajit tools/spec_runner.lua`. Die Spec-Liste ist in `tools/spec_runner.lua:140-154` **hartkodiert** — neue Spec-Dateien dort registrieren, sonst laufen sie nie. `spec/spec_helper.lua` fakt die KOReader-Umgebung über `package.loaded[...]` und trackt Widgets in `_G.ui_tracker`. Nur die in `tools/spec_runner.lua` implementierten `assert.*`-Matcher verwenden. Ohne `SQUASHFS_ROOT` schlagen ~11 AI-Helper-Specs fehl (`nil generationConfig`) — das ist der bekannte Vorzustand, kein Regressionssignal.
- **Vor jedem Edit die Zieldatei lesen** (mindestens den betroffenen Bereich; Zeilennummern in diesem Plan können leicht driften).
- **Syntax-Check:** `python3 tools/check_syntax.py xray.koplugin` (braucht `pip install luaparser`; falls nicht verfügbar: `luajit -bl <datei> > /dev/null` je geänderter Datei).

## Global Constraints

- Bestehendes Verhalten des Online-/Inkrementalmodus **nicht** verändern; der neue Modus ist additiv und opt-in. Neue Parameter nur optional mit Default = heutiges Verhalten.
- `xray_updater.lua` und `prompts/<lang>.lua` **nicht anfassen**. Gemini-Patches (`responseMimeType`, `context_footer`) nicht rückbauen.
- `xray_config.lua`-Keys unverändert lassen.
- CRLF-Zeilenenden in `xray_aihelper.lua`, `_meta.lua`, `localization_xray.lua` beibehalten; neue Dateien mit LF.
- RAM-Schonung: Schreiben über den vorhandenen Streaming-Serializer (`serializeToFile`); nie mehr als einen Snapshot gleichzeitig laden.
- Nach jeder Änderung an `loc:t("key")`-Nutzungen: `python3 tools/sync_translations.py`, danach `python3 tools/check_translations.py`.
- Ein Git-Commit pro abgeschlossener Phase, Commit-Trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- `cache_version` bleibt `"6.0"`; alle Haupt-Cache-Erweiterungen sind additive Keys (Legacy-Caches bleiben ohne Migration lesbar).

## Entscheidungsprotokoll (D1–D6, mit User abgestimmt)

| # | Entscheidung |
|---|---|
| D1 | Kapitelend-Anker (narrativ via `isNonNarrativeChapter`), Ausdünnen auf ~10, **Verdichten**: jedes Intervall > 15 % Buchlänge (inkl. Anfangs-/Endlücke) prozentisch unterteilen, Hard Cap 12, letzter Checkpoint immer Buchende (percent 100). Kein brauchbares TOC → feste 10-%-Schritte. Kein Setting für Granularität. |
| D2 | Hybrid: nur die 4 Entity-Listen pro Checkpoint snapshotten; Timeline einmalig im Haupt-Cache, Anzeige filtert über `ev.page`. Eine Datei pro Checkpoint (`xray_snapshot_NN.lua`) + Manifest als additive Keys im Haupt-Cache. Fehlende Dateien = Resume-Marker. Keine Migration. |
| D3 | Manueller Menüpunkt (ButtonDialog-Fortschritt „i/k" + Cancel) **und** Auto-Modus als Opt-in-Setting (Default aus), still. Auto-Trigger: `onReaderReady` + `onNetworkConnected`, nur bei unvollständigem Manifest, max. 1 Anlauf pro Buch/Sitzung. `runPostFetchDuplicateCheck` während Prefetch unterdrückt, einmal am Ende. |
| D4 | Snapshot-Auflösung **immer** positionsbasiert (auch online): größter Checkpoint mit `page <= Leseposition`, sonst tolerant der **kleinste vorhandene** Snapshot. Zurückblättern folgt. `full_book`-Setting → Auflösung deaktiviert (Haupt-Cache-Sicht). Write-backs: Displayed-Dataset-Regel — eine Snapshot-Sicht schreibt in ihre Snapshot-Datei, **nie** in den Haupt-Cache. |
| D5 | Mentions-Gate entfällt (Phase 4 gestrichen). |
| D6 | Providerneutral über `continueWithFetch`; Gemini-Caching ist Bonus des separaten Auftrags. |

## Dateiplan

| Datei | Rolle |
|---|---|
| `xray.koplugin/xray_prefetch.lua` (neu) | Checkpoint-Berechnung, Prefetch-Schleife, Snapshot-Auflösung, Write-back-Routing, Auto-Trigger |
| `xray.koplugin/xray_cachemanager.lua` | Snapshot-Persistenz (save/load/exists/delete), Cleanup in `clearCache` |
| `xray.koplugin/xray_fetch.lua` | Optionaler `prefetch_page`-Parameter in `continueWithFetch`; Prefetch-Guard im Dupe-Check; Guards in `fetchFromAI`/`updateFromAI` |
| `xray.koplugin/xray_chapteranalyzer.lua` | Optionaler `end_page`-Parameter in `getDetailedChapterSamples` |
| `xray.koplugin/main.lua` | Mixin-Registrierung, Hooks (`onReaderReady`, `onPageUpdate`, `onNetworkConnected`, `autoLoadCache`), Guard in `triggerBackgroundMergeFetch`, Menüpunkte |
| `xray.koplugin/xray_mentions.lua` | `saveMentionsToCache` → Routing über `persistDisplayedEntities` |
| `xray.koplugin/xray_ui.lua` | Timeline-Render über `visibleTimeline()`, Entity-Write-backs routen |
| `spec/xray_prefetch_spec.lua` (neu) | Specs für Checkpoints, Loop, Auflösung, Routing |
| `spec/xray_cachemanager_spec.lua`, `spec/xray_mentions_spec.lua`, `spec/xray_chapteranalyzer_spec.lua` | Spec-Erweiterungen |
| `tools/spec_runner.lua` | Registrierung `spec/xray_prefetch_spec.lua` |
| `xray.koplugin/languages/en.po`, `de.po` | Neue Strings (via `sync_translations.py`) |

---

# Phase 1 — Cache-Schema

### Task 1: Snapshot-Persistenz im CacheManager

**Files:**
- Modify: `xray.koplugin/xray_cachemanager.lua` (Methoden nach `clearCache`, ~Z. 453 anfügen; `clearCache` erweitern)
- Test: `spec/xray_cachemanager_spec.lua` (neuer `describe`-Block; Datei ist bereits im Runner registriert)

**Interfaces:**
- Consumes: vorhandene `CacheManager:serializeToFile(f, obj, indent)`, `DocSettings:getSidecarDir(book_path)`, `CacheManager:ensureDirectory(path)`.
- Produces (von Task 5/6 genutzt):
  - `CacheManager:getSnapshotPath(book_path, index) -> string|nil`
  - `CacheManager:saveSnapshot(book_path, index, data) -> boolean` (stempelt `data.snapshot_version = 1`, `data.created_at`)
  - `CacheManager:loadSnapshot(book_path, index) -> table|nil` (nil bei fehlender Datei/Versions-Mismatch)
  - `CacheManager:snapshotExists(book_path, index) -> boolean`
  - `CacheManager:deleteSnapshots(book_path)` (Indizes 1–24)

- [ ] **Step 1: Bestehende Spec + Helper lesen**

`spec/xray_cachemanager_spec.lua` (komplett) und `spec/spec_helper.lua` lesen — insbesondere wie der existierende Test „saves and loads data correctly" Sidecar-Pfade/Dateisystem im Fake nutzt. Das Setup (temp-Verzeichnis, `book_path`) für die neuen Tests **exakt spiegeln**.

- [ ] **Step 2: Failing Tests schreiben**

Neuen Block in `spec/xray_cachemanager_spec.lua` (Setup-Zeilen an den Nachbar-Test angleichen):

```lua
describe("Snapshot persistence", function()
    local book_path = "/tmp/xray_spec_book.epub" -- an Nachbar-Test angleichen!

    it("builds zero-padded snapshot paths", function()
        local cm = CacheManager:new()
        local path = cm:getSnapshotPath(book_path, 3)
        assert.truthy(path:match("xray_snapshot_03%.lua$"))
        assert.is_nil(cm:getSnapshotPath(nil, 3))
        assert.is_nil(cm:getSnapshotPath(book_path, nil))
    end)

    it("round-trips a snapshot and stamps version", function()
        local cm = CacheManager:new()
        local ok = cm:saveSnapshot(book_path, 2, {
            page = 120, percent = 20,
            characters = { { name = "Rand", description = "A shepherd" } },
            locations = {}, terms = {}, historical_figures = {},
        })
        assert.is_true(ok)
        assert.is_true(cm:snapshotExists(book_path, 2))
        local loaded = cm:loadSnapshot(book_path, 2)
        assert.truthy(loaded)
        assert.equals(1, loaded.snapshot_version)
        assert.equals(120, loaded.page)
        assert.equals("Rand", loaded.characters[1].name)
    end)

    it("returns nil for missing or version-mismatched snapshots", function()
        local cm = CacheManager:new()
        assert.is_nil(cm:loadSnapshot(book_path, 9))
        assert.is_false(cm:snapshotExists(book_path, 9))
        -- Versions-Mismatch: Datei mit fremder Version schreiben
        local path = cm:getSnapshotPath(book_path, 9)
        local f = io.open(path, "w")
        f:write("return { snapshot_version = 99 }\n")
        f:close()
        assert.is_nil(cm:loadSnapshot(book_path, 9))
        os.remove(path)
    end)

    it("deleteSnapshots removes files and clearCache includes them", function()
        local cm = CacheManager:new()
        cm:saveSnapshot(book_path, 1, { page = 10, characters = {} })
        cm:saveSnapshot(book_path, 4, { page = 40, characters = {} })
        cm:deleteSnapshots(book_path)
        assert.is_false(cm:snapshotExists(book_path, 1))
        assert.is_false(cm:snapshotExists(book_path, 4))

        cm:saveSnapshot(book_path, 1, { page = 10, characters = {} })
        cm:saveCache(book_path, { characters = {} })
        cm:clearCache(book_path)
        assert.is_false(cm:snapshotExists(book_path, 1))
    end)
end)
```

- [ ] **Step 3: Tests laufen lassen — Fehlschlag verifizieren**

Run: `luajit tools/spec_runner.lua 2>&1 | tail -20`
Expected: neue Tests FAIL (`attempt to call method 'getSnapshotPath' (a nil value)`); Rest unverändert.

- [ ] **Step 4: Implementierung in `xray_cachemanager.lua`**

Nach `clearCache` (vor `return CacheManager`) anfügen; zusätzlich in `clearCache` vor dem `return` den Aufruf `self:deleteSnapshots(book_path)` ergänzen (Snapshots gehören zum Buch-Cache):

```lua
-- ── Checkpoint-Snapshots (Offline-Prefetch) ────────────────────────────────
-- Eine Datei pro Checkpoint im Sidecar; fehlende Dateien = noch offene
-- Checkpoints (Resume-Marker). Nur die 4 Entity-Listen, keine Timeline (D2).
local SNAPSHOT_VERSION = 1

function CacheManager:getSnapshotPath(book_path, index)
    if not book_path or not index then return nil end
    local dir = DocSettings:getSidecarDir(book_path)
    if not dir then return nil end
    return string.format("%s/xray_snapshot_%02d.lua", dir, index)
end

function CacheManager:saveSnapshot(book_path, index, data)
    local path = self:getSnapshotPath(book_path, index)
    if not path or not data then return false end
    if not self:ensureDirectory(path) then return false end
    data.snapshot_version = SNAPSHOT_VERSION
    data.created_at = os.time()
    local ok = pcall(function()
        local f = assert(io.open(path, "w"))
        f:write("-- X-Ray Snapshot v" .. SNAPSHOT_VERSION .. "\nreturn ")
        self:serializeToFile(f, data, "")
        f:write("\n")
        f:close()
    end)
    if not ok then
        logger.warn("CacheManager: Failed to save snapshot:", path)
        return false
    end
    return true
end

function CacheManager:loadSnapshot(book_path, index)
    local path = self:getSnapshotPath(book_path, index)
    if not path then return nil end
    local probe = io.open(path, "r")
    if not probe then return nil end
    probe:close()
    local ok, data = pcall(dofile, path)
    if not ok or type(data) ~= "table" or data.snapshot_version ~= SNAPSHOT_VERSION then
        logger.warn("CacheManager: Ignoring unreadable/mismatched snapshot:", path)
        return nil
    end
    return data
end

function CacheManager:snapshotExists(book_path, index)
    local path = self:getSnapshotPath(book_path, index)
    if not path then return false end
    local f = io.open(path, "r")
    if f then f:close(); return true end
    return false
end

function CacheManager:deleteSnapshots(book_path)
    -- ponytail: fester Index-Sweep 1..24 statt Verzeichnis-Listing — lfs kann
    -- auf Altgeräten fehlen; 24 liegt großzügig über dem Hard Cap von 12.
    for i = 1, 24 do
        local path = self:getSnapshotPath(book_path, i)
        if path then os.remove(path) end
    end
end
```

- [ ] **Step 5: Tests laufen lassen — grün**

Run: `luajit tools/spec_runner.lua 2>&1 | tail -20`
Expected: neue Tests PASS; Gesamtbild wie Vorzustand (nur die bekannten AI-Helper-Fails ohne SQUASHFS_ROOT).

- [ ] **Step 6: Syntax-Check + Commit (Phase 1)**

```bash
python3 tools/check_syntax.py xray.koplugin
git add xray.koplugin/xray_cachemanager.lua spec/xray_cachemanager_spec.lua docs/superpowers/plans/2026-07-06-checkpoint-prefetch.md
git commit -m "Phase 1: Snapshot-Persistenz im CacheManager (Checkpoint-Prefetch)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

# Phase 2 — Prefetch-Schleife

### Task 2: `getDetailedChapterSamples` bekommt optionalen `end_page`-Parameter

**Files:**
- Modify: `xray.koplugin/xray_chapteranalyzer.lua:613` ff. (Signatur + interne `current_page`-Ableitung ~Z. 626-630)
- Test: `spec/xray_chapteranalyzer_spec.lua` (erweitern)

**Interfaces:**
- Produces: `ChapterAnalyzer:getDetailedChapterSamples(ui, max_chapters, total_limit, is_full_book, start_page, known_chapters, end_page)` — `end_page` optional; `nil` ⇒ exakt heutiges Verhalten (Ableitung aus `ui`).

- [ ] **Step 1: Funktion lesen**

`xray_chapteranalyzer.lua:613-700` lesen: wie `current_page` intern abgeleitet und wofür es genutzt wird (Sampling-Obergrenze).

- [ ] **Step 2: Failing Test schreiben**

Bestehende Analyzer-Specs lesen und deren `ui`-Fake-Muster übernehmen. Testidee (an vorhandene Fakes anpassen):

```lua
it("caps sampling at end_page when given", function()
    -- ui-Fake mit getToc/paging wie in den Nachbar-Tests dieses Spec-Files
    local samples_default = analyzer:getDetailedChapterSamples(ui, 200, 150000, false, nil, nil)
    local samples_capped  = analyzer:getDetailedChapterSamples(ui, 200, 150000, false, nil, nil, 5)
    -- Erwartung: mit end_page=5 tauchen keine Kapitel-Samples > Seite 5 auf
    assert.truthy(samples_capped == nil or not tostring(samples_capped):find(TEXT_BEYOND_PAGE_5))
end)
```

Die konkrete Assertion an das reale Rückgabeformat anpassen (beim Lesen in Step 1 ermitteln); entscheidend ist ein Fall, der mit `end_page` ein anderes (kleineres) Ergebnis liefert als ohne.

- [ ] **Step 3: Test rot verifizieren** — `luajit tools/spec_runner.lua 2>&1 | tail -20`

- [ ] **Step 4: Implementierung (minimal-invasiv)**

```lua
function ChapterAnalyzer:getDetailedChapterSamples(ui, max_chapters, total_limit, is_full_book, start_page, known_chapters, end_page)
```

und an der internen Ableitung (~Z. 626-630):

```lua
    local current_page = end_page  -- Prefetch: Analysegrenze = Checkpoint-Seite
    if not current_page then
        -- (bisheriger Ableitungscode unverändert hierher)
    end
```

Keine weiteren Logikänderungen — alles stromabwärts nutzt `current_page` wie bisher.

- [ ] **Step 5: Test grün verifizieren** — `luajit tools/spec_runner.lua 2>&1 | tail -20`

### Task 3: Fetch-Erweiterungen — `prefetch_page` + Dupe-Check-Guard

**Files:**
- Modify: `xray.koplugin/xray_fetch.lua:229` (Signatur), `:288` (current_page), `:336` (Samples-Aufruf), `:832-834` (Guard)
- Test: `spec/xray_fetch_spec.lua` (Smoke-Test für Guard)

**Interfaces:**
- Produces: `M:continueWithFetch(reading_percent, is_update, last_fetch_page, is_silent, prefetch_page)` — `prefetch_page` optional; wenn gesetzt, ersetzt es `self.ui:getCurrentPage()` als Analyse-Anker (Textgrenze, Samples-Grenze, `last_fetch_page`-Bookkeeping, `history`-Stempel folgen automatisch).
- Produces: `runPostFetchDuplicateCheck` kehrt sofort zurück, wenn `self.prefetch_active` wahr ist.
- Consumes: Task 2 (`end_page`-Parameter).

- [ ] **Step 1: Betroffene Bereiche lesen** — `xray_fetch.lua:225-340` und `:830-840`.

- [ ] **Step 2: Failing Smoke-Test für den Guard schreiben**

In `spec/xray_fetch_spec.lua` (Muster des Files übernehmen — es testet Methoden auf einem Plugin-Fake-Table):

```lua
describe("runPostFetchDuplicateCheck prefetch guard", function()
    it("returns immediately while prefetch is active", function()
        local called = false
        local plugin = {
            prefetch_active = true,
            ai_helper = {
                hasApiKey = function() return true end,
                settings = {},
                findDuplicatesAsync = function() called = true end,
            },
        }
        fetch.runPostFetchDuplicateCheck(plugin, "T", "A", 50, true)
        assert.is_false(called)
    end)
end)
```

(`fetch` = das require-Ergebnis des Moduls, wie im File üblich.)

- [ ] **Step 3: Test rot verifizieren.**

- [ ] **Step 4: Implementierung**

Signatur `:229`:

```lua
function M:continueWithFetch(reading_percent, is_update, last_fetch_page, is_silent, prefetch_page)
```

`:288`:

```lua
        local current_page = prefetch_page or self.ui:getCurrentPage()
```

Samples-Aufruf `:336` (7. Argument anhängen; `prefetch_page or nil` hält den Online-Pfad exakt beim alten Verhalten):

```lua
            local samples, chapter_titles = self.chapter_analyzer:getDetailedChapterSamples(self.ui, 200, 150000, reading_percent == 100, first_missing_page, known_chapters, prefetch_page)
```

Guard als **erste** Zeile in `runPostFetchDuplicateCheck` (`:832`):

```lua
    if self.prefetch_active then return end -- Prefetch: Dupe-Check läuft einmal am Ende (D3)
```

- [ ] **Step 5: Test grün + Vollsuite** — `luajit tools/spec_runner.lua 2>&1 | tail -20` (kein neuer Fail).

### Task 4: Neues Mixin `xray_prefetch.lua` — Checkpoint-Berechnung

**Files:**
- Create: `xray.koplugin/xray_prefetch.lua`
- Modify: `xray.koplugin/main.lua:47` (Mixin-Registrierung), `tools/spec_runner.lua:154` (Spec registrieren)
- Test: Create `spec/xray_prefetch_spec.lua`

**Interfaces:**
- Consumes: `self.ui.document:getToc()/:getPageCount()`, `self:isNonNarrativeChapter(title)` (existiert in `xray_fetch.lua`-Mixin).
- Produces: `M:computeCheckpoints() -> { {page=..., percent=...}, ... }|nil` — aufsteigend, letzter Eintrag `{page = page_count, percent = 100}`, max. 12 Einträge.

- [ ] **Step 1: Failing Tests schreiben (`spec/xray_prefetch_spec.lua`)**

```lua
require("spec.spec_helper")

local prefetch = require("xray.koplugin.xray_prefetch") -- Require-Pfad an Nachbar-Specs angleichen!

-- Minimaler Plugin-Fake: nur was computeCheckpoints braucht
local function makePlugin(toc, page_count)
    local plugin = {
        ui = { document = {
            getToc = function() return toc end,
            getPageCount = function() return page_count end,
        } },
        isNonNarrativeChapter = function(_, title)
            return title and title:lower():match("^copyright") ~= nil
        end,
        log = function() end,
    }
    for k, v in pairs(prefetch) do plugin[k] = v end
    return plugin
end

local function toc_entry(page, title) return { page = page, title = title or ("Ch " .. page) } end

describe("xray_prefetch", function()
    describe("computeCheckpoints", function()
        it("uses chapter end pages and always ends at 100%", function()
            -- 5 Kapitel à 100 Seiten, Buch 500 Seiten
            local toc = {}
            for i = 0, 4 do table.insert(toc, toc_entry(i * 100 + 1)) end
            local p = makePlugin(toc, 500)
            local cps = p:computeCheckpoints()
            assert.truthy(cps)
            assert.equals(500, cps[#cps].page)
            assert.equals(100, cps[#cps].percent)
            -- Kapitelenden (100,200,300,400) enthalten
            local pages = {}
            for _, cp in ipairs(cps) do pages[cp.page] = true end
            assert.is_true(pages[100] and pages[200] and pages[300] and pages[400])
        end)

        it("thins dense TOCs to at most 12 checkpoints", function()
            local toc = {}
            for i = 1, 60 do table.insert(toc, toc_entry(i * 10)) end
            local p = makePlugin(toc, 600)
            local cps = p:computeCheckpoints()
            assert.is_true(#cps <= 12)
            assert.equals(600, cps[#cps].page)
        end)

        it("densifies sparse TOCs so no interval exceeds 15%", function()
            -- 3 Kapitel: Enden bei 33% / 66% / 100% von 300 Seiten
            local toc = { toc_entry(1), toc_entry(101), toc_entry(201) }
            local p = makePlugin(toc, 300)
            local cps = p:computeCheckpoints()
            local prev = 0
            for _, cp in ipairs(cps) do
                assert.is_true(cp.page - prev <= math.floor(300 * 15 / 100) + 1)
                prev = cp.page
            end
            assert.equals(300, cps[#cps].page)
        end)

        it("densifies the leading gap before a late first chapter", function()
            -- erstes Kapitelende erst bei 40%
            local toc = { toc_entry(1), toc_entry(121), toc_entry(281) }
            local p = makePlugin(toc, 300)
            local cps = p:computeCheckpoints()
            assert.is_true(cps[1].page <= math.floor(300 * 15 / 100) + 1)
        end)

        it("falls back to 10% steps without a usable TOC", function()
            local p = makePlugin({}, 400)
            local cps = p:computeCheckpoints()
            assert.equals(10, #cps)
            assert.equals(40, cps[1].page)
            assert.equals(400, cps[#cps].page)
        end)

        it("skips non-narrative chapters as anchors", function()
            local toc = { toc_entry(1), { page = 150, title = "Copyright" }, toc_entry(200) }
            local p = makePlugin(toc, 400)
            local cps = p:computeCheckpoints()
            for _, cp in ipairs(cps) do
                assert.is_true(cp.page ~= 149) -- Ende des Copyright-"Kapitels" ist kein Anker
            end
        end)

        it("returns nil without a document", function()
            local p = makePlugin({}, 400)
            p.ui = nil
            assert.is_nil(p:computeCheckpoints())
        end)
    end)
end)
```

Registrierung in `tools/spec_runner.lua` (Liste ~Z. 154 ergänzen):

```lua
    "spec/xray_prefetch_spec.lua",
```

- [ ] **Step 2: Tests rot verifizieren** (Modul existiert noch nicht → Require-Fehler ist ok).

- [ ] **Step 3: Implementierung `xray.koplugin/xray_prefetch.lua` (neu, LF-Zeilenenden)**

```lua
-- Offline-Prefetch: spoiler-gestaffelte Checkpoint-Snapshots (D1-D6, siehe
-- docs/superpowers/plans/2026-07-06-checkpoint-prefetch.md)
local logger = require("logger")

local M = {}

local MAX_CHECKPOINTS = 10   -- D1: Ziel-Anzahl
local HARD_CAP = 12          -- D1-Nachtrag: absolute Obergrenze
local MAX_INTERVAL_PCT = 15  -- D1-Nachtrag: max. Intervallbreite in % Buchlänge

-- Liste aufsteigender Seitenzahlen gleichmäßig auf target Einträge ausdünnen,
-- letzter Eintrag bleibt immer erhalten.
local function thinTo(pages, target)
    if #pages <= target then return pages end
    local out = {}
    local step = #pages / target
    for i = 1, target do
        out[i] = pages[math.floor(i * step + 0.5)]
    end
    out[target] = pages[#pages]
    local deduped = {}
    for _, p in ipairs(out) do
        if deduped[#deduped] ~= p then table.insert(deduped, p) end
    end
    return deduped
end

function M:computeCheckpoints()
    local doc = self.ui and self.ui.document
    if not doc or not doc.getPageCount then return nil end
    local page_count = doc:getPageCount()
    if not page_count or page_count < 1 then return nil end

    -- 1. Anker: Endseiten narrativer Kapitel
    local toc = (doc.getToc and doc:getToc()) or {}
    local narrative = {}
    for _, entry in ipairs(toc) do
        if entry.page and entry.page >= 1 and entry.page <= page_count
            and not self:isNonNarrativeChapter(entry.title) then
            table.insert(narrative, entry)
        end
    end
    table.sort(narrative, function(a, b) return a.page < b.page end)

    local pages = {}
    for i = 1, #narrative do
        local next_start = narrative[i + 1] and narrative[i + 1].page
        local end_page = next_start and (next_start - 1) or page_count
        if end_page >= 1 and pages[#pages] ~= end_page then
            table.insert(pages, end_page)
        end
    end
    if pages[#pages] ~= page_count then table.insert(pages, page_count) end

    if #pages < 2 then
        -- Fallback (D1): kein brauchbares TOC -> feste 10%-Schritte
        pages = {}
        for pct = 10, 100, 10 do
            local p = math.max(1, math.floor(page_count * pct / 100))
            if pages[#pages] ~= p then table.insert(pages, p) end
        end
        pages[#pages] = page_count
    else
        -- 2. Ausdünnen auf Ziel-Anzahl
        pages = thinTo(pages, MAX_CHECKPOINTS)
        -- 3. Verdichten: kein Intervall > MAX_INTERVAL_PCT (inkl. Anfangslücke)
        local max_gap = math.max(1, math.floor(page_count * MAX_INTERVAL_PCT / 100))
        local densified = {}
        local prev = 0
        for _, p in ipairs(pages) do
            local gap = p - prev
            if gap > max_gap then
                local parts = math.ceil(gap / max_gap)
                for j = 1, parts - 1 do
                    local mid = prev + math.floor(gap * j / parts)
                    if mid > (densified[#densified] or 0) and mid < p then
                        table.insert(densified, mid)
                    end
                end
            end
            table.insert(densified, p)
            prev = p
        end
        -- 4. Hard Cap (kann Intervalle wieder leicht aufweiten -- Cap gewinnt)
        pages = thinTo(densified, HARD_CAP)
    end

    local checkpoints = {}
    for i, p in ipairs(pages) do
        local percent = math.floor(p / page_count * 100)
        if i == #pages then percent = 100 end
        table.insert(checkpoints, { page = p, percent = percent })
    end
    return checkpoints
end

return M
```

Registrierung in `main.lua` nach `safeRequireMixin("xray_fetch")` (~Z. 46):

```lua
safeRequireMixin("xray_prefetch")
```

- [ ] **Step 4: Tests grün verifizieren** — `luajit tools/spec_runner.lua 2>&1 | tail -20`

### Task 5: Prefetch-Schleife (Loop, Watcher, Dialog, Guards)

**Files:**
- Modify: `xray.koplugin/xray_prefetch.lua` (Loop-Methoden anfügen)
- Test: `spec/xray_prefetch_spec.lua` (Loop-Block)

**Interfaces:**
- Consumes: Task 1 (`saveSnapshot`, `snapshotExists`), Task 3 (`continueWithFetch(..., prefetch_page)`), `self.bg_fetch_active`, `self.book_data.last_fetch_page` (wird von `continueWithFetch` bei Erfolg auf die Checkpoint-Seite gesetzt, xray_fetch.lua:780), `UIManager:scheduleIn`, `NetworkMgr`, `self.ai_helper:hasApiKey()`.
- Produces (für Task 6/9):
  - `M:startOfflinePrefetch(is_silent)` — Einstieg (Menü + Auto)
  - `M:isPrefetchComplete() -> boolean`
  - `self.prefetch_active` (Flag, von Task 3-Guard konsumiert)
  - Manifest: `self.book_data.prefetch = { checkpoints = {...}, completed = bool, created_at = ts }`
- **Erfolgskriterium pro Checkpoint (Polling statt Callback):** Der Loop pollt `self.bg_fetch_active`; Call gilt als erfolgreich, wenn danach `self.book_data.last_fetch_page >= checkpoint.page`. Dadurch sind **keine** Änderungen an den 13 Exit-Pfaden von `continueWithFetch` nötig.

- [ ] **Step 1: `spec/spec_helper.lua` lesen** — Verhalten des `UIManager.scheduleIn`-Fakes (Z. ~78) klären: führt er Callbacks sofort aus oder sammelt er sie? Danach richtet sich, wie die Loop-Tests ticken: Bei Sofort-Ausführung Rekursionsschutz im Test beachten; bei Sammlung die Queue manuell abarbeiten. Falls keins von beidem testbar ist: Watcher-Kern als `M:_prefetchTick()` extrahieren und im Test direkt aufrufen (der `scheduleIn`-Wrapper bleibt untestbar-dünn).

- [ ] **Step 2: Failing Tests schreiben** (an Step-1-Erkenntnisse angepasst; Kern-Szenarien):

```lua
describe("prefetch loop", function()
    -- Plugin-Fake: computeCheckpoints-Fake, cache_manager-Fake (records saves),
    -- continueWithFetch-Stub, der Erfolg simuliert:
    --   plugin.continueWithFetch = function(self, pct, is_update, lfp, silent, page)
    --       self.bg_fetch_active = false
    --       self.book_data.last_fetch_page = page
    --       table.insert(calls, { pct = pct, page = page, is_update = is_update })
    --   end

    it("runs all checkpoints in order and marks manifest completed", function()
        -- 3 Checkpoints (100/200/300 von 300) -> 3 continueWithFetch-Aufrufe,
        -- 3 saveSnapshot-Aufrufe mit Index 1..3, manifest.completed == true,
        -- letzter Aufruf mit pct == 100
    end)

    it("skips checkpoints already covered by last_fetch_page", function()
        -- book_data.last_fetch_page = 150 -> Checkpoint 1 (Seite 100) wird
        -- übersprungen (kein Snapshot 1), Loop beginnt bei Checkpoint 2
    end)

    it("resumes at first missing snapshot", function()
        -- snapshotExists(1) == true -> Loop startet bei Index 2
    end)

    it("stops on failure and leaves manifest incomplete", function()
        -- Stub setzt last_fetch_page NICHT -> _finishPrefetch(false),
        -- manifest.completed bleibt falsy, prefetch_active == false
    end)

    it("respects cancellation between checkpoints", function()
        -- Nach Checkpoint 1: plugin.prefetch_cancelled = true -> kein 2. Call
    end)

    it("routes full_book users to a normal fetch", function()
        -- spoiler_setting == "full_book" -> fetchFromAI-Stub aufgerufen,
        -- kein Loop gestartet
    end)

    it("runs the duplicate check once at the end", function()
        -- runPostFetchDuplicateCheck-Stub: 0 Aufrufe während des Loops,
        -- 1 Aufruf nach Abschluss (mit reading_percent = 100)
    end)
end)
```

Die Kommentare sind die Assertions — als echte busted-Tests ausformulieren (Stubs zählen Aufrufe in Locals).

- [ ] **Step 3: Tests rot verifizieren.**

- [ ] **Step 4: Implementierung (an `xray_prefetch.lua` anfügen)**

```lua
local PREFETCH_POLL_SECONDS = 1
local PREFETCH_MAX_TICKS = 600 -- ponytail: 10 min Timeout pro Checkpoint, dann Abbruch mit Resume

function M:isPrefetchComplete()
    local manifest = self.book_data and self.book_data.prefetch
    return (manifest and manifest.completed) == true
end

function M:startOfflinePrefetch(is_silent)
    if self.prefetch_active then return end
    if self.bg_fetch_active or self.bg_fetch_pending then
        if not is_silent then self:showPrefetchInfo(self.loc:t("prefetch_busy") or "A fetch is already running. Try again in a moment.") end
        return
    end
    if not self.ui or not self.ui.document then return end

    local spoiler_setting = self.ai_helper and self.ai_helper.settings and self.ai_helper.settings.spoiler_setting or "spoiler_free"
    if spoiler_setting == "full_book" then
        -- D4: full_book braucht keine Checkpoints -- ein normaler Voll-Fetch genügt
        self:fetchFromAI()
        return
    end

    local NetworkMgr = require("ui/network/manager")
    if not (NetworkMgr:isConnected() and NetworkMgr:isOnline()) then
        if not is_silent then self:showPrefetchInfo(self.loc:t("prefetch_offline") or "No internet connection.") end
        return
    end
    if not (self.ai_helper and self.ai_helper.hasApiKey and self.ai_helper:hasApiKey()) then
        if not is_silent then self:showPrefetchInfo(self.loc:t("prefetch_no_key") or "No API key configured.") end
        return
    end

    if not self.cache_manager then
        self.cache_manager = require(((...) or ""):match("(.-)[^%.]+$") .. "xray_cachemanager"):new()
    end
    self.book_data = self.book_data or self.cache_manager:loadCache(self.ui.document.file) or {}

    local manifest = self.book_data.prefetch
    if not manifest or not manifest.checkpoints or #manifest.checkpoints == 0 then
        local checkpoints = self:computeCheckpoints()
        if not checkpoints then
            if not is_silent then self:showPrefetchInfo(self.loc:t("prefetch_failed") or "Could not analyze book structure.") end
            return
        end
        manifest = { checkpoints = checkpoints, created_at = os.time() }
        self.book_data.prefetch = manifest
        self.cache_manager:asyncSaveCache(self.ui.document.file, self.book_data)
    end
    if manifest.completed then
        if not is_silent then self:showPrefetchInfo(self.loc:t("prefetch_already_done") or "This book is already prepared for offline reading.") end
        return
    end

    self.prefetch_active = true
    self.prefetch_cancelled = false
    self.prefetch_silent = is_silent and true or false
    self:log("XRayPlugin: Offline prefetch started (" .. tostring(#manifest.checkpoints) .. " checkpoints)")
    self:_prefetchNext()
end

-- Ersten offenen Checkpoint finden: kein Snapshot vorhanden UND Seite über dem
-- bereits gefetchten Datenstand (bereits gelesene/gefetchte Abschnitte lassen
-- sich nicht mehr kontext-leckfrei snapshotten, D4-Toleranzregel deckt sie ab).
function M:_nextPendingCheckpoint()
    local manifest = self.book_data and self.book_data.prefetch
    if not manifest then return nil end
    local covered = self.book_data.last_fetch_page or 0
    for i, cp in ipairs(manifest.checkpoints) do
        if not self.cache_manager:snapshotExists(self.ui.document.file, i) and cp.page > covered then
            return i, cp
        end
    end
    return nil
end

function M:_prefetchNext()
    if self.destroyed or not self.ui or not self.ui.document then
        self.prefetch_active = false
        return
    end
    if self.prefetch_cancelled then
        self:_finishPrefetch(false)
        return
    end
    local idx, cp = self:_nextPendingCheckpoint()
    if not idx then
        self:_finishPrefetch(true)
        return
    end

    local manifest = self.book_data.prefetch
    self:_showPrefetchProgress(idx, #manifest.checkpoints)

    local reading_percent = cp.percent
    local is_update = self.timeline and #self.timeline > 0 or false
    local last_fetch_page = self.book_data.last_fetch_page
    self:log(string.format("XRayPlugin: Prefetch checkpoint %d/%d (page %d, %d%%)", idx, #manifest.checkpoints, cp.page, reading_percent))
    self:continueWithFetch(reading_percent, is_update, last_fetch_page, true, cp.page)
    self:_watchPrefetchStep(idx, cp, 0)
end

function M:_watchPrefetchStep(idx, cp, ticks)
    local ok_ui, UIManager = pcall(require, "ui/uimanager")
    if not ok_ui then self:_finishPrefetch(false); return end
    UIManager:scheduleIn(PREFETCH_POLL_SECONDS, function()
        if self.destroyed then return end
        if self.bg_fetch_active and not self.prefetch_cancelled then
            if ticks >= PREFETCH_MAX_TICKS then
                self:log("XRayPlugin: Prefetch checkpoint " .. idx .. " timed out")
                self:_finishPrefetch(false)
                return
            end
            self:_watchPrefetchStep(idx, cp, ticks + 1)
            return
        end
        -- Call beendet: Erfolg <=> Datenstand hat die Checkpoint-Seite erreicht
        if (self.book_data and self.book_data.last_fetch_page or 0) >= cp.page then
            local snap = {
                checkpoint_index = idx,
                page = cp.page,
                percent = cp.percent,
                characters = self.characters or {},
                locations = self.locations or {},
                terms = self.terms or {},
                historical_figures = self.historical_figures or {},
            }
            self.cache_manager:saveSnapshot(self.ui.document.file, idx, snap)
            self:_prefetchNext()
        else
            self:_finishPrefetch(false)
        end
    end)
end

function M:_finishPrefetch(success)
    self.prefetch_active = false
    self:_closePrefetchProgress()

    local manifest = self.book_data and self.book_data.prefetch
    if manifest then
        local all_done = self:_nextPendingCheckpoint() == nil
        manifest.completed = all_done and true or nil
        self.cache_manager:asyncSaveCache(self.ui.document.file, self.book_data)

        if all_done then
            -- Dupe-Check lief während des Prefetch nicht (Guard) -- einmal am Ende
            local props = self.ui and self.ui.document and self.ui.document:getProps() or {}
            self:runPostFetchDuplicateCheck(props.title or "", props.authors or "", 100, true)
        end
    end

    if not self.prefetch_silent then
        local done, total = 0, 0
        if manifest then
            total = #manifest.checkpoints
            for i = 1, total do
                if self.cache_manager:snapshotExists(self.ui.document.file, i) then done = done + 1 end
            end
        end
        local msg
        if manifest and manifest.completed then
            msg = self.loc:t("prefetch_done") or "Book is ready for offline reading."
        elseif self.prefetch_cancelled then
            msg = string.format(self.loc:t("prefetch_cancelled") or "Cancelled. %d of %d checkpoints kept - will resume next time.", done, total)
        else
            msg = string.format(self.loc:t("prefetch_partial") or "Interrupted. %d of %d checkpoints done - will resume next time.", done, total)
        end
        self:showPrefetchInfo(msg)
    end
    -- Anzeige sofort auf die neue Snapshot-Lage bringen (Task 6)
    if self.updateSnapshotViewForPage and self.ui and self.ui.getCurrentPage then
        self:updateSnapshotViewForPage(self.ui:getCurrentPage())
    end
end

-- ── UI-Hilfen (manueller Modus) ────────────────────────────────────────────
function M:showPrefetchInfo(text)
    local ok, InfoMessage = pcall(require, "ui/widget/infomessage")
    local ok_ui, UIManager = pcall(require, "ui/uimanager")
    if ok and ok_ui then UIManager:show(InfoMessage:new{ text = text, timeout = 4 }) end
end

function M:_showPrefetchProgress(idx, total)
    if self.prefetch_silent then return end
    self:_closePrefetchProgress()
    local ok_bd, ButtonDialog = pcall(require, "ui/widget/buttondialog")
    local ok_ui, UIManager = pcall(require, "ui/uimanager")
    if not (ok_bd and ok_ui) then return end
    local tmpl = self.loc:t("prefetch_progress") or "Preparing for offline reading - checkpoint %d of %d"
    self.prefetch_dialog = ButtonDialog:new{
        title = string.format(tmpl, idx, total),
        text = self.loc:t("prefetch_progress_hint") or "You can keep reading. Cancel stops after the current checkpoint.",
        buttons = {{{
            text = self.loc:t("cancel") or "Cancel",
            callback = function()
                self.prefetch_cancelled = true
                self:log("XRayPlugin: Prefetch cancelled by user")
            end,
        }}},
    }
    UIManager:show(self.prefetch_dialog)
end

function M:_closePrefetchProgress()
    if self.prefetch_dialog then
        local ok_ui, UIManager = pcall(require, "ui/uimanager")
        if ok_ui then UIManager:close(self.prefetch_dialog) end
        self.prefetch_dialog = nil
    end
end
```

Hinweis: der `require`-Pfad für den CacheManager in `startOfflinePrefetch` muss dem Muster der anderen Mixins folgen (`plugin_path`-Konstante wie in `xray_fetch.lua:1` ff. — beim Lesen übernehmen; ggf. oben im Modul `local plugin_path = ((...) or ""):match("(.-)[^%.]+$") or ""` definieren und `plugin_path .. "xray_cachemanager"` verwenden).

- [ ] **Step 5: Tests grün + Vollsuite + Syntax-Check.**

- [ ] **Step 6: Commit (Phase 2)**

```bash
git add xray.koplugin/xray_prefetch.lua xray.koplugin/xray_fetch.lua xray.koplugin/xray_chapteranalyzer.lua xray.koplugin/main.lua spec/ tools/spec_runner.lua
git commit -m "Phase 2: Checkpoint-Prefetch-Schleife (Loop, Resume, Guards)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

# Phase 3 — Anzeigefilter (Snapshot-Auflösung)

### Task 6: Positionsbasierte Snapshot-Auflösung

**Files:**
- Modify: `xray.koplugin/xray_prefetch.lua` (Auflösungs-Methoden), `xray.koplugin/main.lua` (`autoLoadCache`-Ende ~Z. 696, `onPageUpdate` ~Z. 365-379, `mergeSeriesContext`-Aufrufstelle)
- Test: `spec/xray_prefetch_spec.lua`

**Interfaces:**
- Produces:
  - `M:resolveSnapshotIndexForPage(page) -> index|nil` — größter existierender Snapshot mit `page <= Position`, sonst kleinster existierender (D4 tolerant), sonst `nil`.
  - `M:applySnapshot(index|nil)` — tauscht die 4 `self.*`-Entity-Listen (Snapshot bzw. zurück auf `self.book_data.*`); setzt `self.active_snapshot_index`, `self.active_snapshot_page`.
  - `M:updateSnapshotViewForPage(page)` — billiger Guard + Grenzübertritts-Check; `full_book` ⇒ Haupt-Cache-Sicht.
- Consumes: Task 1 (`loadSnapshot`, `snapshotExists`), Manifest aus Task 5.

- [ ] **Step 1: Failing Tests schreiben**

```lua
describe("snapshot resolution", function()
    -- Fake: manifest mit Checkpoints {page=100},{page=200},{page=300};
    -- cache_manager-Fake mit konfigurierbarem exists/load

    it("picks the largest snapshot at or below the position", function()
        -- Position 250, Snapshots 1..3 vorhanden -> Index 2
    end)

    it("tolerantly falls back to the smallest existing snapshot before CP1", function()
        -- Position 10, Snapshots 1..3 vorhanden -> Index 1 (D4-Entscheidung)
    end)

    it("skips missing snapshot files", function()
        -- Position 250, nur Snapshot 3 existiert -> Index 3 (kleinster vorhandener)
        -- Position 350, nur Snapshot 1 existiert -> Index 1
    end)

    it("returns nil without manifest or snapshots", function() end)

    it("applySnapshot swaps entity lists and restores main view", function()
        -- applySnapshot(2) -> self.characters == Snapshot-Daten, active_snapshot_index == 2
        -- applySnapshot(nil) -> self.characters == book_data.characters, index nil
    end)

    it("updateSnapshotViewForPage is a no-op when the index is unchanged", function()
        -- loadSnapshot-Zähler bleibt bei 1 nach zwei Aufrufen im selben Intervall
    end)

    it("full_book setting forces the main view", function()
        -- spoiler_setting = "full_book", aktive Snapshot-Sicht -> applySnapshot(nil)
    end)
end)
```

(Wie in Task 5: Kommentar-Szenarien als echte Assertions ausformulieren.)

- [ ] **Step 2: Tests rot verifizieren.**

- [ ] **Step 3: Implementierung (an `xray_prefetch.lua` anfügen)**

```lua
-- ── Positionsbasierte Snapshot-Auflösung (D4) ──────────────────────────────
function M:resolveSnapshotIndexForPage(page)
    local manifest = self.book_data and self.book_data.prefetch
    if not manifest or not manifest.checkpoints or not page then return nil end
    if not self.cache_manager then return nil end
    local best, smallest
    for i, cp in ipairs(manifest.checkpoints) do
        if self:_snapshotExistsCached(i) then
            smallest = smallest or i
            if cp.page <= page then best = i end
        end
    end
    -- D4 (User-Entscheidung): vor dem ersten Checkpoint tolerant den kleinsten
    -- vorhandenen Snapshot zeigen statt einer leeren Ansicht.
    return best or smallest
end

-- io.open-Proben pro Sitzung cachen; Invalidierung bei saveSnapshot/clear.
function M:_snapshotExistsCached(index)
    self._snapshot_exists = self._snapshot_exists or {}
    local hit = self._snapshot_exists[index]
    if hit ~= nil then return hit end
    local exists = self.cache_manager:snapshotExists(self.ui.document.file, index)
    self._snapshot_exists[index] = exists
    return exists
end

function M:invalidateSnapshotExistsCache()
    self._snapshot_exists = nil
end

function M:applySnapshot(index)
    if index == self.active_snapshot_index then return end
    if index == nil then
        local bd = self.book_data or {}
        self.characters = bd.characters or {}
        self.locations = bd.locations or {}
        self.terms = bd.terms or {}
        self.historical_figures = bd.historical_figures or {}
        self.active_snapshot_index = nil
        self.active_snapshot_page = nil
    else
        local snap = self.cache_manager and self.cache_manager:loadSnapshot(self.ui.document.file, index)
        if not snap then return end
        self.characters = snap.characters or {}
        self.locations = snap.locations or {}
        self.terms = snap.terms or {}
        self.historical_figures = snap.historical_figures or {}
        self.active_snapshot_index = index
        self.active_snapshot_page = snap.page
    end
    self:log("XRayPlugin: Snapshot view -> " .. tostring(index))
    -- Serien-Kontext neu über die Sicht legen (Entities aus Vorgängerbänden
    -- sind per Definition spoilerfrei); Referenzen werden beim ersten Merge
    -- in main.lua/mergeSeriesContext auf self._series_ctx gelegt (Step 4b).
    if self._series_ctx and self.mergeSeriesContext then
        pcall(function() self:mergeSeriesContext(self._series_ctx.cache_data, self._series_ctx.series_info) end)
    end
end

function M:updateSnapshotViewForPage(page)
    if not page then return end
    local manifest = self.book_data and self.book_data.prefetch
    if not manifest or not manifest.checkpoints then return end

    local spoiler_setting = self.ai_helper and self.ai_helper.settings and self.ai_helper.settings.spoiler_setting or "spoiler_free"
    if spoiler_setting == "full_book" then
        if self.active_snapshot_index then self:applySnapshot(nil) end
        return
    end
    self:applySnapshot(self:resolveSnapshotIndexForPage(page))
end
```

Zusätzlich in Task-5-Code nachziehen: in `_watchPrefetchStep` direkt nach `saveSnapshot(...)` ein `self:invalidateSnapshotExistsCache()` einfügen; ebenso in `CacheManager`-Aufrufer nach `deleteSnapshots` (Task 8 prüft `clearCache`-Aufrufstellen).

- [ ] **Step 4a: Hooks in `main.lua`**

Am Ende des `if cached_data then`-Blocks von `autoLoadCache` (~Z. 662, nach `xray_mode_enabled`):

```lua
        -- Snapshot-Sicht für die aktuelle Position anwenden (Checkpoint-Prefetch)
        if self.updateSnapshotViewForPage then
            UIManager:scheduleIn(0.1, function()
                if self.destroyed or not self.ui then return end
                self:updateSnapshotViewForPage(self.ui:getCurrentPage())
            end)
        end
```

In `onPageUpdate` (~Z. 366, nach `self.last_pageno = pageno`):

```lua
    if self.updateSnapshotViewForPage then self:updateSnapshotViewForPage(pageno) end
```

(`updateSnapshotViewForPage` ist ohne Manifest ein früher Return — Seitenblättern bleibt für Bücher ohne Prefetch kostenlos.)

- [ ] **Step 4b: Serien-Kontext-Referenzen sichern**

`mergeSeriesContext` in `xray_fetch.lua:1347` ff. lesen; an dessen **Anfang** (nach dem Nil-Guard) ergänzen:

```lua
    self._series_ctx = { cache_data = cache_data, series_info = series_info }
```

- [ ] **Step 5: Tests grün + Vollsuite.**

### Task 7: Timeline-Filterung über Seitenanker

**Files:**
- Modify: `xray.koplugin/xray_prefetch.lua` (`visibleTimeline`), `xray.koplugin/xray_ui.lua` (Timeline-Render-Stellen)
- Test: `spec/xray_prefetch_spec.lua`

**Interfaces:**
- Produces: `M:visibleTimeline() -> table` — bei aktiver Snapshot-Sicht nur Events mit `ev.page <= active_snapshot_page`; sonst `self.timeline` unverändert (identische Referenz).
- **Wichtig:** `self.timeline` wird NIE getauscht/ersetzt (D2: Timeline hat nur eine Wahrheit im Haupt-Cache; ein Tausch würde bei Write-backs Zukunfts-Events vernichten).

- [ ] **Step 1: Failing Tests schreiben**

```lua
describe("visibleTimeline", function()
    it("returns the full timeline without an active snapshot", function()
        -- identische Referenz: assert.equals(plugin.timeline, plugin:visibleTimeline())
    end)
    it("filters events beyond the active snapshot page", function()
        -- timeline = {p10, p150, p999, kein-page} bei active_snapshot_page=200
        -- -> {p10, p150}; Events ohne page werden in Snapshot-Sicht ausgeblendet
    end)
end)
```

- [ ] **Step 2: Rot verifizieren, dann Implementierung:**

```lua
function M:visibleTimeline()
    if not self.active_snapshot_page then return self.timeline or {} end
    local out = {}
    for _, ev in ipairs(self.timeline or {}) do
        -- ponytail: Events ohne Seitenanker in Snapshot-Sicht konservativ
        -- ausblenden (assignTimelinePages vergibt Anker beim Laden; Rest wäre
        -- nicht spoiler-einordenbar).
        if ev.page and ev.page <= self.active_snapshot_page then
            table.insert(out, ev)
        end
    end
    return out
end
```

- [ ] **Step 3: Render-Stellen umstellen**

`rg -n "self\.timeline" xray.koplugin/xray_ui.lua` — jede **Lese/Render**-Stelle (bekannt: Timeline-View ab ~Z. 3342; außerdem Zähler/Badges, falls vorhanden) auf `local timeline = self:visibleTimeline()` umstellen. **Schreib**-Stellen (`self.timeline = ...`) unverändert lassen. Auch den Leer-Check der View (`#self.timeline == 0` ~Z. 3342) auf die gefilterte Liste umstellen, sonst zeigt eine leere Snapshot-Sicht die volle "keine Daten"-Meldung nicht.

- [ ] **Step 4: Tests grün + Vollsuite.**

### Task 8: Write-back-Routing + Fetch-Guards

**Files:**
- Modify: `xray.koplugin/xray_prefetch.lua` (`persistDisplayedEntities`), `xray.koplugin/xray_mentions.lua:146-174`, `xray.koplugin/xray_ui.lua` (~Z. 2100-2130, ~Z. 2210-2240, ~Z. 2350-2370 — vorher lesen!), `xray.koplugin/main.lua:541-547` (Guard), `xray.koplugin/xray_fetch.lua:19-51` (`fetchFromAI`/`updateFromAI`-Guards)
- Test: `spec/xray_mentions_spec.lua` + `spec/xray_prefetch_spec.lua`

**Interfaces:**
- Produces: `M:persistDisplayedEntities()` — Displayed-Dataset-Regel (D4): bei aktiver Snapshot-Sicht in die Snapshot-Datei schreiben, sonst Legacy-Verhalten (Haupt-Cache-Update wie heute).
- **Invariante (testgestützt):** Bei `active_snapshot_index ~= nil` wird `asyncSaveCache`/`saveCache` für Entity-Listen **nicht** aufgerufen.

- [ ] **Step 1: Failing Tests**

```lua
describe("write-back routing", function()
    it("persists to the active snapshot file, never the main cache", function()
        -- active_snapshot_index = 2; persistDisplayedEntities()
        -- -> saveSnapshot(_, 2, ...) aufgerufen, asyncSaveCache NICHT aufgerufen
    end)
    it("uses the legacy main-cache path without an active snapshot", function()
        -- active_snapshot_index = nil -> asyncSaveCache aufgerufen
    end)
end)
-- plus in spec/xray_mentions_spec.lua: saveMentionsToCache delegiert an
-- persistDisplayedEntities (Stub-Zähler), statt selbst asyncSaveCache zu rufen.
```

- [ ] **Step 2: Rot verifizieren, dann Implementierung**

In `xray_prefetch.lua`:

```lua
-- D4 Displayed-Dataset-Regel: Mutationen (Mention-Scans, Edits, Sortierung)
-- persistieren in den gerade angezeigten Datensatz. Eine Snapshot-Sicht darf
-- den Haupt-Cache NIE überschreiben (100%-Daten!).
function M:persistDisplayedEntities()
    if not self.cache_manager then return end
    if self.active_snapshot_index then
        local snap = {
            checkpoint_index = self.active_snapshot_index,
            page = self.active_snapshot_page,
            characters = self.characters or {},
            locations = self.locations or {},
            terms = self.terms or {},
            historical_figures = self.historical_figures or {},
        }
        local manifest = self.book_data and self.book_data.prefetch
        if manifest and manifest.checkpoints and manifest.checkpoints[self.active_snapshot_index] then
            snap.percent = manifest.checkpoints[self.active_snapshot_index].percent
        end
        self.cache_manager:saveSnapshot(self.ui.document.file, self.active_snapshot_index, snap)
        return
    end
    -- Legacy-Pfad: exakt das bisherige Verhalten von saveMentionsToCache
    if not self.book_data then
        self.book_data = self.cache_manager:loadCache(self.ui.document.file) or {}
    end
    local updated = self.book_data
    updated.characters         = self.characters
    updated.historical_figures = self.historical_figures
    updated.locations          = self.locations
    updated.terms              = self.terms
    updated.timeline           = self.timeline
    if self.author_info then updated.author_info = self.author_info end
    self.cache_manager:asyncSaveCache(self.ui.document.file, updated)
end
```

`xray_mentions.lua`: Rumpf von `saveMentionsToCache` (Z. 146-174) ersetzen durch Delegation:

```lua
function M:saveMentionsToCache()
    if not self.cache_manager then
        self.cache_manager = require(plugin_path .. "xray_cachemanager"):new()
    end
    self:persistDisplayedEntities()
end
```

`xray_ui.lua`-Auditstellen: die drei Bereiche lesen. Für jede Stelle, die **Entity-Listen** in den Cache schreibt (`cache.locations = list` o. ä., gefunden ~Z. 2124, ~Z. 2364, Umfeld ~Z. 2234), die Schreiblogik durch `self:persistDisplayedEntities()` ersetzen (die `self.*`-Listen sind an diesen Stellen bereits mutiert). Reine **Flag**-Writes (`ignore_lang_mismatch` Z. 753, `book_mode_override` Z. 1782, `series_context_dismissed` Z. 4392/4453) unverändert lassen — sie schreiben Metadaten in den Haupt-Cache, das ist auch bei aktiver Snapshot-Sicht korrekt (`self.book_data` wird nie getauscht).

- [ ] **Step 3: Fetch-Guards**

`main.lua:542` (in `triggerBackgroundMergeFetch`, nach dem `bg_fetch_active`-Guard):

```lua
    if self.prefetch_active then return end
    if self.active_snapshot_index then return end -- Position von Snapshot abgedeckt (D4)
    if self.isPrefetchComplete and self:isPrefetchComplete() then return end
```

`xray_fetch.lua` `fetchFromAI` (Z. 19) und `updateFromAI` (Z. 33), jeweils als erste Zeilen:

```lua
    if self.active_snapshot_index then
        if self.showPrefetchInfo then self:showPrefetchInfo(self.loc:t("prefetch_position_covered") or "This position is covered by offline data. Clear the cache to re-fetch.") end
        return
    end
```

- [ ] **Step 4: Tests grün + Vollsuite + Syntax-Check.**

- [ ] **Step 5: Commit (Phase 3)**

```bash
git add xray.koplugin/ spec/
git commit -m "Phase 3: Positionsbasierte Snapshot-Anzeige + Write-back-Routing

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

# Phase 5 — Settings, Menü, Lokalisierung (Phase 4 entfällt per D5)

### Task 9: Menüpunkte + Auto-Trigger

**Files:**
- Modify: `xray.koplugin/main.lua` (~Z. 822-841 Menü; `onReaderReady` ~Z. 308; `onNetworkConnected` ~Z. 357), `xray.koplugin/xray_prefetch.lua` (`maybeStartAutoPrefetch`)
- Test: `spec/xray_prefetch_spec.lua`

**Interfaces:**
- Produces: `M:maybeStartAutoPrefetch()`; Setting-Key `offline_prefetch_auto` (boolean, via `self.ai_helper:saveSettings`); Loc-Keys siehe Task 10.
- Consumes: `startOfflinePrefetch(true)`, `isPrefetchComplete()`.

- [ ] **Step 1: Failing Tests (Guard-Matrix für `maybeStartAutoPrefetch`)**

```lua
describe("maybeStartAutoPrefetch", function()
    it("does nothing when the setting is off", function() end)
    it("does nothing when prefetch is complete", function() end)
    it("runs at most once per book/session", function() end)
    it("starts a silent prefetch when all guards pass", function()
        -- startOfflinePrefetch-Stub: 1 Aufruf mit is_silent == true
    end)
end)
```

- [ ] **Step 2: Rot verifizieren, dann Implementierung**

```lua
function M:maybeStartAutoPrefetch()
    local s = self.ai_helper and self.ai_helper.settings or {}
    if s.offline_prefetch_auto ~= true then return end
    if self.auto_prefetch_attempted then return end
    if self.prefetch_active or self.bg_fetch_active or self.bg_fetch_pending then return end
    if self:isPrefetchComplete() then return end
    self.auto_prefetch_attempted = true -- max. 1 Anlauf pro Buch/Sitzung (D3)
    self:startOfflinePrefetch(true)
end
```

Hooks in `main.lua` — `onReaderReady` (nach `autoLoadCache()`-Zeile):

```lua
    UIManager:scheduleIn(5, function()
        if self.destroyed then return end
        if self.maybeStartAutoPrefetch then self:maybeStartAutoPrefetch() end
    end)
```

`onNetworkConnected` (analog, Delay 2 s, gleiche Guard-Struktur — Bestandscode der Methode vorher lesen und Muster übernehmen).

Menü in `main.lua` — im `sub_item_table` von „Content & Fetch Settings" (~Z. 825 ff.), nach dem „Auto X-Ray Settings"-Eintrag:

```lua
                    {
                        text = self.loc:t("menu_prefetch_offline") or "Prepare book for offline reading",
                        callback = function() self:startOfflinePrefetch(false) end,
                    },
                    {
                        text = self.loc:t("menu_prefetch_auto") or "Auto-prepare for offline when online",
                        keep_menu_open = true,
                        checked_func = function()
                            return (self.ai_helper.settings and self.ai_helper.settings.offline_prefetch_auto) == true
                        end,
                        callback = function()
                            local cur = (self.ai_helper.settings and self.ai_helper.settings.offline_prefetch_auto) == true
                            self.ai_helper:saveSettings({ offline_prefetch_auto = not cur })
                        end,
                    },
```

(Exaktes Menü-Item-Format an die Nachbareinträge angleichen — `checked_func`-Muster existiert im File mehrfach.)

- [ ] **Step 3: Tests grün + Vollsuite.**

### Task 10: Lokalisierung + Gesamtverifikation

**Files:**
- Modify: `xray.koplugin/languages/en.po`, `xray.koplugin/languages/de.po` (via Tooling)
- Test: `python3 tools/check_translations.py`

Verwendete neue Keys (alle bereits mit englischem `or`-Fallback im Code): `prefetch_busy`, `prefetch_offline`, `prefetch_no_key`, `prefetch_failed`, `prefetch_already_done`, `prefetch_done`, `prefetch_cancelled`, `prefetch_partial`, `prefetch_progress`, `prefetch_progress_hint`, `prefetch_position_covered`, `menu_prefetch_offline`, `menu_prefetch_auto`.

- [ ] **Step 1:** `python3 tools/sync_translations.py` ausführen (propagiert die Keys in alle `.po`).
- [ ] **Step 2:** In `en.po` die englischen Texte eintragen (identisch zu den `or`-Fallbacks; `%d`-Platzhalter exakt wie im Code: `prefetch_cancelled`/`prefetch_partial` haben zwei `%d`, `prefetch_progress` hat zwei `%d`). In `de.po` die deutschen Übersetzungen (gleiche Platzhalter!).
- [ ] **Step 3:** `python3 tools/check_translations.py` → muss sauber durchlaufen.
- [ ] **Step 4: Gesamtverifikation**

```bash
luajit tools/spec_runner.lua          # kein neuer Fail ggü. Baseline
python3 tools/check_syntax.py xray.koplugin
git status --short                    # nur beabsichtigte Änderungen
```

- [ ] **Step 5: Commit (Phase 5)**

```bash
git add xray.koplugin/ spec/ tools/
git commit -m "Phase 5: Prefetch-Menü, Auto-Setting und Lokalisierung (EN/DE)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 11: Abschluss — Version, Testplan, Bericht

- [ ] **Step 1:** Version in `xray.koplugin/_meta.lua` (CalVer `YY.M.PATCH`, CRLF beibehalten!) um eine Patch-Stufe erhöhen; Commit `chore: bump version` + annotiertes Tag (bare Version, Repo-Konvention). **Kein Push** (kein Remote konfiguriert; `tools/release.py` NICHT verwenden — es pusht).
- [ ] **Step 2:** Manuellen Geräte-Testplan als Abschlussbericht-Abschnitt ausgeben (nicht ausführbar in dieser Umgebung):
  1. Prefetch komplett durchlaufen lassen (WLAN an, Menüpunkt) → Sidecar prüfen: `xray_snapshot_01..NN.lua` + `prefetch`-Manifest in `xray_cache.lua`, Größen plausibel (~15-60 KB/Snapshot).
  2. Prefetch nach 2 Checkpoints abbrechen → WLAN aus/an → erneut starten → Resume ab Checkpoint 3.
  3. Offline lesen bei 5 % / 55 % / 100 % → jeweils passender Datenstand (5 % zeigt Snapshot 1 — tolerante D4-Regel).
  4. Zurückblättern über eine Checkpoint-Grenze → Sicht folgt nach unten.
  5. Altbuch mit Legacy-Cache ohne Snapshots → Verhalten exakt wie vor dem Update.
  6. `spoiler_setting = full_book` → Menüpunkt löst normalen Voll-Fetch aus; Anzeige ungefiltert.
  7. Mention-Scan offline bei aktiver Snapshot-Sicht → Mentions erscheinen; `xray_cache.lua`-`characters` unverändert (Haupt-Cache nicht überschrieben).
- [ ] **Step 3:** Abschlussbericht: alle D-Entscheidungen + Commit-Liste (`git log --oneline`).

---

## Self-Review-Ergebnis (Plan gegen Auftrag geprüft)

- Auftrag §5 Phase 1→Task 1, Phase 2→Tasks 2-5, Phase 3→Tasks 6-8, Phase 4 entfällt (D5), Phase 5→Tasks 9-10, §7-Verifikation→Task 10/11. ✓
- Wiederverwendung statt Duplikation: Netz-Check/Retry/Trunkierung via `continueWithFetch`; Streaming-Serializer via `serializeToFile`; Spoiler-Grenze via bestehende `reading_percent`-Logik. ✓
- Konsistenz der Namen über Tasks: `startOfflinePrefetch`, `isPrefetchComplete`, `computeCheckpoints`, `applySnapshot`, `updateSnapshotViewForPage`, `resolveSnapshotIndexForPage`, `visibleTimeline`, `persistDisplayedEntities`, `maybeStartAutoPrefetch`, `saveSnapshot`/`loadSnapshot`/`snapshotExists`/`deleteSnapshots`/`getSnapshotPath` — kollisionfrei gegen Bestand (geprüft via rg). ✓
