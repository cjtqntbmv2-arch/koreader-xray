-- xray_prefetch_spec.lua
require("spec.spec_helper")

-- Override spec_helper's offline NetworkMgr default: the prefetch loop needs online.
local net_backup = package.loaded["ui/network/manager"]
package.loaded["ui/network/manager"] = {
    isConnected = function() return true end,
    isOnline = function() return true end,
    runWhenOnline = function() end,
}

local prefetch = require("xray_prefetch")
local XRayPlugin = require("main")

local function toc_entry(page, title)
    return { page = page, title = title or ("Chapter " .. page) }
end

local function makePlugin(toc, page_count)
    local plugin = createMockPlugin()
    plugin.ui.document.getToc = function() return toc end
    plugin.ui.document.getPageCount = function() return page_count end
    plugin.isNonNarrativeChapter = function(_, title)
        return title ~= nil and title:lower():match("^copyright") ~= nil
    end
    for k, v in pairs(prefetch) do
        plugin[k] = v
    end
    -- main.lua and the six mixins share one `self` in production (see
    -- CLAUDE.md); mirror that here so guards like isAiFetchingEnabled() work.
    for k, v in pairs(XRayPlugin) do
        if plugin[k] == nil then plugin[k] = v end
    end
    return plugin
end

local function pageSet(checkpoints)
    local set = {}
    for _, cp in ipairs(checkpoints) do set[cp.page] = cp end
    return set
end

