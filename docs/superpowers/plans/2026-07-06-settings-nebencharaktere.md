# Settings-Bereinigung + Nebencharakter-Vollständigkeit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spoiler-Menü ehrlich umformulieren, versteckte Anzahl-Kopplung sichtbar machen, und Nebencharaktere über Segment-Anweisungen im Checkpoint-Prefetch vollständig erfassen — inkl. Schutz gegen Namenskollisionen (gleicher Name, verschiedene Figuren).

**Architecture:** Alle Prompt-Ergänzungen werden Lua-seitig in `xray_aihelper.lua:createPrompt` bzw. `findDuplicates*` als englischer Zusatzkontext angehängt (bestehender Mechanismus, wirkt für alle 16 Sprachen — keine `prompts/<lang>.lua`-Änderungen). Der Fetch-Kontext erhält ein `prefetch_segment`-Flag aus `self.prefetch_active`. Der gefährliche Vornamen-Auto-Merge in `deduplicateByName` (Check 3) entfällt. Menü-Änderungen sind reine Textänderungen (Config-Key `spoiler_setting` und Werte unverändert).

**Tech Stack:** Lua 5.1/LuaJIT (KOReader-Plugin, kein Build-Step), Custom-Spec-Runner (`luajit tools/spec_runner.lua`), gettext `.po` (`python3.12 tools/sync_translations.py`).

**Spec:** `docs/superpowers/specs/2026-07-06-settings-nebencharaktere-design.md` (Entscheidungen E1–E4, Design A1–A3 / B1–B6).

## Global Constraints

- **CRLF-Dateien:** `xray.koplugin/xray_aihelper.lua`, `xray.koplugin/localization_xray.lua`, `xray.koplugin/_meta.lua` haben CRLF-Zeilenenden. Nach JEDEM Edit an diesen Dateien normalisieren und prüfen:
  ```bash
  perl -pi -e 's/\r?\n$/\r\n/' xray.koplugin/xray_aihelper.lua
  file xray.koplugin/xray_aihelper.lua   # muss "CRLF line terminators" zeigen, NICHT "CRLF, LF"
  ```
  (Analog für die anderen beiden Dateien.) Alle übrigen Dateien bleiben LF.
- **Tabu:** Keine Änderungen an `xray.koplugin/prompts/<lang>.lua`, `xray.koplugin/xray_updater.lua`, an der Menüstruktur (nur Texte) oder an Config-Keys/-Werten (`spoiler_setting`: `spoiler_free`|`full_book` bleibt).
- **Kein `assert()` im Plugin-Code** — der Spec-Runner ersetzt `_G.assert` durch eine Matcher-TABLE. Explizite `if not x then ... end`-Prüfungen verwenden.
- **Spec-Runner:** Die Spec-Liste in `tools/spec_runner.lua` ist hartkodiert; die hier betroffenen Dateien (`spec/xray_data_spec.lua`, `spec/xray_aihelper_spec.lua`, `spec/xray_fetch_spec.lua`) sind bereits registriert. Es gibt keinen Einzeltest-Filter — immer die ganze Suite laufen lassen. Nur Matcher aus dem Runner-Subset verwenden (`assert.are.equal`, `assert.is_true`, `assert.is_false` sind sicher; für nil-Checks `assert.is_true(x == nil)` schreiben).
- **Baseline:** 169 passed / 11 failed. Die 11 Fails sind dokumentierte Env-Fails ohne `SQUASHFS_ROOT` (nil `generationConfig`/`response_format` in AI-Helper-Specs) — sie bleiben. Kein neuer Fail erlaubt.
- **Suite-Seiteneffekt:** Der Lauf kann `spec/mocks/xray/series/the_wheel_of_time.lua` umschreiben. Vor jedem Commit `git status` prüfen; wenn die Datei dirty ist und der Task nichts daran geändert hat: `git checkout -- spec/mocks/xray/series/the_wheel_of_time.lua`. Niemals committen.
- **Python-Tools:** immer `python3.12` (System-`python3` ist 3.11 und scheitert an f-Strings in den Tools).
- **Syntax-Check:** `tools/check_syntax.py` braucht das fehlende `luaparser` — stattdessen `luajit -bl <datei> > /dev/null` je geänderter Lua-Datei.
- **Kein Push:** Repo hat keinen Git-Remote (`git remote -v` leer) — nur lokale Commits und Tags.
- **Stil:** bestehenden Lua-Stil matchen, insbesondere das Muster `self.loc:t("key") or "Fallback"`.

---

### Task 1: B6 — Vornamen-Auto-Merge in deduplicateByName entfernen

**Files:**
- Modify: `xray.koplugin/xray_data.lua` (~Zeile 172–178, innerhalb `deduplicateByName`)
- Test: `spec/xray_data_spec.lua` (bestehendes `describe("deduplicateByName")` ab Zeile 57)

