-- X-Ray UI: category lists (Menu) and detail cards (TextViewer).
--
-- Pure display. Every entry comes from XRayDoc, already spoiler-safe for the
-- checkpoint it was fetched for (project CLAUDE.md, "Spoiler-Invarianten
-- D4") -- this module never re-derives or caches that decision, it only
-- renders what it's handed. Reuses the two widgets the old xray.koplugin
-- proved out for this job (Menu for lists, TextViewer for detail cards --
-- xray.koplugin/xray_ui.lua), not that file's much larger custom
-- ButtonDialog/VerticalGroup detail layout, which existed only to host
-- features this rebuild drops (linked entries, mentions, AI reasoning).

local Menu = require("ui/widget/menu")
local TextViewer = require("ui/widget/textviewer")
local InfoMessage = require("ui/widget/infomessage")
local UIManager = require("ui/uimanager")
local Screen = require("device").screen
local logger = require("logger")
local _ = require("xray_i18n")
local XRayDoc = require("xray_doc")

local XRayUI = {}

-- Category key -> menu/section title.
local CATEGORY_LABELS = {
    characters = _("Characters"),
    locations = _("Locations"),
    terms = _("Terms"),
    historical_figures = _("Historical Figures"),
    timeline = _("Timeline"),
}

-- ─────────────────────────────────────────────────────────────────────────
-- Sorting (CLAUDE.md: characters/locations chronological by first_seq, terms
-- alphabetical, historical figures by role weight).
-- ─────────────────────────────────────────────────────────────────────────

-- Role-weight ladder, ported from xray_data.lua:56-66 (`getRoleScore`, a
-- local helper inside the old `sortDataByFrequency`). Only the role-weight
-- signal is ported: that function's other signal (name frequency counted
-- against the full book text) needed the whole book text, which a
-- snapshot-only view never has -- and the project decision is "role weight"
-- alone (task brief; docs/plans/2026-07-25-xray-neuausrichtung.md).
local function roleWeight(role)
    if not role then return 0 end
    local r = role:lower()
    if r:find("protagonist") then return 100 end
    if r:find("main") or r:find("lead") or r:find("hero") or r:find("detective") then return 90 end
    if r:find("deuteragonist") then return 80 end
    if r:find("major") or r:find("antagonist") or r:find("villain") or r:find("primary") then return 70 end
    if r:find("secondary") or r:find("supporting") then return 30 end
    if r:find("minor") or r:find("background") then return 5 end
    return 15 -- default for other specific roles
end