describe("xray_prefetch", function()
    describe("computeCheckpoints", function()
        it("anchors on chapter end pages and always ends at 100%", function()
            -- 5 chapters of 100 pages each, 500-page book
            local toc = {}
            for i = 0, 4 do table.insert(toc, toc_entry(i * 100 + 1)) end
            local plugin = makePlugin(toc, 500)
            local cps = plugin:computeCheckpoints()
            assert.is_not_nil(cps)
            assert.are.equal(500, cps[#cps].page)
            assert.are.equal(100, cps[#cps].percent)
            local pages = pageSet(cps)
            assert.is_not_nil(pages[100])
            assert.is_not_nil(pages[200])
            assert.is_not_nil(pages[300])
            assert.is_not_nil(pages[400])
            assert.are.equal(20, pages[100].percent)
        end)

        it("thins dense TOCs to at most 12 checkpoints", function()
            local toc = {}
            for i = 0, 59 do table.insert(toc, toc_entry(i * 10 + 1)) end
            local plugin = makePlugin(toc, 600)
            local cps = plugin:computeCheckpoints()
            assert.is_true(#cps <= 12)
            assert.are.equal(600, cps[#cps].page)
        end)

        it("densifies sparse TOCs so no interval exceeds 15%", function()
            -- 3 chapters ending at 100/200/300 of a 300-page book
            local toc = { toc_entry(1), toc_entry(101), toc_entry(201) }
            local plugin = makePlugin(toc, 300)
            local cps = plugin:computeCheckpoints()
            local max_gap = math.floor(300 * 15 / 100) + 1
            local prev = 0
            for _, cp in ipairs(cps) do
                assert.is_true(cp.page - prev <= max_gap)
                prev = cp.page
            end
            assert.are.equal(300, cps[#cps].page)
        end)

        it("densifies the leading gap before a late first chapter end", function()
            local toc = { toc_entry(1), toc_entry(121), toc_entry(281) }
            local plugin = makePlugin(toc, 300)
            local cps = plugin:computeCheckpoints()
            assert.is_true(cps[1].page <= math.floor(300 * 15 / 100) + 1)
        end)

        it("falls back to 10% steps without a usable TOC", function()
            local plugin = makePlugin({}, 400)
            local cps = plugin:computeCheckpoints()
            assert.are.equal(10, #cps)
            assert.are.equal(40, cps[1].page)
            assert.are.equal(400, cps[#cps].page)
            assert.are.equal(100, cps[#cps].percent)
        end)

        it("ignores non-narrative chapters as anchors", function()
            -- copyright chapter between ch1 and ch2 must not contribute its end page (149)
            local toc = { toc_entry(1), toc_entry(150, "Copyright"), toc_entry(240) }
            local plugin = makePlugin(toc, 400)
            local cps = plugin:computeCheckpoints()
            local pages = pageSet(cps)
            assert.is_not_nil(pages[239]) -- end of ch1 = start of ch2 - 1 (copyright filtered out)
            assert.is_nil(pages[149])     -- would only exist if copyright counted as a chapter
        end)

        it("returns nil without a document", function()
            local plugin = makePlugin({}, 400)
            plugin.ui = nil
            assert.is_nil(plugin:computeCheckpoints())
        end)
    end)

    describe("prefetch loop", function()
        local function makeLoopPlugin()
            local plugin = createMockPlugin()
            plugin.ui.document.getPageCount = function() return 300 end
            plugin.ui.getCurrentPage = function() return 10 end
            for k, v in pairs(prefetch) do
                plugin[k] = v
            end
            for k, v in pairs(XRayPlugin) do
                if plugin[k] == nil then plugin[k] = v end
            end
            -- isolate the loop from the D1 math
            plugin.computeCheckpoints = function()
                return {
                    { page = 100, percent = 33 },
                    { page = 200, percent = 66 },
                    { page = 300, percent = 100 },
                }
            end
            plugin.book_data = {}

            local snaps = {}
            plugin._spec_snaps = snaps
            plugin._spec_saved_snapshots = {}
            plugin._spec_async_saves = 0
            plugin.cache_manager = {
                snapshotExists = function(_, _, idx) return snaps[idx] == true end,
                saveSnapshot = function(_, _, idx, data)
                    snaps[idx] = true
                    table.insert(plugin._spec_saved_snapshots, { index = idx, page = data.page })
                    return true
                end,
                asyncSaveCache = function()
                    plugin._spec_async_saves = plugin._spec_async_saves + 1
                    return true
                end,
                loadCache = function() return plugin.book_data end,
                loadSnapshot = function(_, _, idx)
                    return {
                        snapshot_version = 1,
                        page = ({ 100, 200, 300 })[idx],
                        percent = ({ 33, 66, 100 })[idx],
                        characters = {}, locations = {}, terms = {}, historical_figures = {},
                    }
                end,
            }

            plugin._spec_fetch_calls = {}
            plugin.continueWithFetch = function(self, pct, is_update, lfp, silent, page)
                table.insert(self._spec_fetch_calls, {
                    pct = pct, page = page, is_update = is_update, silent = silent, lfp = lfp,
                })
                -- simulate a successful fetch that already finished: the real
                -- one fills the timeline and advances last_fetch_page
                self.timeline = self.timeline or {}
                table.insert(self.timeline, { chapter = "c" .. tostring(#self._spec_fetch_calls) })
                self.bg_fetch_active = false
                self.book_data.last_fetch_page = page
            end

            plugin._spec_dupe_calls = 0
            plugin.runPostFetchDuplicateCheck = function(self, _, _, pct)
                self._spec_dupe_calls = self._spec_dupe_calls + 1
                self._spec_dupe_pct = pct
            end

            plugin.ai_helper = {
                hasApiKey = function() return true end,
                settings = {},
                log = function() end,
            }
            plugin.timeline = {}
            _G.ui_tracker.shown = {}
            _G.ui_tracker.last_shown = nil
            _G.ui_tracker.closed = {}
            return plugin
        end

        it("runs all checkpoints in order and marks the manifest completed", function()
            local plugin = makeLoopPlugin()
            plugin:startOfflinePrefetch(true)

            assert.are.equal(3, #plugin._spec_fetch_calls)
            assert.are.equal(100, plugin._spec_fetch_calls[1].page)
            assert.are.equal(200, plugin._spec_fetch_calls[2].page)
            assert.are.equal(300, plugin._spec_fetch_calls[3].page)
            assert.are.equal(100, plugin._spec_fetch_calls[3].pct)
            -- virgin book: first call is a fresh fetch, later ones are updates
            assert.is_false(plugin._spec_fetch_calls[1].is_update)
            assert.is_true(plugin._spec_fetch_calls[2].is_update)

            assert.are.equal(3, #plugin._spec_saved_snapshots)
            assert.are.equal(1, plugin._spec_saved_snapshots[1].index)
            assert.are.equal(100, plugin._spec_saved_snapshots[1].page)
            assert.are.equal(3, plugin._spec_saved_snapshots[3].index)

            assert.is_true(plugin.book_data.prefetch.completed)
            assert.is_true(plugin:isPrefetchComplete())
            assert.is_false(plugin.prefetch_active)
            -- the finish must re-apply the position view (reader at page 10 -> snapshot 1)
            assert.are.equal(1, plugin.active_snapshot_index)
        end)

        it("skips checkpoints already covered by last_fetch_page", function()
            local plugin = makeLoopPlugin()
            plugin.book_data.last_fetch_page = 150
            plugin:startOfflinePrefetch(true)

            assert.are.equal(2, #plugin._spec_fetch_calls)
            assert.are.equal(200, plugin._spec_fetch_calls[1].page)
            assert.are.equal(2, plugin._spec_saved_snapshots[1].index)
            assert.is_true(plugin.book_data.prefetch.completed)
        end)

        it("resumes at the first missing snapshot", function()
            local plugin = makeLoopPlugin()
            plugin._spec_snaps[1] = true
            plugin:startOfflinePrefetch(true)

            assert.are.equal(2, #plugin._spec_fetch_calls)
            assert.are.equal(200, plugin._spec_fetch_calls[1].page)
            assert.are.equal(300, plugin._spec_fetch_calls[2].page)
        end)

        it("stops on failure and leaves the manifest incomplete", function()
            local plugin = makeLoopPlugin()
            plugin.continueWithFetch = function(self, pct, is_update, lfp, silent, page)
                table.insert(self._spec_fetch_calls, { page = page })
                self.bg_fetch_active = false
                -- last_fetch_page NOT advanced -> failure
            end
            plugin:startOfflinePrefetch(true)

            assert.are.equal(1, #plugin._spec_fetch_calls)
            assert.are.equal(0, #plugin._spec_saved_snapshots)
            assert.falsy(plugin.book_data.prefetch.completed)
            assert.is_false(plugin.prefetch_active)
        end)

        it("honors cancellation between checkpoints", function()
            local plugin = makeLoopPlugin()
            local orig = plugin.continueWithFetch
            plugin.continueWithFetch = function(self, ...)
                orig(self, ...)
                self.prefetch_cancelled = true
            end
            plugin:startOfflinePrefetch(true)

            assert.are.equal(1, #plugin._spec_fetch_calls)
            assert.are.equal(1, #plugin._spec_saved_snapshots) -- finished checkpoint is kept
            assert.falsy(plugin.book_data.prefetch.completed)
            assert.is_false(plugin.prefetch_active)
        end)

        it("routes full_book users to a normal full fetch", function()
            local plugin = makeLoopPlugin()
            plugin.ai_helper.settings.spoiler_setting = "full_book"
            local full_fetches = 0
            plugin.fetchFromAI = function() full_fetches = full_fetches + 1 end
            plugin:startOfflinePrefetch(true)

            assert.are.equal(1, full_fetches)
            assert.are.equal(0, #plugin._spec_fetch_calls)
            assert.falsy(plugin.prefetch_active)
        end)

        it("runs the duplicate check exactly once at the end", function()
            local plugin = makeLoopPlugin()
            plugin:startOfflinePrefetch(true)

            assert.are.equal(1, plugin._spec_dupe_calls)
            assert.are.equal(100, plugin._spec_dupe_pct)
        end)

        it("shows a progress dialog and a final info in manual mode", function()
            local plugin = makeLoopPlugin()
            plugin:startOfflinePrefetch(false)

            local dialogs, infos = 0, 0
            for _, w in ipairs(_G.ui_tracker.shown) do
                if w.type == "ButtonDialog" then dialogs = dialogs + 1 end
                if w.type == "InfoMessage" then infos = infos + 1 end
            end
            assert.is_true(dialogs >= 1)
            assert.are.equal(1, infos)
            assert.are.equal("InfoMessage", _G.ui_tracker.last_shown.type)
        end)
    end)

    describe("maybeStartAutoPrefetch", function()
        local function makeAutoPlugin()
            local plugin = createMockPlugin()
            for k, v in pairs(prefetch) do
                plugin[k] = v
            end
            for k, v in pairs(XRayPlugin) do
                if plugin[k] == nil then plugin[k] = v end
            end
            plugin.book_data = {}
            plugin._spec_starts = {}
            plugin.startOfflinePrefetch = function(self, silent)
                table.insert(self._spec_starts, silent)
            end
            plugin.ai_helper = { settings = { offline_prefetch_auto = true } }
            return plugin
        end

        it("does nothing when the setting is off", function()
            local plugin = makeAutoPlugin()
            plugin.ai_helper.settings.offline_prefetch_auto = nil
            plugin:maybeStartAutoPrefetch()
            assert.are.equal(0, #plugin._spec_starts)
        end)

        it("does nothing when the prefetch is already complete", function()
            local plugin = makeAutoPlugin()
            plugin.book_data.prefetch = { checkpoints = {}, completed = true }
            plugin:maybeStartAutoPrefetch()
            assert.are.equal(0, #plugin._spec_starts)
        end)

        it("does nothing while a fetch or prefetch is running", function()
            local plugin = makeAutoPlugin()
            plugin.bg_fetch_active = true
            plugin:maybeStartAutoPrefetch()
            assert.are.equal(0, #plugin._spec_starts)
        end)

        it("runs at most once per book and session", function()
            local plugin = makeAutoPlugin()
            plugin:maybeStartAutoPrefetch()
            plugin:maybeStartAutoPrefetch()
            assert.are.equal(1, #plugin._spec_starts)
        end)

        it("starts a silent prefetch when all guards pass", function()
            local plugin = makeAutoPlugin()
            plugin:maybeStartAutoPrefetch()
            assert.are.equal(1, #plugin._spec_starts)
            assert.is_true(plugin._spec_starts[1])
        end)
    end)

    describe("snapshot resolution", function()
        local function makeViewPlugin(existing)
            local plugin = createMockPlugin()
            plugin.ui.document.getPageCount = function() return 300 end
            for k, v in pairs(prefetch) do
                plugin[k] = v
            end
            plugin.book_data = {
                characters = { { name = "MainChar" } },
                locations = { { name = "MainLoc" } },
                terms = {},
                historical_figures = {},
                prefetch = {
                    checkpoints = {
                        { page = 100, percent = 33 },
                        { page = 200, percent = 66 },
                        { page = 300, percent = 100 },
                    },
                },
            }
            plugin.characters = plugin.book_data.characters
            plugin.locations = plugin.book_data.locations
            plugin.terms = plugin.book_data.terms
            plugin.historical_figures = plugin.book_data.historical_figures
            plugin._spec_loads = 0
            plugin._spec_snapshot_saves = {}
            plugin._spec_async_saves = 0
            plugin.cache_manager = {
                snapshotExists = function(_, _, idx) return existing[idx] == true end,
                saveSnapshot = function(_, _, idx, data)
                    table.insert(plugin._spec_snapshot_saves, { index = idx, data = data })
                    return true
                end,
                asyncSaveCache = function()
                    plugin._spec_async_saves = plugin._spec_async_saves + 1
                    return true
                end,
                loadSnapshot = function(_, _, idx)
                    plugin._spec_loads = plugin._spec_loads + 1
                    return {
                        snapshot_version = 1,
                        page = plugin.book_data.prefetch.checkpoints[idx].page,
                        percent = plugin.book_data.prefetch.checkpoints[idx].percent,
                        characters = { { name = "SnapChar" .. idx } },
                        locations = {},
                        terms = {},
                        historical_figures = {},
                    }
                end,
            }
            plugin.ai_helper = { settings = {} }
            return plugin
        end

        it("picks the largest snapshot at or below the position", function()
            local plugin = makeViewPlugin({ [1] = true, [2] = true, [3] = true })
            assert.are.equal(2, plugin:resolveSnapshotIndexForPage(250))
            assert.are.equal(3, plugin:resolveSnapshotIndexForPage(300))
        end)

        it("tolerantly falls back to the smallest existing snapshot before CP1", function()
            local plugin = makeViewPlugin({ [1] = true, [2] = true, [3] = true })
            assert.are.equal(1, plugin:resolveSnapshotIndexForPage(10))
        end)

        it("skips missing snapshot files", function()
            local plugin = makeViewPlugin({ [3] = true })
            assert.are.equal(3, plugin:resolveSnapshotIndexForPage(250))
            local plugin2 = makeViewPlugin({ [1] = true })
            assert.are.equal(1, plugin2:resolveSnapshotIndexForPage(350))
        end)

        it("returns nil without a manifest or without snapshots", function()
            local plugin = makeViewPlugin({})
            assert.is_nil(plugin:resolveSnapshotIndexForPage(150))
            local plugin2 = makeViewPlugin({ [1] = true })
            plugin2.book_data.prefetch = nil
            assert.is_nil(plugin2:resolveSnapshotIndexForPage(150))
        end)

        it("propagateEntityForward writes later snapshots and the main cache, never the active or earlier ones", function()
            local plugin = makeViewPlugin({ [1] = true, [2] = true, [3] = true })
            plugin.active_snapshot_index = 2
            plugin:propagateEntityForward({ name = "Newcomer" }, "character")

            local saved = {}
            for _, s in ipairs(plugin._spec_snapshot_saves) do saved[s.index] = s.data end

            -- later snapshot (index 3) receives the entity
            assert.is_not_nil(saved[3])
            local in3 = false
            for _, c in ipairs(saved[3].characters) do if c.name == "Newcomer" then in3 = true end end
            assert.is_true(in3)

            -- the active (2) and earlier (1) snapshots are never rewritten by propagate
            assert.is_nil(saved[1])
            assert.is_nil(saved[2])

            -- the main cache gains the entity and is saved
            local in_main = false
            for _, c in ipairs(plugin.book_data.characters) do if c.name == "Newcomer" then in_main = true end end
            assert.is_true(in_main)
            assert.is_true(plugin._spec_async_saves >= 1)
        end)

        it("propagateEntityForward does not duplicate an entity already in a later snapshot", function()
            local plugin = makeViewPlugin({ [1] = true, [2] = true, [3] = true })
            plugin.active_snapshot_index = 1
            -- snapshot 3's mock already contains "SnapChar3"; snapshot 2 does not
            plugin:propagateEntityForward({ name = "SnapChar3" }, "character")

            local saved = {}
            for _, s in ipairs(plugin._spec_snapshot_saves) do saved[s.index] = s.data end

            -- snapshot 2 lacked it -> appended
            assert.is_not_nil(saved[2])
            local in2 = false
            for _, c in ipairs(saved[2].characters) do if c.name == "SnapChar3" then in2 = true end end
            assert.is_true(in2)

            -- snapshot 3 already had it -> never duplicated
            if saved[3] then
                local count = 0
                for _, c in ipairs(saved[3].characters) do if c.name == "SnapChar3" then count = count + 1 end end
                assert.are.equal(1, count)
            end
        end)

        it("applySnapshot swaps entity lists and restores the main view", function()
            local plugin = makeViewPlugin({ [2] = true })
            plugin:applySnapshot(2)
            assert.are.equal("SnapChar2", plugin.characters[1].name)
            assert.are.equal(2, plugin.active_snapshot_index)
            assert.are.equal(200, plugin.active_snapshot_page)

            plugin:applySnapshot(nil)
            assert.are.equal("MainChar", plugin.characters[1].name)
            assert.is_nil(plugin.active_snapshot_index)
            assert.is_nil(plugin.active_snapshot_page)
        end)

        it("updateSnapshotViewForPage loads a snapshot only on boundary crossings", function()
            local plugin = makeViewPlugin({ [1] = true, [2] = true, [3] = true })
            plugin:updateSnapshotViewForPage(150)
            assert.are.equal(1, plugin.active_snapshot_index)
            assert.are.equal(1, plugin._spec_loads)
            plugin:updateSnapshotViewForPage(180) -- same interval -> no reload
            assert.are.equal(1, plugin._spec_loads)
            plugin:updateSnapshotViewForPage(250) -- crossing -> reload
            assert.are.equal(2, plugin.active_snapshot_index)
            assert.are.equal(2, plugin._spec_loads)
        end)

        it("prefers the fresher main cache when online fetches passed the snapshots", function()
            local plugin = makeViewPlugin({ [1] = true, [2] = true })
            plugin.book_data.last_fetch_page = 250
            -- boundary 250 is spoiler-free at page 260 and newer than snapshot 2 (page 200)
            assert.is_nil(plugin:resolveSnapshotIndexForPage(260))
            -- at page 150 the boundary (250) would spoil -> snapshot 1 protects
            assert.are.equal(1, plugin:resolveSnapshotIndexForPage(150))
        end)

        it("freezes the view while a prefetch is active", function()
            local plugin = makeViewPlugin({ [1] = true, [2] = true, [3] = true })
            plugin.prefetch_active = true
            plugin:updateSnapshotViewForPage(250)
            assert.is_nil(plugin.active_snapshot_index)
        end)

        it("full_book setting forces the main view", function()
            local plugin = makeViewPlugin({ [1] = true, [2] = true, [3] = true })
            plugin:updateSnapshotViewForPage(250)
            assert.are.equal(2, plugin.active_snapshot_index)
            plugin.ai_helper.settings.spoiler_setting = "full_book"
            plugin:updateSnapshotViewForPage(250)
            assert.is_nil(plugin.active_snapshot_index)
            assert.are.equal("MainChar", plugin.characters[1].name)
        end)

        it("visibleTimeline returns the full timeline without an active snapshot", function()
            local plugin = makeViewPlugin({})
            plugin.timeline = { { text = "a", page = 10 }, { text = "b", page = 999 } }
            assert.are.equal(plugin.timeline, plugin:visibleTimeline())
        end)

        it("visibleTimeline filters events beyond the active snapshot page", function()
            local plugin = makeViewPlugin({ [2] = true })
            plugin.timeline = {
                { text = "early", page = 10 },
                { text = "mid", page = 150 },
                { text = "late", page = 999 },
                { text = "unanchored" }, -- no page -> hidden in snapshot view
            }
            plugin:applySnapshot(2) -- active_snapshot_page = 200
            local visible = plugin:visibleTimeline()
            assert.are.equal(2, #visible)
            assert.are.equal("early", visible[1].text)
            assert.are.equal("mid", visible[2].text)
        end)

        it("visibleTimeline keeps series_prior events despite missing page anchors", function()
            local plugin = makeViewPlugin({ [1] = true })
            plugin.timeline = {
                { text = "prior", source = "series_prior" },
                { text = "late", page = 999 },
            }
            plugin:applySnapshot(1) -- active_snapshot_page = 100
            local visible = plugin:visibleTimeline()
            assert.are.equal(1, #visible)
            assert.are.equal("prior", visible[1].text)
        end)

        it("re-applies stored series context after a snapshot swap", function()
            local plugin = makeViewPlugin({ [2] = true })
            local merged = 0
            plugin.mergeSeriesContext = function(self, cache_data, series_info)
                merged = merged + 1
                self._spec_series_args = { cache_data, series_info }
            end
            plugin._series_ctx = { cache_data = { books = {} }, series_info = { index = 2 } }
            plugin:applySnapshot(2)
            assert.are.equal(1, merged)
        end)

        it("write-back routing persists to the active snapshot file, never the main cache", function()
            local plugin = makeViewPlugin({ [2] = true })
            plugin:applySnapshot(2)
            plugin.characters[1].mentions = { { page = 150 } }
            plugin:persistDisplayedEntities()

            assert.are.equal(1, #plugin._spec_snapshot_saves)
            assert.are.equal(2, plugin._spec_snapshot_saves[1].index)
            assert.are.equal("SnapChar2", plugin._spec_snapshot_saves[1].data.characters[1].name)
            assert.are.equal(200, plugin._spec_snapshot_saves[1].data.page)
            assert.are.equal(0, plugin._spec_async_saves)
            -- main cache lists untouched
            assert.are.equal("MainChar", plugin.book_data.characters[1].name)
        end)

        it("write-back routing uses the legacy main-cache path without an active snapshot", function()
            local plugin = makeViewPlugin({})
            plugin.timeline = { { text = "t" } }
            plugin:persistDisplayedEntities()

            assert.are.equal(0, #plugin._spec_snapshot_saves)
            assert.are.equal(1, plugin._spec_async_saves)
            assert.are.equal(plugin.characters, plugin.book_data.characters)
            assert.are.equal(plugin.timeline, plugin.book_data.timeline)
        end)
    end)

    describe("watch loop hardening", function()
        it("_watchPrefetchStep releases prefetch_active when the document disappears", function()
            local plugin = createMockPlugin()
            for k, v in pairs(prefetch) do plugin[k] = v end
            plugin.prefetch_active = true
            plugin.bg_fetch_active = false
            plugin.book_data = { last_fetch_page = 0,
                prefetch = { checkpoints = { { page = 100, percent = 50 } } } }
            plugin.ui = nil
            plugin:_watchPrefetchStep(1, { page = 100, percent = 50 }, 0)
            assert.is_false(plugin.prefetch_active)
        end)

        it("a crashing tick never leaves prefetch_active locked", function()
            local plugin = createMockPlugin()
            for k, v in pairs(prefetch) do plugin[k] = v end
            plugin.prefetch_active = true
            plugin.bg_fetch_active = false
            plugin.book_data = { last_fetch_page = 100 }
            plugin.cache_manager = {
                saveSnapshot = function() error("disk full") end,
                snapshotExists = function() return false end,
            }
            plugin:_watchPrefetchStep(1, { page = 100, percent = 50 }, 0)
            assert.is_false(plugin.prefetch_active)
        end)
    end)

    describe("propagateEntityForward ordering", function()
        it("inserts a character at its first-appearance position, not at the end", function()
            local saved = {}
            local plugin = require("xray_prefetch")
            -- minimal host object
            local host = setmetatable({
                active_snapshot_index = 1,
                ui = { document = { file = "/book.epub" } },
                book_data = {
                    characters = {
                        { name = "Early", first_page = 10, first_seq = 1 },
                        { name = "Late",  first_page = 90, first_seq = 3 },
                    },
                    prefetch = { checkpoints = { { page = 20 }, { page = 100 } } },
                },
                cache_manager = {
                    asyncSaveCache = function() end,
                    loadSnapshot = function() return nil end,
                    saveSnapshot = function() end,
                },
                _snapshotExistsCached = function() return false end,
            }, { __index = plugin })
            -- borrow the real sorter
            host.sortEntityList = require("xray_data").sortEntityList
            host.sortByFirstAppearance = require("xray_data").sortByFirstAppearance
            host.sortByName = require("xray_data").sortByName
            host.sortDataByFrequency = require("xray_data").sortDataByFrequency

            host:propagateEntityForward(
                { name = "Middle", first_page = 50, first_seq = 2 }, "character")

            local names = {}
            for _, c in ipairs(host.book_data.characters) do names[#names+1] = c.name end
            assert.same({ "Early", "Middle", "Late" }, names)
        end)
    end)

    describe("ai fetching main switch", function()
        local function switchPlugin(enabled)
            local toc = {}
            for i = 0, 4 do table.insert(toc, toc_entry(i * 100 + 1)) end
            local plugin = makePlugin(toc, 500)
            -- isAiFetchingEnabled comes from makePlugin's XRayPlugin merge.
            plugin.ai_helper.settings.ai_fetching_enabled = enabled
            plugin.ai_helper.hasApiKey = function() return true end
            return plugin
        end

        it("maybeStartAutoPrefetch is a no-op when disabled", function()
            local plugin = switchPlugin(false)
            plugin.ai_helper.settings.offline_prefetch_auto = true
            local started = false
            plugin.startOfflinePrefetch = function() started = true end
            plugin:maybeStartAutoPrefetch()
            assert.is_false(started)
        end)

        it("startOfflinePrefetch refuses silently when disabled", function()
            local plugin = switchPlugin(false)
            local fetched = false
            plugin.continueWithFetch = function() fetched = true end
            plugin.fetchFromAI = function() fetched = true end
            plugin:startOfflinePrefetch(true)
            assert.is_false(fetched)
            assert.falsy(plugin.prefetch_active)
        end)

        it("_prefetchNext aborts a running loop when disabled mid-run", function()
            local plugin = switchPlugin(false)
            plugin.prefetch_active = true
            plugin.prefetch_silent = true
            plugin.book_data = { prefetch = { checkpoints = { { page = 100, percent = 20 } } } }
            local finished
            plugin._finishPrefetch = function(_, completed) finished = completed end
            local fetched = false
            plugin.continueWithFetch = function() fetched = true end
            plugin:_prefetchNext()
            assert.is_false(fetched)
            assert.is_false(finished)
        end)

        it("startOfflinePrefetch(false) with switch off shows the disabled hint and returns early", function()
            local plugin = switchPlugin(false)
            local shown_text = nil
            plugin.showPrefetchInfo = function(_, text) shown_text = text end
            local fetched = false
            plugin.continueWithFetch = function() fetched = true end
            plugin.fetchFromAI = function() fetched = true end

            -- Tripwire: computeCheckpoints should never be called if the guard returns early
            plugin.computeCheckpoints = function() error("startOfflinePrefetch did not return early", 0) end

            plugin:startOfflinePrefetch(false)

            assert.are.equal("ai_fetching_disabled_hint", shown_text)
            assert.falsy(plugin.prefetch_active)
            assert.is_false(fetched)
        end)

        it("_prefetchNext with switch off and prefetch_silent false shows hint, sets silent flag, and calls _finishPrefetch", function()
            local plugin = switchPlugin(false)
            plugin.prefetch_active = true
            plugin.prefetch_silent = false
            plugin.book_data = { prefetch = { checkpoints = { { page = 100, percent = 20 } } } }

            local shown_text = nil
            plugin.showPrefetchInfo = function(_, text) shown_text = text end

            local finished_with = nil
            plugin._finishPrefetch = function(_, completed) finished_with = completed end

            plugin:_prefetchNext()

            assert.are.equal("ai_fetching_disabled_hint", shown_text)
            assert.is_true(plugin.prefetch_silent)
            assert.is_false(finished_with)
        end)
    end)
end)

-- Restore the network manager fake to spec_helper's default (offline with runWhenOnline).
-- This file's override leaks into subsequent specs when run in a shared Lua process.
package.loaded["ui/network/manager"] = net_backup