**Interfaces:**
- Consumes: `xray_data:deduplicateByName(list, key)` — bestehende Signatur, unverändert.
- Produces: Verhaltensänderung: mehrteilige eingehende Namen mergen nur noch über exakten Namens-Treffer (Check 1) oder vollen Alias-Treffer (Check 2); der First-Name-Treffer (Check 3) entfällt.

- [ ] **Step 1: Failing Test schreiben**

In `spec/xray_data_spec.lua`, innerhalb des bestehenden `describe("deduplicateByName", function()`-Blocks (nach dem Test `"should merge aliases and promote names"`), einfügen:

```lua
        it("does not merge different multi-word names sharing a first-name alias", function()
            local list = {
                { name = "Aegon Targaryen", aliases = {"Aegon"} },
                { name = "Aegon Blackfyre", aliases = {} }
            }
            local result = xray_data:deduplicateByName(list, "name")
            assert.are.equal(2, #result)
        end)

        it("still merges a bare first name into the matching full name", function()
            local list = {
                { name = "Daenerys Targaryen", aliases = {"Daenerys"} },
                { name = "Daenerys", aliases = {} }
            }
            local result = xray_data:deduplicateByName(list, "name")
            assert.are.equal(1, #result)
            assert.are.equal("Daenerys Targaryen", result[1].name)
        end)
```

- [ ] **Step 2: Test laufen lassen — muss fehlschlagen**

Run: `luajit tools/spec_runner.lua 2>&1 | tail -20`
Expected: Der Aegon-Test FAILT mit „Expected 2, got 1" (Check 3 merged heute „Aegon Blackfyre" in „Aegon Targaryen"). Der Daenerys-Test passt schon (Check 2). Gesamt: 170 passed / 12 failed.

- [ ] **Step 3: Check 3 entfernen**

In `xray.koplugin/xray_data.lua`, diesen Block (innerhalb `deduplicateByName`):

```lua
            -- Check 3: first-name component of canonical name matches a known alias
            if not existing then
                local first = k:match("^(%S+)")
                if first and first ~= k and #first >= 5 then
                    existing = alias_map[first]
                end
            end
```

ersetzen durch:

```lua
            -- No first-name-only matching for multi-word names: dynastic books
            -- (ASOIAF etc.) reuse first names across different characters, so a
            -- shared first name must never fuse two full names. Bare first names
            -- ("Daenerys") are still caught by the alias check above.
```

- [ ] **Step 4: Suite laufen lassen — muss grün sein**

Run: `luajit tools/spec_runner.lua 2>&1 | tail -5`
Expected: 171 passed / 11 failed (beide neuen Tests grün, keine Regression — der bestehende „John Watson"/„Watson"-Test nutzt Check 2 und bleibt grün).

- [ ] **Step 5: Syntax-Check + Commit**

```bash
luajit -bl xray.koplugin/xray_data.lua > /dev/null && echo OK
git status   # the_wheel_of_time.lua dirty? -> git checkout -- spec/mocks/xray/series/the_wheel_of_time.lua
git add xray.koplugin/xray_data.lua spec/xray_data_spec.lua
git commit -m "B6: Vornamen-Auto-Merge in deduplicateByName entfernt (Namensvettern-Schutz)"
```

---

### Task 2: B1+B4 — Charakter-Vollständigkeits- und Namensregeln im comprehensive-Prompt

**Files:**
- Modify: `xray.koplugin/xray_aihelper.lua` (~Zeile 1401–1403, in `AIHelper:createPrompt`) — **CRLF!**
- Test: `spec/xray_aihelper_spec.lua`

**Interfaces:**
- Consumes: `AIHelper:createPrompt(title, author, context, section_name, targeted_word)` (Zeile 1202) — bestehende Signatur.
- Produces: Für `section_name == "comprehensive_xray"` enthält der Prompt immer die Marker-Blöcke `CHARACTER COMPLETENESS RULES:` und `NAME DISAMBIGUATION RULES:`. Task 3 fügt in DENSELBEN `if`-Block den Segment-Teil ein.

- [ ] **Step 1: Failing Tests schreiben**

In `spec/xray_aihelper_spec.lua`, vor dem abschließenden `end)` des äußeren `describe("AIHelper", ...)`, neuen Block einfügen:

