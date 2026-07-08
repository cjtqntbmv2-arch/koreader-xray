-- spec/xray_logger_spec.lua — Logging-Gate: kein Flash-Write ohne debug_logging
require("spec.spec_helper")

-- spec_helper stubbt xray_logger; für diesen Spec das ECHTE Modul laden
local logger_stub = package.loaded["xray_logger"]
package.loaded["xray_logger"] = nil
local Logger = require("xray_logger")

describe("xray_logger gate", function()
    local log_file = "./spec/xray.log"

    local function cleanup()
        os.remove(log_file)
        os.remove(log_file .. ".old")
    end

    it("does not write when disabled (default)", function()
        cleanup()
        Logger.path = "./spec"
        Logger.enabled = false
        Logger:log("should not appear")
        local f = io.open(log_file, "r")
        assert.falsy(f)
        if f then f:close() end
        cleanup()
    end)

    it("writes when enabled", function()
        cleanup()
        Logger.path = "./spec"
        Logger.enabled = true
        Logger:log("hello gate")
        local f = io.open(log_file, "r")
        assert.truthy(f)
        local content = f:read("*a")
        f:close()
        assert.is_true(content:find("hello gate", 1, true) ~= nil)
        Logger.enabled = false
        cleanup()
    end)
end)

-- Stub für alle danach laufenden Spec-Dateien wiederherstellen
package.loaded["xray_logger"] = logger_stub
