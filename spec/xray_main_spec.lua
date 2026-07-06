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
end)