```lua
    describe("createPrompt character rules", function()
        setup(function()
            -- Prompt-Templates direkt laden (Runner läuft im Repo-Root)
            AIHelper.prompts = AIHelper.prompts or dofile("xray.koplugin/prompts/en.lua")
        end)

        it("appends completeness and name disambiguation rules for comprehensive_xray", function()
            local prompt = AIHelper:createPrompt("T", "A", { book_text = "text", reading_percent = 50 }, "comprehensive_xray")
            assert.is_true(prompt:find("CHARACTER COMPLETENESS RULES", 1, true) ~= nil)
            assert.is_true(prompt:find("NAME DISAMBIGUATION RULES", 1, true) ~= nil)
        end)

        it("does not append segment mode without the flag", function()
            local prompt = AIHelper:createPrompt("T", "A", { book_text = "text", reading_percent = 50 }, "comprehensive_xray")
            assert.is_true(prompt:find("SEGMENT COMPLETENESS MODE", 1, true) == nil)
        end)

        it("does not append character rules for other sections", function()
            local prompt = AIHelper:createPrompt("T", "A", { book_text = "text", reading_percent = 50 }, "more_terms")
            assert.is_true(prompt:find("CHARACTER COMPLETENESS RULES", 1, true) == nil)
        end)
    end)
```

- [ ] **Step 2: Tests laufen lassen — Test 1 muss fehlschlagen**

Run: `luajit tools/spec_runner.lua 2>&1 | tail -20`
Expected: „appends completeness…" FAILT (Marker nicht im Prompt); die beiden Negativ-Tests passen schon. 173 passed / 12 failed.

- [ ] **Step 3: Regeln anhängen**

In `xray.koplugin/xray_aihelper.lua`, nach diesem bestehenden Block (Zeile ~1401):

```lua
    if section_name == "comprehensive_xray" or section_name == "more_terms" then
        extra_context = extra_context .. "\n- For each term, provide up to 3 alternative names, acronyms, or synonyms in an 'aliases' array. CRITICAL: These aliases MUST be variations or names that actually appear in the provided book text; do not hallucinate external synonyms."
    end
```

direkt darunter einfügen:

```lua
    if section_name == "comprehensive_xray" then
        extra_context = extra_context
            .. "\n\nCHARACTER COMPLETENESS RULES:"
            .. "\n- A character is ANY figure who speaks or acts in the provided text, explicitly including minor characters with only a single scene."
            .. "\n- Do NOT create entries for figures that appear only in enumerations, genealogies, family trees, or passing mentions."
            .. "\n- If output space runs short, prioritize by importance and shorten minor characters' descriptions first."
            .. "\n\nNAME DISAMBIGUATION RULES:"
            .. "\n- Different characters may share the same name (dynasties, relatives). ALWAYS use a distinguishing canonical name for each (numeral, epithet, or seat, e.g. \"Aegon II Targaryen\", \"Walder Frey, Lord of the Crossing\")."
            .. "\n- NEVER list the bare shared name as an alias for any of these characters."
            .. "\n- Treat a newly found character as an already-known one ONLY if the text clearly refers to the same person; otherwise create a separate, disambiguated entry."
    end
```

- [ ] **Step 4: CRLF normalisieren + Suite laufen lassen**

```bash
perl -pi -e 's/\r?\n$/\r\n/' xray.koplugin/xray_aihelper.lua
file xray.koplugin/xray_aihelper.lua   # Expected: "... with CRLF line terminators"
luajit -bl xray.koplugin/xray_aihelper.lua > /dev/null && echo OK
luajit tools/spec_runner.lua 2>&1 | tail -5
```
Expected: 174 passed / 11 failed.

- [ ] **Step 5: Commit**

```bash
git status   # WoT-Mock ggf. zurücksetzen (siehe Global Constraints)
git add xray.koplugin/xray_aihelper.lua spec/xray_aihelper_spec.lua
git commit -m "B1+B4: Charakter-Vollständigkeits- und Namensregeln im comprehensive-Prompt"
```

---

### Task 3: B2 — prefetch_segment-Flag + Segment-Anweisung

**Files:**
- Modify: `xray.koplugin/xray_aihelper.lua` (im Task-2-Block) — **CRLF!**
- Modify: `xray.koplugin/xray_fetch.lua` (~Zeile 363–376, Kontext-Tabelle in `continueWithFetch`)
- Test: `spec/xray_aihelper_spec.lua`, `spec/xray_fetch_spec.lua`

**Interfaces:**
- Consumes: den Task-2-Block `if section_name == "comprehensive_xray" then … end` in `createPrompt`; `M:continueWithFetch(reading_percent, is_update, last_fetch_page, is_silent, prefetch_page)` (xray_fetch.lua:245); `self.prefetch_active` (gesetzt von `xray_prefetch.lua` während der Checkpoint-Schleife).
- Produces: Kontext-Feld `context.prefetch_segment` (true|nil); Prompt-Marker `SEGMENT COMPLETENESS MODE:` genau bei gesetztem Flag.

- [ ] **Step 1: Failing Test (aihelper-Seite) schreiben**

In `spec/xray_aihelper_spec.lua`, innerhalb des in Task 2 angelegten `describe("createPrompt character rules", ...)`-Blocks, anfügen:

