-- spec/xray_updater_spec.lua — Config-Erhalt und %-sichere Key-Injektion
require("spec.spec_helper")

package.loaded["ui/widget/confirmbox"] = package.loaded["ui/widget/confirmbox"]
    or { new = function(_, o) return o end }

local updater = require("xray_updater")

local function write(path, content)
    local f = io.open(path, "w")
    f:write(content)
    f:close()
end

describe("xray_updater hardening", function()
    it("_injectValue keeps percent signs literal", function()
        local out = updater._injectValue('custom1_endpoint = ""', "custom1_endpoint", "https://x.test/v1%2Fchat")
        assert.are.equal('custom1_endpoint = "https://x.test/v1%2Fchat"', out)
    end)

    it("_injectValue leaves content untouched for empty values", function()
        local content = 'gemini_api_key = ""'
        assert.are.equal(content, updater._injectValue(content, "gemini_api_key", ""))
        assert.are.equal(content, updater._injectValue(content, "gemini_api_key", nil))
    end)

    it("restoreConfigBackup restores keys after an interrupted update", function()
        local cfg = "/tmp/xray_spec_config.lua"
        local bak = cfg .. ".bak"
        write(cfg, 'return { gemini_api_key = "" }')
        write(bak, 'return { gemini_api_key = "SECRET" }')
        updater.restoreConfigBackup(cfg)
        local restored = dofile(cfg)
        assert.are.equal("SECRET", restored.gemini_api_key)
        assert.is_nil(io.open(bak, "r"))
        pcall(os.remove, cfg)
    end)

    it("restoreConfigBackup only cleans up when the live config still has keys", function()
        local cfg = "/tmp/xray_spec_config2.lua"
        local bak = cfg .. ".bak"
        write(cfg, 'return { gemini_api_key = "LIVE" }')
        write(bak, 'return { gemini_api_key = "OLD" }')
        updater.restoreConfigBackup(cfg)
        local live = dofile(cfg)
        assert.are.equal("LIVE", live.gemini_api_key)
        assert.is_nil(io.open(bak, "r"))
        pcall(os.remove, cfg)
    end)
end)
