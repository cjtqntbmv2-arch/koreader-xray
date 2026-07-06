# Audit-Fixes Implementation Plan (Robustheit, Akku, UX, Logging)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die 9 priorisierten Maßnahmen aus dem Multi-Agent-Audit vom 2026-07-06 umsetzen: Teardown/Suspend-Verdrahtung, Prefetch-Invarianten-Guard für `fetchSingleWord`, pcall-Härtung der Fetch-Pipeline, atomares Cache-Schreiben, onPageUpdate-Kurzschluss, echtes Quick-Menü, lückenloses Fetch-Logging, Defaults/Onboarding, Updater-Härtung.

**Architecture:** KOReader-Plugin (Lua 5.1/LuaJIT), Mixin-Muster: alle `xray_*.lua`-Module werden auf EIN `XRayPlugin`-Objekt gemergt (`main.lua:29-48`). Methodennamen müssen über alle Mixin-Dateien eindeutig sein. Es gibt keinen Build-Schritt; das Artefakt ist der Ordner `xray.koplugin/`.

**Tech Stack:** Lua 5.1 (LuaJIT), KOReader-APIs (UIManager, NetworkMgr, DocSettings), eigener busted-kompatibler Spec-Runner (`tools/spec_runner.lua`).

## Global Constraints

- **Lua 5.1** — kein `goto`, kein `//`-Operator, `unpack` statt `table.unpack`.
- **Kein `assert(...)` im Plugin-Code** — der Spec-Runner ersetzt `_G.assert` durch eine Matcher-Tabelle; `assert()` wirft im Test-Env "attempt to call a table value". Konvention: expliziter nil-Check + `logger.warn`/`self:log` + `return false`.
- **Alte KOReader-Versionen unterstützen** — `require` bleibt `pcall`-gewrappt wo es heute so ist; bestehende Modul-Pfad-Shims nicht entfernen.
- **`xray_config.lua`-Keys niemals umbenennen** (User-Configs im Feld).
- **Syntax-Check:** `luajit -bl <datei> > /dev/null` je geänderter Lua-Datei (`tools/check_syntax.py` braucht das nicht installierte `luaparser`).
- **Tests:** `luajit tools/spec_runner.lua` aus dem Repo-Root. **Baseline zuerst erfassen** (Task 1, Step 1): ohne `SQUASHFS_ROOT` schlagen ~11 AI-Helper-Specs fehl (fehlendes `json`-Modul) — das ist der erlaubte Bestand. Kriterium jedes Tasks: **keine NEUEN Fehlschläge** gegenüber der Baseline.
- **Neue Spec-Dateien** müssen in die hartkodierte Liste in `tools/spec_runner.lua` (Zeilen 140-156) eingetragen werden, sonst laufen sie stumm nie.
- **`package.loaded`-Mocks in Specs immer restaurieren** (alte Referenz sichern, in `after_each` zurückschreiben) — sie leaken sonst suite-weit.
- **Neue `loc:t("key")`-Nutzungen:** danach `python3.12 tools/sync_translations.py` ausführen (nicht `python3` — f-String-Syntax braucht 3.12), dann die neuen msgids in `xray.koplugin/languages/en.po` **manuell** mit dem englischen Text füllen (sync überschreibt bestehende en.po-msgstr nie), dann `python3.12 tools/check_translations.py`.
- **D4-Invarianten des Checkpoint-Prefetch** (dürfen nicht verletzt werden): (1) Anzeige immer positionsbasiert; (2) aktive Snapshot-Sicht überschreibt NIE den Haupt-Cache — Entity-Write-backs laufen über `persistDisplayedEntities()`; (3) während `prefetch_active` ist die View-Auflösung eingefroren; (4) manuelle AI-Fetches sind bei aktiver Snapshot-Sicht per `guardSnapshotViewActive()` gesperrt.
- **Commits:** pro Task ein Commit (Nachricht unten je Task angegeben), Abschluss mit `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Teardown & Suspend verdrahten (Audit H1/H2/H3, M1-Lifecycle)

**Files:**
- Modify: `xray.koplugin/main.lua` (nach `destroy()`, Zeile 243)
- Modify: `xray.koplugin/xray_fetch.lua:425` (Poll-Abbruchbedingung), `:246` (Flag-Reset)
- Modify: `xray.koplugin/xray_prefetch.lua:111-112` (Poll-Intervall), `:240-275` (`_watchPrefetchStep`), `:277-279` (`_finishPrefetch`-Guard)
- Create: `spec/xray_main_spec.lua`
- Modify: `spec/xray_prefetch_spec.lua` (neue `it`-Blöcke am Ende des `describe("xray_prefetch")`)
- Modify: `tools/spec_runner.lua:155` (Spec registrieren)

**Interfaces:**
- Produces: `XRayPlugin:onCloseWidget()`, `:onCloseDocument()`, `:onSuspend()`, `:onResume()`; neues Flag `self.fetch_abort_requested` (boolean; `true` = laufender Poll soll beim nächsten Tick aufräumen und abbrechen). Task 7 nutzt `self._fetch_started_at` nicht — unabhängig.
- Consumes: bestehendes `self:destroy()`, `self.ai_helper:cancelAsyncChild()`, `self:_finishPrefetch()`, `self:_closePrefetchProgress()`.

- [ ] **Step 1: Baseline erfassen**

Run: `cd /Users/dniehof/Programming/Programme/koreader-xray-plugin-main && luajit tools/spec_runner.lua 2>&1 | tail -5`
Erwartet: `Passed: <N>` / `Failed: <M>` — N und M notieren; M sind die bekannten AI-Helper-Fehlschläge ohne `SQUASHFS_ROOT`. Alle folgenden Läufe müssen `Failed <= M` und `Passed >= N` (plus neue Tests) liefern.

- [ ] **Step 2: Failing Spec schreiben — neue Datei `spec/xray_main_spec.lua`**

```lua
-- spec/xray_main_spec.lua — Lifecycle-Verdrahtung und onPageUpdate-Kurzschluss
require("spec.spec_helper")

local XRayPlugin = require("main")

local function mkPlugin()
    local plugin = createMockPlugin()
    for k, v in pairs(XRayPlugin) do
        if plugin[k] == nil then plugin[k] = v end
    end
    plugin.closeAllMenus = function() end
    return plugin
end

describe("xray_main lifecycle", function()
    it("wires KOReader teardown events to destroy", function()
        assert.are.equal("function", type(XRayPlugin.onCloseWidget))
        assert.are.equal("function", type(XRayPlugin.onCloseDocument))
        local plugin = mkPlugin()
        local cancelled = 0
        plugin.ai_helper = { settings = {}, cancelAsyncChild = function() cancelled = cancelled + 1 end }
        plugin:onCloseWidget()
        assert.is_true(plugin.destroyed)
        assert.are.equal(1, cancelled)
    end)

    it("destroy is idempotent", function()
        local plugin = mkPlugin()
        local cancelled = 0
        plugin.ai_helper = { settings = {}, cancelAsyncChild = function() cancelled = cancelled + 1 end }
        plugin:destroy()
        plugin:destroy()
        assert.are.equal(1, cancelled)
    end)

    it("onSuspend aborts active work without destroying the session", function()
        local plugin = mkPlugin()
        local cancelled = 0
        plugin.ai_helper = { settings = {}, cancelAsyncChild = function() cancelled = cancelled + 1 end }
        plugin.prefetch_active = true
        plugin.bg_fetch_active = true
        plugin:onSuspend()
        assert.is_true(plugin.fetch_abort_requested)
        assert.is_true(plugin.prefetch_cancelled)
        assert.are.equal(1, cancelled)
        assert.falsy(plugin.destroyed)
        plugin:onResume()
        assert.is_false(plugin.fetch_abort_requested)
    end)
end)
```

- [ ] **Step 3: Spec im Runner registrieren**

In `tools/spec_runner.lua` in der `specs`-Liste nach `"spec/xray_prefetch_spec.lua"` ergänzen:

```lua
    "spec/xray_prefetch_spec.lua",
    "spec/xray_main_spec.lua"
```

- [ ] **Step 4: Test laufen lassen — muss fehlschlagen**

Run: `luajit tools/spec_runner.lua 2>&1 | grep -A2 "xray_main"`
Erwartet: FAIL — `Expected function, got nil` für `onCloseWidget` (Handler existiert noch nicht). Falls schon `require("main")` scheitert: Fehlermeldung lesen und den fehlenden Fake im Spec via `package.loaded[...]` VOR dem `require("main")` ergänzen (Muster: `spec/xray_prefetch_spec.lua:6-9`) — mit Restore nicht nötig, da nur additiv für nicht gefakte Module.

- [ ] **Step 5: Handler in `main.lua` implementieren**

In `xray.koplugin/main.lua`: erste Zeile von `destroy()` (nach Zeile 225) ergänzen und direkt NACH der `destroy()`-Funktion (nach Zeile 243) die vier Handler einfügen:

```lua
function XRayPlugin:destroy()
    if self.destroyed then return end
    self:log("XRayPlugin: destroy called, marking as destroyed")
    self.destroyed = true
    -- ... (Rest unverändert)
end