```lua
        it("appends segment completeness mode when context.prefetch_segment is set", function()
            local prompt = AIHelper:createPrompt("T", "A", { book_text = "text", reading_percent = 50, prefetch_segment = true }, "comprehensive_xray")
            assert.is_true(prompt:find("SEGMENT COMPLETENESS MODE", 1, true) ~= nil)
            assert.is_true(prompt:find("applies to NEW characters", 1, true) ~= nil)
            -- Platzhalter muss ersetzt sein (Anhang läuft vor der gsub-Substitution):
            assert.is_true(prompt:find("{NUM_CHARS}", 1, true) == nil)
        end)
```

- [ ] **Step 2: Failing Test (fetch-Seite) schreiben**

In `spec/xray_fetch_spec.lua`, vor dem abschließenden `end)` des äußeren `describe("xray_fetch", ...)`, neuen Block einfügen. Der Test fährt das echte `continueWithFetch` synchron bis `buildComprehensiveRequest` (der UIManager-Fake führt `scheduleIn` sofort aus) und bricht dort kontrolliert ab:

```lua
    describe("prefetch_segment flag", function()
        local function runFetchAndCaptureContext(p)
            local captured
            p.ui.getCurrentPage = function() return 42 end
            p.ui.document.getToc = function() return {} end
            p.chapter_analyzer = {
                getTextForAnalysis = function() return "This is definitely enough book text for the test run." end,
                getDetailedChapterSamples = function() return "SAMPLES", { "Chapter 1" } end,
                getAnnotationsForAnalysis = function() return nil end,
            }
            p.ai_helper = {
                settings = {},
                buildComprehensiveRequest = function(self, title, author, context)
                    captured = context
                    return nil, "test_abort", "test abort"
                end,
            }
            -- is_silent=true -> kein Dialog, Abbruchpfad ohne UI
            p:continueWithFetch(50, false, nil, true)
            return captured
        end

        it("marks the context as segment fetch while prefetch is active", function()
            plugin.prefetch_active = true
            local ctx = runFetchAndCaptureContext(plugin)
            assert.is_true(ctx ~= nil)
            assert.is_true(ctx.prefetch_segment == true)
        end)

        it("does not mark the context outside of prefetch", function()
            plugin.prefetch_active = nil
            local ctx = runFetchAndCaptureContext(plugin)
            assert.is_true(ctx ~= nil)
            assert.is_true(ctx.prefetch_segment == nil)
        end)
    end)
```

- [ ] **Step 3: Tests laufen lassen — beide neuen Positiv-Tests müssen fehlschlagen**

Run: `luajit tools/spec_runner.lua 2>&1 | tail -25`
Expected: aihelper-Segmenttest FAILT (Marker fehlt), fetch-Test „marks the context…" FAILT (`ctx.prefetch_segment == nil`), „does not mark…" passt. 175 passed / 13 failed.
Falls der fetch-Test stattdessen mit einem Fehler in `continueWithFetch` abbricht (fehlender Stub), den Stub in `runFetchAndCaptureContext` ergänzen statt Produktionscode anzufassen.

- [ ] **Step 4: aihelper-Seite implementieren**

In `xray.koplugin/xray_aihelper.lua`, im Task-2-Block vor dessen `end` einfügen (Ergebnis-Block hier vollständig gezeigt):

```lua
    if section_name == "comprehensive_xray" then
        extra_context = extra_context
            .. "\n\nCHARACTER COMPLETENESS RULES:"
            .. "\n- A character is ANY figure who speaks or acts in the provided text, explicitly including minor characters with only a single scene."
            .. "\n- Do NOT create entries for figures that appear only in enumerations, genealogies, family trees, or passing mentions."
            .. "\n- If output space runs short, prioritize by importance and shorten minor characters' descriptions first."
            .. "\n\nNAME DISAMBIGUATION RULES:"
            .. "\n- Different characters may share the same name (dynasties, relatives). ALWAYS use a distinguishing canonical name for each (numeral, epithet, or seat, e.g. \"Aegon II Targaryen\", \"Walder Frey, Lord of the Crossing\")."
            .. "\n- NEVER list the bare shared name as an alias for any of these characters."
            .. "\n- Treat a newly found character as an already-known one ONLY if the text clearly refers to the same person; otherwise create a separate, disambiguated entry."
        if context and context.prefetch_segment then
            extra_context = extra_context
                .. "\n\nSEGMENT COMPLETENESS MODE:"
                .. "\n- This fetch covers ONE bounded text segment of the book. Extract EVERY character who speaks or acts within the provided samples, including minor ones."
                .. "\n- The character count target of {NUM_CHARS} applies to NEW characters found in this segment, NOT to the total list."
                .. "\n- Give minor characters short descriptions. If output space runs short, drop the least important characters first."
        end
    end
```

- [ ] **Step 5: fetch-Seite implementieren**

