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
