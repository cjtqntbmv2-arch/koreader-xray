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

-- Shortest snippet we hand to findAllText. Below this, a phrase calibre proved
-- unique in its normalized text is not plausibly unique in the rendered text.
local MIN_SNIPPET = 12

-- Same narrative-chapter derivation the device's own computeCheckpoints uses
-- (xray_prefetch.lua:41-51). Must stay in step with it: calibre's checkpoint
-- offsets were derived from the SAME filter over the SAME table of contents
-- (its NON_NARRATIVE list is a port of xray_data.lua's), so a chapter anchor
-- only resolves exactly if we filter identically.
function M:_narrativeToc()
    local doc = self.ui and self.ui.document
    if not doc or not doc.getToc then return {} end
    local page_count = doc:getPageCount()
    local toc = doc:getToc() or {}
    local out = {}
    for _, entry in ipairs(toc) do
        if entry.page and entry.page >= 1 and entry.page <= page_count
            and not self:isNonNarrativeChapter(entry.title) then
            table.insert(out, entry)
        end
    end
    table.sort(out, function(a, b) return a.page < b.page end)
    return out
end

-- A calibre chapter anchor names the chapter that ENDS at the checkpoint, so
-- the device page is the page before the next narrative chapter starts --
-- byte-identical to computeCheckpoints' `end_page = next_start - 1`.
-- `cursor` makes the scan monotonic, so books with repeated titles
-- ("Chapter 1" in several parts) resolve each occurrence in reading order.
function M:_tocEndPage(narrative, toc_title, cursor, page_count)
    local want = M._normTitle(toc_title)
    if want == "" then return nil end
    for i = cursor + 1, #narrative do
        if M._normTitle(narrative[i].title) == want then
            local next_start = narrative[i + 1] and narrative[i + 1].page
            local page = (next_start and (next_start - 1)) or page_count
            -- Two headings can render on the same page (coarse e-reader
            -- pagination); if that page is 1, next_start - 1 is 0, which
            -- would silently drop checkpoint 1 in _resolveCheckpointPages.
            -- Clamp, don't drop: the chapter genuinely ends on page 1, so
            -- activating its snapshot there shows the reader data they have
            -- actually reached -- computeCheckpoints guards the identical
            -- end_page the same way (`>= 1`).
            if page < 1 then page = 1 end
            return page, i
        end
    end
    return nil
end

-- findAllText (NOT findText -- see Global Constraints) scans the whole document
-- and returns every hit. We ask for two: the snippet's END is the spoiler
-- boundary, so a second occurrence anywhere means the first hit might sit
-- before the real boundary and would activate this snapshot early.
--
-- ponytail: one scan per unanchored checkpoint, no retry with a shorter tail.
-- This is a whole-book scan (KOReader runs its own in a subprocess for exactly
-- that reason) and it only fires for densified mid-chapter checkpoints. If real
-- books miss often -- e.g. because crengine's flattened text spaces words
-- differently than calibre's normalizer -- retry with the last ~60 chars before
-- reaching for anything cleverer.
function M:_snippetPage(snippet)
    if type(snippet) ~= "string" or #snippet < MIN_SNIPPET then return nil end
    local doc = self.ui and self.ui.document
    if not doc or not doc.findAllText then return nil end
    local ok, res = pcall(function()
        return doc:findAllText(snippet, false, 0, 2, false)
    end)
    if not ok or type(res) ~= "table" then return nil end
    if #res ~= 1 then return nil end   -- 0 = not found, >1 = ambiguous
    return res[1].page
end

-- Map every checkpoint to a device page. Three tiers, most exact first:
-- chapter anchor -> unique text snippet -> percent.
--
-- Invariants the writer downstream relies on:
--   * pages strictly ascend (resolveSnapshotIndexForPage picks the largest
--     cp.page <= current_page; equal pages would let the richer snapshot win)
--   * a COMPLETE document's last entry is exactly page_count / percent 100
--
-- Only a complete document's final checkpoint is the end of the book. A partial
-- calibre run (quota, crash) stops mid-book; pinning its last checkpoint to
-- page_count would claim coverage that does not exist, and the device
-- checkpoints appended in _buildImportedCache would have nothing left to fetch.
--
-- When a mapped page does not exceed its predecessor we DROP that checkpoint --
-- i.e. the LATER, richer one of a colliding pair. The reader keeps the earlier,
-- smaller snapshot for longer: spoiler-safe. Lowering a page instead would
-- activate a richer snapshot before the reader got there. Note this never drops
-- checkpoint 1 (the percent tier always yields a page >= 1), which matters:
-- resolveSnapshotIndexForPage shows the SMALLEST existing snapshot to a reader
-- who has not reached the first checkpoint, so slot 1 must hold the poorest data.
function M:_resolveCheckpointPages(doc_json, yield_fn)
    local doc = self.ui and self.ui.document
    if not doc or not doc.getPageCount then return nil end
    local page_count = doc:getPageCount()
    -- Below 3 pages there is no room for a staged view.
    if not page_count or page_count < 3 then return nil end

    local narrative = self:_narrativeToc()
    local cps = doc_json.checkpoints
    local out, cursor, prev = {}, 0, 0

    for i, cp in ipairs(cps) do
        local is_final = (i == #cps) and (doc_json.complete == true)
        local page
        if is_final then
            page = page_count
        else
            -- type()-guarded: json.decode may give a truthy null sentinel.
            local anchor = cp.chapter_anchor
            if type(anchor) == "table" and anchor.toc_title then
                local p, idx = self:_tocEndPage(narrative, anchor.toc_title, cursor, page_count)
                if p then page, cursor = p, idx end
            end
            if not page then page = self:_snippetPage(cp.snippet_anchor) end
            if not page then
                -- Last resort. calibre's percent is a CHARACTER percent, so this
                -- can land below the true device page on a book with uneven
                -- character density. Same formula as computeCheckpoints' no-TOC
                -- fallback, but not the same guarantee -- see the plan's
                -- design-decision 3.
                page = math.max(1, math.floor(page_count * (tonumber(cp.percent) or 0) / 100))
            end
            if page >= page_count then page = page_count - 1 end
        end

        if page > prev then
            -- Multiply before dividing: page/page_count*100 loses precision in
            -- doubles for some integer pairs (e.g. 29/100*100 = 28.999999999999996,
            -- flooring to 28 instead of 29); page*100/page_count does not.
            local percent = is_final and 100 or math.floor(page * 100 / page_count)
            table.insert(out, { index = i, page = page, percent = percent })
            prev = page
        else
            self:log("XRayPlugin: import drops checkpoint " .. tostring(i)
                .. " (page " .. tostring(page) .. " <= " .. tostring(prev) .. ")")
        end

        if yield_fn then yield_fn() end
    end

    if #out == 0 then return nil end
    return out
end

return M
