-- X-Ray for KOReader -- display only.
--
-- Shows the characters, locations, terms, historical figures and events of a
-- book up to the current reading position. The data is generated on the desktop
-- and delivered inside the book file; this plugin reads it, picks the snapshot
-- the reader has earned, and renders it. It never calls an AI, never writes to
-- the book, and keeps exactly one setting.
--
-- Menu shape is deliberate: the five categories sit directly under "X-Ray" in
-- the reader menu (one tap to a list), and everything that is not reading
-- material is tucked into a single "More" submenu.

local ButtonDialog = require("ui/widget/buttondialog")
local ConfirmBox = require("ui/widget/confirmbox")
local DataStorage = require("datastorage")
local Dispatcher = require("dispatcher")
local InfoMessage = require("ui/widget/infomessage")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local logger = require("logger")

local _ = require("xray_i18n")
local XRayDoc = require("xray_doc")
local XRayLookup = require("xray_lookup")
local XRayUI = require("xray_ui")
local Updater = require("xray_updater")

-- KOReader's own settings store rather than a private settings.json: it is one
-- boolean and one timestamp, it survives a plugin update (it lives outside the
-- plugin directory), and it costs no file handling of our own.
local SETTING_DICT = "xray_dict_integration"
local SETTING_LAST_UPDATE_CHECK = "xray_last_update_check"
local WEEK_SECONDS = 7 * 24 * 60 * 60
-- Far enough after opening that the check never competes with rendering the
-- first page; short enough that it still happens in a normal reading session.
local UPDATE_CHECK_DELAY = 20

local XRayPlugin = WidgetContainer:extend{
    name = "xray",
    is_doc_only = true,
}

local CATEGORIES = {
    { key = "characters",         label = "Characters" },
    { key = "locations",          label = "Locations" },
    { key = "terms",              label = "Terms" },
    { key = "historical_figures", label = "Historical Figures" },
    { key = "timeline",           label = "Timeline" },
}

function XRayPlugin:init()
    self.dict_integration_enabled = G_reader_settings:nilOrTrue(SETTING_DICT)

    -- Nothing here may cost the reader the book: a plugin that throws during
    -- init takes the document open with it.
    pcall(function()
        if self.ui and self.ui.menu then
            self.ui.menu:registerToMainMenu(self)
        end
    end)
    pcall(function() XRayLookup.setup(self) end)
    pcall(function() self:onDispatcherRegisterActions() end)
    pcall(function()
        UIManager:scheduleIn(UPDATE_CHECK_DELAY, function()
            pcall(function() self:maybeCheckForUpdates() end)
        end)
    end)
end

-- ---------------------------------------------------------------------------
-- Reading position -> the snapshot the reader has earned
-- ---------------------------------------------------------------------------

--- Returns doc, cp_idx, pct -- or nil plus a message ready to show.
-- cp_idx may be nil while doc is valid: that is the "read on, there is nothing
-- for you yet" case, and the views render a placeholder for it rather than
-- falling back to the smallest snapshot (which covers unread text).
function XRayPlugin:current()
    local doc, err = XRayDoc.load(self.ui)
    if not doc then
        return nil, nil, nil, err or _("No X-Ray data for this book.")
    end
    local pct = XRayDoc.position(self.ui)
    if not pct then
        return doc, nil, nil, nil
    end
    return doc, XRayDoc.selectCheckpoint(doc, pct), pct, nil
end

function XRayPlugin:showCategory(category)
    local doc, cp_idx, _pct, err = self:current()
    if not doc then
        UIManager:show(InfoMessage:new{ text = err })
        return
    end
    pcall(function() XRayUI.showList(self.ui, doc, cp_idx, category) end)
end

function XRayPlugin:showStatus()
    local doc, cp_idx, pct, err = self:current()
    if not doc then
        UIManager:show(InfoMessage:new{ text = err })
        return
    end
    pcall(function() XRayUI.showStatus(self, doc, cp_idx, pct) end)
end

function XRayPlugin:showDiagnostics()
    local doc, cp_idx, pct = self:current()
    pcall(function() XRayUI.showDiagnostics(self, doc, cp_idx, pct) end)
end

