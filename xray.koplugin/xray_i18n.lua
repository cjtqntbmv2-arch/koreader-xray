--[[
xray_i18n.lua -- minimal gettext-style lookup, self-contained.

KOReader's own _() does not find plugin-private .po catalogs on this build,
and language switching does not retranslate already-loaded strings either
(changeLang is broken on measured hardware) -- this reads one .po file, once,
at require time, and never revisits it.

Usage: `local _ = require("xray_i18n")`, then `_("Characters")`. Never name a
loop/throwaway variable `_` elsewhere in this plugin -- it shadows this
function and has crashed a real run.
]]

-- Locates this file's own directory, so the .po lookup works whether the
-- module was found via an absolute path (KOReader's plugin loader) or a
-- relative one (a busted spec run from the repo root).
local function scriptDir()
    local source = debug.getinfo(1, "S").source:sub(2)
    return source:match("(.*/)") or "./"
end

-- The user's explicit setting wins; gettext's own detection is the fallback
-- for a language never explicitly chosen; "en" is last because the source
-- strings already are English.
local function detectLanguage()
    local ok, lang = pcall(function()
        local configured = G_reader_settings and G_reader_settings:readSetting("language")
        if configured and configured ~= "" then return configured end
        return require("gettext").current_lang
    end)
    if ok and lang and lang ~= "" then return lang end
    return "en"
end

-- KOReader language codes carry a region tag ("de_DE"); only the primary
-- subtag selects a catalog file.
local function catalogCode(lang)
    return (tostring(lang or ""):match("^(%a+)") or ""):lower()
end

local function unescape(s)
    return (s:gsub("\\(.)", function(c) return c == "n" and "\n" or c end))
end

-- Parses "msgid \"...\"" / "msgstr \"...\"" pairs, including the
-- continuation lines gettext uses to wrap long strings (a bare "..." line
-- directly below). No comments, no msgctxt, no plural forms -- this
-- plugin's strings need none of them.
local function parsePo(path)
    local fh = io.open(path, "r")
    if not fh then return nil end

    local strings = {}
    local msgid, msgstr, in_msgstr = nil, nil, false
    local function commit()
        if msgid and msgid ~= "" and msgstr and msgstr ~= "" then
            strings[unescape(msgid)] = unescape(msgstr)
        end
        msgid, msgstr, in_msgstr = nil, nil, false
    end

    for line in fh:lines() do
        local id = line:match('^msgid%s+"(.*)"%s*$')
        local str = line:match('^msgstr%s+"(.*)"%s*$')
        local cont = line:match('^%s*"(.*)"%s*$')
        if id then
            commit()
            msgid = id
        elseif str then
            msgstr, in_msgstr = str, true
        elseif cont and in_msgstr then
            msgstr = msgstr .. cont
        elseif cont and msgid and not msgstr then
            msgid = msgid .. cont
        end
    end
    commit()
    fh:close()
    return strings
end

local strings = {}
local code = catalogCode(detectLanguage())
if code ~= "" and code ~= "en" then
    local ok, parsed = pcall(parsePo, scriptDir() .. "languages/" .. code .. ".po")
    if ok and parsed then strings = parsed end
end

return setmetatable({}, { __call = function(_self, key) return strings[key] or key end })
