# Companion-file X-Ray Import — Implementation Plan (koreader-xray-plugin)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the KOReader importer adopt X-Ray data from a **companion file next to the book** (`<book_path> .. ".xray.json"`), so a book can carry X-Ray without modifying the EPUB — preserving KOReader reading statistics. The embedded `xray/xray.json` stays as a fallback.

**Architecture:** Add a small companion reader (`io.open` + `json.decode`, no unzip) and a caller-level source selector that tries **companion → embedded**, gating each source and **falling through** to embedded if the companion is missing or fails the gate. Both the automatic (`onReaderReady`) and manual (menu) import paths use the selector. No schema change, no new UI strings.

**Tech Stack:** Lua 5.1 / LuaJIT, KOReader plugin (`xray.koplugin/`). Tests via the repo's custom busted-compatible runner. No new third-party deps.

## Global Constraints

- **Deployable artifact is `xray.koplugin/`**, copied verbatim into KOReader; no build step. Defend against old KOReader — keep existing `pcall`-wrapped requires / shims; don't assume modern APIs.
- **Tests:** `luajit tools/spec_runner.lua`. The spec list is **hardcoded in `tools/spec_runner.lua`** — a new `spec/*_spec.lua` file MUST be added to that list or it never runs. The custom runner **replaces `assert`**: bare `assert(cond)` does NOT work. Only these matchers exist: `assert.is_true/is_false/is_nil/is_not_nil/is_table/is_string/is_number/is_boolean/truthy/falsy`, `assert.are.equal`, `assert.are.same`, `assert.are_not.equal`, `assert.equals`, `assert.same`. `spec/spec_helper.lua` fakes the KOReader env (`package.loaded[...]`); mocks live under `spec/mocks/`. Read an existing `spec/*_spec.lua` before writing tests and match its harness.
- **Mixin model:** `xray_import.lua` is one of six mixins merged onto the single `XRayPlugin` `self`. Method names must be unique across the six; a collision silently overwrites. New methods (`_readCompanionXray`, `_selectXraySource`) must not clash with any existing method in the six mixin files (grep before naming).
- **No new localization strings** — reuse the existing keys `import_rejected`, `import_no_data`, `import_replace_confirm`. (If you somehow add a `loc:t("...")` key, you MUST run `python3 tools/sync_translations.py`; avoid it.)
- **Schema / two-repo:** the companion carries the **byte-identical** `xray.json` the desktop already produces. **No `schema_version` bump**, no change to `M.SUPPORTED_SCHEMA` or `M:_gateImport`. Future-version rejection must keep working for the companion (it does, because the gate runs in the caller on any doc).
- **Cross-repo filename contract (pinned):** companion path is **append-form** `book_path .. ".xray.json"` (e.g. `/mnt/onboard/Book.epub` → `/mnt/onboard/Book.epub.xray.json`). The desktop repo (`calibre-xray`, Spec A) writes exactly `os.path.basename(epub) + ".xray.json"`. Do NOT use `gsub("%.epub$", …)` — the entry guard admits books case-insensitively (`.epub`/`.EPUB`), and a case-sensitive gsub would misfire on `Book.EPUB` and point at the EPUB itself.
- **Versioning (CalVer `YY.M.PATCH` in `xray.koplugin/_meta.lua` + README badge):** a bump is routine after this feature, but **local only — never tag, never push.** Releases happen only on explicit user instruction. Never stage `xray.koplugin/xray_config.lua`; never `git add -A`/`.`/`-a` in this repo.
- **Device gotcha (informational):** the companion reader uses `io.open`, NOT `unzip`, so it has no BusyBox `-d`-doesn't-create-dir hazard and IS unit-testable off-device. The embedded fallback still shells out to `unzip` (unchanged) and remains device-only-verifiable.

---

## Orientation (read before Task 1)

Open `xray.koplugin/xray_import.lua` and read these exact spots (line numbers approximate — grep to confirm):

