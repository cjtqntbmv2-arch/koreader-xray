-- X-Ray dictionary/highlight lookup: hooks KOReader's word-lookup UI so an
-- exact name/alias match in the CURRENT snapshot opens the matching detail
-- card. Search is deliberately snapshot-scoped, never document-wide -- a
-- document-wide search would leak names/aliases the reader hasn't reached
-- yet through the dictionary button, defeating the point of checkpoint-gated
-- snapshots (task brief; project CLAUDE.md "Spoiler-Invarianten D4").
--
-- Matching is exact-name/exact-alias only, case-insensitive. The old
-- xray_lookupmanager.lua's multi-tier contains/fuzzy scoring (261 lines) is
-- deliberately not ported -- explicitly out of scope per the task brief.

local UIManager = require("ui/uimanager")
local InfoMessage = require("ui/widget/infomessage")
local ButtonDialog = require("ui/widget/buttondialog")
local logger = require("logger")
local _ = require("xray_i18n")
local XRayDoc = require("xray_doc")
local XRayUI = require("xray_ui")

local XRayLookup = {}

local CATEGORIES = { "characters", "locations", "terms", "historical_figures" }

-- Trims leading/trailing non-word characters and lowercases, so a selection
-- like "Frodo." or a dict popup's punctuation-wrapped word still matches the
-- stored name "Frodo". Ported verbatim from xray_lookupmanager.lua:23-28
-- (`normalize`) -- still an exact match, just tolerant of surrounding
-- punctuation, unlike the contains-scoring around it that this rebuild drops.
local function normalize(text)
    if type(text) ~= "string" or text == "" then return "" end
    return text:gsub("^[^%w]+", ""):gsub("[^%w]+$", ""):lower()
end

-- The word to look up from a dictionary popup: the reader's QUERY, never the
-- headword the dictionary matched it to.
--
-- KOReader's DictQuickLookup carries both. `word` is the original query
-- (readerdictionary.lua sets it under the comment "original lookup word");
-- `lookupword`/`displayword` hold the entry that was found, which on a fuzzy
-- match is a different word in a different language -- a German book with an
-- English-German dictionary installed turns "Frodo" into the entry "brood".
-- KOReader itself shows the difference ("(query: Frodo)" under the headword).
-- Matching X-Ray names against the headword can therefore only ever miss.
--
-- No fallback to the headword when the query is empty: a whitespace-only
-- manual lookup trims to "", and searching for the dictionary's artefact
-- instead would turn "nothing to look up" into a confident wrong answer.
function XRayLookup.wordFromDictPopup(dict_popup)
    local word = dict_popup and dict_popup.word
    if type(word) ~= "string" then return nil end
    word = word:gsub("^%s+", ""):gsub("%s+$", "")
    if word == "" then return nil end
    return word
end

local function matchesEntry(entry, query)
    if normalize(entry.name) == query then return true end
    if type(entry.aliases) == "table" then
        for _unused, alias in ipairs(entry.aliases) do
            if type(alias) == "string" and normalize(alias) == query then return true end
        end
    end
    return false
end

-- snapshot: {characters=, locations=, terms=, historical_figures=} as
-- returned by XRayDoc.snapshot -- never a document's full entity lists.
-- Returns a list of {entry=, category=}, possibly empty.
function XRayLookup.find(snapshot, word)
    local results = {}
    if type(snapshot) ~= "table" or type(word) ~= "string" then return results end
    local query = normalize(word)
    if query == "" then return results end

    for _unused, category in ipairs(CATEGORIES) do
        local list = snapshot[category]
        if type(list) == "table" then
            for _unused, entry in ipairs(list) do
                if type(entry) == "table" and matchesEntry(entry, query) then
                    table.insert(results, { entry = entry, category = category })
                end
            end
        end
    end
    return results
end

local function showInfo(text)
    UIManager:show(InfoMessage:new{ text = text, timeout = 3 })
end

-- Same "never fall back to a spoilery default" rule as xray_ui.lua's
-- showNotYetAvailable; kept as a separate copy here rather than a shared
-- export because the two call sites present it in different widgets
-- (InfoMessage toast vs. list/status screen) and the duplication is a few
-- lines, not a maintained contract.
local function showNotYetAvailable(doc)
    local ok, first_pct = pcall(XRayDoc.firstAvailablePercent, doc)
    local text = (ok and first_pct)
        and string.format(_("X-Ray data available from %d%%."), first_pct)
        or _("X-Ray data is not available yet.")
    showInfo(text)
end

