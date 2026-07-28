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

-- doc/cp_idx are threaded through purely so the detail card can offer the
-- ego-net button; every consumer below tolerates them being nil, which is what
-- a caller that has no document in scope passes.
-- No `keep_menu_open` and no `separator` on these rows, here or anywhere else a
-- `Menu` item is built: both are 0 occurrences in KOReader's menu.lua across
-- every version from v2015.11 to master (8 and 5 in touchmenu.lua, which is a
-- different widget -- main.lua:244 sits on a TouchMenu entry and keeps its
-- separator). They never did anything: the rules were never drawn, and the list
-- stays open after a tap only because onMenuSelect calls close_callback when it
-- is set and this plugin sets none. Same species as the `subtext` note below.
local function buildRow(entry, category, doc, cp_idx)
    if category == "timeline" then
        return {
            text = (entry.chapter or "") .. ": " .. (entry.event or ""),
            callback = function() XRayUI.showEntry(entry, category) end,
        }
    end
    local row = {
        text = "\226\128\162 " .. (entry.name or "?"), -- U+2022 BULLET
        callback = function() XRayUI.showEntry(entry, category, doc, cp_idx) end,
    }
    local preview = PREVIEW_FIELD[category] and entry[PREVIEW_FIELD[category]]
    if preview and preview ~= "" then
        -- `mandatory`, not `subtext`: Menu has no `subtext` field (0
        -- occurrences in KOReader's menu.lua across master, v2023.05 and
        -- v2025.04), so these previews were never drawn. Shortened to fit --
        -- `mandatory` is right-aligned beside the row text, not a second line.
        row.mandatory = truncate(preview, 40)
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
                callback = function() end,
            })
        end
        for _unused, entry in ipairs(entries) do
            table.insert(items, buildRow(entry, category, doc, cp_idx))
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

-- The ego net of one figure: its directly related figures, each tappable to
-- open THEIR net. UIManager stacks the menus, so "back" walks the path the
-- reader took without this having to keep a history of its own.
--
-- Displayed as a Menu, the same widget the category lists use. The design's
-- drawn two-column net is phase 2 and replaces only this function; everything
-- that decides WHICH figures appear -- and therefore the whole spoiler
-- guarantee -- lives in XRayDoc.egoNet and is unaffected by that swap.
-- `menu` is the Menu this net is already living in. Passed only when hopping
-- from one figure to the next INSIDE an open net: that widget then switches its
-- item_table instead of a second full-screen Menu stacking on top of it.
-- Omitted everywhere else (card, word lookup, category list), which opens a
-- fresh net as before.
function XRayUI.showEgoNet(doc, cp_idx, entry, menu)
    local ok, err = pcall(function()
        local net = XRayDoc.egoNet(doc, cp_idx, entry)
        if #net == 0 then return end

        local title = (entry.name or "") .. " \226\128\148 " .. _("Relations")

        -- The rows need the Menu they belong to, and on the first call it does
        -- not exist until after them. Captured as an upvalue, so the closures
        -- see whatever it is assigned below -- Lua binds the variable, not the
        -- value. Never reference the `menu` parameter from a row: it is nil on
        -- exactly the call that creates the widget.
        local target = menu

        local items = {}
        for _unused, neighbour in ipairs(net) do
            local name = neighbour.entry.name or "?"
            -- Historical figures are marked because a tap on one opens a card
            -- from a different category than the reader expects.
            if neighbour.category == "historical_figures" then
                name = name .. " \226\128\148 " .. _("historical") -- U+2014 EM DASH
            end
            table.insert(items, {
                text = "\226\128\162 " .. name, -- U+2022 BULLET
                -- `mandatory`, not `subtext`: Menu has no `subtext` field at
                -- all (0 occurrences in KOReader's menu.lua in master,
                -- v2023.05 and v2025.04), so a label put there is simply never
                -- drawn -- and the label is this view's whole payload.
                -- `mandatory` is the real field for a short, right-aligned
                -- value next to the row text.
                mandatory = neighbour.label,
                -- A tap opens the NEIGHBOUR's card, not the next net: without
                -- it there is no way from here to a description at all, and
                -- "who was that again" is the commoner question. Walking on is
                -- still one tap away, through that card's own button.
                callback = function()
                    XRayUI.showEntry(neighbour.entry, neighbour.category, doc, cp_idx,
                        function(viewer)
                            -- Closing the card is the caller's job, not
                            -- showEntry's: the switch underneath would be
                            -- invisible otherwise, while the already-accepted
                            -- list -> card -> net path must keep its card.
                            UIManager:close(viewer)
                            XRayUI.showEgoNet(doc, cp_idx, neighbour.entry, target)
                        end)
                end,
            })
        end

        if target then
            -- Menu's own stack. onMenuSelect pushes by itself only for
            -- sub_item_table (menu.lua:1364-1387), so the push is ours; the pop
            -- is upstream's -- Menu:onClose returns to the parent table and
            -- only really closes once the stack is empty (menu.lua:1460-1468),
            -- which makes "back" land on the previous figure for free.
            target.item_table.title = target.title
            table.insert(target.item_table_stack, target.item_table)
            target:switchItemTable(title, items)
            -- switchItemTable paints the title into the TitleBar but never
            -- assigns self.title -- that field is read-only at all three of its
            -- occurrences in menu.lua, in every version from v2015.11 to
            -- master. Without this line every push from depth 2 on records the
            -- Menu's CONSTRUCTION title, and the way back shows the right rows
            -- under the wrong name.
            target.title = title
        else
            target = Menu:new{
                title = title,
                item_table = items,
                is_borderless = true,
                width = Screen:getWidth(),
                height = Screen:getHeight(),
            }
            UIManager:show(target)
        end
    end)
    if not ok then
        logger.warn("XRayUI.showEgoNet failed: " .. tostring(err))
    end
