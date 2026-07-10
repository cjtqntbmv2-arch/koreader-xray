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
        -- Add saveSettings mock function for toggle tests
        plugin.ai_helper.saveSettings = function(self, settings_update)
            if type(settings_update) == "table" then
                for k, v in pairs(settings_update) do
                    self.settings[k] = v
                end
            end
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

    local function findItem(items, needle)
        for _, item in ipairs(items) do
            if type(item.text) == "string" and item.text:find(needle, 1, true) then
                return item
            end
            if type(item.sub_item_table) == "table" then
                local found = findItem(item.sub_item_table, needle)
                if found then return found end
            end
        end
        return nil
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
        -- These three items live in fetch_items and must always be visible
        assert.are.equal(1, #findText(items, "menu_book_mode"))
        assert.are.equal(1, #findText(items, "menu_desc_length_settings"))
        assert.are.equal(1, #findText(items, "spoiler_preference_title"))
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
        -- These three items live in fetch_items and must always be visible even when disabled
        assert.are.equal(1, #findText(items, "menu_book_mode"))
        assert.are.equal(1, #findText(items, "menu_desc_length_settings"))
        assert.are.equal(1, #findText(items, "spoiler_preference_title"))
    end)

    it("toggle callback updates ai_fetching_enabled setting", function()
        local plugin = mkPlugin()
        local items = plugin:getSubMenuItems()
        -- Find the toggle item (nested in Settings > sub_item_table)
        local toggle = findItem(items, "menu_ai_fetching")
        assert.is_not_nil(toggle)
        -- Default state: ai_fetching should be enabled (no setting = default true)
        assert.is_true(toggle.checked_func())
        -- Call the callback to toggle it
        toggle.callback()
        -- After toggle, it should be disabled
        assert.is_false(toggle.checked_func())
        assert.is_false(plugin.ai_helper.settings.ai_fetching_enabled)
        -- Toggle again to re-enable
        toggle.callback()
        assert.is_true(toggle.checked_func())
        assert.is_true(plugin.ai_helper.settings.ai_fetching_enabled)
    end)
end)