-- KOReader teardown: ReaderUI broadcasts CloseWidget/CloseDocument when the
-- document or reader closes. Without these handlers destroy() is never
-- reached and every `if self.destroyed` guard in the poll loops is dead code.
function XRayPlugin:onCloseWidget()
    self:destroy()
end

function XRayPlugin:onCloseDocument()
    self:destroy()
end

-- Suspend: the OS may kill the forked fetch child during sleep; polling on
-- after resume would only burn the tick budget. Abort in-flight work but do
-- NOT set self.destroyed -- the reading session continues after resume.
function XRayPlugin:onSuspend()
    if self.bg_fetch_active or self.prefetch_active then
        self:log("XRayPlugin: onSuspend - aborting active fetch/prefetch")
    end
    self.fetch_abort_requested = true
    if self.prefetch_active then
        self.prefetch_cancelled = true
    end
    if self.ai_helper and self.ai_helper.cancelAsyncChild then
        self.ai_helper:cancelAsyncChild()
    end
end

function XRayPlugin:onResume()
    self.fetch_abort_requested = false
    -- Let the opt-in auto-prefetch retry after wake-up (it aborted on suspend)
    self.auto_prefetch_attempted = false
end
```

- [ ] **Step 6: Abort-Flag im Fetch-Poll respektieren**

`xray.koplugin/xray_fetch.lua`: In `continueWithFetch` nach Zeile 246 (`self.bg_fetch_active = true`) einfügen:

```lua
    self.fetch_abort_requested = false
```

Und die Poll-Abbruchbedingung (Zeile 425) erweitern — aus

```lua
                if is_cancelled or self.destroyed then
```

wird

```lua
                if is_cancelled or self.destroyed or self.fetch_abort_requested then
```

- [ ] **Step 7: Failing Specs für den Prefetch-Loop schreiben**

Am Ende des äußeren `describe("xray_prefetch", ...)`-Blocks in `spec/xray_prefetch_spec.lua` (vor dessen schließendem `end)`) ergänzen:

```lua
    describe("watch loop hardening", function()
        it("_watchPrefetchStep releases prefetch_active when the document disappears", function()
            local plugin = createMockPlugin()
            for k, v in pairs(prefetch) do plugin[k] = v end
            plugin.prefetch_active = true
            plugin.bg_fetch_active = false
            plugin.book_data = { last_fetch_page = 0,
                prefetch = { checkpoints = { { page = 100, percent = 50 } } } }
            plugin.ui = nil
            plugin:_watchPrefetchStep(1, { page = 100, percent = 50 }, 0)
            assert.is_false(plugin.prefetch_active)
        end)

        it("a crashing tick never leaves prefetch_active locked", function()
            local plugin = createMockPlugin()
            for k, v in pairs(prefetch) do plugin[k] = v end
            plugin.prefetch_active = true
            plugin.bg_fetch_active = false
            plugin.book_data = { last_fetch_page = 100 }
            plugin.cache_manager = {
                saveSnapshot = function() error("disk full") end,
                snapshotExists = function() return false end,
            }
            plugin:_watchPrefetchStep(1, { page = 100, percent = 50 }, 0)
            assert.is_false(plugin.prefetch_active)
        end)
    end)
```

Run: `luajit tools/spec_runner.lua 2>&1 | grep -B1 -A2 "watch loop"`
Erwartet: beide FAIL (nil-Index auf `self.ui.document.file` bzw. durchschlagender `error("disk full")`).

- [ ] **Step 8: `_watchPrefetchStep` und `_finishPrefetch` härten, Poll-Intervall entspannen**

`xray.koplugin/xray_prefetch.lua`, Zeilen 111-112 ersetzen:

```lua
local PREFETCH_POLL_SECONDS = 2 -- battery: a network fetch takes tens of seconds; 1s polling tripled the wakeups
local PREFETCH_MAX_TICKS = 300 -- ponytail: ~10 min tick budget per checkpoint (scheduler pauses during suspend), then stop with resume
```

`_watchPrefetchStep` (Zeilen 240-275) komplett ersetzen:

```lua
function M:_watchPrefetchStep(idx, cp, ticks)
    local ok_ui, UIManager = pcall(require, "ui/uimanager")
    if not ok_ui or not UIManager then
        self:_finishPrefetch(false)
        return
    end
    UIManager:scheduleIn(PREFETCH_POLL_SECONDS, function()
        -- Document gone / plugin torn down: never leave a dangling prefetch
        -- lock (it would permanently block manual fetches and view updates).
        if self.destroyed or not self.ui or not self.ui.document then
            self.prefetch_active = false
            self:_closePrefetchProgress()
            return
        end
        local tick_ok, tick_err = pcall(function()
            if self.bg_fetch_active and not self.prefetch_cancelled then
                if ticks >= PREFETCH_MAX_TICKS then
                    self:log("XRayPlugin: Prefetch checkpoint " .. idx .. " timed out")
                    self:_finishPrefetch(false)
                    return
                end
                self:_watchPrefetchStep(idx, cp, ticks + 1)
                return
            end
            -- Fetch call ended: success <=> the data boundary reached the checkpoint page
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
                if self.invalidateSnapshotExistsCache then self:invalidateSnapshotExistsCache() end
                self:_prefetchNext()
            else
                self:_finishPrefetch(false)
            end
        end)
        if not tick_ok then
            self:log("XRayPlugin: Prefetch tick failed: " .. tostring(tick_err))
            self.prefetch_active = false
            self:_closePrefetchProgress()
        end
    end)
end
```

`_finishPrefetch` (Zeile 277): nach den ersten beiden Zeilen einen Guard einfügen:

```lua
function M:_finishPrefetch(success)
    self.prefetch_active = false
    self:_closePrefetchProgress()
    if not self.ui or not self.ui.document then return end
    -- ... (Rest unverändert ab `local manifest = ...`)