-- Writes the same text next to KOReader's own settings, so it can be read over
-- USB instead of retyped or photographed off the screen. Plain sibling file
-- rather than a subdirectory: no mkdir, nothing to clean up, and the settings
-- directory is exactly where someone debugging this device already looks.
function XRayPlugin:saveDiagnostics()
    local doc, cp_idx, pct = self:current()
    local path = DataStorage:getSettingsDir() .. "/xray_diagnostics.txt"
    local ok, err = pcall(function()
        local fh = assert(io.open(path, "w"))
        fh:write(XRayUI.diagnosticsText(self, doc, cp_idx, pct), "\n")
        fh:close()
    end)
    UIManager:show(InfoMessage:new{
        text = ok and (_("Saved to") .. "\n" .. path)
            or (_("Could not save the diagnostics.") .. "\n" .. tostring(err)),
    })
end

-- ---------------------------------------------------------------------------
-- Menu
-- ---------------------------------------------------------------------------

function XRayPlugin:addToMainMenu(menu_items)
    menu_items.xray = {
        text = _("X-Ray"),
        sorting_hint = "tools",
        sub_item_table_func = function() return self:getSubMenuItems() end,
    }
end

-- Why X-Ray sits where it sits, and how to get past that:
--
-- A plugin cannot choose its position in KOReader's menu. `MenuSorter` matches
-- entry keys against `reader_menu_order.lua`; a key that is not listed there
-- -- and a third-party plugin's never is -- becomes an "orphan" and gets
-- `table.insert`ed at the END of whatever `sorting_hint` names. With "tools"
-- already holding a dozen entries plus a "More tools" submenu, the end means
-- page two. Every other tab is similarly full, so moving tabs only relocates
-- the problem, and the one real override (a `reader_menu_order.lua` in the
-- device's settings directory) means a plugin writing into the reader's own
-- menu configuration, which is not ours to do.
--
-- A dispatcher action is the supported way to be one gesture away instead:
-- assign it under Taps and gestures, and X-Ray opens without any menu at all.
function XRayPlugin:onDispatcherRegisterActions()
    Dispatcher:registerAction("xray_show",
        {category="none", event="XRayShow", title=_("X-Ray"), reader=true})
end

function XRayPlugin:onXRayShow()
    local buttons = {}
    for i = 1, #CATEGORIES do
        local category = CATEGORIES[i]
        buttons[#buttons + 1] = {{
            text = _(category.label),
            callback = function()
                UIManager:close(self.category_dialog)
                self.category_dialog = nil
                self:showCategory(category.key)
            end,
        }}
    end
    self.category_dialog = ButtonDialog:new{ title = _("X-Ray"), buttons = buttons }
    UIManager:show(self.category_dialog)
    return true
end

-- KOReader v2026.03 and every earlier release deliver the dictionary popup's
-- buttons through this event; `ui.dictionary:addToDictButtons` (see
-- xray_lookup.lua) exists only on master since PR #15184 and in no release
-- yet. The event was deleted in that same commit, so exactly one of the two
-- paths is live on any given build and they cannot double up.
function XRayPlugin:onDictButtonsReady(dict_popup, buttons)
    if not self.dict_integration_enabled then return end
    if not dict_popup or dict_popup.is_wiki_fullpage then return end
    -- The reader's query, not the headword the dictionary matched -- see
    -- XRayLookup.wordFromDictPopup for why those differ and why it matters.
    local word = XRayLookup.wordFromDictPopup(dict_popup)
    if not word then return end
    -- Proof that this build really routes through the event, not just that we
    -- registered for it -- the distinction the diagnostics screen reports.
    self.dict_event_fired = true

    table.insert(buttons, 1, {{
        id = "xray_lookup",
        text = _("X-Ray"),
        font_bold = false,
        callback = function()
            XRayLookup.dismiss(self.ui, dict_popup)
            XRayLookup.perform(self, word)
        end,
    }})
end

function XRayPlugin:getSubMenuItems()
    local items = {}
    for i = 1, #CATEGORIES do
        local category = CATEGORIES[i]
        table.insert(items, {
            text = _(category.label),
            callback = function() self:showCategory(category.key) end,
        })
    end
    table.insert(items, {
        text = _("More"),
        separator = true,
        sub_item_table = {
            {
                text = _("Status"),
                callback = function() self:showStatus() end,
            },
            {
                text = _("Diagnostics"),
                callback = function() self:showDiagnostics() end,
            },
            {
                text = _("Save diagnostics"),
                help_text = _("Writes the diagnostics to xray_diagnostics.txt in KOReader's settings directory, where it can be read over USB."),
                callback = function() self:saveDiagnostics() end,
            },
            {
                text = _("Dictionary integration"),
                checked_func = function() return self.dict_integration_enabled end,
                callback = function() self:toggleDictIntegration() end,
                help_text = _("Adds an X-Ray button to the dictionary and text selection popups. Takes effect on the next book you open."),
            },
            {
                text = _("Check for updates"),
                callback = function() pcall(function() Updater.checkNow(self) end) end,
            },
            {
                text = _("Remove old X-Ray data"),
                callback = function() self:cleanUpLegacyData() end,
            },
        },
    })
    return items