In `xray.koplugin/xray_fetch.lua`, in der Kontext-Tabelle (~Zeile 363) die Zeile `book_type = self.book_type,` ergänzen zu:

```lua
                book_type = self.book_type,
                prefetch_segment = self.prefetch_active or nil,
```

- [ ] **Step 6: CRLF normalisieren + Suite laufen lassen**

```bash
perl -pi -e 's/\r?\n$/\r\n/' xray.koplugin/xray_aihelper.lua
file xray.koplugin/xray_aihelper.lua   # Expected: nur "CRLF line terminators"
luajit -bl xray.koplugin/xray_aihelper.lua > /dev/null && luajit -bl xray.koplugin/xray_fetch.lua > /dev/null && echo OK
luajit tools/spec_runner.lua 2>&1 | tail -5
```
Expected: 177 passed / 11 failed.

- [ ] **Step 7: Commit**

```bash
git status   # WoT-Mock ggf. zurücksetzen
git add xray.koplugin/xray_aihelper.lua xray.koplugin/xray_fetch.lua spec/xray_aihelper_spec.lua spec/xray_fetch_spec.lua
git commit -m "B2: Segment-Vollständigkeit im Checkpoint-Fetch (prefetch_segment-Flag)"
```

---

### Task 4: B5 — Namenskollisions-Warnung in der Duplikat-Prüfung

**Files:**
- Modify: `xray.koplugin/xray_aihelper.lua` (`AIHelper:findDuplicates` ~Zeile 2007 und `AIHelper:findDuplicatesAsync` ~Zeile 2043) — **CRLF!**
- Test: `spec/xray_aihelper_spec.lua`

**Interfaces:**
- Consumes: `AIHelper:findDuplicates(title, author, entities, entity_type_label, reading_percent)` und `AIHelper:findDuplicatesAsync(...)` — beide bauen `prompt` via `string.format(template, ...)` und rufen dann `executeUnifiedRequest` bzw. den Async-Pfad.
- Produces: Beide Prompts enden mit dem Marker-Block `NAME COLLISION WARNING:`.

- [ ] **Step 1: Failing Test schreiben**

In `spec/xray_aihelper_spec.lua`, innerhalb `describe("createPrompt character rules", ...)` (nutzt dasselbe `AIHelper.prompts`-Setup), anfügen:

```lua
        it("appends a name collision warning to the duplicate check prompt", function()
            local captured
            local orig = AIHelper.executeUnifiedRequest
            AIHelper.executeUnifiedRequest = function(self, prompt)
                captured = prompt
                return { duplicate_pairs = {} }
            end
            AIHelper:findDuplicates("T", "A", { { name = "Aegon Targaryen" } }, "characters", 50)
            AIHelper.executeUnifiedRequest = orig
            assert.is_true(captured ~= nil)
            assert.is_true(captured:find("NAME COLLISION WARNING", 1, true) ~= nil)
        end)
```

- [ ] **Step 2: Test laufen lassen — muss fehlschlagen**

Run: `luajit tools/spec_runner.lua 2>&1 | tail -15`
Expected: neuer Test FAILT (Marker fehlt). 177 passed / 12 failed.

- [ ] **Step 3: Warnung implementieren**

In `xray.koplugin/xray_aihelper.lua`, direkt VOR `function AIHelper:findDuplicates(...)` eine lokale Konstante einfügen:

```lua
local DUPE_NAME_GUARD = "\n\nNAME COLLISION WARNING:"
    .. "\n- An identical or similar name does NOT prove the same entity -- dynasties and families reuse names across different people."
    .. "\n- Compare roles and descriptions before flagging a pair. Only flag when they clearly describe the same person or place; when in doubt, do NOT flag."
```

In `AIHelper:findDuplicates`, nach dem `string.format`-Aufbau:

```lua
    local prompt = string.format(template,
        title or "Unknown", author or "Unknown",
        p, entity_type_label or "entities",
        table.concat(lines, "\n"), p
    )
    prompt = prompt .. DUPE_NAME_GUARD
```

In `AIHelper:findDuplicatesAsync` denselben Anhang (`prompt = prompt .. DUPE_NAME_GUARD`) direkt nach dessen `string.format`-Aufbau einfügen (identische Struktur; die Zeile kommt jeweils VOR `prompt = self:sanitize_utf8(prompt)`).

- [ ] **Step 4: CRLF normalisieren + Suite laufen lassen**

```bash
perl -pi -e 's/\r?\n$/\r\n/' xray.koplugin/xray_aihelper.lua
file xray.koplugin/xray_aihelper.lua
luajit -bl xray.koplugin/xray_aihelper.lua > /dev/null && echo OK
luajit tools/spec_runner.lua 2>&1 | tail -5
```
Expected: 178 passed / 11 failed.

- [ ] **Step 5: Commit**