```

- [ ] **Step 9: Syntax + Tests grün**

Run: `luajit -bl xray.koplugin/main.lua > /dev/null && luajit -bl xray.koplugin/xray_fetch.lua > /dev/null && luajit -bl xray.koplugin/xray_prefetch.lua > /dev/null && luajit tools/spec_runner.lua 2>&1 | tail -5`
Erwartet: Syntax ok; neue Tests PASS; `Failed` ≤ Baseline. Falls bestehende Prefetch-Loop-Specs durch das pcall-Wrapping anders reagieren: Fehlermeldung lesen, Ursache beheben (nicht die Alt-Specs aufweichen).

- [ ] **Step 10: Commit**

```bash
git add xray.koplugin/main.lua xray.koplugin/xray_fetch.lua xray.koplugin/xray_prefetch.lua spec/xray_main_spec.lua spec/xray_prefetch_spec.lua tools/spec_runner.lua
git commit -m "fix: Teardown/Suspend-Handler verdrahtet, Prefetch-Loop gegen Dauersperre gehärtet (Audit H1-H3)"
```

---

### Task 2: `fetchSingleWord` — Snapshot-Guard + Invarianten-konformer Write-back (Audit Cache-H1)

**Files:**
- Modify: `xray.koplugin/xray_fetch.lua:67` (Guard) und `:213-231` (Write-back)
- Modify: `spec/xray_fetch_spec.lua` (neuer describe-Block am Dateiende)

**Interfaces:**
- Consumes: `self:guardSnapshotViewActive()` (xray_fetch.lua:22), `self:persistDisplayedEntities()` (xray_prefetch.lua:367 — routet bei aktiver Snapshot-Sicht in die Snapshot-Datei, sonst in den Haupt-Cache).
- Produces: nichts Neues — Verhalten: Lookup-Fetch ist bei aktiver Snapshot-Sicht gesperrt (identisch zu `fetchFromAI`/`updateFromAI`).

- [ ] **Step 1: Failing Spec schreiben**

Am Ende von `spec/xray_fetch_spec.lua` anhängen:

```lua
describe("fetchSingleWord snapshot guard", function()
    local old_net

    before_each(function()
        old_net = package.loaded["ui/network/manager"]
    end)

    after_each(function()
        package.loaded["ui/network/manager"] = old_net
    end)

    it("blocks the lookup fetch while a snapshot view is active", function()
        local fetch = require("xray_fetch")
        local plugin = createMockPlugin()
        for k, v in pairs(fetch) do plugin[k] = v end
        plugin.active_snapshot_index = 2
        local info_shown = 0
        plugin.showPrefetchInfo = function() info_shown = info_shown + 1 end
        local network_called = false
        package.loaded["ui/network/manager"] = {
            runWhenOnline = function(_, cb) network_called = true end,
        }
        plugin:fetchSingleWord("Gandalf")
        assert.are.equal(1, info_shown)
        assert.is_false(network_called)
    end)
end)
```

- [ ] **Step 2: Test laufen lassen — muss fehlschlagen**

Run: `luajit tools/spec_runner.lua 2>&1 | grep -A2 "snapshot guard"`
Erwartet: FAIL — `network_called` ist `true` (kein Guard vorhanden).

- [ ] **Step 3: Guard und Write-back fixen**

`xray.koplugin/xray_fetch.lua`, Zeile 67 — Funktionskopf ergänzen:

```lua
function M:fetchSingleWord(text, pos0, pos1)
    if self:guardSnapshotViewActive() then return end
    require("ui/network/manager"):runWhenOnline(function()
```

Zeilen 213-231 — den Block ab `-- Sort and save cache` bis einschließlich `self.cache_manager:asyncSaveCache(self.ui.document.file, updated)` ersetzen durch:

```lua
                    -- Sort and save via the D4 displayed-dataset rule: a
                    -- snapshot view must never overwrite the main cache.
                    self:sortDataByFrequency(target_list, book_text, "name")
                    self:persistDisplayedEntities()
```

(Die Zuweisungen `updated.characters = self.characters` etc. entfallen ersatzlos — `persistDisplayedEntities()` übernimmt exakt diese Felder inkl. `timeline` und `author_info`; `book_type` wird in diesem Flow nie verändert.)

- [ ] **Step 4: Syntax + Tests grün**

Run: `luajit -bl xray.koplugin/xray_fetch.lua > /dev/null && luajit tools/spec_runner.lua 2>&1 | tail -5`
Erwartet: neuer Test PASS, `Failed` ≤ Baseline.

- [ ] **Step 5: Commit**

```bash
git add xray.koplugin/xray_fetch.lua spec/xray_fetch_spec.lua
git commit -m "fix: fetchSingleWord respektiert Snapshot-Sicht (Guard + persistDisplayedEntities, D4-Invarianten 2+4)"
```

---

### Task 3: Fetch-Pipeline pcall-Härtung + Concurrency-Guard (Audit Fetch-H1/H2/H3/M1/M2)

**Files:**
- Modify: `xray.koplugin/xray_aihelper.lua:463` (Child-HTTP), `:780-813` (checkAsyncResult-Extraktion)
- Modify: `xray.koplugin/xray_fetch.lua:31-34` und `:46-49` (Concurrency-Guard), `:466-470` (finalize-pcall)
- Modify: `spec/xray_aihelper_spec.lua`, `spec/xray_fetch_spec.lua` (je neuer Block am Dateiende)

**Interfaces:**
- Consumes: `self.loc:t("prefetch_busy")` (Key existiert in en.po:692), `utils:getFriendlyError` (xray_utils.lua:24).
- Produces: `checkAsyncResult` gibt bei JEDEM unerwarteten Response-Shape `false, "error_parse", <msg>` zurück statt einen Lua-Error zu werfen (Vertrag, auf den der Poll-Loop und damit der Prefetch-Loop angewiesen sind).

- [ ] **Step 1: Failing Spec für checkAsyncResult schreiben**

Am Ende von `spec/xray_aihelper_spec.lua` anhängen:

```lua
describe("checkAsyncResult response-shape hardening", function()
    local ok_json = pcall(require, "json")
    if not ok_json then
        print("SKIP: checkAsyncResult shape tests need the json module (SQUASHFS_ROOT)")
    else
        it("returns error_parse instead of crashing on choices without message", function()
            local AIHelper = require("xray_aihelper")
            local path = "/tmp/xray_spec_async_result.json"
            local f = io.open(path, "w")
            f:write("200\nchatgpt\n" .. '{"choices":[{"finish_reason":"content_filter"}]}')
            f:close()
            local data, code = AIHelper:checkAsyncResult(path)
            pcall(os.remove, path)
            assert.is_false(data)
            assert.are.equal("error_parse", code)
        end)

        it("returns error_parse on a non-table JSON body", function()
            local AIHelper = require("xray_aihelper")
            local path = "/tmp/xray_spec_async_result2.json"
            local f = io.open(path, "w")
            f:write("200\nchatgpt\ntrue")
            f:close()
            local data, code = AIHelper:checkAsyncResult(path)
            pcall(os.remove, path)
            assert.is_false(data)
            assert.are.equal("error_parse", code)
        end)
    end
end)
```

- [ ] **Step 2: Test laufen lassen**

Run: `luajit tools/spec_runner.lua 2>&1 | grep -B1 -A2 "shape"`
Erwartet: FAIL mit "attempt to index"-Fehler (bzw. SKIP-Ausgabe, wenn `json` lokal fehlt — dann gelten die Tests erst im WSL-/CI-Lauf; weiter mit Step 3, der Fix ist unabhängig verifizierbar per Syntax-Check und Code-Review).

- [ ] **Step 3: `checkAsyncResult` härten**

`xray.koplugin/xray_aihelper.lua`, Zeilen 780-813 ersetzen (ab `local success, data = pcall(json.decode, response_text)` bis vor `local parsed_data, parse_err = ...`):

```lua
    local success, data = pcall(json.decode, response_text)
    if not success then return false, "error_parse", "JSON decode failed" end
    if type(data) ~= "table" then return false, "error_parse", "Non-object JSON response" end

    -- The extraction below indexes provider-specific shapes; a single
    -- unexpected response (e.g. choices without message) must return an
    -- error instead of crashing the poll callback -- a crash there leaves
    -- bg_fetch_active locked and stalls the prefetch loop.
    local ai_text = ""
    local extract_ok, extract_err = pcall(function()
        if provider == "gemini" then
            if data.candidates and data.candidates[1] and
               data.candidates[1].content and data.candidates[1].content.parts then
                local parts = data.candidates[1].content.parts
                for _, p in ipairs(parts) do
                    if p.text and not p.thought then
                        ai_text = ai_text .. p.text
                    end
                end
            end
        elseif provider == "claude" or self:isAnthropic(provider, self.providers[provider] and self.providers[provider].endpoint) then
            if data.content and data.content[1] and data.content[1].text then
                local content_text = data.content[1].text
                if content_text:find("^%s*{") then
                    ai_text = content_text
                else
                    ai_text = "{" .. content_text
                end
            end
        else
            if data.choices and data.choices[1] and data.choices[1].message then
                ai_text = data.choices[1].message.content or ""
            end
        end
    end)
    if not extract_ok then
        self:log("AIHelper: Response extraction failed for " .. tostring(provider) .. ": " .. tostring(extract_err))
        return false, "error_parse", "Unexpected response shape from " .. tostring(provider)
    end

    if not ai_text or #ai_text == 0 then
        local finish_reason
        if provider == "gemini" then
            finish_reason = (data.candidates and data.candidates[1] and data.candidates[1].finishReason) or "unknown"
        end
        self:log("AIHelper: " .. tostring(provider) .. " ai_text empty"
            .. (finish_reason and (". finishReason=" .. finish_reason) or ""))
        return false, "error_parse", "No text in AI response (" .. tostring(provider) .. ")"
    end
```

- [ ] **Step 4: Child-HTTP pcall'en (Fallback-Provider retten)**

`xray.koplugin/xray_aihelper.lua`, Zeile 463 — aus

```lua
                    ok, code, response_headers, status = http_req.request(request)
```

wird

```lua
                    local req_ok, req_err = pcall(function()
                        ok, code, response_headers, status = http_req.request(request)
                    end)
                    if not req_ok then
                        -- socket-level crash: mark this provider failed and let the loop try the fallback
                        self:log("AIHelper Child: http.request crashed for " .. tostring(req.provider) .. ": " .. tostring(req_err))
                        ok, code, response_headers, status = nil, "crash", nil, tostring(req_err)
                    end
```

- [ ] **Step 5: Failing Spec für den Concurrency-Guard**

Am Ende von `spec/xray_fetch_spec.lua` anhängen:

```lua
describe("manual fetch concurrency guard", function()
    local old_net

    before_each(function()
        old_net = package.loaded["ui/network/manager"]
    end)

    after_each(function()
        package.loaded["ui/network/manager"] = old_net
    end)

    it("refuses a second manual fetch while one is active", function()
        local fetch = require("xray_fetch")
        local plugin = createMockPlugin()
        for k, v in pairs(fetch) do plugin[k] = v end
        plugin.bg_fetch_active = true
        local network_called = false
        package.loaded["ui/network/manager"] = {
            runWhenOnline = function(_, cb) network_called = true end,
        }
        plugin:fetchFromAI()
        assert.is_false(network_called)
        plugin.bg_fetch_active = false
        plugin.bg_fetch_pending = true
        plugin:updateFromAI()
        assert.is_false(network_called)
    end)
end)
```

Run: `luajit tools/spec_runner.lua 2>&1 | grep -A2 "concurrency"` — Erwartet: FAIL.

- [ ] **Step 6: Guard + finalize-pcall implementieren**

`xray.koplugin/xray_fetch.lua` — in `fetchFromAI` (Zeile 31) und `updateFromAI` (Zeile 46) direkt nach dem `guardSnapshotViewActive()`-Return einfügen (in BEIDEN Funktionen identisch):

```lua
    if self.bg_fetch_active or self.bg_fetch_pending then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("prefetch_busy") or "A fetch is already running. Try again in a moment.",
            timeout = 4,
        })
        return
    end
```

Und den `finalizeXRayData`-Aufruf im Poll (Zeilen 466-470) ersetzen — aus

```lua
                else
                    if wait_msg then UIManager:close(wait_msg) end
                    self.bg_fetch_active = false
                    self:finalizeXRayData(data, title, author, book_text, is_update, is_silent, current_page)
                end