local function sortEntries(list, category)
    if category == "characters" or category == "locations" then
        -- first_seq is a monotonic per-book stamp the generator assigns once
        -- per entity (xray_core/schema.py _CHRONOLOGY_FIELDS) -- always
        -- present and unique, so no tiebreak is needed.
        table.sort(list, function(a, b) return (a.first_seq or 0) < (b.first_seq or 0) end)
    elseif category == "terms" then
        table.sort(list, function(a, b) return (a.name or ""):lower() < (b.name or ""):lower() end)
    elseif category == "historical_figures" then
        table.sort(list, function(a, b)
            local wa, wb = roleWeight(a.role), roleWeight(b.role)
            if wa ~= wb then return wa > wb end
            return (a.name or ""):lower() < (b.name or ""):lower()
        end)
    elseif category == "timeline" then
        -- table.sort is not stable, so events sharing one `pct` would
        -- otherwise reshuffle on every render (same fix as old
        -- xray_ui.lua:470-473's comment for TOC-based ordering).
        for i, ev in ipairs(list) do ev._idx = ev._idx or i end
        table.sort(list, function(a, b)
            if (a.pct or 0) ~= (b.pct or 0) then return (a.pct or 0) < (b.pct or 0) end
            return a._idx < b._idx
        end)
    end
    return list
end

-- ─────────────────────────────────────────────────────────────────────────
-- Lists
-- ─────────────────────────────────────────────────────────────────────────

local function truncate(text, max_len)
    if not text or #text <= max_len then return text end
    return text:sub(1, max_len) .. "..."
end

-- Field whose truncated value previews under an entry's name in list rows.
local PREVIEW_FIELD = {
    characters = "description",
    locations = "description",
    terms = "definition",
    historical_figures = "biography",
}

local function buildRow(entry, category)
    if category == "timeline" then
        return {
            text = (entry.chapter or "") .. ": " .. (entry.event or ""),
            keep_menu_open = true,
            separator = true,
            callback = function() XRayUI.showEntry(entry, category) end,
        }
    end
    local row = {
        text = "\226\128\162 " .. (entry.name or "?"), -- U+2022 BULLET
        keep_menu_open = true,
        separator = true,
        callback = function() XRayUI.showEntry(entry, category) end,
    }
    local preview = PREVIEW_FIELD[category] and entry[PREVIEW_FIELD[category]]
    if preview and preview ~= "" then
        row.subtext = truncate(preview, 80)
    end
    return row
end

-- Shown by showList/showStatus instead of any real data when the reader
-- hasn't reached the first checkpoint yet. Deliberately never falls back to
-- the smallest available snapshot -- that snapshot covers text past the
-- current reading position, i.e. a spoiler (task brief; the plan's warning
-- against tolerant fallbacks, xray_import.lua:184-193, "calibre's percent is
-- a CHARACTER percent").
local function showNotYetAvailable(doc)
    local ok, first_pct = pcall(XRayDoc.firstAvailablePercent, doc)
    local text = (ok and first_pct)
        and string.format(_("X-Ray data available from %d%%."), first_pct)
        or _("X-Ray data is not available yet.")
    UIManager:show(InfoMessage:new{ text = text, timeout = 3 })
end

-- category: "characters"|"locations"|"terms"|"historical_figures"|"timeline"
function XRayUI.showList(ui, doc, cp_idx, category)
    local ok, err = pcall(function()
        -- Dismiss KOReader's own top reader menu so it doesn't sit stacked
        -- behind ours (attested pattern: xray.koplugin/xray_ui.lua:757-762).
        if ui and ui.menu and type(ui.menu.onCloseReaderMenu) == "function" then
            pcall(function() ui.menu:onCloseReaderMenu() end)
        end

        if not cp_idx then
            showNotYetAvailable(doc)
            return
        end

        local entries
        if category == "timeline" then
            entries = XRayDoc.timeline(doc, cp_idx) or {}
        else
            local snapshot = XRayDoc.snapshot(doc, cp_idx) or {}
            entries = snapshot[category] or {}
        end
        sortEntries(entries, category)

        local items = {}
        if #entries == 0 then
            table.insert(items, {
                text = _("No entries in this category yet."),
                keep_menu_open = true,
                callback = function() end,
            })
        end
        for _unused, entry in ipairs(entries) do
            table.insert(items, buildRow(entry, category))
        end

        UIManager:show(Menu:new{
            title = (CATEGORY_LABELS[category] or category) .. " (" .. #entries .. ")",
            item_table = items,
            is_borderless = true,
            width = Screen:getWidth(),
            height = Screen:getHeight(),
        })
    end)
    if not ok then
        logger.warn("XRayUI.showList failed: " .. tostring(err))
    end
end

-- ─────────────────────────────────────────────────────────────────────────
-- Detail cards
-- ─────────────────────────────────────────────────────────────────────────

local function addLine(lines, label, value)
    if value and value ~= "" then
        table.insert(lines, label .. ": " .. value)
    end
end

local function addAliases(lines, aliases)
    if aliases and #aliases > 0 then
        table.insert(lines, _("Aliases") .. ": " .. table.concat(aliases, ", "))
    end
end

-- Each builder appends its category's short attribute lines and returns the
-- long-form body field (description/definition/biography). Empty fields are
-- never rendered -- the generator leaves them blank rather than emit a
-- placeholder like "Not Specified" (project CLAUDE.md, "Keine
-- Inhalts-Platzhalter"), so "blank" reliably means "nothing to show", not
-- "the AI declined to answer".
local DETAIL_BUILDERS = {
    characters = function(e, lines)
        addLine(lines, _("Role"), e.role)
        addLine(lines, _("Occupation"), e.occupation)
        addLine(lines, _("Gender"), e.gender)
        addAliases(lines, e.aliases)
        return e.description
    end,
    locations = function(e, lines)
        addLine(lines, _("Importance"), e.importance)
        addAliases(lines, e.aliases)
        return e.description
    end,
    terms = function(e, lines)
        if e.expanded and e.expanded ~= "" and e.expanded ~= e.name then
            addLine(lines, _("Also Known As"), e.expanded)
        end
        addLine(lines, _("Category"), e.category)
        addAliases(lines, e.aliases)
        return e.definition
    end,
    historical_figures = function(e, lines)
        addLine(lines, _("Role"), e.role)
        addLine(lines, _("Importance in Book"), e.importance_in_book)
        addLine(lines, _("Context in Book"), e.context_in_book)
        return e.biography
    end,
}

-- category: same vocabulary as showList; entry: one item from a snapshot
-- list (or a timeline event when category == "timeline").
function XRayUI.showEntry(entry, category)
    local ok, err = pcall(function()
        if not entry then return end

        if category == "timeline" then
            UIManager:show(TextViewer:new{
                title = entry.chapter or "",
                text = entry.event or "",
            })
            return
        end

        local builder = DETAIL_BUILDERS[category]
        if not builder then return end

        local lines = {}
        local body = builder(entry, lines)
        if body and body ~= "" then
            if #lines > 0 then table.insert(lines, "") end
            table.insert(lines, body)
        end

        UIManager:show(TextViewer:new{
            title = entry.name or "",
            text = table.concat(lines, "\n"),
        })
    end)
    if not ok then
        logger.warn("XRayUI.showEntry failed: " .. tostring(err))
    end
end

-- ─────────────────────────────────────────────────────────────────────────
-- Status
-- ─────────────────────────────────────────────────────────────────────────

-- doc/cp_idx: as elsewhere; pct: current reading position 0..100
-- (XRayDoc.position's return value, forwarded by the caller).
function XRayUI.showStatus(doc, cp_idx, pct)
    local ok, err = pcall(function()
        if not cp_idx then
            showNotYetAvailable(doc)
            return
        end

        -- ASSUMPTION: `doc.source` ("embedded"|"companion") is not part of
        -- the six documented XRayDoc functions -- xray_doc.lua doesn't exist
        -- yet, so this field is a best guess at how it will report which
        -- file won (plan: "Liegt <buch>.epub.xray.json daneben, gewinnt
        -- sie."). Falls back to "embedded", the default/primary path.
        local source = (doc.source == "companion") and _("companion file") or _("embedded")

        local snapshot = XRayDoc.snapshot(doc, cp_idx) or {}
        local timeline = XRayDoc.timeline(doc, cp_idx) or {}

        local lines = {}
        table.insert(lines, _("Source") .. ": " .. source)
        table.insert(lines, _("Reading position") .. ": " .. string.format("%.0f%%", pct or 0))

        -- doc.checkpoints[cp_idx].percent: doc is assumed to be the parsed
        -- xray.json (schema/xray.schema.json's top-level `checkpoints`
        -- array), cp_idx the 1-based index XRayDoc.selectCheckpoint returned
        -- into it -- inferred from the schema, not spelled out in the
        -- six-function XRayDoc interface itself.
        local checkpoint = doc.checkpoints and doc.checkpoints[cp_idx]
        if checkpoint and checkpoint.percent then
            table.insert(lines, _("X-Ray data up to") .. ": " .. tostring(checkpoint.percent) .. "%")
        end

        table.insert(lines, "")
        table.insert(lines, _("Characters") .. ": " .. #(snapshot.characters or {}))
        table.insert(lines, _("Locations") .. ": " .. #(snapshot.locations or {}))
        table.insert(lines, _("Terms") .. ": " .. #(snapshot.terms or {}))
        table.insert(lines, _("Historical Figures") .. ": " .. #(snapshot.historical_figures or {}))
        table.insert(lines, _("Timeline Events") .. ": " .. #timeline)

        -- complete/last_percent are top-level xray.json fields (schema.py);
        -- the plan calls for surfacing them here when generation stopped
        -- before covering the whole book.
        if doc.complete ~= true and doc.last_percent then
            table.insert(lines, "")
            table.insert(lines, string.format(_("Data incomplete: generated up to %d%%."), doc.last_percent))
        end

        UIManager:show(TextViewer:new{
            title = _("X-Ray Status"),
            text = table.concat(lines, "\n"),
        })
    end)
    if not ok then
        logger.warn("XRayUI.showStatus failed: " .. tostring(err))
    end
end

return XRayUI