```bash
git status   # WoT-Mock ggf. zurücksetzen
git add xray.koplugin/xray_aihelper.lua spec/xray_aihelper_spec.lua
git commit -m "B5: Namenskollisions-Warnung in der Duplikat-Prüfung"
```

---

### Task 5: A1 — Spoiler-Menü ehrlich umformulieren

**Files:**
- Modify: `xray.koplugin/xray_ui.lua` (Fallback-Literale in `showSpoilerSettings`, ~Zeile 2549–2590)
- Modify: `xray.koplugin/localization_xray.lua` (Fallback `spoiler_free_about`, ~Zeile 229) — **CRLF!**
- Modify: `xray.koplugin/languages/en.po`, `de.po` (4 Keys), übrige 14 `.po` (Skript)

**Interfaces:**
- Consumes: Loc-Keys `spoiler_free_menu_option`, `full_book_option`, `spoiler_preference_desc`, `spoiler_free_about` (alle existieren in den `.po`-Dateien; `spoiler_free_about` zusätzlich als Fallback in `localization_xray.lua`).
- Produces: neue Texte, keine neuen Keys, kein Verhaltenswechsel. Der tote Fallback-Key `spoiler_free_option` (localization_xray.lua:228, nirgends referenziert) bleibt unangetastet.

Neue EN-Texte (verbindlich):
- `spoiler_free_menu_option`: `Spoiler-free (recommended)`
- `full_book_option`: `Show everything (full book)`
- `spoiler_preference_desc`: `Spoiler-free shows X-Ray data only up to your current reading position. Show everything displays all fetched data immediately - for non-fiction and re-reads.`
- `spoiler_free_about`: `Spoiler-free: the X-Ray display always follows your reading position. After preparing a book for offline reading, the whole book's data is stored locally - spoiler-free controls what is SHOWN, not what is fetched. New AI fetches stay limited to the pages you have read.\n\nShow everything (full book): analyzes and displays the entire book at once (one AI request, no position filtering). Recommended for non-fiction and re-reads - may contain spoilers on a first read.`

Neue DE-Texte (verbindlich):
- `spoiler_free_menu_option`: `Spoilerfrei (empfohlen)`
- `full_book_option`: `Alles anzeigen (Vollbuch)`
- `spoiler_preference_desc`: `Spoilerfrei zeigt X-Ray-Daten nur bis zu deiner aktuellen Leseposition. Alles anzeigen zeigt alle abgerufenen Daten sofort – für Sachbücher und Re-Reads.`
- `spoiler_free_about`: `Spoilerfrei: Die X-Ray-Anzeige folgt immer deiner Leseposition. Nach der Offline-Vorbereitung liegen die Daten des ganzen Buchs lokal vor – Spoilerfrei steuert die ANZEIGE, nicht den Abruf. Neue KI-Abrufe bleiben auf die bereits gelesenen Seiten begrenzt.\n\nAlles anzeigen (Vollbuch): analysiert und zeigt das ganze Buch sofort (ein KI-Abruf, keine Positionsfilterung). Empfohlen für Sachbücher und Re-Reads – beim Erstlesen sind Spoiler möglich.`

- [ ] **Step 1: Fallback-Literale in xray_ui.lua aktualisieren**

In `showSpoilerSettings` (xray_ui.lua:2549 ff.) die drei `or "..."`-Literale und den About-Fallback auf die neuen EN-Texte ändern:
- `or "Spoiler-free"` → `or "Spoiler-free (recommended)"`
- `or "Full Book Mode"` → `or "Show everything (full book)"`
- `or "Select your spoiler preference for X-Ray data:"` → neuer `spoiler_preference_desc`-EN-Text
- Das lange `or "Spoiler-free mode limits AI extraction..."`-Literal (About-Button) → neuer `spoiler_free_about`-EN-Text (mit `\n\n` wie im Original).

- [ ] **Step 2: Fallback in localization_xray.lua aktualisieren**

`spoiler_free_about = "..."` (Zeile ~229) auf den neuen EN-Text setzen. Danach:

```bash
perl -pi -e 's/\r?\n$/\r\n/' xray.koplugin/localization_xray.lua
file xray.koplugin/localization_xray.lua   # Expected: nur "CRLF line terminators"
```

- [ ] **Step 3: Übersetzungs-Sync laufen lassen und en.po prüfen**

```bash
python3.12 tools/sync_translations.py
grep -A1 'msgid "spoiler_free_menu_option"' xray.koplugin/languages/en.po
grep -A1 'msgid "full_book_option"' xray.koplugin/languages/en.po
grep -A1 'msgid "spoiler_preference_desc"' xray.koplugin/languages/en.po
grep -A1 'msgid "spoiler_free_about"' xray.koplugin/languages/en.po
```
Expected: en.po trägt die neuen EN-Texte. Falls der Sync bestehende msgstr NICHT überschreibt: die vier msgstr in `en.po` manuell per Edit setzen.