```

wird

```lua
                else
                    if wait_msg then UIManager:close(wait_msg) end
                    self.bg_fetch_active = false
                    local fin_ok, fin_err = pcall(function()
                        self:finalizeXRayData(data, title, author, book_text, is_update, is_silent, current_page)
                    end)
                    if not fin_ok then
                        self:log("XRayPlugin: finalizeXRayData failed: " .. tostring(fin_err))
                        if not is_silent then
                            local err_title, err_text = utils:getFriendlyError("error_parse", tostring(fin_err), self.loc)
                            UIManager:show(ConfirmBox:new{
                                text = err_title .. "\n\n" .. err_text,
                                ok_text = self.loc:t("ok") or "OK",
                                cancel_text = nil,
                            })
                        end
                    end
                end
```

- [ ] **Step 7: Syntax + Tests grün**

Run: `luajit -bl xray.koplugin/xray_aihelper.lua > /dev/null && luajit -bl xray.koplugin/xray_fetch.lua > /dev/null && luajit tools/spec_runner.lua 2>&1 | tail -5`
Erwartet: Concurrency-Test PASS; Shape-Tests PASS oder SKIP; `Failed` ≤ Baseline.

- [ ] **Step 8: Commit**

```bash
git add xray.koplugin/xray_aihelper.lua xray.koplugin/xray_fetch.lua spec/xray_aihelper_spec.lua spec/xray_fetch_spec.lua
git commit -m "fix: Fetch-Pipeline pcall-gehärtet (Response-Shapes, finalize, Child-HTTP) + Doppel-Fetch-Sperre"
```

---

### Task 4: Atomares Schreiben von Cache und Snapshots (Audit Cache-H2)

**Files:**
- Modify: `xray.koplugin/xray_cachemanager.lua:90-127` (saveCache), `:157-244` (asyncSaveCache-Coop-Pfad), `:252-261` (Fork-Child), `:484-507` (saveSnapshot)
- Modify: `spec/xray_cachemanager_spec.lua` (neuer Block am Dateiende)

**Interfaces:**
- Produces: unverändertes Public-API (`saveCache`, `asyncSaveCache`, `saveSnapshot` — gleiche Signaturen/Rückgaben). Neu ist nur die Garantie: die Zieldatei wird erst per `os.rename` ersetzt, wenn der Schreibvorgang komplett ist.

- [ ] **Step 1: Failing Specs schreiben**

Am Ende von `spec/xray_cachemanager_spec.lua` anhängen:

```lua
describe("atomic writes", function()
    local CacheManager = require("xray_cachemanager")

    it("keeps the previous cache intact when a save fails midway", function()
        local cm = CacheManager:new()
        local book = "atomic_test.epub"
        assert.is_true(cm:saveCache(book, { characters = { { name = "Alice" } } }))
        cm.serializeToFile = function() error("boom") end
        assert.is_false(cm:saveCache(book, { characters = { { name = "Bob" } } }))
        cm.serializeToFile = nil -- restore class method via __index
        local data = cm:loadCache(book)
        assert.is_not_nil(data)
        assert.are.equal("Alice", data.characters[1].name)
    end)

    it("keeps the previous snapshot intact when a snapshot save fails midway", function()
        local cm = CacheManager:new()
        local book = "atomic_test.epub"
        assert.is_true(cm:saveSnapshot(book, 1, { characters = { { name = "Alice" } } }))
        cm.serializeToFile = function() error("boom") end
        assert.is_false(cm:saveSnapshot(book, 1, { characters = { { name = "Bob" } } }))
        cm.serializeToFile = nil
        local snap = cm:loadSnapshot(book, 1)
        assert.is_not_nil(snap)
        assert.are.equal("Alice", snap.characters[1].name)
    end)
end)
```

- [ ] **Step 2: Tests laufen lassen — müssen fehlschlagen**

Run: `luajit tools/spec_runner.lua 2>&1 | grep -A3 "atomic"`
Erwartet: beide FAIL — die Zieldatei wurde direkt geöffnet/trunkiert, `loadCache`/`loadSnapshot` liefern `nil`.

- [ ] **Step 3: `saveCache` auf temp+rename umstellen**

In `xray.koplugin/xray_cachemanager.lua`, den `pcall`-Block in `saveCache` (Zeilen 90-119) ersetzen:

```lua
    -- ponytail: single fixed .tmp name per cache file; concurrent saves to the
    -- same book were last-writer-wins before too -- rename keeps that atomic.
    local tmp_file = cache_file .. ".tmp"
    local success, err = pcall(function()
        local f, open_err = io.open(tmp_file, "w")

        if not f then
            logger.warn("CacheManager: Cannot open file for writing:", tmp_file)
            logger.warn("CacheManager: Error:", open_err or "unknown")
            error("open failed: " .. tostring(open_err)) -- propagate: a bare `return false` inside pcall would make saveCache return true
        end

        f:write("-- X-Ray Cache v6.0\n")
        f:write("-- Generated: " .. os.date("%Y-%m-%d %H:%M:%S") .. "\n\n")
        f:write("return ")

        local ok2, write_err = pcall(function()
            self:serializeToFile(f, data, "")
        end)

        f:write("\n")
        f:close()

        if not ok2 then
            logger.warn("CacheManager: Serialization error:", write_err or "unknown")
            AIHelper:log("CacheManager: Serialization error: " .. tostring(write_err or "unknown"))
            pcall(os.remove, tmp_file)
            error("serialization failed") -- propagate so the outer pcall returns false
        end

        local rn_ok, rn_err = os.rename(tmp_file, cache_file)
        if not rn_ok then
            logger.warn("CacheManager: Rename failed:", rn_err or "unknown")
            pcall(os.remove, tmp_file)
            error("rename failed")
        end

        logger.info("CacheManager: Saved cache to:", cache_file)
        AIHelper:log("CacheManager: Saved cache to: " .. tostring(cache_file))
        return true
    end)

    if not success then
        logger.warn("CacheManager: Failed to save cache:", err or "unknown error")
        AIHelper:log("CacheManager: Failed to save cache: " .. tostring(err or "unknown error"))
        return false
    end

    return success
```

- [ ] **Step 4: `asyncSaveCache`-Kooperativ-Pfad umstellen**

Im UIManager-Pfad (ab Zeile 157): `io.open(cache_file, "w")` durch temp-Datei ersetzen und beim Abschluss umbenennen. Konkret: nach `if ok_ui and UIManager then` die Open-Zeilen ersetzen durch

```lua
        local tmp_file = cache_file .. ".tmp"
        local f, open_err = io.open(tmp_file, "w")
        if not f then
            logger.warn("CacheManager: Cannot open cache file for async write:", open_err or "unknown")
            if on_done_cb then on_done_cb(false) end
            return false
        end
```

und `resumeCoroutine` (Zeilen 222-239) ersetzen durch

```lua
        local function resumeCoroutine()
            local ok, err = coroutine.resume(co)
            if not ok then
                logger.warn("CacheManager: Error during async serialization:", err or "unknown")
                f:close()
                pcall(os.remove, tmp_file)
                if on_done_cb then on_done_cb(false) end
                return
            end

            if coroutine.status(co) == "dead" then
                f:close()
                local rn_ok, rn_err = os.rename(tmp_file, cache_file)
                if not rn_ok then
                    logger.warn("CacheManager: Async rename failed:", rn_err or "unknown")
                    pcall(os.remove, tmp_file)
                    if on_done_cb then on_done_cb(false) end
                    return
                end
                logger.info("CacheManager: Saved cache asynchronously (cooperative) to:", cache_file)
                AIHelper:log("CacheManager: Saved cache asynchronously (cooperative) to: " .. tostring(cache_file))
                if on_done_cb then on_done_cb(true) end
            else
                UIManager:scheduleIn(0.02, resumeCoroutine)
            end
        end
```

- [ ] **Step 5: Fork-Child und `saveSnapshot` umstellen**

Fork-Child (`child_logic`, Zeilen 252-261): den inneren `pcall`-Schreibblock ersetzen durch

```lua
        pcall(function()
            local tmp_file = cache_file .. ".tmp"
            local f = io.open(tmp_file, "w")
            if f then
                f:write(serialized_str)
                f:close()
                os.rename(tmp_file, cache_file)
            end
        end)
```

`saveSnapshot` (Zeilen 484-507) ersetzen:

```lua
function CacheManager:saveSnapshot(book_path, index, data)
    local path = self:getSnapshotPath(book_path, index)
    if not path or not data then return false end
    if not self:ensureDirectory(path) then return false end
    data.snapshot_version = SNAPSHOT_VERSION
    data.created_at = os.time()
    local tmp_path = path .. ".tmp"
    local f, open_err = io.open(tmp_path, "w")
    if not f then
        logger.warn("CacheManager: Cannot open snapshot for writing:", open_err or "unknown")
        return false
    end
    local ok = pcall(function()
        f:write("-- X-Ray Snapshot v" .. SNAPSHOT_VERSION .. "\nreturn ")
        self:serializeToFile(f, data, "")
        f:write("\n")
    end)
    f:close()
    if not ok then
        logger.warn("CacheManager: Failed to save snapshot:", path)
        AIHelper:log("CacheManager: Failed to save snapshot: " .. tostring(path))
        pcall(os.remove, tmp_path)
        return false
    end
    local rn_ok = os.rename(tmp_path, path)
    if not rn_ok then
        pcall(os.remove, tmp_path)
        return false
    end
    return true
