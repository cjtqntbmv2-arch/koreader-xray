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

local UIManager = require("ui/uimanager")
local InfoMessage = require("ui/widget/infomessage")

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

-- Char-percent -> device page.
local function pctToPage(pct, page_count)
    local p = math.floor(page_count * (tonumber(pct) or 0) / 100)
    if p < 1 then p = 1 end
    if p > page_count then p = page_count end
    return p
end

-- The embedded JSON is a trust boundary: it rides inside a user-supplied EPUB.
-- The native fetch path coerces every field through ensureString
-- (xray_aihelper.lua:2014-2052) before the UI ever sees it; do the same here.
-- xray_ui.lua calls :sub() on term.definition and concatenates character.role,
-- so a JSON number in either place would crash the reader, not degrade.
local STRING_FIELDS = {
    characters        = { "name", "role", "description", "gender", "occupation" },
    locations         = { "name", "description", "importance" },
    terms             = { "name", "definition", "expanded", "category" },
    historical_figures = { "name", "biography", "role", "importance_in_book", "context_in_book" },
}

local function coerceList(list, kind)
    local fields = STRING_FIELDS[kind]
    for _, e in ipairs(list or {}) do
        if type(e) == "table" then
            for _, f in ipairs(fields) do
                if e[f] ~= nil and type(e[f]) ~= "string" then e[f] = tostring(e[f]) end
            end
            if type(e.aliases) ~= "table" then e.aliases = {} end
        end
    end
    return list or {}
end

