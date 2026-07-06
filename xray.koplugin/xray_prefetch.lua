-- Offline prefetch: spoiler-staged checkpoint snapshots.
-- Design decisions (D1-D6) and task plan:
-- docs/superpowers/plans/2026-07-06-checkpoint-prefetch.md
local logger = require("logger")
local plugin_path = ((...) or ""):match("(.-)[^%.]+$") or ""

local M = {}

local MAX_CHECKPOINTS = 10   -- D1: target count
local HARD_CAP = 12          -- D1 addendum: absolute upper bound
local MAX_INTERVAL_PCT = 15  -- D1 addendum: max interval width in % of book length

-- Thin an ascending list of pages evenly down to `target` entries.
-- The last entry (book end) is always kept.
local function thinTo(pages, target)
    if #pages <= target then return pages end
    local out = {}
    local step = #pages / target
    for i = 1, target do
        out[i] = pages[math.floor(i * step + 0.5)]
    end
    out[target] = pages[#pages]
    local deduped = {}
    for _, p in ipairs(out) do
        if deduped[#deduped] ~= p then table.insert(deduped, p) end
    end
    return deduped
end

-- Compute the checkpoint list for the current book (D1):
-- narrative chapter end pages, thinned to ~MAX_CHECKPOINTS, then densified so
-- no interval (including the leading gap) exceeds MAX_INTERVAL_PCT of the
-- book; hard-capped at HARD_CAP. Books without a usable TOC get fixed 10%
-- steps. The last checkpoint is always the book end with percent = 100.
function M:computeCheckpoints()
    local doc = self.ui and self.ui.document
    if not doc or not doc.getPageCount then return nil end
    local page_count = doc:getPageCount()
    if not page_count or page_count < 1 then return nil end

    -- 1. Anchors: end pages of narrative chapters
    local toc = (doc.getToc and doc:getToc()) or {}
    local narrative = {}
    for _, entry in ipairs(toc) do
        if entry.page and entry.page >= 1 and entry.page <= page_count
            and not self:isNonNarrativeChapter(entry.title) then
            table.insert(narrative, entry)
        end
    end
    table.sort(narrative, function(a, b) return a.page < b.page end)

    local pages = {}
    for i = 1, #narrative do
        local next_start = narrative[i + 1] and narrative[i + 1].page
        local end_page = next_start and (next_start - 1) or page_count
        if end_page >= 1 and end_page <= page_count and pages[#pages] ~= end_page then
            table.insert(pages, end_page)
        end
    end
    if pages[#pages] ~= page_count then table.insert(pages, page_count) end

    if #pages < 2 then
        -- Fallback (D1): no usable TOC -> fixed 10% steps
        pages = {}
        for pct = 10, 100, 10 do
            local p = math.max(1, math.floor(page_count * pct / 100))
            if pages[#pages] ~= p then table.insert(pages, p) end
        end
        pages[#pages] = page_count
    else
        -- 2. Thin to the target count
        pages = thinTo(pages, MAX_CHECKPOINTS)
        -- 3. Densify: no interval wider than MAX_INTERVAL_PCT (incl. leading gap)
        local max_gap = math.max(1, math.floor(page_count * MAX_INTERVAL_PCT / 100))
        local densified = {}
        local prev = 0
        for _, p in ipairs(pages) do
            local gap = p - prev
            if gap > max_gap then
                local parts = math.ceil(gap / max_gap)
                for j = 1, parts - 1 do
                    local mid = prev + math.floor(gap * j / parts)
                    if mid > (densified[#densified] or 0) and mid < p then
                        table.insert(densified, mid)
                    end
                end
            end
            table.insert(densified, p)
            prev = p
        end
        -- 4. Hard cap (may re-widen some intervals -- the cap wins)
        pages = thinTo(densified, HARD_CAP)
    end

    local checkpoints = {}
    for i, p in ipairs(pages) do
        local percent = math.floor(p / page_count * 100)
        if i == #pages then percent = 100 end
        table.insert(checkpoints, { page = p, percent = percent })
    end
    return checkpoints
end

-- ── Prefetch loop (D3) ─────────────────────────────────────────────────────
-- Sequentially runs continueWithFetch once per pending checkpoint. Instead of
-- threading a completion callback through the 13 exit paths of the fetch
-- pipeline, the loop polls bg_fetch_active; a step counts as successful when
-- book_data.last_fetch_page has reached the checkpoint page (set only by the
-- fetch success path). Every finished checkpoint is persisted immediately, so
-- a missing snapshot file doubles as the resume marker.
local PREFETCH_POLL_SECONDS = 1
local PREFETCH_MAX_TICKS = 600 -- ponytail: 10 min timeout per checkpoint, then stop with resume

function M:isPrefetchComplete()
    local manifest = self.book_data and self.book_data.prefetch
    return (manifest and manifest.completed) == true
end

function M:startOfflinePrefetch(is_silent)
    if self.prefetch_active then return end
    if self.bg_fetch_active or self.bg_fetch_pending then
        if not is_silent then
            self:showPrefetchInfo(self.loc:t("prefetch_busy") or "A fetch is already running. Try again in a moment.")
        end
        return
    end
    if not self.ui or not self.ui.document then return end

    local spoiler_setting = self.ai_helper and self.ai_helper.settings and self.ai_helper.settings.spoiler_setting or "spoiler_free"
    if spoiler_setting == "full_book" then
        -- D4: full_book needs no checkpoints -- one normal full fetch equals it.
        -- Drop any leftover snapshot view first so the fetch guard lets it pass.
        if self.applySnapshot then self:applySnapshot(nil) end
        self:fetchFromAI()
        return
    end

    local NetworkMgr = require("ui/network/manager")
    if not (NetworkMgr:isConnected() and NetworkMgr:isOnline()) then
        if not is_silent then
            self:showPrefetchInfo(self.loc:t("prefetch_offline") or "No internet connection.")
        end
        return
    end
    if not (self.ai_helper and self.ai_helper.hasApiKey and self.ai_helper:hasApiKey()) then
        if not is_silent then
            self:showPrefetchInfo(self.loc:t("prefetch_no_key") or "No API key configured.")
        end
        return
    end

    if not self.cache_manager then
        self.cache_manager = require(plugin_path .. "xray_cachemanager"):new()
    end
    self.book_data = self.book_data or self.cache_manager:loadCache(self.ui.document.file) or {}

    local manifest = self.book_data.prefetch
    if not manifest or not manifest.checkpoints or #manifest.checkpoints == 0 then
        local checkpoints = self:computeCheckpoints()
        if not checkpoints then
            if not is_silent then
                self:showPrefetchInfo(self.loc:t("prefetch_failed") or "Could not analyze the book structure.")
            end
            return
        end
        manifest = { checkpoints = checkpoints, created_at = os.time() }
        self.book_data.prefetch = manifest
        self.cache_manager:asyncSaveCache(self.ui.document.file, self.book_data)
    end
    if manifest.completed then
        if not is_silent then
            self:showPrefetchInfo(self.loc:t("prefetch_already_done") or "This book is already prepared for offline reading.")
        end
        return
    end

    self.prefetch_active = true
    self.prefetch_cancelled = false
    self.prefetch_silent = is_silent and true or false
    self:log("XRayPlugin: Offline prefetch started (" .. tostring(#manifest.checkpoints) .. " checkpoints)")
    self:_prefetchNext()
end

-- Find the first open checkpoint: no snapshot file yet AND its page lies above
-- the already fetched data boundary. Checkpoints at or below the boundary can
-- no longer be snapshotted without a context leak (the merged data already
-- knows later content); the tolerant D4 resolution rule covers those readers.
function M:_nextPendingCheckpoint()
    local manifest = self.book_data and self.book_data.prefetch
    if not manifest or not manifest.checkpoints then return nil end
    local covered = self.book_data.last_fetch_page or 0
    for i, cp in ipairs(manifest.checkpoints) do
        if cp.page > covered and not self.cache_manager:snapshotExists(self.ui.document.file, i) then
            return i, cp
        end
    end
    return nil
end

function M:_prefetchNext()
    if self.destroyed or not self.ui or not self.ui.document then
        self.prefetch_active = false
        return
    end
    if self.prefetch_cancelled then
        self:_finishPrefetch(false)
        return
    end
    local idx, cp = self:_nextPendingCheckpoint()
    if not idx then
        self:_finishPrefetch(true)
        return
    end

    local manifest = self.book_data.prefetch
    self:_showPrefetchProgress(idx, #manifest.checkpoints)

    local is_update = (self.timeline and #self.timeline > 0) and true or false
    local last_fetch_page = self.book_data.last_fetch_page
    self:log(string.format("XRayPlugin: Prefetch checkpoint %d/%d (page %d, %d%%)",
        idx, #manifest.checkpoints, cp.page, cp.percent))
    self:continueWithFetch(cp.percent, is_update, last_fetch_page, true, cp.page)
    self:_watchPrefetchStep(idx, cp, 0)
end

function M:_watchPrefetchStep(idx, cp, ticks)
    local ok_ui, UIManager = pcall(require, "ui/uimanager")
    if not ok_ui or not UIManager then
        self:_finishPrefetch(false)
        return
    end
    UIManager:scheduleIn(PREFETCH_POLL_SECONDS, function()
        if self.destroyed then return end
        if self.bg_fetch_active and not self.prefetch_cancelled then
            if ticks >= PREFETCH_MAX_TICKS then
                self:log("XRayPlugin: Prefetch checkpoint " .. idx .. " timed out")
                self:_finishPrefetch(false)
                return
            end
            self:_watchPrefetchStep(idx, cp, ticks + 1)
            return
        end
        -- Fetch call ended: success <=> the data boundary reached the checkpoint page
        if (self.book_data and self.book_data.last_fetch_page or 0) >= cp.page then
            local snap = {
                checkpoint_index = idx,
                page = cp.page,
                percent = cp.percent,
                characters = self.characters or {},
                locations = self.locations or {},
                terms = self.terms or {},
                historical_figures = self.historical_figures or {},
            }
            self.cache_manager:saveSnapshot(self.ui.document.file, idx, snap)
            if self.invalidateSnapshotExistsCache then self:invalidateSnapshotExistsCache() end
            self:_prefetchNext()
        else
            self:_finishPrefetch(false)
        end
    end)
end

function M:_finishPrefetch(success)
    self.prefetch_active = false
    self:_closePrefetchProgress()

    local manifest = self.book_data and self.book_data.prefetch
    local done, total = 0, 0
    if manifest then
        total = #manifest.checkpoints
        for i = 1, total do
            if self.cache_manager:snapshotExists(self.ui.document.file, i) then done = done + 1 end
        end
        manifest.completed = (self:_nextPendingCheckpoint() == nil) and true or nil
        self.cache_manager:asyncSaveCache(self.ui.document.file, self.book_data)

        if success and manifest.completed then
            -- The dupe check was suppressed during the prefetch (guard in
            -- runPostFetchDuplicateCheck) -- run it once against the full data.
            local props = self.ui and self.ui.document and self.ui.document.getProps
                and self.ui.document:getProps() or {}
            self:runPostFetchDuplicateCheck(props.title or "", props.authors or "", 100, true)
        end
    end

    if not self.prefetch_silent then
        local msg
        if manifest and manifest.completed then
            msg = self.loc:t("prefetch_done") or "Book is ready for offline reading."
        elseif self.prefetch_cancelled then
            msg = self.loc:t("prefetch_cancelled_msg", done, total)
                or string.format("Cancelled. %d of %d checkpoints kept - will resume next time.", done, total)
        else
            msg = self.loc:t("prefetch_partial", done, total)
                or string.format("Interrupted. %d of %d checkpoints done - will resume next time.", done, total)
        end
        self:showPrefetchInfo(msg)
    end
    self:log("XRayPlugin: Prefetch finished (" .. done .. "/" .. tostring(total) .. " snapshots)")

    -- Bring the display in line with the new snapshot situation. The fetch
    -- chain replaced the self.* lists, so force a fresh apply even if the
    -- index looks unchanged.
    if self.updateSnapshotViewForPage and self.ui and self.ui.getCurrentPage then
        self.active_snapshot_index = nil
        self.active_snapshot_page = nil
        self:updateSnapshotViewForPage(self.ui:getCurrentPage())
    end
end

-- ── UI helpers (manual mode) ───────────────────────────────────────────────
function M:showPrefetchInfo(text)
    local ok_im, InfoMessage = pcall(require, "ui/widget/infomessage")
    local ok_ui, UIManager = pcall(require, "ui/uimanager")
    if ok_im and ok_ui and InfoMessage and UIManager then
        UIManager:show(InfoMessage:new{ text = text, timeout = 4 })
    end
end

function M:_showPrefetchProgress(idx, total)
    if self.prefetch_silent then return end
    self:_closePrefetchProgress()
    local ok_bd, ButtonDialog = pcall(require, "ui/widget/buttondialog")
    local ok_ui, UIManager = pcall(require, "ui/uimanager")
    if not (ok_bd and ok_ui and ButtonDialog and UIManager) then return end
    self.prefetch_dialog = ButtonDialog:new{
        title = self.loc:t("prefetch_progress", idx, total)
            or string.format("Preparing for offline reading - checkpoint %d of %d", idx, total),
        text = self.loc:t("prefetch_progress_hint")
            or "You can keep reading. Cancel stops after the current checkpoint.",
        buttons = {{{
            text = self.loc:t("cancel") or "Cancel",
            callback = function()
                self.prefetch_cancelled = true
                self:log("XRayPlugin: Prefetch cancelled by user")
            end,
        }}},
    }
    UIManager:show(self.prefetch_dialog)
end

function M:_closePrefetchProgress()
    if self.prefetch_dialog then
        local ok_ui, UIManager = pcall(require, "ui/uimanager")
        if ok_ui and UIManager then UIManager:close(self.prefetch_dialog) end
        self.prefetch_dialog = nil
    end
end

-- D4 displayed-dataset rule: mutations (mention scans, edits, merges) persist
-- into the dataset currently on display. A snapshot view must NEVER overwrite
-- the main cache -- that would destroy the 100% data.
function M:persistDisplayedEntities()
    if not self.cache_manager then
        self.cache_manager = require(plugin_path .. "xray_cachemanager"):new()
    end
    if self.active_snapshot_index then
        local snap = {
            checkpoint_index = self.active_snapshot_index,
            page = self.active_snapshot_page,
            characters = self.characters or {},
            locations = self.locations or {},
            terms = self.terms or {},
            historical_figures = self.historical_figures or {},
        }
        local manifest = self.book_data and self.book_data.prefetch
        local cp = manifest and manifest.checkpoints and manifest.checkpoints[self.active_snapshot_index]
        if cp then snap.percent = cp.percent end
        self.cache_manager:saveSnapshot(self.ui.document.file, self.active_snapshot_index, snap)
        return
    end
    -- Legacy path: today's behavior (see the former saveMentionsToCache body)
    if not self.book_data then
        self.book_data = self.cache_manager:loadCache(self.ui.document.file) or {}
    end
    local updated = self.book_data
    updated.characters         = self.characters
    updated.historical_figures = self.historical_figures
    updated.locations          = self.locations
    updated.terms              = self.terms
    updated.timeline           = self.timeline
    if self.author_info then updated.author_info = self.author_info end
    self.cache_manager:asyncSaveCache(self.ui.document.file, updated)
end

-- ── Position-based snapshot resolution (D4) ────────────────────────────────
-- The displayed view is ALWAYS position-based (online too): after a prefetch
-- the main cache holds 100% data, so showing self.* unfiltered would leak.
-- Rule: largest existing snapshot with page <= position; before the first
-- checkpoint the smallest existing snapshot is shown (tolerant, per user
-- decision); without any snapshots the main cache behaves exactly as today.

function M:resolveSnapshotIndexForPage(page)
    local manifest = self.book_data and self.book_data.prefetch
    if not manifest or not manifest.checkpoints or not page then return nil end
    if not self.cache_manager then return nil end
    local best, smallest
    for i, cp in ipairs(manifest.checkpoints) do
        if self:_snapshotExistsCached(i) then
            if not smallest then smallest = i end
            if cp.page <= page then best = i end
        end
    end
    if best then
        -- If online fetches advanced the main cache PAST the best snapshot but
        -- not past the reading position, the main cache is the fresher view
        -- and still spoiler-free here -> show it (index nil).
        local boundary = self.book_data and self.book_data.last_fetch_page
        local best_page = manifest.checkpoints[best] and manifest.checkpoints[best].page
        if boundary and best_page and boundary <= page and boundary >= best_page then
            return nil
        end
    end
    return best or smallest
end

-- Cache the io.open probes per session; invalidated on save/clear.
function M:_snapshotExistsCached(index)
    self._snapshot_exists = self._snapshot_exists or {}
    local hit = self._snapshot_exists[index]
    if hit ~= nil then return hit end
    local exists = self.cache_manager:snapshotExists(self.ui.document.file, index) and true or false
    self._snapshot_exists[index] = exists
    return exists
end

function M:invalidateSnapshotExistsCache()
    self._snapshot_exists = nil
end

function M:applySnapshot(index)
    if index == self.active_snapshot_index then return end
    if index == nil then
        -- restore the main-cache view
        local bd = self.book_data or {}
        self.characters = bd.characters or {}
        self.locations = bd.locations or {}
        self.terms = bd.terms or {}
        self.historical_figures = bd.historical_figures or {}
        self.active_snapshot_index = nil
        self.active_snapshot_page = nil
    else
        local snap = self.cache_manager and self.cache_manager:loadSnapshot(self.ui.document.file, index)
        if not snap then return end
        self.characters = snap.characters or {}
        self.locations = snap.locations or {}
        self.terms = snap.terms or {}
        self.historical_figures = snap.historical_figures or {}
        self.active_snapshot_index = index
        self.active_snapshot_page = snap.page
    end
    self:log("XRayPlugin: Snapshot view -> " .. tostring(index))
    -- Re-apply the series context on top of the new view: entities from
    -- previous books are spoiler-free by definition. The refs are stored by
    -- mergeSeriesContext on first merge.
    if self._series_ctx and self.mergeSeriesContext then
        pcall(function()
            self:mergeSeriesContext(self._series_ctx.cache_data, self._series_ctx.series_info)
        end)
    end
end

-- D2: the timeline is never swapped -- it has exactly one truth in the main
-- cache. The display filters it through the active snapshot's page anchor.
function M:visibleTimeline()
    if not self.active_snapshot_page then return self.timeline or {} end
    local out = {}
    for _, ev in ipairs(self.timeline or {}) do
        if ev.source == "series_prior" then
            -- events from previous books have no spoiler axis in this book
            table.insert(out, ev)
        elseif ev.page and ev.page <= self.active_snapshot_page then
            -- ponytail: this-book events without a page anchor are hidden in
            -- snapshot view (conservative -- not placeable on the spoiler axis).
            table.insert(out, ev)
        end
    end
    return out
end

function M:updateSnapshotViewForPage(page)
    if not page then return end
    -- Frozen during a prefetch run: the fetch chain owns self.* (its merged
    -- state feeds the next checkpoint as existing_characters); _finishPrefetch
    -- re-applies the position view once the chain is done.
    if self.prefetch_active then return end
    local manifest = self.book_data and self.book_data.prefetch
    if not manifest or not manifest.checkpoints then return end

    local spoiler_setting = self.ai_helper and self.ai_helper.settings and self.ai_helper.settings.spoiler_setting or "spoiler_free"
    if spoiler_setting == "full_book" then
        if self.active_snapshot_index then self:applySnapshot(nil) end
        return
    end
    self:applySnapshot(self:resolveSnapshotIndexForPage(page))
end

return M