- [ ] **Step 4: de.po übersetzen**

Die vier msgstr in `xray.koplugin/languages/de.po` per Edit auf die verbindlichen DE-Texte setzen (msgid-Zeile unverändert lassen; `\n\n` als Literal im msgstr wie bei den Bestandstexten).

- [ ] **Step 5: Übrige 14 Sprachen auf neuen EN-Text setzen**

Die alten Übersetzungen beschreiben die falsche (alte) Semantik — auf EN-Platzhalter setzen. Skript nach `$SCRATCHPAD/update_spoiler_po.py` schreiben und ausführen:

```python
import glob, re

NEW = {
    "spoiler_free_menu_option": "Spoiler-free (recommended)",
    "full_book_option": "Show everything (full book)",
    "spoiler_preference_desc": "Spoiler-free shows X-Ray data only up to your current reading position. Show everything displays all fetched data immediately - for non-fiction and re-reads.",
    "spoiler_free_about": "Spoiler-free: the X-Ray display always follows your reading position. After preparing a book for offline reading, the whole book's data is stored locally - spoiler-free controls what is SHOWN, not what is fetched. New AI fetches stay limited to the pages you have read.\\n\\nShow everything (full book): analyzes and displays the entire book at once (one AI request, no position filtering). Recommended for non-fiction and re-reads - may contain spoilers on a first read.",
}

for path in glob.glob("xray.koplugin/languages/*.po"):
    if path.endswith("en.po") or path.endswith("de.po"):
        continue
    with open(path, encoding="utf-8") as f:
        text = f.read()
    for key, val in NEW.items():
        # val enthält \n bereits als Zwei-Zeichen-Sequenz (Backslash+n), wie im .po-Format üblich.
        # Lambda-Replacement, damit re keine Backslashes im Ersatztext interpretiert.
        pattern = re.compile(r'(msgid "' + re.escape(key) + r'"\nmsgstr )"(?:[^"\\]|\\.)*"')
        text, n = pattern.subn(lambda m, v=val: m.group(1) + '"' + v + '"', text)
        if n != 1:
            print(f"WARN {path}: {key} ersetzt {n}x")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
print("done")
```

Run: `python3.12 $SCRATCHPAD/update_spoiler_po.py` (aus dem Repo-Root; `$SCRATCHPAD` = Session-Scratchpad-Verzeichnis).
Expected: keine `WARN`-Zeilen. Bei WARN (mehrzeiliges msgstr o. Ä.): betroffene Datei manuell per Edit fixen.

- [ ] **Step 6: Verifikation**

```bash
python3.12 tools/check_translations.py    # Expected: alle Dateien in sync
luajit -bl xray.koplugin/xray_ui.lua > /dev/null && luajit -bl xray.koplugin/localization_xray.lua > /dev/null && echo OK
luajit tools/spec_runner.lua 2>&1 | tail -3   # Expected: 178 passed / 11 failed (unverändert)
```
Kein neuer Spec: reine Textänderung ohne Logik.

- [ ] **Step 7: Commit**

```bash
git status   # WoT-Mock ggf. zurücksetzen
git add xray.koplugin/xray_ui.lua xray.koplugin/localization_xray.lua xray.koplugin/languages/*.po
git commit -m "A1: Spoiler-Menü neu gerahmt (Anzeige-Semantik nach Checkpoint-Prefetch)"
```

---

### Task 6: A2 — Anzahl-Kopplung im Längen-Dialog sichtbar machen

**Files:**
- Modify: `xray.koplugin/xray_ui.lua` (`showEntityLengthPresets`, ButtonDialog ~Zeile 2711)
- Modify: `xray.koplugin/localization_xray.lua` (neuer Fallback-Key) — **CRLF!**
- Modify: `xray.koplugin/languages/en.po`, `de.po` (neuer Key `desc_length_count_hint`)

**Interfaces:**
- Consumes: `showEntityLengthPresets(setting_key, entity_name, is_timeline)` — der ButtonDialog dort hat bisher nur `title` + `buttons`.
- Produces: neuer Loc-Key `desc_length_count_hint`; Timeline-Dialog bleibt ohne Hinweis (Timeline hat keinen Anzahl-Trade-off — immer 1 Event pro Kapitel).

- [ ] **Step 1: Hinweiszeile in den Dialog einbauen**

In `xray.koplugin/xray_ui.lua`, den Block:

```lua
        info_dialog = ButtonDialog:new{
            title = entity_name .. " — " .. (self.loc:t("menu_desc_length_settings") or "Description Length"),
            buttons = buttons,
        }
```

ersetzen durch:

```lua
        info_dialog = ButtonDialog:new{
            title = entity_name .. " — " .. (self.loc:t("menu_desc_length_settings") or "Description Length"),
            text = (not is_timeline) and (self.loc:t("desc_length_count_hint") or "Note: Longer descriptions reduce how many entries each AI fetch returns.") or nil,
            buttons = buttons,
        }
```

