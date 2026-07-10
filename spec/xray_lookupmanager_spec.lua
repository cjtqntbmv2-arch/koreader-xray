-- xray_lookupmanager_spec.lua
require("spec/spec_helper")

describe("xray_lookupmanager", function()
    local LookupManager
    local lm
    local plugin

    setup(function()
        LookupManager = require("xray_lookupmanager")
        plugin = createMockPlugin()
        plugin.characters = {}
        plugin.historical_figures = {}
        plugin.locations = {}
        lm = LookupManager:new(plugin)
    end)

    describe("normalize", function()
        it("should lowercase and strip non-alphanumeric at ends", function()
            assert.are.equal("hello", lm:normalize("...Hello!"))
            assert.are.equal("john's", lm:normalize("John's"))
            assert.are.equal("watson", lm:normalize("Watson,"))
        end)
    end)

    describe("lookupAll", function()
        before_each(function()
            plugin.characters = {
                { name = "Sherlock Holmes", _norm_name = "sherlock holmes", aliases = {"Sherlock"}, _norm_aliases = {"sherlock"} },
                { name = "John Watson", _norm_name = "john watson" }
            }
            plugin.locations = {
                { name = "221B Baker Street", _norm_name = "221b baker street" }
            }
        end)

        it("should find exact match", function()
            local results = lm:lookupAll("John Watson")
            assert.are.equal(1, #results)
            assert.are.equal("John Watson", results[1].item.name)
            assert.are.equal(100, results[1].score)
        end)

        it("should find exact alias match", function()
            local results = lm:lookupAll("Sherlock")
            assert.are.equal(1, #results)
            assert.are.equal("Sherlock Holmes", results[1].item.name)
            assert.are.equal(95, results[1].score)
        end)

        it("should find contains match", function()
            local results = lm:lookupAll("Holmes")
            assert.are.equal(1, #results)
            assert.are.equal("Sherlock Holmes", results[1].item.name)
            assert.are.equal(50, results[1].score)
        end)

        it("should find contained match", function()
            local results = lm:lookupAll("John Watson and someone else")
            assert.are.equal(1, #results)
            assert.are.equal("John Watson", results[1].item.name)
            assert.are.equal(50, results[1].score)
        end)

        it("should prioritize better matches", function()
            -- Add a character whose alias is a substring of another
            table.insert(plugin.characters, { name = "Holmes Senior", _norm_name = "holmes senior" })
            
            local results = lm:lookupAll("Sherlock Holmes")
            -- "Sherlock Holmes" matches exactly.
            -- "Holmes Senior" might match partially (query contains "holmes").
            assert.are.equal(100, results[1].score)
            assert.are.equal("Sherlock Holmes", results[1].item.name)
        end)

        it("should filter out partial matches when an exact match is present", function()
            -- Add "Coherence" which is a substring/partial match
            plugin.terms = {
                { name = "associative coherence", _norm_name = "associative coherence" },
                { name = "Coherence", _norm_name = "coherence" }
            }
            local results = lm:lookupAll("associative coherence")
            -- Should only return "associative coherence" (score 100), not "Coherence" (score 30)
            assert.are.equal(1, #results)
            assert.are.equal("associative coherence", results[1].item.name)
            assert.are.equal(100, results[1].score)
        end)

        it("caches _norm_name onto the item after a lookup", function()
            local item = { name = "Rand al'Thor" }
            plugin.characters = { item }
            lm:lookupAll("Rand al'Thor")
            assert.truthy(item._norm_name)
        end)
    end)

    describe("handleLookup no-match branch and the ai fetching main switch", function()
        local XRayPlugin = require("main")
        local nm_plugin, nm_lm

        before_each(function()
            nm_plugin = createMockPlugin()
            nm_plugin.characters = {}
            nm_plugin.historical_figures = {}
            nm_plugin.locations = {}
            nm_plugin.terms = {}
            nm_plugin.isAiFetchingEnabled = XRayPlugin.isAiFetchingEnabled
            nm_lm = LookupManager:new(nm_plugin)
            _G.ui_tracker.shown = {}
            _G.ui_tracker.last_shown = nil
        end)

        it("offers the Look it up? ConfirmBox when ai fetching is enabled (absent = enabled)", function()
            nm_lm:handleLookup("nonexistent word", nil, nil)
            local last = _G.ui_tracker.last_shown
            assert.is_not_nil(last)
            assert.are.equal("ConfirmBox", last.type)
            assert.is_not_nil(last.args.ok_callback)
        end)

        it("does not offer single-word fetch when ai fetching is disabled", function()
            nm_plugin.ai_helper.settings.ai_fetching_enabled = false
            local fetch_called = false
            nm_plugin.fetchSingleWord = function() fetch_called = true end

            nm_lm:handleLookup("nonexistent word", nil, nil)

            assert.are.equal(false, fetch_called)
            local last = _G.ui_tracker.last_shown
            assert.is_not_nil(last)
            assert.are.equal("InfoMessage", last.type)
            -- InfoMessage instead of ConfirmBox: a ConfirmBox would carry ok_callback
            assert.is_nil(last.args.ok_callback)
            assert.are.equal("lookup_no_data_found", last.args.text)
        end)

        it("fails safe (hint, not fetch) when isAiFetchingEnabled is entirely absent from the plugin", function()
            nm_plugin.isAiFetchingEnabled = nil
            nm_lm:handleLookup("nonexistent word", nil, nil)
            local last = _G.ui_tracker.last_shown
            assert.is_not_nil(last)
            assert.are.equal("InfoMessage", last.type)
            assert.is_nil(last.args.ok_callback)
        end)
    end)
end)