end

-- category: same vocabulary as showList; entry: one item from a snapshot
-- list (or a timeline event when category == "timeline").
--
-- doc/cp_idx are optional and only enable the "Relations" button. Callers that
-- have no document in scope pass nothing and get the card unchanged.
--
-- on_relations(viewer) replaces what that button does. Only showEgoNet passes
-- it, to switch the net it already has open instead of stacking a second one;
-- it also decides whether this card closes on the way. Everyone else omits it
-- and the button opens a fresh net, unchanged.
function XRayUI.showEntry(entry, category, doc, cp_idx, on_relations)
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

        -- Only offered when this figure actually has neighbours the reader may
        -- see. Checking costs nothing here -- the document is already loaded,
        -- unlike in getSubMenuItems, which stays deliberately data-free.
        --
        -- Built fresh per call, never as a module constant: TextViewer appends
        -- its default row with table.insert, mutating whatever it was handed.
        -- And add_default_buttons is required, because a caller's buttons_table
        -- otherwise REPLACES that row -- the Close button would vanish from
        -- exactly the cards this feature touches (textviewer.lua).
        -- Only figures have relations. Without the category test a term or a
        -- location whose name happens to match a `from` gets the button too,
        -- and it opens a net titled after the term.
        local is_figure = category == "characters" or category == "historical_figures"
        local buttons, add_defaults = nil, nil
        -- Same upvalue trick as showEgoNet: the button needs the viewer it sits
        -- in, and that is built after the buttons.
        local viewer
        if is_figure and doc and cp_idx and #XRayDoc.egoNet(doc, cp_idx, entry) > 0 then
            buttons = {{
                {
                    text = _("Relations"),
                    callback = function()
                        if on_relations then
                            on_relations(viewer)
                        else
                            XRayUI.showEgoNet(doc, cp_idx, entry)
                        end
                    end,
                },
            }}
            add_defaults = true
        end

        viewer = TextViewer:new{
            title = entry.name or "",
            text = table.concat(lines, "\n"),
            buttons_table = buttons,
            add_default_buttons = add_defaults,
        }
        UIManager:show(viewer)
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
-- "37 / 151" -- what this stage holds against what the finished book holds.
-- One number alone cannot tell "staged, keep reading" from "the extraction
-- found little", and those two want opposite reactions from the reader.
-- Takes an ALREADY translated label: `_()` has to wrap a literal at the call
-- site or the catalog extractor cannot see the string, and an untranslated
-- entry only surfaces when someone reads the screen in German.
local function countLine(label, now, total)
    return label .. ": " .. tostring(now) .. " / " .. tostring(total)
end


-- Which of the two dictionary-button mechanisms is live on THIS build, and --
-- for the event one -- whether it has ever actually fired. Both installations
-- sit inside pcall guards, so without this line a missing button is
-- indistinguishable from a button nobody pressed. That ambiguity cost a full
-- debugging round on a released device.
local function dictHookLine(plugin)
    local hooks = (plugin and plugin.hooks) or {}
    if hooks.dict_api then
        return _("Dictionary button") .. ": " .. _("new API (addToDictButtons)")
    end
    if plugin and plugin.dict_integration_enabled == false then
        return _("Dictionary button") .. ": " .. _("switched off")
    end
    return _("Dictionary button") .. ": " .. (plugin and plugin.dict_event_fired
        and _("legacy event (fired)") or _("legacy event (not fired yet)"))