- [ ] **Step 2: Fallback-Key in localization_xray.lua ergänzen**

In der Fallback-Tabelle von `Localization:t` (direkt nach der Zeile `desc_len_about_chars = "..."`, ~Zeile 328) einfügen:

```lua
            desc_length_count_hint = "Note: Longer descriptions reduce how many entries each AI fetch returns.",
```

Danach CRLF normalisieren + prüfen:

```bash
perl -pi -e 's/\r?\n$/\r\n/' xray.koplugin/localization_xray.lua
file xray.koplugin/localization_xray.lua
```

- [ ] **Step 3: Sync + DE-Übersetzung**

```bash
python3.12 tools/sync_translations.py
grep -A1 'msgid "desc_length_count_hint"' xray.koplugin/languages/en.po
```
Expected: Key in allen `.po` vorhanden, en.po mit EN-Text. Dann in `de.po` das msgstr per Edit setzen auf:
`Hinweis: Längere Beschreibungen verringern, wie viele Einträge pro KI-Abruf erfasst werden.`

- [ ] **Step 4: Verifikation**

```bash
python3.12 tools/check_translations.py
luajit -bl xray.koplugin/xray_ui.lua > /dev/null && luajit -bl xray.koplugin/localization_xray.lua > /dev/null && echo OK
luajit tools/spec_runner.lua 2>&1 | tail -3   # Expected: 178 passed / 11 failed
```

- [ ] **Step 5: Commit**

```bash
git status   # WoT-Mock ggf. zurücksetzen
git add xray.koplugin/xray_ui.lua xray.koplugin/localization_xray.lua xray.koplugin/languages/*.po
git commit -m "A2: Hinweis auf Anzahl-Kopplung im Beschreibungslängen-Dialog"
```

---

### Task 7: Gesamtverifikation, Version 26.7.3, Tag

**Files:**
- Modify: `xray.koplugin/_meta.lua` (version) — **CRLF!**
- Memory: `~/.claude/projects/-Users-dniehof-Programming-Programme-koreader-xray-plugin-main/memory/checkpoint-prefetch-design.md` (Ergänzung)

**Interfaces:**
- Consumes: alle vorherigen Tasks committed.
- Produces: Version `26.7.3`, annotierter Tag `26.7.3`, aktualisierte Memory-Notiz.

- [ ] **Step 1: Volle Verifikation**

```bash
luajit tools/spec_runner.lua 2>&1 | tail -5     # Expected: 178 passed / 11 failed
python3.12 tools/check_translations.py           # Expected: in sync
for f in xray.koplugin/xray_data.lua xray.koplugin/xray_aihelper.lua xray.koplugin/xray_fetch.lua xray.koplugin/xray_ui.lua xray.koplugin/localization_xray.lua; do luajit -bl "$f" > /dev/null || echo "SYNTAX FAIL: $f"; done
file xray.koplugin/xray_aihelper.lua xray.koplugin/localization_xray.lua xray.koplugin/_meta.lua   # alle: nur CRLF
git status   # WoT-Mock ggf. zurücksetzen; sonst clean
```

- [ ] **Step 2: Version bumpen**

In `xray.koplugin/_meta.lua`: `version = "26.7.2"` → `version = "26.7.3"` (einzeiliger Edit, CRLF bleibt automatisch erhalten; mit `file` gegenprüfen).

- [ ] **Step 3: Memory ergänzen**

In der Memory-Datei `checkpoint-prefetch-design.md` einen Abschnitt anfügen: Version 26.7.3 ergänzt Segment-Vollständigkeit (`context.prefetch_segment` → SEGMENT COMPLETENESS MODE), Namensregeln (B4/B5) und entfernt den Vornamen-Auto-Merge (B6, Check 3 in `deduplicateByName`); Spoiler-Menü ist reine Anzeige-Semantik-Beschreibung.

- [ ] **Step 4: Commit + Tag (kein Push — kein Remote)**

```bash
git add xray.koplugin/_meta.lua
git commit -m "chore: bump version to 26.7.3"
git tag -a 26.7.3 -m "26.7.3"
git log --oneline -8
git remote -v   # Expected: leer -> kein Push
```

---

## Bewusst NICHT in diesem Plan (laut Spec §6)

- Kein Mehrpass-Mechanismus für den Einzel-Fetch; der bleibt top-N.
- Keine Änderungen an `prompts/<lang>.lua`, keine neuen UI-Listen-Features, keine Config-Key-Änderungen.
- Die Anzahl-Kopplung selbst bleibt bestehen (nur Transparenz via A2).
- Mentions-Disambiguierung bei Namensvettern: bekannte Grenze, wird nicht gelöst.
