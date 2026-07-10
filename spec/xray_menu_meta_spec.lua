-- xray_menu_meta_spec.lua
require("spec/spec_helper")
local xray_ui = require("xray_ui")

describe("language slimming", function()
    local plugin
    before_each(function()
        plugin = createMockPlugin()
        for k, v in pairs(xray_ui) do plugin[k] = v end
    end)

    it("resolveLanguage never returns a removed language for a foreign code", function()
        plugin.loc.available_languages = nil  -- force the hardcoded fallback path
        assert.are.equal("en", plugin:resolveLanguage("fr"))
        assert.are.equal("en", plugin:resolveLanguage("zh_CN"))
        assert.are.equal("de", plugin:resolveLanguage("de"))
    end)

    it("isRTL is always false (no RTL language shipped)", function()
        assert.falsy(plugin:isRTL())
    end)
end)

describe("ai fetching main switch menu visibility", function()
    local XRayPlugin = require("main")

    local function mkPlugin()
        local plugin = createMockPlugin()
        for k, v in pairs(XRayPlugin) do
            if plugin[k] == nil then plugin[k] = v end
        end
        return plugin
    end

    local function findText(items, needle, found)
        found = found or {}
        for _, item in ipairs(items) do
            if type(item.text) == "string" and item.text:find(needle, 1, true) then
                table.insert(found, item.text)
            end
            if type(item.sub_item_table) == "table" then
                findText(item.sub_item_table, needle, found)
            end
        end
        return found
    end

    it("shows fetch items when enabled (default)", function()
        local items = mkPlugin():getSubMenuItems()
        assert.are.equal(1, #findText(items, "menu_update_xray"))
        assert.are.equal(1, #findText(items, "menu_prefetch_offline"))
        assert.are.equal(1, #findText(items, "menu_prefetch_auto"))
        assert.are.equal(1, #findText(items, "menu_frequency"))
        assert.are.equal(1, #findText(items, "menu_series_context"))
        assert.are.equal(1, #findText(items, "menu_ai_fetching"))
        assert.are.equal(1, #findText(items, "menu_import_calibre"))
    end)

    it("hides fetch items when disabled, keeps toggle/import/display items", function()
        local plugin = mkPlugin()
        plugin.ai_helper.settings.ai_fetching_enabled = false
        local items = plugin:getSubMenuItems()
        assert.are.equal(0, #findText(items, "menu_update_xray"))
        assert.are.equal(0, #findText(items, "menu_prefetch_offline"))
        assert.are.equal(0, #findText(items, "menu_prefetch_auto"))
        assert.are.equal(0, #findText(items, "menu_frequency"))
        assert.are.equal(0, #findText(items, "menu_series_context"))
        assert.are.equal(1, #findText(items, "menu_ai_fetching"))
        assert.are.equal(1, #findText(items, "menu_import_calibre"))
        assert.are.equal(1, #findText(items, "menu_characters"))
        assert.are.equal(1, #findText(items, "auto_dupe_check_setting_title"))
    end)
end)