end

function XRayPlugin:toggleDictIntegration()
    self.dict_integration_enabled = not self.dict_integration_enabled
    G_reader_settings:saveSetting(SETTING_DICT, self.dict_integration_enabled)
    -- The hooks are installed once per document; removing an installed one is
    -- not part of KOReader's API, so the change lands on the next book rather
    -- than pretending to take effect now.
    UIManager:show(InfoMessage:new{
        text = _("Takes effect on the next book you open."),
    })
end

-- ---------------------------------------------------------------------------
-- Weekly update check
-- ---------------------------------------------------------------------------

function XRayPlugin:maybeCheckForUpdates()
    local last = G_reader_settings:readSetting(SETTING_LAST_UPDATE_CHECK) or 0
    if os.time() - last < WEEK_SECONDS then return end

    local ok_net, NetworkMgr = pcall(require, "ui/network/manager")
    if not ok_net then return end
    -- Never bring up the network for this: a background update check is not
    -- worth a connection the reader did not ask for.
    if not (NetworkMgr:isConnected() and NetworkMgr:isOnline()) then return end

    -- Stamp before the check, not after: a failing check that retried on every
    -- book open would be worse than one missed week.
    G_reader_settings:saveSetting(SETTING_LAST_UPDATE_CHECK, os.time())
    Updater.checkSilently(self)
end

-- ---------------------------------------------------------------------------
-- One-time cleanup of the pre-rewrite plugin's leftovers
--
-- Throwaway code with an expiry date: drop it once every device that ever ran
-- the old plugin has been cleaned. It exists because the old plugin cached
-- generated data per book and kept API keys in a file inside its own directory,
-- and an update simply unzips over that directory without removing anything.
-- ---------------------------------------------------------------------------

local function collectLegacyPaths(plugin)
    local paths = {}
    local ok_lfs, lfs = pcall(require, "libs/libkoreader-lfs")
    if not ok_lfs then ok_lfs, lfs = pcall(require, "lfs") end

    local function addIfPresent(path)
        if path and lfs and lfs.attributes(path) then
            table.insert(paths, path)
        end
    end

    -- The API key file: the one item here that is not merely dead weight.
    if plugin.path then
        addIfPresent(plugin.path .. "/xray_config.lua")
    end
    pcall(function()
        addIfPresent(DataStorage:getSettingsDir() .. "/xray/series")
    end)

    -- This book's sidecar cache. Other books keep their leftovers until they
    -- are opened -- harmless dead files, and walking the whole library to find
    -- them would be a lot of machinery for a one-time chore.
    pcall(function()
        local DocSettings = require("docsettings")
        local book_path = plugin.ui and plugin.ui.document and plugin.ui.document.file
        if not book_path then return end
        local sidecar = DocSettings:getSidecarDir(book_path)
        if not (sidecar and lfs and lfs.attributes(sidecar)) then return end
        addIfPresent(sidecar .. "/xray_cache.lua")
        for entry in lfs.dir(sidecar) do
            if entry:match("^xray_snapshot_.*%.lua$") then
                addIfPresent(sidecar .. "/" .. entry)
            end
        end
    end)
    return paths
end

function XRayPlugin:cleanUpLegacyData()
    local ok, paths = pcall(collectLegacyPaths, self)
    if not ok or #paths == 0 then
        UIManager:show(InfoMessage:new{ text = _("No old X-Ray data found.") })
        return
    end

    -- Show what will go before anything goes.
    local listing = {}
    for i = 1, #paths do
        table.insert(listing, "- " .. paths[i]:gsub("^.*/", ""))
    end
    UIManager:show(ConfirmBox:new{
        text = _("Delete these leftovers from the previous X-Ray version?")
            .. "\n\n" .. table.concat(listing, "\n"),
        ok_text = _("Delete"),
        ok_callback = function()
            local removed = 0
            for i = 1, #paths do
                local shell_safe = "'" .. paths[i]:gsub("'", "'\\''") .. "'"
                if os.execute("rm -rf " .. shell_safe) then
                    removed = removed + 1
                end
            end
            logger.info("XRay: removed " .. removed .. " legacy item(s)")
            UIManager:show(InfoMessage:new{
                text = string.format(_("Removed %d item(s)."), removed),
            })
        end,
    })
end

return XRayPlugin
