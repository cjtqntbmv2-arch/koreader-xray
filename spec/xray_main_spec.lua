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

    it("nests Duplicate Check under Content & Fetch, not Auto X-Ray Settings", function()
        local plugin = mkPlugin()          -- REQUIRED: no shared `plugin` in this spec
        local items = plugin:getSubMenuItems()
        local function find(tbl, key)
            for _, it in ipairs(tbl) do
                if it.text == key then return it end
            end
        end
        -- In specs loc:t(key) returns the key string itself, so match on keys.
        local settings = find(items, "menu_settings")
        local content = find(settings.sub_item_table, "menu_content_fetch_settings")
        local auto = find(content.sub_item_table, "menu_auto_update_frequency")
        -- Auto X-Ray now holds only Frequency
        assert.are.equal(1, #auto.sub_item_table)
        assert.are.equal("menu_frequency", auto.sub_item_table[1].text)
        -- Duplicate Check now sits directly under Content & Fetch
        assert.is_not_nil(find(content.sub_item_table, "auto_dupe_check_setting_title"))
    end)
end)

describe("auto-fetch retry cap", function()
    it("increments the same key the cap checks and stops after 3 attempts", function()
        local plugin = mkPlugin()
        plugin.auto_fetch_enabled = true
        plugin.chapters_fetched = {}
        plugin.timeline = {}
        plugin.fetch_attempts = {}
        plugin.ai_helper = {
            settings = { auto_fetch_cooldown = 0 },
            hasApiKey = function() return true end,
        }
        plugin.ui.document.getToc = function()
            return { { page = 1, title = "Chapter 1" } }
        end
        plugin.ui.document.getPageCount = function() return 100 end
        plugin.ui.getCurrentPage = function() return 5 end
        local net = package.loaded["ui/network/manager"]
        local old_conn, old_online = net.isConnected, net.isOnline
        net.isConnected = function() return true end
        net.isOnline = function() return true end
        local fetches = 0
        plugin.continueWithFetch = function() fetches = fetches + 1 end

        for i = 1, 5 do
            plugin.last_auto_chapter = nil          -- Kapitel-Hopping simulieren
            plugin.chapters_fetched = plugin.chapters_fetched or {}
            plugin:onPageUpdate(5)
        end

        net.isConnected, net.isOnline = old_conn, old_online
        assert.are.equal(3, fetches)
        assert.are.equal(3, plugin.fetch_attempts["Chapter 1_1"] or 0)
        assert.is_true(plugin.chapters_fetched["Chapter 1_1"] == true)
    end)
end)