- `M:_gateImport(doc, props)` (~45–61): pure gate. Returns `nil` if OK, else a reason string. Checks: `type(doc)=="table"`, `doc.schema_version` present and `<= M.SUPPORTED_SCHEMA`, `#doc.checkpoints > 0`, and a lenient title match against `props.title`. **Unchanged by this plan.**
- `M:_readEmbeddedXray(book_path)` (~496–538): zip-probe (`M._zipHasEntry`) → `unzip` into `<book>.sdr/xray_import_tmp` → `json.decode` → returns a table or `nil`. Note the empty-string guard `if not raw or raw == "" then return nil end` (~531) — the companion reader mirrors it. **Unchanged.**
- `M:maybeImportEmbeddedXray()` (~541–562): automatic path. Guards (`prefetch_active`/`bg_fetch_active`, `book_path` ends `.epub` case-insensitively) → `_readEmbeddedXray` → `_gateImport` → on reason: log + `InfoMessage` (`import_rejected`) + return; else `self:importEmbeddedXray(doc_json)`.
- `M:manualImportEmbeddedXray()` (~567–622): manual menu path. Like the auto path but: shows `import_no_data` when no doc; and if `self.book_data` already exists, shows a `ConfirmBox` (`import_replace_confirm`) before importing (this path runs even when a cache exists).
- Caller in `main.lua`: `if not self.book_data and self.maybeImportEmbeddedXray then ... self:maybeImportEmbeddedXray() ... end` (~405) — automatic import only fires when there is no cache yet. **No change needed here**; the companion is picked up inside `maybeImportEmbeddedXray`. Manual menu wiring is `main.lua:~898`. **No change needed.**
- Top-of-file requires: `DocSettings`, `UIManager`, `InfoMessage`, and `json` via `pcall(require, "json")` inside the readers. `self:log(...)` is the gated logger.

**Precedence reality (important, drives the design):** automatic import fires only `if not self.book_data` (`main.lua:405`), and `book_data` is loaded from the `.sdr` cache on every open. So a companion "wins" only on the **first cacheless open**. Once ANY source has imported and written the `.sdr` cache, that cache shadows both sources on later opens — a companion dropped next to an **already-read** book is adopted only via the **manual menu** (which this plan teaches to read the companion). Automatic override of a stale cache (mtime/version compare) is explicitly **out of scope**.

---

## Task 1: Companion reader `_readCompanionXray`

**Files:**
- Modify: `xray.koplugin/xray_import.lua` (add one method near `_readEmbeddedXray`)
- Modify — **APPEND ONLY**: `spec/xray_import_spec.lua`. ⚠️ This file **already exists (~1185 lines, ~50 tests)**. **Read it first; NEVER `Write` over it** — that would delete the entire embedded-path regression suite. Append new `describe(...)` blocks as siblings of the existing top-level `describe("xray_import", …)`.
- (NO change to `tools/spec_runner.lua` — `spec/xray_import_spec.lua` is **already registered** at ~line 160. Adding it again creates a broken duplicate entry.)

**Interfaces:**
- Produces: `M:_readCompanionXray(book_path) -> table | nil`. Reads `book_path .. ".xray.json"` via `io.open`, returns the decoded table, or `nil` if the file is absent / empty / not valid JSON. Does NOT gate (the caller gates, exactly like the embedded path).

