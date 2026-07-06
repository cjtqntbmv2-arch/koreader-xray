-- xray_fetch_spec.lua
require("spec.spec_helper")
local fetch = require("xray_fetch")

describe("xray_fetch", function()
    local plugin

    before_each(function()
        plugin = createMockPlugin()
        -- Mix in fetch methods
        for k, v in pairs(fetch) do
            plugin[k] = v
        end
        plugin.cache_manager = {
            saveCache = function() return true end,
            asyncSaveCache = function() return true end,
            loadCache = function() return {} end
        }
    end)

    describe("finalizeXRayData", function()
        it("merges new characters correctly in update mode", function()
            plugin.characters = {
                { name = "Alice", description = "Old description" }
            }
            local new_data = {
                characters = {
                    { name = "Alice", description = "New description" },
                    { name = "Bob", description = "A new character" }
                },
                locations = {},
                historical_figures = {},
                timeline = {}
            }

            plugin:finalizeXRayData(new_data, "Test Title", "Test Author", "Some text", true, true, 10)

            assert.are.equal(2, #plugin.characters)
            assert.are.equal("New description", plugin.characters[1].description)
            assert.are.equal("Bob", plugin.characters[2].name)
        end)

        it("filters non-narrative timeline entries", function()
            plugin.isNonNarrativeChapter = function(self, title)
                return title == "Table of Contents"
            end

            local new_data = {
                characters = {},
                locations = {},
                historical_figures = {},
                timeline = {
                    { chapter = "Chapter 1", text = "Event 1" },
                    { chapter = "Table of Contents", text = "Event 2" }
                }
            }

            plugin:finalizeXRayData(new_data, "Test Title", "Test Author", "Some text", false, true, 10)

            assert.are.equal(1, #plugin.timeline)
            assert.are.equal("Chapter 1", plugin.timeline[1].chapter)
        end)

        it("aborts and protects existing data when AI returns all-empty results", function()
            -- Set up existing data
            plugin.characters = { { name = "Alice", description = "Existing" } }
            plugin.locations = { { name = "Wonderland", description = "Existing" } }
            plugin.timeline = { { chapter = "Start", page = 1 } }
            plugin.historical_figures = { { name = "Lewis Carroll", biography = "Existing" } }

            local empty_data = {
                characters = {},
                locations = {},
                historical_figures = {},
                timeline = {}
            }

            -- Spy on cache save to ensure it's NOT called
            local save_called = false
            plugin.cache_manager.saveCache = function()
                save_called = true
                return true
            end

            plugin:finalizeXRayData(empty_data, "Test Title", "Test Author", "Some text", true, true, 20)

            -- Existing data should be UNTOUCHED
            assert.are.equal(1, #plugin.characters)
            assert.are.equal("Alice", plugin.characters[1].name)
            assert.are.equal(1, #plugin.locations)
            assert.are.equal(1, #plugin.timeline)
            assert.are.equal(1, #plugin.historical_figures)
            
            -- Cache save should NOT have happened
            assert.is_false(save_called)
        end)
    end)

    describe("runPostFetchDuplicateCheck", function()
        it("returns immediately while prefetch is active", function()
            local called = false
            plugin.prefetch_active = true
            plugin.ai_helper = {
                hasApiKey = function() return true end,
                settings = {},
                findDuplicatesAsync = function() called = true; return nil end,
            }
            plugin.characters = { { name = "A" }, { name = "B" }, { name = "C" } }
            plugin.locations = { { name = "X" }, { name = "Y" } }

            plugin:runPostFetchDuplicateCheck("Title", "Author", 50, true)

            assert.is_false(called)
        end)
    end)

    describe("prefetch_segment flag", function()
        local function runFetchAndCaptureContext(p)
            local captured
            p.ui.getCurrentPage = function() return 42 end
            p.ui.document.getToc = function() return {} end
            p.chapter_analyzer = {
                getTextForAnalysis = function() return "This is definitely enough book text for the test run." end,
                getDetailedChapterSamples = function() return "SAMPLES", { "Chapter 1" } end,
                getAnnotationsForAnalysis = function() return nil end,
            }
            p.ai_helper = {
                settings = {},
                buildComprehensiveRequest = function(self, title, author, context)
                    captured = context
                    return nil, "test_abort", "test abort"
                end,
            }
            -- is_silent=true -> kein Dialog, Abbruchpfad ohne UI
            p:continueWithFetch(50, false, nil, true)
            return captured
        end

        it("marks the context as segment fetch while prefetch is active", function()
            plugin.prefetch_active = true
            local ctx = runFetchAndCaptureContext(plugin)
            assert.is_true(ctx ~= nil)
            assert.is_true(ctx.prefetch_segment == true)
        end)

        it("does not mark the context outside of prefetch", function()
            plugin.prefetch_active = nil
            local ctx = runFetchAndCaptureContext(plugin)
            assert.is_true(ctx ~= nil)
            assert.is_true(ctx.prefetch_segment == nil)
        end)
    end)
end)

describe("fetchSingleWord snapshot guard", function()
    local old_net

    before_each(function()
        old_net = package.loaded["ui/network/manager"]
    end)

    after_each(function()
        package.loaded["ui/network/manager"] = old_net
    end)

    it("blocks the lookup fetch while a snapshot view is active", function()
        local fetch = require("xray_fetch")
        local plugin = createMockPlugin()
        for k, v in pairs(fetch) do plugin[k] = v end
        plugin.active_snapshot_index = 2
        local info_shown = 0
        plugin.showPrefetchInfo = function() info_shown = info_shown + 1 end
        local network_called = false
        package.loaded["ui/network/manager"] = {
            runWhenOnline = function(_, cb) network_called = true end,
        }
        plugin:fetchSingleWord("Gandalf")
        assert.are.equal(1, info_shown)
        assert.is_false(network_called)
    end)
end)

describe("manual fetch concurrency guard", function()
    local old_net

    before_each(function()
        old_net = package.loaded["ui/network/manager"]
    end)

    after_each(function()
        package.loaded["ui/network/manager"] = old_net
    end)

    it("refuses a second manual fetch while one is active", function()
        local fetch = require("xray_fetch")
        local plugin = createMockPlugin()
        for k, v in pairs(fetch) do plugin[k] = v end
        plugin.bg_fetch_active = true
        local network_called = false
        package.loaded["ui/network/manager"] = {
            runWhenOnline = function(_, cb) network_called = true end,
        }
        plugin:fetchFromAI()
        assert.is_false(network_called)
        plugin.bg_fetch_active = false
        plugin.bg_fetch_pending = true
        plugin:updateFromAI()
        assert.is_false(network_called)
    end)
end)

describe("auto dupe check default", function()
    it("does not start a duplicate check unless explicitly enabled", function()
        local fetch = require("xray_fetch")
        local plugin = createMockPlugin()
        for k, v in pairs(fetch) do plugin[k] = v end
        local started = 0
        plugin.prefetch_active = false
        plugin.characters = { { name = "A" }, { name = "B" } }
        plugin.locations = {}
        plugin.ai_helper = {
            settings = {}, -- user never touched the setting
            hasApiKey = function() return true end,
            findDuplicatesAsync = function() started = started + 1; return nil end,
        }
        plugin:runPostFetchDuplicateCheck("T", "A", 50, true)
        assert.are.equal(0, started)
        plugin.ai_helper.settings.auto_dupe_check_enabled = true
        plugin:runPostFetchDuplicateCheck("T", "A", 50, true)
        assert.is_true(started > 0)
    end)
end)