end


function XRayUI.showStatus(plugin, doc, cp_idx, pct)
    local ok, err = pcall(function()
        local ui = plugin and plugin.ui
        if not cp_idx then
            showNotYetAvailable(doc)
            return
        end

        -- Reported by XRayDoc.load, not guessed. This line used to fall back
        -- to "embedded" whenever it did not know -- which is to say always,
        -- because nothing ever set the field it read.
        local meta = XRayDoc.meta(ui) or {}
        local source = (meta.source == "companion") and _("companion file")
            or (meta.source == "embedded") and _("embedded")
            or _("unknown")

        local snapshot = XRayDoc.snapshot(doc, cp_idx) or {}
        local timeline = XRayDoc.timeline(doc, cp_idx) or {}
        local totals = XRayDoc.totals(doc)

        local lines = {}
        table.insert(lines, _("Source") .. ": " .. source)
        -- One decimal, not zero: MARGIN is 2 points, and a position rounded to
        -- whole percent cannot be used to judge -- let alone calibrate -- a
        -- threshold that fine.
        table.insert(lines, _("Reading position") .. ": " .. string.format("%.1f%%", pct or 0))

        -- doc.checkpoints[cp_idx].percent: doc is assumed to be the parsed
        -- xray.json (schema/xray.schema.json's top-level `checkpoints`
        -- array), cp_idx the 1-based index XRayDoc.selectCheckpoint returned
        -- into it -- inferred from the schema, not spelled out in the
        -- six-function XRayDoc interface itself.
        local checkpoint = doc.checkpoints and doc.checkpoints[cp_idx]
        if checkpoint and checkpoint.percent then
            table.insert(lines, _("X-Ray data up to") .. ": " .. tostring(checkpoint.percent) .. "%")
        end

        -- The question a thin list actually raises: is more coming, and when?
        -- The unlock threshold is percent + MARGIN, so that is the distance to
        -- report -- reporting the bare percent would promise data a point or
        -- two before it appears.
        local next_pct = XRayDoc.nextPercent(doc, cp_idx)
        if next_pct then
            local unlock_at = math.min(next_pct + XRayDoc.MARGIN, 100)
            table.insert(lines, string.format(
                _("Next stage: %d%% (in %.1f%%)"), next_pct, math.max(0, unlock_at - (pct or 0))))
        else
            table.insert(lines, _("Next stage") .. ": " .. _("none -- this is the last one"))
        end

        table.insert(lines, "")
        table.insert(lines, countLine(_("Characters"), #(snapshot.characters or {}), totals.characters))
        table.insert(lines, countLine(_("Locations"), #(snapshot.locations or {}), totals.locations))
        table.insert(lines, countLine(_("Terms"), #(snapshot.terms or {}), totals.terms))
        table.insert(lines, countLine(_("Historical Figures"),
            #(snapshot.historical_figures or {}), totals.historical_figures))
        table.insert(lines, countLine(_("Timeline Events"), #timeline, totals.timeline))

        table.insert(lines, "")
        table.insert(lines, dictHookLine(plugin))

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


-- Unlike the category views, this one is reachable on documents that carry no
-- recaps at all, so the "there is none" case is answered here rather than by
-- hiding the menu entry: hiding it would force XRayDoc.load -- and with it a
-- mkdir+unzip over the whole EPUB -- into the menu build, which is data-free
-- today.
--
-- The two empty cases are NOT the same and must not share a message.
-- showNotYetAvailable speaks about checkpoints ("X-Ray data available from
-- N%") and is right only while the reader is still short of the first one; on
-- a document whose first stage sits at 1%, telling a reader at 60% that data
-- starts at 1% would be false twice over.
function XRayUI.showRecap(doc, cp_idx)
    local ok, err = pcall(function()
        if not cp_idx then
            showNotYetAvailable(doc)
            return
        end

        local text = XRayDoc.recap(doc, cp_idx)
        if not text then
            UIManager:show(InfoMessage:new{
                text = _("This book's X-Ray data contains no recap."),
                timeout = 3,
            })
            return
        end

        UIManager:show(TextViewer:new{
            title = _("Story so far"),
            text = text,
        })
    end)
    if not ok then
        logger.warn("XRayUI.showRecap failed: " .. tostring(err))
    end
end

-- ---------------------------------------------------------------------------
-- Diagnostics
--
-- Deliberately NOT folded into the status screen: status answers "where am I
-- and what can I see", which is a reading question and has to stay short. This
-- answers "why is it behaving like that", which is only ever asked while
-- debugging -- and it is verbose enough to ruin the other screen.
--
-- Every value here was, at some point, something that had to be reconstructed
-- from outside the device: the book path out of history.lua, the source by
-- elimination, the KOReader version by grepping the install. Each line is one
-- such round trip that does not have to happen again.
-- ---------------------------------------------------------------------------

local function safe(fn, fallback)
    local ok, value = pcall(fn)
    if not ok or value == nil then return fallback or "?" end
    return value
end


-- Page-axis percent alongside the text-axis one the staging actually uses.
-- The desktop stamps a CHARACTER share; the device reads a rendered position.
-- Seeing both at once is what turns the R2 risk from an assumption into a
-- measurement -- and MARGIN is the knob it calibrates.
local function pageAxisPercent(ui)
    return safe(function()
        local document = ui and ui.document
        if not document then return nil end
        local page = (ui.view and ui.view.state and ui.view.state.page)
            or document:getCurrentPage()
        local count = document:getPageCount()
        if type(page) ~= "number" or type(count) ~= "number" or count == 0 then return nil end
        return string.format("%.1f%%", page / count * 100)
    end)
end


function XRayUI.diagnosticsText(plugin, doc, cp_idx, pct)
    local ui = plugin and plugin.ui
    local meta = XRayDoc.meta(ui) or {}
    local checkpoint = doc and doc.checkpoints and cp_idx and doc.checkpoints[cp_idx]
    local fingerprint = (doc and doc.book_fingerprint) or {}
    local hooks = (plugin and plugin.hooks) or {}
    local lines = {}

    local function add(label, value)
        table.insert(lines, label .. ": " .. tostring(value))
    end

    add("plugin", safe(function() return plugin.version end, "?"))
    add("koreader", safe(function() return require("version"):getCurrentRevision() end))
    table.insert(lines, "")

    add("book", meta.book_path or safe(function() return ui.document.file end))
    add("source", meta.source or "none")
    add("bytes", meta.bytes or "?")
    add("load", meta.load_ms and string.format("%.0f ms", meta.load_ms) or "?")
    table.insert(lines, "")

    add("schema_version", doc and doc.schema_version or "?")
    add("generator", doc and doc.generator or "?")
    add("generator_version", doc and doc.generator_version or "?")
    add("detail_level", doc and doc.detail_level or "?")
    add("language", doc and doc.language or "?")
    add("stages", doc and doc.checkpoints and #doc.checkpoints or 0)
    add("complete", tostring(doc and doc.complete))
    add("last_percent", doc and doc.last_percent or "?")
    -- Prefix only: the whole hash is 71 characters of unreadable hex on a
    -- 6-inch screen, and a mismatch shows up in the first few just as well.
    add("text_hash", tostring(fingerprint.text_hash or "?"):sub(1, 26))
    add("title", fingerprint.title or "?")
    table.insert(lines, "")

    add("position (text axis)", pct and string.format("%.2f%%", pct) or "?")
    add("position (page axis)", pageAxisPercent(ui))
    add("stage", checkpoint and (tostring(checkpoint.percent) .. "%") or "none reached")
    add("stage index", cp_idx or "-")
    add("MARGIN", XRayDoc.MARGIN)
    table.insert(lines, "")

    add("hook: highlight dialog", tostring(hooks.highlight == true))
    add("hook: dict new API", tostring(hooks.dict_api == true))
    add("hook: dict legacy event fired", tostring(plugin and plugin.dict_event_fired == true))
    add("dictionary integration", tostring(plugin and plugin.dict_integration_enabled))

    return table.concat(lines, "\n")
end


function XRayUI.showDiagnostics(plugin, doc, cp_idx, pct)
    local ok, err = pcall(function()
        UIManager:show(TextViewer:new{
            title = _("X-Ray Diagnostics"),
            text = XRayUI.diagnosticsText(plugin, doc, cp_idx, pct),
        })
    end)
    if not ok then
        logger.warn("XRayUI.showDiagnostics failed: " .. tostring(err))
    end
end


return XRayUI
