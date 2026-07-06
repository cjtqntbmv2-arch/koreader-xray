-- xray_aihelper_spec.lua
require("spec/spec_helper")

describe("AIHelper", function()
    local AIHelper

    setup(function()
        -- Load the real module
        AIHelper = require("xray_aihelper")
    end)

    describe("sanitize_utf8", function()
        it("should preserve valid ASCII", function()
            local input = "Hello World"
            assert.are.equal("Hello World", AIHelper:sanitize_utf8(input))
        end)

        it("should preserve valid multi-byte UTF-8 (Cyrillic)", function()
            local input = "Привет"
            assert.are.equal("Привет", AIHelper:sanitize_utf8(input))
        end)

        it("should strip invalid continuation bytes", function()
            -- 0x80 is an invalid start byte
            local input = "Hello" .. string.char(0x80) .. "World"
            assert.are.equal("HelloWorld", AIHelper:sanitize_utf8(input))
        end)

        it("should strip truncated multi-byte sequences", function()
            -- "П" is 0xD0 0x9F. If we slice it to 0xD0:
            local input = string.char(0xD0) 
            assert.are.equal("", AIHelper:sanitize_utf8(input))
        end)
    end)

    describe("getChatGPTTokenConfig", function()
        it("should use max_completion_tokens for o1/o3 models", function()
            local param, val = AIHelper:getChatGPTTokenConfig("o1-preview")
            assert.are.equal("max_completion_tokens", param)
        end)

        it("should use max_completion_tokens for gpt-5 models", function()
            local param, val = AIHelper:getChatGPTTokenConfig("gpt-5.4-mini")
            assert.are.equal("max_completion_tokens", param)
        end)

        it("should use max_tokens for deepseek/r1 models", function()
            local param, val = AIHelper:getChatGPTTokenConfig("deepseek-reasoner")
            assert.are.equal("max_tokens", param)
            
            param, val = AIHelper:getChatGPTTokenConfig("deepseek/r1")
            assert.are.equal("max_tokens", param)
        end)

        it("should fallback to max_tokens for gpt-4", function()
            local param, val = AIHelper:getChatGPTTokenConfig("gpt-4")
            assert.are.equal("max_tokens", param)
        end)
    end)

    describe("fixTruncatedJSON", function()
        it("should close missing braces", function()
            local input = '{"name": "test"'
            local fixed = AIHelper:fixTruncatedJSON(input)
            assert.are.equal('{"name": "test"}', fixed)
        end)

        it("should handle nested structures", function()
            local input = '{"chars": [{"name": "Jo"'
            local fixed = AIHelper:fixTruncatedJSON(input)
            assert.are.equal('{"chars": [{"name": "Jo"}]}', fixed)
        end)

        it("should handle strings with braces", function()
            local input = '{"text": "Value with } brace"'
            local fixed = AIHelper:fixTruncatedJSON(input)
            assert.are.equal('{"text": "Value with } brace"}', fixed)
        end)
    end)

    describe("buildComprehensiveRequest", function()
        before_each(function()
            AIHelper.settings = {
                primary_ai = { provider = "gemini", model = "gemini-2.5-flash" },
                reasoning_effort = "medium"
            }
            AIHelper.providers.gemini.api_key = "test_key"
        end)

        it("should build a Gemini request", function()
            local requests = AIHelper:buildComprehensiveRequest("Title", "Author", {})
            -- By default it builds 2 requests (primary and secondary fallback)
            assert.are.equal(2, #requests)
            assert.are.equal("gemini", requests[1].provider)
            assert.is_not_nil(requests[1].url:find("gemini%-2%.5%-flash"))
            assert.are.equal("test_key", requests[1].headers["x-goog-api-key"])
        end)

        it("should include thinkingConfig for Gemini 2.5", function()
            AIHelper.settings.primary_ai.model = "gemini-2.5-flash"
            local requests = AIHelper:buildComprehensiveRequest("Title", "Author", {})
            local body = require("json").decode(requests[1].body)
            assert.is_not_nil(body.generationConfig.thinkingConfig)
            assert.are.equal(4096, body.generationConfig.thinkingConfig.thinkingBudget)
        end)
    end)

    describe("normalizeKeys", function()
        it("should lowercase keys and replace spaces with underscores", function()
            -- normalizeKeys is local, but validateAndCleanData calls it
            local data = { ["Full Name"] = "John", ["Bio Data"] = { ["Birth Date"] = "1900" } }
            local result = AIHelper:validateAndCleanData(data)
            -- validateAndCleanData also transforms the structure, so we check the result of that
            -- but let's test normalizeKeys behavior by looking at what it does to 'data' 
            -- (actually it returns a new table)
        end)
    end)

    describe("loadSettings migration", function()
        it("should apply ui_defaults_migrated_v2 defaults", function()
            local old_open = io.open
            io.open = function(path, mode)
                if path:find("settings.json") then
                    return {
                        read = function(self, fmt)
                            return '{"primary_ai": {"provider": "gemini", "model": "gemini-2.5-flash"}}'
                        end,
                        close = function() end
                    }
                end
                return old_open(path, mode)
            end

            local saved = false
            local old_save = AIHelper.saveSettings
            AIHelper.saveSettings = function(self)
                saved = true
            end

            AIHelper:loadSettings()

            io.open = old_open
            AIHelper.saveSettings = old_save

            assert.is_true(AIHelper.settings.ui_popup_intext)
            assert.is_false(AIHelper.settings.ui_popup_menu)
            assert.is_true(AIHelper.settings.ui_defaults_migrated_v2)
            assert.is_true(saved)
        end)
    end)

    describe("isAnthropic", function()
        it("should return true for claude provider", function()
            assert.is_true(AIHelper:isAnthropic("claude", nil))
        end)

        it("should return false for chatgpt/gemini providers", function()
            assert.is_false(AIHelper:isAnthropic("chatgpt", nil))
            assert.is_false(AIHelper:isAnthropic("gemini", nil))
        end)

        it("should return true for custom provider if format is explicitly anthropic", function()
            AIHelper.providers.custom1.format = "anthropic"
            assert.is_true(AIHelper:isAnthropic("custom1", "https://api.openai.com/v1/chat/completions"))
            AIHelper.providers.custom1.format = nil
        end)

        it("should return false for custom provider if format is explicitly openai", function()
            AIHelper.providers.custom1.format = "openai"
            assert.is_false(AIHelper:isAnthropic("custom1", "https://api.anthropic.com/v1/messages"))
            AIHelper.providers.custom1.format = nil
        end)

        it("should auto-detect anthropic endpoints via URL search", function()
            assert.is_true(AIHelper:isAnthropic("custom1", "https://api.openmodel.ai/v1/messages"))
            assert.is_true(AIHelper:isAnthropic("custom1", "http://localhost:8000/messages"))
            assert.is_false(AIHelper:isAnthropic("custom1", "https://openrouter.ai/api/v1/chat/completions"))
        end)
    end)

    describe("Anthropic request headers", function()
        it("should send only x-api-key for native claude or anthropic.com", function()
            AIHelper.settings.primary_ai = { provider = "claude", model = "claude-3-7-sonnet-latest" }
            AIHelper.providers.claude.api_key = "sk-ant-test"
            local requests = AIHelper:buildComprehensiveRequest("Title", "Author", {})
            local req = requests[1]
            assert.are.equal("sk-ant-test", req.headers["x-api-key"])
            assert.is_nil(req.headers["Authorization"])
        end)

        it("should send only Authorization Bearer for custom slot proxies", function()
            AIHelper.settings.primary_ai = { provider = "custom1", model = "deepseek-v4-flash" }
            AIHelper.providers.custom1.api_key = "openmodel-key"
            AIHelper.providers.custom1.endpoint = "https://api.openmodel.ai/v1/messages"
            local requests = AIHelper:buildComprehensiveRequest("Title", "Author", {})
            local req = requests[1]
            assert.are.equal("Bearer openmodel-key", req.headers["Authorization"])
            assert.is_nil(req.headers["x-api-key"])
        end)
    end)

    describe("saveSettings with keys_to_delete", function()
        it("should update settings and delete specified keys", function()
            local old_open = io.open
            local written_content = nil
            local json = require("json")
            io.open = function(path, mode)
                if path:find("settings.json") and mode == "w" then
                    return {
                        write = function(self, content)
                            written_content = content
                        end,
                        close = function() end
                    }
                end
                return old_open(path, mode)
            end

            -- Setup starting settings
            AIHelper.settings = {
                keep_me = "value",
                delete_me = "value2",
                also_delete_me = "value3"
            }

            -- Save new settings and delete some keys
            AIHelper:saveSettings({ new_key = "new_val" }, { "delete_me", "also_delete_me" })

            io.open = old_open

            assert.is_not_nil(written_content)
            local decoded = json.decode(written_content)
            assert.are.equal("value", decoded.keep_me)
            assert.are.equal("new_val", decoded.new_key)
            assert.is_nil(decoded.delete_me)
            assert.is_nil(decoded.also_delete_me)
        end)
    end)

    describe("createPrompt character rules", function()
        setup(function()
            -- Templates HART laden (Runner läuft im Repo-Root): createPrompt hat keinen
            -- loadLanguage-Guard; mit `or` hinge der Testausgang an der Spec-Reihenfolge
            -- (Crash statt sauberem Fail, wenn .prompts noch nil ist).
            AIHelper.prompts = dofile("xray.koplugin/prompts/en.lua")
        end)

        it("appends completeness and name disambiguation rules for comprehensive_xray", function()
            local prompt = AIHelper:createPrompt("T", "A", { book_text = "text", reading_percent = 50 }, "comprehensive_xray")
            assert.is_true(prompt:find("CHARACTER COMPLETENESS RULES", 1, true) ~= nil)
            assert.is_true(prompt:find("NAME DISAMBIGUATION RULES", 1, true) ~= nil)
        end)

        it("requests ~50 characters at the default description length", function()
            AIHelper.settings = { char_desc_len = 200 }
            local prompt = AIHelper:createPrompt("T", "A", { book_text = "text", reading_percent = 50 }, "comprehensive_xray")
            assert.is_true(prompt:find("(50 normal", 1, true) ~= nil)
        end)

        it("does not append segment mode without the flag", function()
            local prompt = AIHelper:createPrompt("T", "A", { book_text = "text", reading_percent = 50 }, "comprehensive_xray")
            assert.is_true(prompt:find("SEGMENT COMPLETENESS MODE", 1, true) == nil)
        end)

        it("does not append character rules for other sections", function()
            local prompt = AIHelper:createPrompt("T", "A", { book_text = "text", reading_percent = 50 }, "more_terms")
            assert.is_true(prompt:find("CHARACTER COMPLETENESS RULES", 1, true) == nil)
        end)

        it("appends segment completeness mode when context.prefetch_segment is set", function()
            local prompt = AIHelper:createPrompt("T", "A", { book_text = "text", reading_percent = 50, prefetch_segment = true }, "comprehensive_xray")
            assert.is_true(prompt:find("SEGMENT COMPLETENESS MODE", 1, true) ~= nil)
            assert.is_true(prompt:find("applies to NEW characters", 1, true) ~= nil)
        end)

        it("appends a name collision warning to the duplicate check prompt", function()
            local captured
            local orig = AIHelper.executeUnifiedRequest
            AIHelper.executeUnifiedRequest = function(self, prompt)
                captured = prompt
                return { duplicate_pairs = {} }
            end
            AIHelper:findDuplicates("T", "A", { { name = "Aegon Targaryen" } }, "characters", 50)
            AIHelper.executeUnifiedRequest = orig
            assert.is_true(captured ~= nil)
            assert.is_true(captured:find("NAME COLLISION WARNING", 1, true) ~= nil)
        end)
    end)
end)

describe("checkAsyncResult response-shape hardening", function()
    local ok_json = pcall(require, "json")
    if not ok_json then
        print("SKIP: checkAsyncResult shape tests need the json module (SQUASHFS_ROOT)")
    else
        it("returns error_parse instead of crashing on choices without message", function()
            local AIHelper = require("xray_aihelper")
            local path = "/tmp/xray_spec_async_result.json"
            local f = io.open(path, "w")
            f:write("200\nchatgpt\n" .. '{"choices":[{"finish_reason":"content_filter"}]}')
            f:close()
            local data, code = AIHelper:checkAsyncResult(path)
            pcall(os.remove, path)
            assert.is_false(data)
            assert.are.equal("error_parse", code)
        end)

        it("returns error_parse on a non-table JSON body", function()
            local AIHelper = require("xray_aihelper")
            local path = "/tmp/xray_spec_async_result2.json"
            local f = io.open(path, "w")
            f:write("200\nchatgpt\ntrue")
            f:close()
            local data, code = AIHelper:checkAsyncResult(path)
            pcall(os.remove, path)
            assert.is_false(data)
            assert.are.equal("error_parse", code)
        end)
    end
end)