-- Characters and locations sort by first appearance (xray_data.lua:164-172),
-- which needs first_page. Terms sort by name and historical figures by role
-- weight, so neither gets stamped. first_seq comes from calibre and is only the
-- tiebreaker.
--
-- sort_order is stamped on EVERY list: main.lua:756-767 re-sorts characters and
-- historical_figures by `sort_order or 9999` on each cache load, and a table of
-- ties feeds Lua's unstable sort an all-false comparator, which is free to
-- permute. The lists arrive from calibre already in the intended order.
local function prepareList(list, kind, page_count)
    list = coerceList(list, kind)
    for i, e in ipairs(list) do
        if (kind == "characters" or kind == "locations") and e.first_pct then
            -- Recomputed every call, not stamped once: a retried import (e.g.
            -- after a font-size change alters KOReader's own page_count) must
            -- not keep a first_page computed against the previous pagination.
            e.first_page = pctToPage(e.first_pct, page_count)
        end
        e.sort_order = i
    end
    return list
end

-- fp.authors is JSON from the trust boundary: calibre normally emits an
-- array, but a hand-edited or malformed xray.json can hand us a bare string
-- (kept verbatim as the one author) or an array containing a non-string
-- element (coerced with tostring). table.concat raises on either shape.
local function joinAuthors(authors)
    if type(authors) == "string" then return authors end
    if type(authors) ~= "table" then return "" end
    local out = {}
    for _, a in ipairs(authors) do table.insert(out, tostring(a)) end
    return table.concat(out, ", ")
end

-- Build exactly what a completed on-device prefetch leaves behind: the main
-- cache (entities of the LAST checkpoint + the one true timeline + the prefetch
-- manifest) and one snapshot table per checkpoint.
function M:_buildImportedCache(doc_json, mapped)
    local page_count = self.ui.document:getPageCount()
    local fp = doc_json.book_fingerprint or {}

    local snapshots = {}
    for n, m in ipairs(mapped) do
        local src = doc_json.checkpoints[m.index].snapshot or {}
        snapshots[n] = {
            checkpoint_index = n,
            page = m.page,
            percent = m.percent,
            characters = prepareList(src.characters, "characters", page_count),
            locations = prepareList(src.locations, "locations", page_count),
            terms = prepareList(src.terms, "terms", page_count),
            historical_figures = prepareList(src.historical_figures, "historical_figures", page_count),
        }
    end

    local last = snapshots[#snapshots]

    -- D2: exactly one timeline, in the main cache, page-anchored. An event
    -- with a missing or non-numeric pct cannot be placed on the spoiler axis;
    -- leave page nil so visibleTimeline() (xray_prefetch.lua) falls into its
    -- conservative hidden branch, instead of pctToPage's min-1 clamp planting
    -- it on page 1 -- which would show it to the reader from checkpoint 1
    -- onward and invert the spoiler-safety direction.
    local timeline = {}
    for _, ev in ipairs(doc_json.timeline or {}) do
        local pct = tonumber(ev.pct)
        table.insert(timeline, {
            page = pct and pctToPage(pct, page_count),
            chapter = tostring(ev.chapter or ""),
            event = tostring(ev.event or ""),
        })
    end

    local manifest_cps = {}
    for _, m in ipairs(mapped) do
        table.insert(manifest_cps, { page = m.page, percent = m.percent })
    end

    -- A partial calibre run (quota, crash) leaves the tail of the book
    -- uncovered. Append the device's own checkpoints beyond the imported
    -- boundary so the normal prefetch can finish the job: _nextPendingCheckpoint
    -- picks the first manifest entry above last_fetch_page with no snapshot file.
    local completed = true
    if doc_json.complete ~= true then
        local device_cps = (self.computeCheckpoints and self:computeCheckpoints()) or {}
        for _, cp in ipairs(device_cps) do
            if cp.page > last.page then
                table.insert(manifest_cps, { page = cp.page, percent = cp.percent })
                completed = false
            end
        end
    end

    local book_data = {
        book_title = tostring(fp.title or ""),
        author = joinAuthors(fp.authors),
        book_type = doc_json.book_type,
        characters = last.characters,
        locations = last.locations,
        terms = last.terms,
        historical_figures = last.historical_figures,
        timeline = timeline,
        last_fetch_page = last.page,
        prefetch = {
            checkpoints = manifest_cps,
            created_at = os.time(),
            completed = completed or nil,
        },
    }

    return book_data, snapshots
end

-- Import a validated document. The anchor resolution and the snapshot writes run
-- on a coroutine that yields after every checkpoint, so a multi-checkpoint
-- findAllText sweep never freezes the reader for the whole run -- the same
-- cooperative pattern as scanMentionsAsync (xray_chapteranalyzer.lua:1191).
--
-- ponytail: each individual findAllText scan and each saveSnapshot still block
-- for their duration (a yield between them cannot interrupt them). Measure on a
-- Clara BW before reaching for asyncSaveCache's chunked serializer or a
-- Trapper subprocess.
--
-- prefetch_active is held for the whole run: it is the flag maybeStartAutoPrefetch
-- (xray_prefetch.lua:192) and updateSnapshotViewForPage already honour, so the
-- network prefetch cannot start on top of us and the view cannot churn mid-import.
function M:importEmbeddedXray(doc_json)
    local book_path = self.ui.document.file
    self.prefetch_active = true

    local function finish(ok, err)
        self.prefetch_active = false
        if not ok then
            self:log("XRayPlugin: import failed: " .. tostring(err))
            -- Orphan snapshot files without a manifest are worse than none:
            -- a later partial write could mix them with fresh data.
            pcall(function() self.cache_manager:deleteSnapshots(book_path) end)
            UIManager:show(InfoMessage:new{
                text = self.loc:t("import_failed") or "Could not import the embedded X-Ray data.",
                timeout = 4,
            })
            return
        end
        self:invalidateSnapshotExistsCache()
        local page = (self.ui and self.ui.getCurrentPage) and self.ui:getCurrentPage() or nil
        if page then self:updateSnapshotViewForPage(page) end

        local msg
        if self.book_data.prefetch.completed then
            msg = self.loc:t("import_done") or "X-Ray data imported from calibre."
        else
            local pct = math.floor(self.book_data.last_fetch_page / self.ui.document:getPageCount() * 100)
            msg = string.format(
                self.loc:t("import_done_partial") or "X-Ray data imported (prepared to %d%%).", pct)
        end
        UIManager:show(InfoMessage:new{ text = msg, timeout = 4 })
    end

    local co = coroutine.create(function()
        local mapped = self:_resolveCheckpointPages(doc_json, coroutine.yield)
        if not mapped then error("no usable checkpoints for this book", 0) end

        local book_data, snapshots = self:_buildImportedCache(doc_json, mapped)

        -- saveSnapshot/saveCache RETURN false on I/O failure, they do not throw.
        -- Adopt the main cache only once every gating snapshot is durable.
        for i, snap in ipairs(snapshots) do
            if not self.cache_manager:saveSnapshot(book_path, i, snap) then
                error("snapshot " .. tostring(i) .. " could not be written", 0)
            end
            coroutine.yield()
        end
        if not self.cache_manager:saveCache(book_path, book_data) then
            error("main cache could not be written", 0)
        end

        self.book_data = book_data
        self.timeline = book_data.timeline
    end)

    local function step()
        if self.destroyed then
            self.prefetch_active = false
            return
        end
        local ok, err = coroutine.resume(co)
        if not ok then
            finish(false, err)
        elseif coroutine.status(co) == "dead" then
            finish(true)
        else
            UIManager:scheduleIn(0.01, step)
        end
    end

    self:log("XRayPlugin: importing embedded X-Ray data for " .. tostring(book_path))
    UIManager:show(InfoMessage:new{
        text = self.loc:t("import_running") or "Preparing X-Ray data from calibre…",
        timeout = 3,
    })
    step()
end

return M