**Harness facts (verified — do not re-derive):**
- The module is required as `require("xray_import")` — **NOT** `require("xray.koplugin.xray_import")` (the dotted path fails; `xray.koplugin` is a literal-dot directory name). See `spec/xray_import_spec.lua:3`.
- `spec/spec_helper.lua` installs a **fake `package.loaded["json"]` whose `decode` returns `{}` for ANY input and never errors** (real `dkjson`/`json` is absent off-device). So any test that needs real JSON decoding MUST locally stub `package.loaded["json"]` and restore it — the existing `_readEmbeddedXray` tests (~spec line 1150+) already do exactly this; copy that pattern.
- Reuse the existing plugin factory the file already uses (grep the file for `mock_plugin` / `createMockPlugin` / `prepared` and mirror a neighboring test's construction). Task 2 wraps that factory in a thin `newPlugin(overrides)` helper only to set fake methods on a genuine instance — it does NOT bypass the real factory.
- Allowed assert matchers only: `is_true/is_false/is_nil/is_not_nil/is_table/is_string/is_number/is_boolean/truthy/falsy`, `are.equal`, `are.same`, `are_not.equal`, `equals`, `same`.

- [ ] **Step 1: Append the first failing tests (with a local `json` stub)**

Append to `spec/xray_import_spec.lua`. Construct the plugin with the file's existing factory (shown here as `mock_plugin()` — replace with the real helper name you find). Stub `json` locally so decode actually works:

```lua
describe("companion xray import", function()
    local orig_json
    before_each(function() orig_json = package.loaded["json"] end)
    after_each(function() package.loaded["json"] = orig_json end)

    it("reads a valid companion file next to the book", function()
        package.loaded["json"] = { decode = function() return { schema_version = 1, checkpoints = {{}} } end }
        local path = os.tmpname()
        local companion = path .. ".xray.json"
        local fh = io.open(companion, "w"); fh:write("{...}"); fh:close()

        local p = mock_plugin()             -- <- the file's existing factory
        local doc = p:_readCompanionXray(path)
        os.remove(companion)

        assert.is_not_nil(doc)
        assert.are.equal(1, doc.schema_version)
    end)

    it("returns nil when no companion file exists", function()
        local p = mock_plugin()
        assert.is_nil(p:_readCompanionXray("/no/such/book.epub"))
    end)

    it("returns nil for a malformed companion (decode errors)", function()
        package.loaded["json"] = { decode = function() error("bad json") end }
        local path = os.tmpname()
        local companion = path .. ".xray.json"
        local fh = io.open(companion, "w"); fh:write("not json {"); fh:close()
        local p = mock_plugin()
        local doc = p:_readCompanionXray(path)
        os.remove(companion)
        assert.is_nil(doc)
    end)
end)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `luajit tools/spec_runner.lua`
Expected: the three companion tests FAIL (method `_readCompanionXray` is nil). The existing ~50 tests in the file must still PASS (you only appended).

- [ ] **Step 3: Implement `_readCompanionXray`**

Add near `_readEmbeddedXray` in `xray_import.lua`:

```lua
-- Companion file next to the book: `<book_path>.xray.json` (append-form,
-- case-proof, format-agnostic; the cross-repo contract with calibre-xray).
-- Same document the desktop would embed, but without touching the EPUB, so
-- KOReader's reading statistics (keyed by the file's partial digest) survive.
-- Plain file read -- no unzip, no BusyBox hazard. Does NOT gate; the caller does.
function M:_readCompanionXray(book_path)
    if not book_path then return nil end
    local fh = io.open(book_path .. ".xray.json", "r")
    if not fh then return nil end
    local raw = fh:read("*a")
    fh:close()
    if not raw or raw == "" then return nil end

    local ok_json, json = pcall(require, "json")
    if not ok_json or type(json) ~= "table" or not json.decode then return nil end
    local ok, doc = pcall(json.decode, raw)
    if not ok or type(doc) ~= "table" then
        self:log("XRayPlugin: companion xray.json is not valid JSON")
        return nil
    end
    return doc
end
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `luajit tools/spec_runner.lua`
Expected: the three companion-reader tests PASS; all previously-passing specs still PASS.

- [ ] **Step 5: Commit**

```bash
git add xray.koplugin/xray_import.lua spec/xray_import_spec.lua
git commit -m "feat(import): add companion xray.json reader (no unzip, gate-free)"
```

---

## Task 2: Source selector `_selectXraySource` (companion → embedded, gate-fallback)

**Files:**
- Modify: `xray.koplugin/xray_import.lua` (add one method)
- Modify: `spec/xray_import_spec.lua` (add selection tests)

