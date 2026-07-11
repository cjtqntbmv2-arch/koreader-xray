require("spec/spec_helper")

describe("Prompt output language and historical_figures intent", function()
    local AIHelper

    before_each(function()
        AIHelper = require("xray_aihelper")
        AIHelper.settings = {}
    end)

    local function build_de()
        AIHelper.current_language = "de"
        AIHelper.prompts = dofile("xray.koplugin/prompts/de.lua")
        return AIHelper:createPrompt("Buch", "Autor", { reading_percent = 50 }, "comprehensive_xray")
    end

    local function build_en()
        AIHelper.current_language = "en"
        AIHelper.prompts = dofile("xray.koplugin/prompts/en.lua")
        return AIHelper:createPrompt("Book", "Author", { reading_percent = 50 }, "comprehensive_xray")
    end

    it("de timeline uses the German example, not the English one", function()
        local prompt = build_de()
        assert.is_not_nil(prompt:find("Der Held entkommt der brennenden Stadt", 1, true))
        assert.is_nil(prompt:find("The hero escapes the burning city", 1, true))
    end)

    it("de output-language directive is in system_instruction with the verbatim exemption", function()
        build_de()
        local si = AIHelper.prompts.system_instruction
        assert.is_not_nil(si:find("AUSGABESPRACHE", 1, true))
        assert.is_not_nil(si:find("AUSNAHME", 1, true))
    end)

    it("de prompt tail (context_footer) also carries the directive", function()
        local prompt = build_de()
        assert.is_not_nil(prompt:find("AUSGABESPRACHE", 1, true))
    end)

    it("de historical_figures clarification is present", function()
        local prompt = build_de()
        assert.is_not_nil(prompt:find("KLARSTELLUNG", 1, true))
    end)

    it("en timeline keeps the English example", function()
        local prompt = build_en()
        assert.is_not_nil(prompt:find("The hero escapes the burning city", 1, true))
    end)

    it("en output-language directive is in system_instruction", function()
        build_en()
        local si = AIHelper.prompts.system_instruction
        assert.is_not_nil(si:find("OUTPUT LANGUAGE", 1, true))
        assert.is_not_nil(si:find("verbatim", 1, true))
    end)
end)
