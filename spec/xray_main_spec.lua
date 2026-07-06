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