**Interfaces:**
- Produces: `M:_selectXraySource(book_path, props) -> doc_json|nil, reason|nil, had_source(bool)`.
  - Tries companion first: if it reads to a table AND `_gateImport` passes → returns `(doc, nil, true)`.
  - If the companion exists but fails the gate → tries embedded; if embedded passes → returns `(embedded, nil, true)`; else returns `(nil, embedded_reason_or_companion_reason, true)`.
  - If no companion: tries embedded; passes → `(doc, nil, true)`; fails → `(nil, reason, true)`; absent → `(nil, nil, false)`.
  - **Key property (grill P1):** a companion that parses but fails the gate must NOT abort — embedded is still tried.

- [ ] **Step 1: Write failing selection tests (inject fake readers + gate)**

Append to `spec/xray_import_spec.lua`. The snippets use `newPlugin{overrides}` for brevity — implement it once, over the file's REAL factory, so fakes are set as fields on a genuine instance:

```lua
local function newPlugin(overrides)
    local p = mock_plugin()                 -- <- the file's existing factory
    for k, v in pairs(overrides or {}) do p[k] = v end
    return p
end
```

```lua
describe("xray source selection", function()
    local GOOD = { schema_version = 1, checkpoints = {{}}, book_fingerprint = { title = "T" } }
    local BADGATE = { schema_version = 1, checkpoints = {} }  -- 0 checkpoints -> gate rejects

    it("prefers a valid companion over embedded", function()
        local p = newPlugin{
            _readCompanionXray = function() return GOOD end,
            _readEmbeddedXray  = function() error("embedded must not be read") end,
            _gateImport        = function() return nil end,
        }
        local doc, reason, had = p:_selectXraySource("/b.epub", {})
        assert.are.equal(GOOD, doc)
        assert.is_nil(reason)
        assert.is_true(had)
    end)

    it("falls through to embedded when the companion fails the gate", function()
        local p = newPlugin{
            _readCompanionXray = function() return BADGATE end,
            _readEmbeddedXray  = function() return GOOD end,
            _gateImport = function(self, doc)
                if doc == BADGATE then return "no checkpoints" end
                return nil
            end,
        }
        local doc, reason, had = p:_selectXraySource("/b.epub", {})
        assert.are.equal(GOOD, doc)     -- embedded adopted, not aborted
        assert.is_nil(reason)
        assert.is_true(had)
    end)

    it("reports no source when neither exists", function()
        local p = newPlugin{
            _readCompanionXray = function() return nil end,
            _readEmbeddedXray  = function() return nil end,
        }
        local doc, reason, had = p:_selectXraySource("/b.epub", {})
        assert.is_nil(doc)
        assert.is_false(had)
    end)

    it("returns a reason when the only source fails the gate", function()
        local p = newPlugin{
            _readCompanionXray = function() return nil end,
            _readEmbeddedXray  = function() return BADGATE end,
            _gateImport = function() return "no checkpoints" end,
        }
        local doc, reason, had = p:_selectXraySource("/b.epub", {})
        assert.is_nil(doc)
        assert.are.equal("no checkpoints", reason)
        assert.is_true(had)
    end)
end)
```

- [ ] **Step 2: Run to verify failure**

Run: `luajit tools/spec_runner.lua`
Expected: the four selection tests FAIL (`_selectXraySource` nil).

- [ ] **Step 3: Implement `_selectXraySource`**

```lua
-- Ordered source selection: companion first, then embedded, gating each and
-- falling through on failure (a parseable-but-mismatched companion must never
-- shadow a valid embedded doc). Returns (doc|nil, reason|nil, had_source).
function M:_selectXraySource(book_path, props)
    local companion = self:_readCompanionXray(book_path)
    local companion_reason
    if companion then
        companion_reason = self:_gateImport(companion, props)
        if not companion_reason then return companion, nil, true end
    end

    local embedded = self:_readEmbeddedXray(book_path)
    if embedded then
        local reason = self:_gateImport(embedded, props)
        if not reason then return embedded, nil, true end
        return nil, reason, true
    end

    if companion then return nil, companion_reason, true end
    return nil, nil, false
end
```

- [ ] **Step 4: Run to verify pass**

Run: `luajit tools/spec_runner.lua`
Expected: the four selection tests PASS; everything else still PASS.

- [ ] **Step 5: Commit**