end
```

- [ ] **Step 6: Syntax + Tests grün**

Run: `luajit -bl xray.koplugin/xray_cachemanager.lua > /dev/null && luajit tools/spec_runner.lua 2>&1 | tail -5`
Erwartet: beide Atomic-Tests PASS; bestehende Cachemanager-Specs unverändert grün.

- [ ] **Step 7: Commit**

```bash
git add xray.koplugin/xray_cachemanager.lua spec/xray_cachemanager_spec.lua
git commit -m "fix: Cache/Snapshot-Schreiben atomar (temp+rename) - Stromverlust zerstoert keinen Bestand mehr"
```

---

### Task 5: onPageUpdate-Kurzschluss vor TOC/Timeline-Scan ziehen (Audit Akku-HOCH-1)

**Files:**
- Modify: `xray.koplugin/main.lua:493-546` (Standard-Kapitelmodus in `onPageUpdate`)
- Modify: `spec/xray_main_spec.lua` (neuer describe-Block)

**Interfaces:**
- Consumes: `XRayPlugin` aus Task 1s Spec (`require("main")`, `mkPlugin()`-Helper in derselben Datei).
- Produces: identisches Außenverhalten; nur die Reihenfolge der Guards ändert sich (billige Session-Checks vor dem Timeline-String-Scan).

- [ ] **Step 1: Failing Spec schreiben**

In `spec/xray_main_spec.lua` einen weiteren describe-Block anhängen:

```lua
describe("onPageUpdate battery short-circuit", function()
    it("does no timeline string work on later pages of an already handled chapter", function()
        local plugin = mkPlugin()
        plugin.auto_fetch_enabled = true
        plugin.ai_helper = { settings = {} }
        plugin.chapters_fetched = {}
        plugin.ui.document.getToc = function()
            return { { page = 1, title = "Chapter 1" }, { page = 50, title = "Chapter 2" } }
        end
        plugin.timeline = { { chapter = "Chapter 1", page = 1 } }
        local normalize_calls = 0
        plugin.normalizeChapterName = function(_, name)
            normalize_calls = normalize_calls + 1
            return (name or ""):lower()
        end
        plugin:onPageUpdate(5)
        local after_first = normalize_calls
        assert.is_true(after_first > 0) -- first page of the chapter does the populated-scan
        plugin:onPageUpdate(6)
        assert.are.equal(after_first, normalize_calls) -- every later page must be string-work-free
    end)
end)
```

- [ ] **Step 2: Test laufen lassen — muss fehlschlagen**

Run: `luajit tools/spec_runner.lua 2>&1 | grep -A2 "short-circuit"`
Erwartet: FAIL — der Timeline-Scan läuft heute auf jeder Seite erneut.

- [ ] **Step 3: Guards umordnen**

In `xray.koplugin/main.lua`, Standard-Kapitelmodus: den Abschnitt von `local unique_id = ...` (Zeile 493) bis einschließlich `if unique_id == self.last_auto_chapter then return end / self.last_auto_chapter = unique_id` (Zeilen 540-541) ersetzen durch:

```lua
    local unique_id = chapter_title .. "_" .. tostring(chapter_page)

    -- Cheap session-level short-circuits FIRST: within one chapter every page
    -- after the first is a no-op; skip the timeline string scan (E-Ink battery).
    if self.chapters_fetched[unique_id] then return end
    if unique_id == self.last_auto_chapter then return end

    -- Skip non-narrative chapters (Frontmatter/Backmatter)
    if self:isNonNarrativeChapter(chapter_title) then
        self:log("XRayPlugin: Skipping non-narrative chapter: " .. tostring(chapter_title) .. " (page " .. tostring(chapter_page) .. ")")
        self.chapters_fetched[unique_id] = true
        return
    end

    -- Check if it's already populated in the timeline data
    local is_populated = false
    local norm_title = self:normalizeChapterName(chapter_title)
    for _, ev in ipairs(self.timeline or {}) do
        -- Duplicate = same chapter name AND same page number.
        -- If either page is nil, treat as distinct (prevents omnibus chapter collapse).
        if self:normalizeChapterName(ev.chapter or "") == norm_title then
            if ev.page and chapter_page and ev.page == chapter_page then
                is_populated = true
                break
            end
        end
    end

    if is_populated then
        self:log("XRayPlugin: Chapter already populated in data: " .. tostring(chapter_title) .. " (page " .. tostring(chapter_page) .. ")")
        self.chapters_fetched[unique_id] = true
        return
    end

    -- It is NOT populated. Limit retries to prevent API spamming.
    self.fetch_attempts = self.fetch_attempts or {}
    if (self.fetch_attempts[unique_id] or 0) >= 3 then
        self:log("XRayPlugin: Max fetch attempts reached for: " .. tostring(unique_id))
        self.chapters_fetched[unique_id] = true
        return
    end

    self.last_auto_chapter = unique_id
