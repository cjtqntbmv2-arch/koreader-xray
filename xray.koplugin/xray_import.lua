--[[
X-Ray Importer -- adopt a calibre-generated xray.json embedded in the EPUB.

The calibre desktop plugin (repo: calibre-xray) generates the same spoiler-
staged checkpoint data this plugin would fetch on-device, and embeds it as the
zip member `xray/xray.json`. This module imports it once, into exactly the
files a completed on-device prefetch would have left behind:

    <book>.sdr/xray_cache.lua        -- main cache + prefetch manifest + timeline
    <book>.sdr/xray_snapshot_NN.lua  -- one per checkpoint, entity lists only

Afterwards nothing distinguishes imported data from natively prefetched data:
snapshot resolution, word-lookup top-ups and propagateEntityForward all work
unchanged.

The one thing calibre cannot know is device pagination, so the JSON stores each
checkpoint as (chapter anchor, text snippet, percent) and this module resolves
those to page numbers here. See _resolveCheckpointPages.
]]

local M = {}

M.SUPPORTED_SCHEMA = 1

-- Lower-case, collapse whitespace runs, trim. Used to compare the calibre title
-- against the EPUB's own metadata title -- both come from the same OPF, so this
-- only has to survive re-conversion whitespace noise.
function M._normTitle(s)
    s = tostring(s or ""):lower()
    s = s:gsub("%s+", " ")
    s = s:gsub("^ +", ""):gsub(" +$", "")
    return s
end

-- Returns nil when the document may be imported, otherwise a short reason
-- string. Deliberately lenient on authors: the JSON is embedded inside this
-- very EPUB, so title + schema already pin the identity, and calibre's
-- "Surname, First" ordering would false-reject constantly. text_hash is
-- advisory only -- Python's \s and Lua's %s disagree about NBSP, so the two
-- runtimes cannot reproduce each other's hash.
function M:_gateImport(doc, props)
    if type(doc) ~= "table" then return "not a table" end
    local schema = tonumber(doc.schema_version)
    if not schema then return "missing schema_version" end
    if schema > M.SUPPORTED_SCHEMA then
        return "schema " .. tostring(schema) .. " is newer than " .. tostring(M.SUPPORTED_SCHEMA)
    end
    if type(doc.checkpoints) ~= "table" or #doc.checkpoints == 0 then
        return "no checkpoints"
    end
    local want = M._normTitle(doc.book_fingerprint and doc.book_fingerprint.title)
    local have = M._normTitle(props and props.title)
    if want ~= "" and have ~= "" and want ~= have then
        return "title mismatch: '" .. want .. "' vs '" .. have .. "'"
    end
    return nil
end

return M