```bash
git add xray.koplugin/xray_import.lua spec/xray_import_spec.lua
git commit -m "feat(import): source selector tries companion then embedded with gate fallback"
```

---

## Task 3: Wire both import callers to the selector

**Files:**
- Modify: `xray.koplugin/xray_import.lua` (`maybeImportEmbeddedXray`, `manualImportEmbeddedXray`)
- Modify: `spec/xray_import_spec.lua` (caller-level tests)

**Interfaces:**
- Consumes: `_selectXraySource` (Task 2), `importEmbeddedXray` (existing).
- Produces: both callers adopt a companion when present/valid; manual path adopts a companion even when a cache exists (with the existing confirm).

- [ ] **Step 1: Write failing caller tests**

Add to `spec/xray_import_spec.lua` (use `_G.ui_tracker` per `spec_helper` to assert import happened; match how `xray_main_spec` checks `importEmbeddedXray` — you may instead spy on `importEmbeddedXray` by injecting it):

```lua
describe("callers adopt companion", function()
    local GOOD = { schema_version = 1, checkpoints = {{}}, book_fingerprint = { title = "T" } }

    local function pluginWithDoc(book_data)
        local imported = {}
        local p = newPlugin{
            book_data = book_data,
            ui = { document = { file = "/b.epub", getProps = function() return { title = "T" } end } },
            loc = { t = function(_, k) return k end },
            _selectXraySource = function() return GOOD, nil, true end,
            importEmbeddedXray = function(self, doc) imported.doc = doc end,
        }
        return p, imported
    end

    it("auto path imports the selected companion", function()
        local p, imported = pluginWithDoc(nil)
        p:maybeImportEmbeddedXray()
        assert.are.equal(GOOD, imported.doc)
    end)

    it("manual path imports the selected companion (no existing cache)", function()
        local p, imported = pluginWithDoc(nil)
        p:manualImportEmbeddedXray()
        assert.are.equal(GOOD, imported.doc)
    end)
end)
```

- [ ] **Step 2: Run to verify failure**

Run: `luajit tools/spec_runner.lua`
Expected: caller tests FAIL (the callers still call `_readEmbeddedXray` directly, so injecting `_selectXraySource` has no effect yet — the `imported.doc` stays nil, OR the real `_readEmbeddedXray` runs and returns nil).

- [ ] **Step 3: Refactor `maybeImportEmbeddedXray` to use the selector**

Replace its body (keep the entry guards) with:

```lua
function M:maybeImportEmbeddedXray()
    if self.prefetch_active or self.bg_fetch_active then return end
    local book_path = self.ui and self.ui.document and self.ui.document.file
    if not book_path or not book_path:lower():match("%.epub$") then return end

    local props = (self.ui.document.getProps and self.ui.document:getProps()) or {}
    local doc_json, reason = self:_selectXraySource(book_path, props)
    if not doc_json then
        if reason then
            self:log("XRayPlugin: X-Ray data rejected -- " .. reason)
            UIManager:show(InfoMessage:new{
                text = self.loc:t("import_rejected") or "The embedded X-Ray data does not match this book.",
                timeout = 4,
            })
        end
        return
    end
    self:importEmbeddedXray(doc_json)
end
```

- [ ] **Step 4: Refactor `manualImportEmbeddedXray` to use the selector**

Replace its read-and-gate head (keep the `prefetch_active` guard, the `import_no_data` message, the `book_data` ConfirmBox flow, and the final `importEmbeddedXray`) with a `_selectXraySource` call:

```lua
function M:manualImportEmbeddedXray()
    if self.prefetch_active or self.bg_fetch_active then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("prefetch_busy") or "A fetch is already running. Try again in a moment.",
            timeout = 4,
        })
        return
    end
    local book_path = self.ui and self.ui.document and self.ui.document.file
    if not book_path or not book_path:lower():match("%.epub$") then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("import_no_data") or "No calibre X-Ray data found in this book.",
            timeout = 4,
        })
        return
    end
    local props = (self.ui.document.getProps and self.ui.document:getProps()) or {}
    local doc_json, reason = self:_selectXraySource(book_path, props)
    if not doc_json then
        if reason then self:log("XRayPlugin: manual import rejected -- " .. reason) end
        UIManager:show(InfoMessage:new{
            text = (reason and (self.loc:t("import_rejected") or "The embedded X-Ray data does not match this book."))
                   or (self.loc:t("import_no_data") or "No calibre X-Ray data found in this book."),
            timeout = 4,
        })
        return
    end
    if self.book_data then
        local ConfirmBox = require("ui/widget/confirmbox")
        local confirm
        confirm = ConfirmBox:new{
            text = self.loc:t("import_replace_confirm")
                or "This replaces the existing X-Ray data for this book (characters, locations, terms, snapshots). Continue?",
            ok_text = self.loc:t("menu_import_calibre") or "Import calibre X-Ray",
            cancel_text = self.loc:t("cancel") or "Cancel",
            ok_callback = function()
                if self.prefetch_active or self.bg_fetch_active then
                    UIManager:close(confirm)
                    UIManager:show(InfoMessage:new{
                        text = self.loc:t("prefetch_busy") or "A fetch is already running. Try again in a moment.",
                        timeout = 4,
                    })
                    return
                end
                UIManager:close(confirm)
                self:importEmbeddedXray(doc_json)
            end,
        }
        UIManager:show(confirm)
        return
    end
    self:importEmbeddedXray(doc_json)
end
```

(The message already distinguishes "rejected" from "no source" via `reason`, so the selector's third return value `had` is not needed here.)

- [ ] **Step 5: Run tests to verify pass**

Run: `luajit tools/spec_runner.lua`
Expected: caller tests PASS; ALL specs green. If any pre-existing embedded-path spec broke, it likely asserted the old direct `_readEmbeddedXray` call — update it to the selector, preserving the same observable behavior (embedded still adopted when no companion).

- [ ] **Step 6: Syntax check + commit**

```bash
python3 tools/check_syntax.py xray.koplugin
git add xray.koplugin/xray_import.lua spec/xray_import_spec.lua
git commit -m "feat(import): auto + manual import adopt companion xray.json (embedded fallback)"
```

---

## Task 4: Version bump (local only) + on-device acceptance

**Files:**
- Modify: `xray.koplugin/_meta.lua` (version), `README.md` (version badge)

- [ ] **Step 1: Bump the CalVer version**

Set the same new `YY.M.PATCH` in `xray.koplugin/_meta.lua` (`version`) and the README badge. Commit **locally only — do NOT tag, do NOT push**:

```bash
git add xray.koplugin/_meta.lua README.md
git commit -m "chore: bump version to <YY.M.PATCH>"
```

- [ ] **Step 2: On-device acceptance test (manual, the stats-safety gate)**

This is the acceptance criterion the unit tests cannot cover (the plugin has zero coupling to `statistics.sqlite3`; stats identity is KOReader-core). On a real Kobo/Kindle (see the device setup notes):
1. Take an **already-read** book (has reading progress AND accumulated reading time).
2. Place `<book>.epub.xray.json` (a valid companion, matching title) next to it. Do NOT modify the EPUB.
3. Open the book, use the X-Ray menu → manual import (auto won't fire once a cache exists).
4. Confirm: X-Ray data appears; reading **progress** is intact; reading-**time statistics** are intact (the whole point). Also verify the embedded fallback still works on a book that has no companion but does have embedded `xray/xray.json`.

Record the result. If reading-time stats do NOT survive even with an untouched book file, that points to a KOReader-core behavior to investigate separately — but it does not regress anything here.

---

## Done criteria
- `luajit tools/spec_runner.lua` fully green, with `spec/xray_import_spec.lua` registered and passing.
- `python3 tools/check_syntax.py xray.koplugin` clean.
- Companion (`<book>.epub.xray.json`) is adopted by both auto and manual import; a missing/malformed/mismatched companion falls through to the embedded path unchanged; no schema bump; no new loc strings.
- Version bumped locally only (no tag, no push).
- On-device acceptance recorded.