```

(Die alten Checks `if self.chapters_fetched[unique_id] then return end` bei Zeile 535 und `if unique_id == self.last_auto_chapter then return end` bei Zeile 540 sind jetzt oben und entfallen unten; die `if not self.chapters_fetched[unique_id]`-Bedingungen um die Log-Zeilen entfallen, weil die Funktion diese Pfade pro Kapitel nur noch einmal erreicht. Der Rest — Debounce + `scheduleIn(2, ...)` — bleibt wörtlich unverändert.)

- [ ] **Step 4: Syntax + Tests grün**

Run: `luajit -bl xray.koplugin/main.lua > /dev/null && luajit tools/spec_runner.lua 2>&1 | tail -5`
Erwartet: neuer Test PASS; `Failed` ≤ Baseline.

- [ ] **Step 5: Commit**

```bash
git add xray.koplugin/main.lua spec/xray_main_spec.lua
git commit -m "perf: onPageUpdate-Kurzschluss vor TOC/Timeline-Scan (kein String-Scan pro Seitenwechsel mehr)"
```

---

### Task 6: Echtes Quick-Menü (Audit UX-H1, N1)

**Files:**
- Modify: `xray.koplugin/xray_ui.lua:3712-3723` (Quick/Full-Menü) und 5 `on_close_callback`-Stellen (Zeilen 894, 1711, 2797, 3397, 3706)
- Modify: `xray.koplugin/languages/en.po` (+ alle anderen `.po` via sync-Tool)
- Modify: `spec/xray_ui_spec.lua` (neuer Block am Dateiende)

**Interfaces:**
- Produces: `M:showQuickXRayMenu()` (eigenständiges 7-Punkte-Menü), `M:reopenXRayMenu()` (öffnet Quick oder Full je nach Herkunft), Flag `self.last_menu_was_quick`. Neue loc-Keys: `quick_menu_full`.
- Consumes: `self:newMenu(...)` (bestehender Menü-Builder), `Screen` (Datei-Upvalue in xray_ui.lua), bestehende `show*`-Methoden, loc-Key `quick_menu_title` (existiert, en.po:722).

- [ ] **Step 1: Failing Spec schreiben**

Am Ende von `spec/xray_ui_spec.lua` anhängen:

```lua
describe("quick xray menu", function()
    it("is a real short menu, not an alias of the full menu", function()
        local ui = require("xray_ui")
        local plugin = createMockPlugin()
        for k, v in pairs(ui) do plugin[k] = v end
        local captured
        plugin.newMenu = function(_, _, opts)
            captured = opts
            return { fake_menu = true }
        end
        plugin.getSubMenuItems = function()
            error("quick menu must not build the full menu tree")
        end
        plugin:showQuickXRayMenu()
        assert.is_not_nil(captured)
        assert.are.equal(7, #captured.item_table)
        assert.is_true(plugin.last_menu_was_quick)
    end)
end)
```

- [ ] **Step 2: Test laufen lassen — muss fehlschlagen**

Run: `luajit tools/spec_runner.lua 2>&1 | grep -A2 "quick xray"`
Erwartet: FAIL — heute ruft `showQuickXRayMenu` das Vollmenü (`getSubMenuItems` wirft den Spec-Error).

- [ ] **Step 3: Quick-Menü implementieren**

`xray.koplugin/xray_ui.lua`, Zeile 3712 (`function M:showQuickXRayMenu() self:showFullXRayMenu() end`) ersetzen durch:

```lua
-- Everyday entry point: the 5 entity views + update. Settings/Maintenance/
-- About live only in the full menu (hold / tools submenu / "All options").
function M:showQuickXRayMenu()
    if self.xray_menu then UIManager:close(self.xray_menu); self.xray_menu = nil end
    self.last_menu_was_quick = true
    local items = {
        { text = self.loc:t("menu_characters") or "Characters", callback = function() self:showCharacters() end },
        { text = self.loc:t("menu_timeline") or "Timeline", callback = function() self:showTimeline() end },
        { text = self.loc:t("menu_locations") or "Locations", callback = function() self:showLocations() end },
        { text = self.loc:t("menu_terms") or "Glossary", callback = function() self:showTerms() end },
        { text = self.loc:t("menu_historical_figures") or "Historical Figures", callback = function() self:showHistoricalFigures() end },
        { text = self.loc:t("menu_update_xray") or "Update X-Ray Data (Merge)", callback = function() self:updateFromAI() end, separator = true },
        { text = self.loc:t("quick_menu_full") or "All options...", callback = function() self:showFullXRayMenu() end },
    }
    self.xray_menu = self:newMenu("xray_menu", {
        title = self.loc:t("quick_menu_title") or "X-Ray Quick Menu",
        item_table = items,
        is_borderless = true,
        width = Screen:getWidth(),
        height = Screen:getHeight(),
    })
    UIManager:show(self.xray_menu)
end

-- Entity views close back into whichever menu opened them.
function M:reopenXRayMenu()
    if self.last_menu_was_quick then
        self:showQuickXRayMenu()
    else
        self:showFullXRayMenu()
    end
end
```

Und in `showFullXRayMenu` (jetzt direkt darunter) als erste Zeile im Funktionskörper ergänzen:

```lua
    self.last_menu_was_quick = false
```

- [ ] **Step 4: Entity-Menü-Rücksprünge umstellen**

Run: `grep -n "self:showFullXRayMenu()" xray.koplugin/xray_ui.lua`
Erwartet: Treffer auf den Zeilen ~894, ~1711, ~2797, ~3397, ~3706 (innerhalb von `on_close_callback`-Bodies) plus die neue "All options"-Zeile aus Step 3. **Nur die 5 `on_close_callback`-Treffer** jeweils ersetzen durch:

```lua
            self:reopenXRayMenu()
```

- [ ] **Step 5: Loc-Key synchronisieren**

Run: `python3.12 tools/sync_translations.py`
Dann in `xray.koplugin/languages/en.po` beim neuen msgid `quick_menu_full` den msgstr eintragen: `All options...`
Run: `python3.12 tools/check_translations.py` — Erwartet: OK.

- [ ] **Step 6: Syntax + Tests grün**

Run: `luajit -bl xray.koplugin/xray_ui.lua > /dev/null && luajit tools/spec_runner.lua 2>&1 | tail -5`
Erwartet: Quick-Menü-Test PASS; `Failed` ≤ Baseline.

- [ ] **Step 7: Commit**

```bash
git add xray.koplugin/xray_ui.lua xray.koplugin/languages/ spec/xray_ui_spec.lua
git commit -m "feat: echtes Quick-Menue (5 Ansichten + Update), Ruecksprung merkt sich Herkunft"
```

---

### Task 7: Fetch-Lebenszyklus lückenlos loggen (Audit Logging M4/M5)

**Files:**
- Modify: `xray.koplugin/xray_fetch.lua:270-272` (START-Zeile), `:821-824` (Erfolgs-Zeile mit Dauer)
- Modify: `xray.koplugin/xray_aihelper.lua:213-215` (Provider-Skip-Log in `buildComprehensiveRequest`)

**Interfaces:**
- Produces: `self._fetch_started_at` (os.time beim Fetch-Start; nur für die Dauer-Berechnung).
- Consumes: bestehendes `self:log(...)`.

Reine Log-Zeilen — Trivial-Ausnahme, kein eigener Spec; Verifikation über Syntax-Check + bestehende Suite (Log-Format-Änderung bricht keinen Spec, da `log` in Specs gestubbt ist).

- [ ] **Step 1: START-Zeile in `continueWithFetch`**

`xray.koplugin/xray_fetch.lua`, direkt nach `local author = sanitizeMetadata(props.authors)` (Zeile 272) einfügen:

```lua
    self._fetch_started_at = os.time()
    local fetch_kind = prefetch_page and "prefetch" or (is_silent and "auto" or (is_update and "update" or "full"))
    self:log(string.format("XRayPlugin: Fetch START type=%s book=%s percent=%s last_fetch_page=%s",
        fetch_kind, title, tostring(reading_percent), tostring(last_fetch_page)))
```

- [ ] **Step 2: Erfolgs-Zeile mit Dauer für ALLE Fetches**

In `finalizeXRayData` den Block (Zeilen 821-824)

```lua
    if is_silent then
        self:log(string.format("XRayPlugin: Silent merge complete - Chars: %d, Locs: %d, Events: %d, Cache: %s",
            #self.characters, #self.locations, #self.timeline,
            cache_saved and "saved" or "failed"))
    else
```

ersetzen durch

```lua
    local fetch_duration = self._fetch_started_at and (os.time() - self._fetch_started_at) or -1
    self:log(string.format("XRayPlugin: Fetch OK in %ds - Chars: %d, Locs: %d, Events: %d, Terms: %d, Cache: %s",
        fetch_duration, #self.characters, #self.locations, #self.timeline, #self.terms,
        cache_saved and "saved" or "failed"))
    if not is_silent then
```

(Der bisherige `else`-Zweig mit dem Erfolgs-Dialog bleibt wörtlich erhalten, nur die Bedingung wird zu `if not is_silent then`; das schließende `end` bleibt.)

- [ ] **Step 3: Provider-Skip loggen**

`xray.koplugin/xray_aihelper.lua`, in `buildComprehensiveRequest` (Schleife ab Zeile 213): dem `if config and config.api_key and config.api_key ~= "" then`-Block (endet vor Zeile 404 `end`) einen `else`-Zweig geben:

```lua
        else
            self:log("AIHelper: Skipping provider " .. tostring(ai.provider) .. " - no API key configured")
        end
```

- [ ] **Step 4: Syntax + Tests grün + Commit**

Run: `luajit -bl xray.koplugin/xray_fetch.lua > /dev/null && luajit -bl xray.koplugin/xray_aihelper.lua > /dev/null && luajit tools/spec_runner.lua 2>&1 | tail -5`
Erwartet: `Failed` ≤ Baseline.

```bash
git add xray.koplugin/xray_fetch.lua xray.koplugin/xray_aihelper.lua
git commit -m "feat: Fetch-Lebenszyklus im Log (START-Zeile, Erfolg mit Dauer+Counts, Provider-Skip-Grund)"
```

---

### Task 8: Sparsame Defaults + API-Key-Onboarding (Audit UX-H3, M4)

**Files:**
- Modify: `xray.koplugin/xray_fetch.lua:852` (Dupe-Check-Default), Onboarding-Hook in `fetchFromAI`/`updateFromAI`
- Modify: `xray.koplugin/xray_ui.lua:1894` (Dupe-Check-Anzeige-Default), neue Onboarding-Funktionen, `getProviderKeySubMenu`-Dedup
- Modify: `xray.koplugin/languages/en.po` (+ sync)
- Modify: `spec/xray_fetch_spec.lua`, `spec/xray_ui_spec.lua`

**Interfaces:**
- Produces: `M:showApiKeyOnboarding()`, `M:showProviderKeyOnboardingMenu()`, `M:promptProviderKey(provider, provider_name)` (alle in xray_ui.lua). Neue loc-Keys: `onboarding_no_key`, `onboarding_setup_now`, `onboarding_pick_provider`, `onboarding_key_saved`. Setting-Semantik: `auto_dupe_check_enabled` ist ab jetzt **opt-in** (`== true` statt `~= false`).
- Consumes: `self.ai_helper:hasApiKey()` (xray_aihelper.lua:414), `self.ai_helper:saveSettings(...)`, `self:newMenu(...)`. Baut auf Task 3 auf (Concurrency-Guard steht bereits in `fetchFromAI`/`updateFromAI`).

- [ ] **Step 1: Failing Specs schreiben**

Am Ende von `spec/xray_fetch_spec.lua` anhängen:

```lua
describe("auto dupe check default", function()
    it("does not start a duplicate check unless explicitly enabled", function()
        local fetch = require("xray_fetch")
        local plugin = createMockPlugin()
        for k, v in pairs(fetch) do plugin[k] = v end
        local started = 0
        plugin.prefetch_active = false
        plugin.characters = { { name = "A" }, { name = "B" } }
        plugin.locations = {}
        plugin.ai_helper = {
            settings = {}, -- user never touched the setting
            hasApiKey = function() return true end,
            findDuplicatesAsync = function() started = started + 1; return nil end,
        }
        plugin:runPostFetchDuplicateCheck("T", "A", 50, true)
        assert.are.equal(0, started)
        plugin.ai_helper.settings.auto_dupe_check_enabled = true
        plugin:runPostFetchDuplicateCheck("T", "A", 50, true)
        assert.is_true(started > 0)
    end)
end)
```

Am Ende von `spec/xray_ui_spec.lua` anhängen:

```lua
describe("api key onboarding", function()
    local old_net

    before_each(function()
        old_net = package.loaded["ui/network/manager"]
    end)

    after_each(function()
        package.loaded["ui/network/manager"] = old_net
    end)

    it("manual fetch without any key opens the onboarding instead of fetching", function()
        local fetch = require("xray_fetch")
        local ui = require("xray_ui")
        local plugin = createMockPlugin()
        for k, v in pairs(ui) do plugin[k] = v end
        for k, v in pairs(fetch) do plugin[k] = v end
        plugin.ai_helper = { settings = {}, hasApiKey = function() return false end }
        local onboarded = 0
        plugin.showApiKeyOnboarding = function() onboarded = onboarded + 1 end
        local network_called = false
        package.loaded["ui/network/manager"] = {
            runWhenOnline = function(_, cb) network_called = true end,
        }
        plugin:fetchFromAI()
        assert.are.equal(1, onboarded)
        assert.is_false(network_called)
    end)
end)
```

Run: `luajit tools/spec_runner.lua 2>&1 | grep -A2 "dupe check default\|onboarding"` — Erwartet: beide FAIL.

- [ ] **Step 2: Dupe-Check-Default umdrehen**

`xray.koplugin/xray_fetch.lua:852` — aus

```lua
    if self.ai_helper.settings and self.ai_helper.settings.auto_dupe_check_enabled == false then return end
```

wird

```lua
    -- Opt-in: every check costs one extra AI call; default off (battery/quota)
    if not (self.ai_helper.settings and self.ai_helper.settings.auto_dupe_check_enabled == true) then return end
```

`xray.koplugin/xray_ui.lua:1894` — aus

```lua
        local current_setting = self.ai_helper.settings.auto_dupe_check_enabled ~= false -- default is true
```

wird

```lua
        local current_setting = self.ai_helper.settings.auto_dupe_check_enabled == true -- default is false (opt-in, costs an extra AI call)
```

- [ ] **Step 3: Onboarding-Hook in die manuellen Fetch-Einstiege**

`xray.koplugin/xray_fetch.lua` — in `fetchFromAI` UND `updateFromAI`, direkt nach dem in Task 3 eingefügten Concurrency-Guard, einfügen:

```lua
    if not (self.ai_helper and self.ai_helper.hasApiKey and self.ai_helper:hasApiKey()) then
        self:showApiKeyOnboarding()
        return
    end
```

- [ ] **Step 4: Onboarding-Funktionen in `xray_ui.lua`**

Direkt VOR `function M:getProviderKeySubMenu(...)` (Zeile 3764) einfügen:

```lua
-- First-run onboarding: the only mandatory configuration is one provider key.
-- Instead of a dead-end error message, walk the user straight to the input.
function M:showApiKeyOnboarding()
    local ConfirmBox = require("ui/widget/confirmbox")
    UIManager:show(ConfirmBox:new{
        text = self.loc:t("onboarding_no_key")
            or "X-Ray needs an AI provider API key (one-time setup).\n\nGoogle Gemini offers a free tier (aistudio.google.com).\n\nSet it up now?",
        ok_text = self.loc:t("onboarding_setup_now") or "Set up now",
        cancel_text = self.loc:t("cancel") or "Cancel",
        ok_callback = function()
            self:showProviderKeyOnboardingMenu()
        end,
    })
end

function M:showProviderKeyOnboardingMenu()
    local items = {}
    for _, p in ipairs({
        { id = "gemini", name = "Google Gemini" },
        { id = "chatgpt", name = "OpenAI ChatGPT" },
        { id = "deepseek", name = "DeepSeek" },
        { id = "claude", name = "Anthropic Claude" },
    }) do
        table.insert(items, {
            text = p.name,
            callback = function() self:promptProviderKey(p.id, p.name) end,
        })
    end
    self.onboarding_menu = self:newMenu("onboarding_menu", {
        title = self.loc:t("onboarding_pick_provider") or "Choose your AI provider",
        item_table = items,
        is_borderless = true,
        width = Screen:getWidth(),
        height = Screen:getHeight(),
    })
    UIManager:show(self.onboarding_menu)
end

function M:promptProviderKey(provider, provider_name)
    local InputDialog = require("ui/widget/inputdialog")
    local ui_key = (self.ai_helper and self.ai_helper.settings) and self.ai_helper.settings[provider .. "_api_key"] or ""
    local input_dialog
    input_dialog = InputDialog:new{
        title = provider_name .. " API Key",
        input = ui_key,
        buttons = {
            {
                { text = self.loc:t("cancel"), callback = function() UIManager:close(input_dialog) end },
                { text = self.loc:t("save"), is_enter_default = true, callback = function()
                    local key = input_dialog:getInputText()
                    UIManager:close(input_dialog)
                    if key and #key > 0 then
                        self.ai_helper:saveSettings({
                            [provider .. "_api_key"] = key,
                            [provider .. "_use_ui_key"] = true,
                        })
                        self.ai_helper:init(self.path)
                        if self.onboarding_menu then UIManager:close(self.onboarding_menu); self.onboarding_menu = nil end
                        UIManager:show(require("ui/widget/infomessage"):new{
                            text = self.loc:t("onboarding_key_saved") or "API key saved. X-Ray is ready - fetch data via the X-Ray menu.",
                            timeout = 5,
                        })
                        UIManager:setDirty(nil, "ui")
                    end
                end },
            },
        },
    }
    UIManager:show(input_dialog)
    input_dialog:onShowKeyboard()
end
```

- [ ] **Step 5: `getProviderKeySubMenu` dedupen**

In `getProviderKeySubMenu` (nach dem `if provider:find("custom") then ... return end`-Block, Zeilen 3871-3894): den inline gebauten `InputDialog` für Nicht-Custom-Provider ersetzen durch:

```lua
                self:promptProviderKey(provider, provider_name)
```

(Der bisherige `input_dialog`-Block inkl. `UIManager:show(input_dialog)` und `input_dialog:onShowKeyboard()` entfällt. Verhaltensgleich: gleicher Titel, gleiche Buttons, gleiches saveSettings.)

- [ ] **Step 6: Loc-Keys synchronisieren**

Run: `python3.12 tools/sync_translations.py`
In `xray.koplugin/languages/en.po` die msgstr der 4 neuen msgids füllen:
- `onboarding_no_key` → `X-Ray needs an AI provider API key (one-time setup).\n\nGoogle Gemini offers a free tier (aistudio.google.com).\n\nSet it up now?`
- `onboarding_setup_now` → `Set up now`
- `onboarding_pick_provider` → `Choose your AI provider`
- `onboarding_key_saved` → `API key saved. X-Ray is ready - fetch data via the X-Ray menu.`
Run: `python3.12 tools/check_translations.py` — Erwartet: OK.

- [ ] **Step 7: Syntax + Tests grün**

Run: `luajit -bl xray.koplugin/xray_ui.lua > /dev/null && luajit -bl xray.koplugin/xray_fetch.lua > /dev/null && luajit tools/spec_runner.lua 2>&1 | tail -5`
Erwartet: beide neuen Tests PASS; `Failed` ≤ Baseline.

- [ ] **Step 8: Commit**

```bash
git add xray.koplugin/xray_fetch.lua xray.koplugin/xray_ui.lua xray.koplugin/languages/ spec/xray_fetch_spec.lua spec/xray_ui_spec.lua
git commit -m "feat: API-Key-Onboarding statt Sackgassen-Fehler; AI-Dupe-Check jetzt opt-in (spart 1 API-Call pro Fetch)"
```

---

### Task 9: Updater-Härtung — Config-Backup, %-Escaping, Zip-Test (Audit Cache-H3/H4/M1)

**Files:**
- Modify: `xray.koplugin/xray_updater.lua` (`_applyUpdate`/`doDownloadAndInstall`, neue Modul-Funktionen `M._injectValue`, `M.restoreConfigBackup`)
- Modify: `xray.koplugin/main.lua` (`init`, vor dem `AIHelper`-Init, Zeile ~108)
- Create: `spec/xray_updater_spec.lua`
- Modify: `tools/spec_runner.lua` (Spec registrieren)

**Interfaces:**
- Produces: `M._injectValue(content, field, value)` — ersetzt `field = ""` durch den Wert, `%`-sicher; `M.restoreConfigBackup(config_path)` — `config_path` optional (Default: `_plugin_dir .. "/xray_config.lua"`), stellt nach abgebrochenem Update die Keys aus `xray_config.lua.bak` wieder her, löscht das `.bak` immer.
- Consumes: bestehende `_plugin_dir`, `_httpGetToFile`, `_unzip`, `logger`.

- [ ] **Step 1: Failing Specs schreiben — neue Datei `spec/xray_updater_spec.lua`**

```lua
-- spec/xray_updater_spec.lua — Config-Erhalt und %-sichere Key-Injektion
require("spec.spec_helper")

package.loaded["ui/widget/confirmbox"] = package.loaded["ui/widget/confirmbox"]
    or { new = function(_, o) return o end }

local updater = require("xray_updater")

local function write(path, content)
    local f = io.open(path, "w")
    f:write(content)
    f:close()
end

describe("xray_updater hardening", function()
    it("_injectValue keeps percent signs literal", function()
        local out = updater._injectValue('custom1_endpoint = ""', "custom1_endpoint", "https://x.test/v1%2Fchat")
        assert.are.equal('custom1_endpoint = "https://x.test/v1%2Fchat"', out)
    end)

    it("_injectValue leaves content untouched for empty values", function()
        local content = 'gemini_api_key = ""'
        assert.are.equal(content, updater._injectValue(content, "gemini_api_key", ""))
        assert.are.equal(content, updater._injectValue(content, "gemini_api_key", nil))
    end)

    it("restoreConfigBackup restores keys after an interrupted update", function()
        local cfg = "/tmp/xray_spec_config.lua"
        local bak = cfg .. ".bak"
        write(cfg, 'return { gemini_api_key = "" }')
        write(bak, 'return { gemini_api_key = "SECRET" }')
        updater.restoreConfigBackup(cfg)
        local restored = dofile(cfg)
        assert.are.equal("SECRET", restored.gemini_api_key)
        assert.is_nil(io.open(bak, "r"))
        pcall(os.remove, cfg)
    end)

    it("restoreConfigBackup only cleans up when the live config still has keys", function()
        local cfg = "/tmp/xray_spec_config2.lua"
        local bak = cfg .. ".bak"
        write(cfg, 'return { gemini_api_key = "LIVE" }')
        write(bak, 'return { gemini_api_key = "OLD" }')
        updater.restoreConfigBackup(cfg)
        local live = dofile(cfg)
        assert.are.equal("LIVE", live.gemini_api_key)
        assert.is_nil(io.open(bak, "r"))
        pcall(os.remove, cfg)
    end)
end)
```

In `tools/spec_runner.lua` in die `specs`-Liste nach `"spec/xray_main_spec.lua"` eintragen: `"spec/xray_updater_spec.lua"`.

Run: `luajit tools/spec_runner.lua 2>&1 | grep -A2 "updater hardening"`
Erwartet: FAIL — `_injectValue`/`restoreConfigBackup` existieren nicht ("attempt to call a nil value"). Falls schon `require("xray_updater")` scheitert (nicht gefaktes Widget): fehlendes Fake analog zur `confirmbox`-Zeile oben im Spec ergänzen.

- [ ] **Step 2: `_injectValue` und `restoreConfigBackup` implementieren**

In `xray.koplugin/xray_updater.lua`, nach der `t(...)`-Helper-Funktion (Zeile 54) einfügen:

```lua
-- gsub-safe key injection: user values (custom endpoints/models) may contain
-- '%', which is a capture escape in gsub replacement strings.
function M._injectValue(content, field, value)
    if not value or value == "" then return content end
    local safe = value:gsub("%%", "%%%%")
    return content:gsub(field .. '%s*=%s*""', field .. ' = "' .. safe .. '"')
end

-- Recover from an update that died between unzip and key re-injection: the
-- .bak written before the unzip is then the only copy of the user's keys.
-- Always removes the .bak. config_path parameter exists for tests.
function M.restoreConfigBackup(config_path)
    config_path = config_path or (_plugin_dir .. "/xray_config.lua")
    local bak_path = config_path .. ".bak"
    local probe = io.open(bak_path, "r")
    if not probe then return false end
    probe:close()
    local ok_live, live = pcall(dofile, config_path)
    local has_key = ok_live and type(live) == "table" and (
        (live.gemini_api_key or "") ~= "" or (live.chatgpt_api_key or "") ~= "" or
        (live.deepseek_api_key or "") ~= "" or (live.claude_api_key or "") ~= "" or
        (live.custom1_api_key or "") ~= "" or (live.custom2_api_key or "") ~= "")
    if not has_key then
        local src = io.open(bak_path, "r")
        if src then
            local content = src:read("*a")
            src:close()
            local dst = io.open(config_path, "w")
            if dst then
                dst:write(content)
                dst:close()
                logger.info("xray updater: restored xray_config.lua from backup after interrupted update")
            end
        end
    end
    pcall(os.remove, bak_path)
    return true
end
```

- [ ] **Step 3: `doDownloadAndInstall` härten**

In `xray.koplugin/xray_updater.lua`, innerhalb `doDownloadAndInstall`:

(a) Direkt nach dem `saved_keys`-Block (Zeile 385) einfügen:

```lua
        -- Backup the live config: between unzip (overwrites it) and the key
        -- re-injection below, the RAM table is otherwise the only key copy.
        local bak_path = config_path .. ".bak"
        pcall(function()
            local src = io.open(config_path, "r")
            if src then
                local content = src:read("*a")
                src:close()
                local dst = io.open(bak_path, "w")
                if dst then dst:write(content); dst:close() end
            end
        end)
```

(b) Zwischen Download (Zeile 391) und Unzip (Zeile 394) einen Integritätstest einfügen:

```lua
        -- Reject truncated downloads before extracting over the live install.
        -- ponytail: no staged install; zip -t + config backup cover the
        -- realistic failure (partial download). Upgrade path: unzip to a
        -- staging dir + directory swap if half-written installs ever show up.
        local test_ret = os.execute(string.format("unzip -tqq %q >/dev/null 2>&1", tmp_zip))
        if test_ret ~= 0 and test_ret ~= true then
            os.remove(tmp_zip)
            return { success = false, stage = "download", err = "corrupted download (zip integrity test failed)" }
        end
```

(c) Die zehn `content:gsub('..._key%s*=%s*""', ...)`-Blöcke (Zeilen 412-444) ersetzen durch:

```lua
                content = M._injectValue(content, "gemini_api_key", saved_keys.gemini)
                content = M._injectValue(content, "chatgpt_api_key", saved_keys.chatgpt)
                content = M._injectValue(content, "deepseek_api_key", saved_keys.deepseek)
                content = M._injectValue(content, "claude_api_key", saved_keys.claude)
                content = M._injectValue(content, "custom1_api_key", saved_keys.custom1_key)
                content = M._injectValue(content, "custom1_endpoint", saved_keys.custom1_endpoint)
                content = M._injectValue(content, "custom1_model", saved_keys.custom1_model)
                content = M._injectValue(content, "custom2_api_key", saved_keys.custom2_key)
                content = M._injectValue(content, "custom2_endpoint", saved_keys.custom2_endpoint)
                content = M._injectValue(content, "custom2_model", saved_keys.custom2_model)
```

(d) Nach dem erfolgreichen Zurückschreiben (nach `outh:close()`, Zeile 449) und ebenso direkt vor `return { success = true }` sicherstellen, dass das Backup entfernt wird — konkret vor `return { success = true }` einfügen:

```lua
        pcall(os.remove, bak_path)
```

- [ ] **Step 4: Restore-Hook beim Plugin-Start**

`xray.koplugin/main.lua`, in `init()` direkt VOR `local AIHelper = require(plugin_path .. "xray_aihelper")` (Zeile 108) einfügen:

```lua
    -- Recover API keys if a previous OTA update died mid-install
    pcall(function()
        require(plugin_path .. "xray_updater").restoreConfigBackup()
    end)
```

- [ ] **Step 5: Syntax + Tests grün**

Run: `luajit -bl xray.koplugin/xray_updater.lua > /dev/null && luajit -bl xray.koplugin/main.lua > /dev/null && luajit tools/spec_runner.lua 2>&1 | tail -5`
Erwartet: 4 neue Updater-Tests PASS; `Failed` ≤ Baseline.

- [ ] **Step 6: Commit**

```bash
git add xray.koplugin/xray_updater.lua xray.koplugin/main.lua spec/xray_updater_spec.lua tools/spec_runner.lua
git commit -m "fix: OTA-Update verliert keine API-Keys mehr (.bak+Restore), %-sichere Injektion, Zip-Integritaetstest"
```

---

### Task 10: Gesamtverifikation + Version 26.7.4

**Files:**
- Modify: `xray.koplugin/_meta.lua` (Version)

**Interfaces:**
- Consumes: alle vorherigen Tasks committed.

- [ ] **Step 1: Voller Testlauf + Syntax über alle geänderten Dateien**

```bash
for f in xray.koplugin/main.lua xray.koplugin/xray_fetch.lua xray.koplugin/xray_prefetch.lua xray.koplugin/xray_aihelper.lua xray.koplugin/xray_cachemanager.lua xray.koplugin/xray_ui.lua xray.koplugin/xray_updater.lua; do luajit -bl "$f" > /dev/null || echo "SYNTAX FAIL: $f"; done
luajit tools/spec_runner.lua 2>&1 | tail -8
python3.12 tools/check_translations.py
```

Erwartet: keine SYNTAX FAIL-Zeile; `Passed` = Baseline-N + Anzahl neuer Tests; `Failed` ≤ Baseline-M; Translations OK.

- [ ] **Step 2: Git-Status sauber prüfen**

Run: `git status --short && git log --oneline -12`
Erwartet: keine uncommitteten Änderungen außer ggf. `_meta.lua`-Bump aus Step 3; 9 Task-Commits sichtbar.

- [ ] **Step 3: Version bumpen und Release anstoßen (CHECKPOINT — Nutzer fragen)**

Das Projekt versioniert CalVer-ähnlich (`YY.M.PATCH`, aktuell `26.7.3` in `xray.koplugin/_meta.lua`). `python3.12 tools/release.py 26.7.4` bumpt `_meta.lua`, committet `Release 26.7.4`, taggt und **pusht** (der Push triggert eine **Draft**-GitHub-Release). Vor diesem Schritt beim Nutzer rückfragen, ob Release oder nur lokaler Bump gewünscht ist. Bei "nur Bump": Version in `_meta.lua` manuell auf `26.7.4` setzen und committen:

```bash
git add xray.koplugin/_meta.lua
git commit -m "chore: bump version to 26.7.4"
```