-- Multiple exact hits (e.g. a name shared across categories, or an alias
-- that collides with another entry's name): let the reader disambiguate,
-- same ButtonDialog-of-rows pattern as the old xray_lookupmanager.lua:186-222.
local function showPicker(results, word)
    local buttons = {}
    local dialog
    for _unused, result in ipairs(results) do
        table.insert(buttons, {
            {
                text = result.entry.name or "?",
                callback = function()
                    UIManager:close(dialog)
                    XRayUI.showEntry(result.entry, result.category)
                end,
            }
        })
    end
    table.insert(buttons, {
        { text = _("Close"), callback = function() UIManager:close(dialog) end }
    })
    dialog = ButtonDialog:new{
        title = string.format(_("Multiple matches for '%s'"), (word or ""):sub(1, 30)),
        buttons = buttons,
    }
    UIManager:show(dialog)
end

-- Resolves the reader's current position down to a snapshot and searches it.
-- Shared by both hooks installed below so the "load -> position ->
-- checkpoint -> snapshot -> search" sequence exists exactly once, and so a
-- word looked up right after a page turn always reflects that page's
-- position, not whatever was current when setup() ran.
local function performLookup(plugin, word)
    local ok, err = pcall(function()
        if type(word) ~= "string" or word == "" then return end
        local ui = plugin.ui
        if not ui then return end

        local doc, load_err = XRayDoc.load(ui)
        if not doc then
            showInfo(load_err and ("X-Ray: " .. load_err) or _("X-Ray: no data available for this book."))
            return
        end

        local pct = XRayDoc.position(ui)
        local cp_idx = pct and XRayDoc.selectCheckpoint(doc, pct)
        if not cp_idx then
            showNotYetAvailable(doc)
            return
        end

        local snapshot = XRayDoc.snapshot(doc, cp_idx)
        local results = XRayLookup.find(snapshot, word)

        if #results == 0 then
            showInfo(string.format(_("No X-Ray data found for '%s'."), word:sub(1, 30)))
        elseif #results == 1 then
            XRayUI.showEntry(results[1].entry, results[1].category)
        else
            showPicker(results, word)
        end
    end)
    if not ok then
        logger.warn("XRayLookup: lookup failed: " .. tostring(err))
    end
end

-- Closes the widget that triggered the lookup (highlight dialog or dict
-- popup) and clears the text selection before showing our own UI --
-- otherwise KOReader's own dictionary popup can re-assert itself over ours
-- (observed and fixed in the old plugin: main.lua:183-198).
local function closeAndClearSelection(ui, widget)
    if widget then
        pcall(function()
            if widget.onClose then widget:onClose() end
        end)
        pcall(function() UIManager:close(widget) end)
    end
    if ui and ui.handleEvent then
        local ok_event, Event = pcall(require, "ui/event")
        if ok_event then
            pcall(function() ui:handleEvent(Event:new("ClearSelection")) end)
        end
    end
end

-- Installs both lookup entry points on `plugin.ui`. No-ops entirely when the
-- plugin's one setting (dictionary integration) is off.
--
-- ASSUMPTION: `plugin.dict_integration_enabled` -- main.lua doesn't exist
-- yet, so this exact field name isn't attested anywhere; it's this module's
-- best guess at how the caller exposes the setting on the plugin instance.
-- Checked as "== false" (opt-out) rather than truthy (opt-in) so that if the
-- real field name turns out different, hooks still install instead of
-- silently never installing.
function XRayLookup.setup(plugin)
    if not plugin or plugin.dict_integration_enabled == false then
        return
    end
    local ui = plugin.ui
    if not ui then return end
    -- What actually got installed, for the diagnostics screen. Both blocks
    -- below sit in pcall guards and skip silently on a build that lacks the
    -- API -- without recording it, "no X-Ray button" and "X-Ray button nobody
    -- pressed" look identical from the outside.
    plugin.hooks = plugin.hooks or {}

    -- Highlight dialog: long-press on an existing highlight (attested hook
    -- point: xray.koplugin/main.lua:172-205).
    pcall(function()
        if ui.highlight and type(ui.highlight.addToHighlightDialog) == "function" then
            ui.highlight:addToHighlightDialog("xray_lookup", function(highlight_instance)
                return {
                    text = "X-Ray",
                    callback = function()
                        local sel = highlight_instance and highlight_instance.selected_text or {}
                        closeAndClearSelection(ui, highlight_instance)
                        performLookup(plugin, sel.text)
                    end,
                }
            end)
            plugin.hooks.highlight = true
        end
    end)

    -- The NEW dict-button API. It arrived with PR #15184 on 2026-05-04 and is
    -- in no tagged release yet: v2026.03 does not have it, which is why a
    -- device running a release showed no X-Ray button at all until the
    -- DictButtonsReady handler in main.lua was added. The two are mutually
    -- exclusive -- the event was deleted in the same commit that added this
    -- method -- so each side guards on what the running build actually offers
    -- and exactly one of them fires.
    pcall(function()
        if ui.dictionary and type(ui.dictionary.addToDictButtons) == "function" then
            ui.dictionary:addToDictButtons{
                id = "xray_lookup",
                menu_text = _("X-Ray"),
                text = "X-Ray",
                callback = function(dict_popup)
                    local word = XRayLookup.wordFromDictPopup(dict_popup)
                    closeAndClearSelection(ui, dict_popup)
                    performLookup(plugin, word)
                end,
            }
            plugin.hooks.dict_api = true
        end
    end)
end

-- Exposed for main.lua's onDictButtonsReady handler. That event is delivered
-- to the plugin object, because the plugin is what sits in KOReader's event
-- chain -- so the button's callback has to live there while the work stays
-- here.
XRayLookup.perform = performLookup
XRayLookup.dismiss = closeAndClearSelection

return XRayLookup
