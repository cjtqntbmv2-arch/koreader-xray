-- xray_data_spec.lua
require("spec/spec_helper")

describe("xray_data", function()
    local xray_data

    setup(function()
        xray_data = require("xray_data")
    end)

    describe("normalizeChapterName", function()
        it("should handle digits", function()
            assert.are.equal("1", xray_data:normalizeChapterName("Chapter 1"))
            assert.are.equal("12", xray_data:normalizeChapterName("Ch. 12"))
        end)

        it("should handle written numbers", function()
            assert.are.equal("1", xray_data:normalizeChapterName("Chapter One"))
            assert.are.equal("20", xray_data:normalizeChapterName("Twenty"))
        end)

        it("should handle Roman numerals", function()
            assert.are.equal("4", xray_data:normalizeChapterName("IV"))
            assert.are.equal("9", xray_data:normalizeChapterName("Chapter IX"))
        end)

        it("should strip prefixes", function()
            assert.are.equal("intro", xray_data:normalizeChapterName("Chapter Intro"))
            assert.are.equal("prologue", xray_data:normalizeChapterName("Book Prologue"))
        end)

        -- Characterization test: pins current behavior (incl. the memo, once
        -- added) before/across the single-gsub refactor. Note the hyphenated
        -- word_to_num keys ("twenty-one" etc.) never fire, in old or new code
        -- (see xray_data.lua comment) -- "twenty-one" normalizes to "20-1".
        it("normalizes consistently and memo returns identical results", function()
            local cases = {
                { "Chapter Twenty", "20" },
                { "chapter twenty-one", "20-1" },
                { "CHAPTER 13", "13" },
                { "Part Three", "3" },
                { "XIV", "14" },
                { "  The   Dragon  Reborn ", "the dragon reborn" },
                { "Ch. 7", "7" },
            }
            for _, c in ipairs(cases) do
                local first = xray_data:normalizeChapterName(c[1])
                assert.are.equal(c[2], first)
                assert.are.equal(first, xray_data:normalizeChapterName(c[1]))
            end
        end)
    end)

    describe("isNonNarrativeChapter", function()
        it("should identify non-narrative chapters", function()
            assert.is_true(xray_data:isNonNarrativeChapter("Table of Contents"))
            assert.is_true(xray_data:isNonNarrativeChapter("About the Author"))
            assert.is_true(xray_data:isNonNarrativeChapter("Cover"))
        end)

        it("should identify narrative chapters", function()
            assert.is_false(xray_data:isNonNarrativeChapter("Chapter 1"))
            assert.is_false(xray_data:isNonNarrativeChapter("The Journey Begins"))
        end)
    end)

    describe("isMoreCompleteName", function()
        it("should return true if new name contains old name", function()
            assert.is_true(xray_data:isMoreCompleteName("John Watson", "Watson"))
            assert.is_true(xray_data:isMoreCompleteName("Dr. John Watson", "John Watson"))
        end)

        it("should return false if names are unrelated", function()
            assert.is_false(xray_data:isMoreCompleteName("Sherlock", "Watson"))
        end)
    end)

    describe("deduplicateByName", function()
        it("should merge aliases and promote names", function()
            local list = {
                { name = "John Watson", aliases = {"Watson"} },
                { name = "Watson", aliases = {"Doctor"} }
            }
            local result = xray_data:deduplicateByName(list, "name")
            assert.are.equal(1, #result)
            assert.are.equal("John Watson", result[1].name)
            local aliases = {}
            for _, a in ipairs(result[1].aliases) do aliases[a:lower()] = true end
            assert.is_true(aliases["doctor"])
            assert.is_true(aliases["watson"])
        end)

        it("does not merge different multi-word names sharing a first-name alias", function()
            local list = {
                { name = "Aegon Targaryen", aliases = {"Aegon"} },
                { name = "Aegon Blackfyre", aliases = {} }
            }
            local result = xray_data:deduplicateByName(list, "name")
            assert.are.equal(2, #result)
        end)

        it("still merges a bare first name into the matching full name", function()
            local list = {
                { name = "Daenerys Targaryen", aliases = {"Daenerys"} },
                { name = "Daenerys", aliases = {} }
            }
            local result = xray_data:deduplicateByName(list, "name")
            assert.are.equal(1, #result)
            assert.are.equal("Daenerys Targaryen", result[1].name)
        end)

        it("invalidates the _norm_aliases cache when merging aliases without promotion", function()
            -- Same canonical name (no promoteName), incoming duplicate adds a
            -- new alias -- the stale lazy cache must be dropped so lookupAll
            -- rebuilds it and sees the new alias.
            local kept = { name = "Alice", aliases = { "Al" }, _norm_aliases = { "al" } }
            local dup  = { name = "Alice", aliases = { "Ally" } }
            local result = xray_data:deduplicateByName({ kept, dup }, "name")
            assert.are.equal(1, #result)
            assert.are.equal(2, #kept.aliases)
            assert.is_true(kept._norm_aliases == nil)
        end)
    end)

    describe("mergeEntries", function()
        it("invalidates the _norm_aliases cache when absorbing aliases without promotion", function()
            -- Primary keeps its name (non-promote branch): secondary's name and
            -- aliases are absorbed, so the stale lazy cache must be dropped.
            local primary   = { name = "Alice Smith", aliases = { "Al" }, _norm_aliases = { "al" }, description = "d1" }
            local secondary = { name = "Alice", aliases = { "Ally" }, description = "d2" }
            local list = { primary, secondary }
            assert.is_true(xray_data:mergeEntries(list, "Alice Smith", "Alice"))
            assert.are.equal(1, #list)
            assert.is_true(primary._norm_aliases == nil)
        end)
    end)

    describe("assignTimelinePages", function()
        it("should match by exact name", function()
            local timeline = { { chapter = "Chapter 1" } }
            local toc = { { title = "Chapter 1", page = 5 } }
            xray_data:assignTimelinePages(timeline, toc)
            assert.are.equal(5, timeline[1].page)
        end)

        it("should match by leading number", function()
            local timeline = { { chapter = "1" } }
            local toc = { { title = "Chapter 1: The Start", page = 10 } }
            xray_data:assignTimelinePages(timeline, toc)
            assert.are.equal(10, timeline[1].page)
        end)

        it("should match multiple chapters with same number to sequential TOC entries", function()
            local timeline = { 
                { chapter = "1.1" },
                { chapter = "1.2" }
            }
            local toc = { 
                { title = "Chapter 1 Part 1", page = 10 },
                { title = "Chapter 1 Part 2", page = 20 }
            }
            xray_data:assignTimelinePages(timeline, toc)
            assert.are.equal(10, timeline[1].page)
            assert.are.equal(20, timeline[2].page)
        end)
    end)

    describe("assignTimelinePages battery guards", function()
        local M = require("xray_data")

        local function mkHost()
            local host = { normalize_calls = 0 }
            for k, v in pairs(M) do host[k] = v end
            local orig = host.normalizeChapterName
            host.normalizeChapterName = function(self, name)
                self.normalize_calls = self.normalize_calls + 1
                return orig(self, name)
            end
            return host
        end

        it("skips re-matching when fully assigned and the TOC is unchanged", function()
            local host = mkHost()
            local toc = { { page = 3, title = "One" }, { page = 9, title = "Two" } }
            local timeline = { { chapter = "One", page = 3 }, { chapter = "Two", page = 9 } }
            host:assignTimelinePages(timeline, toc, true)   -- 1st run: matches + records TOC fingerprint
            host.normalize_calls = 0
            host:assignTimelinePages(timeline, toc, true)   -- steady state (every menu open)
            assert.are.equal(0, host.normalize_calls)
        end)

        it("re-runs the matching after repagination (TOC pages shifted)", function()
            local host = mkHost()
            local timeline = { { chapter = "One", page = 3 } }
            host:assignTimelinePages(timeline, { { page = 3, title = "One" } }, true)
            host.normalize_calls = 0
            host:assignTimelinePages(timeline, { { page = 7, title = "One" } }, true)
            assert.is_true(host.normalize_calls > 0)
            assert.are.equal(7, timeline[1].page)   -- page repair still happens
        end)

        it("memoizes failed findText lookups per event", function()
            local host = mkHost()
            local find_calls = 0
            host.ui = { document = { findText = function() find_calls = find_calls + 1; return nil end } }
            local ev = { chapter = "Unfindable Chapter Name" }
            host:assignTimelinePages({ ev }, {}, true)
            assert.are.equal(1, find_calls)
            assert.is_true(ev._findtext_failed == true)
            host:assignTimelinePages({ ev }, {}, true)
            assert.are.equal(1, find_calls)   -- no second book-wide scan
        end)

        it("does not memoize a transient findText crash", function()
            local host = mkHost()
            local find_calls = 0
            host.ui = { document = { findText = function()
                find_calls = find_calls + 1
                error("engine busy")
            end } }
            local ev = { chapter = "Unfindable Chapter Name" }
            host:assignTimelinePages({ ev }, {}, true)
            assert.falsy(ev._findtext_failed)
            host:assignTimelinePages({ ev }, {}, true)
            assert.are.equal(2, find_calls)   -- retried, not memoized
        end)
    end)

    describe("sortDataByFrequency", function()
        it("should rank protagonists higher", function()
            local list = {
                { name = "Sidekick", role = "Supporting" },
                { name = "Hero", role = "Protagonist" }
            }
            xray_data:sortDataByFrequency(list, "Hero Hero Sidekick", "name")
            assert.are.equal("Hero", list[1].name)
        end)

        it("should rank by frequency when roles are same", function()
            local list = {
                { name = "Rare", role = "Minor" },
                { name = "Frequent", role = "Minor" }
            }
            -- Use high enough counts to overcome normalization
            xray_data:sortDataByFrequency(list, "Frequent Frequent Frequent Frequent Frequent Frequent Rare", "name")
            assert.are.equal("Frequent", list[1].name)
        end)
    end)

    describe("sortByFirstAppearance", function()
        it("orders by first_page ascending", function()
            local list = {
                { name = "Late",  first_page = 300, first_seq = 3 },
                { name = "Early", first_page = 10,  first_seq = 1 },
                { name = "Mid",   first_page = 150, first_seq = 2 },
            }
            xray_data:sortByFirstAppearance(list)
            assert.are.equal("Early", list[1].name)
            assert.are.equal("Mid",   list[2].name)
            assert.are.equal("Late",  list[3].name)
        end)

        it("breaks ties deterministically by first_seq", function()
            local list = {
                { name = "B", first_page = 50, first_seq = 2 },
                { name = "A", first_page = 50, first_seq = 1 },
            }
            xray_data:sortByFirstAppearance(list)
            assert.are.equal("A", list[1].name)
            assert.are.equal("B", list[2].name)
        end)

        it("falls back to history[1].page then to the end", function()
            local list = {
                { name = "NoStamp" },
                { name = "HistOnly", history = { { page = 5 } } },
            }
            xray_data:sortByFirstAppearance(list)
            assert.are.equal("HistOnly", list[1].name)
            assert.are.equal("NoStamp",  list[2].name)
        end)

        it("stamps sort_order for cache loads", function()
            local list = { { name = "X", first_page = 1, first_seq = 1 } }
            xray_data:sortByFirstAppearance(list)
            assert.are.equal(1, list[1].sort_order)
        end)
    end)

    describe("sortByName", function()
        it("orders case-insensitively by name", function()
            local list = { { name = "banana" }, { name = "Apple" }, { name = "cherry" } }
            xray_data:sortByName(list)
            assert.are.equal("Apple",  list[1].name)
            assert.are.equal("banana", list[2].name)
            assert.are.equal("cherry", list[3].name)
        end)
    end)

    describe("sortEntityList", function()
        it("routes characters/locations to first-appearance", function()
            local list = { { name = "Z", first_page = 9 }, { name = "A", first_page = 2 } }
            xray_data:sortEntityList(list, "character")
            assert.are.equal("A", list[1].name)
        end)
        it("routes terms to alphabetical", function()
            local list = { { name = "Zeta", first_page = 1 }, { name = "Alpha", first_page = 9 } }
            xray_data:sortEntityList(list, "term")
            assert.are.equal("Alpha", list[1].name)
        end)
    end)

    describe("glossary ordering", function()
        it("sortEntityList term ignores first_page and role", function()
            local list = {
                { name = "Wildfire", role = "primary", first_page = 1 },
                { name = "Aegon's Conquest", first_page = 900 },
            }
            xray_data:sortEntityList(list, "term")
            assert.are.equal("Aegon's Conquest", list[1].name)
            assert.are.equal("Wildfire", list[2].name)
        end)
    end)

    describe("stampFirstAppearance", function()
        it("sets first_page and a monotonic first_seq only once", function()
            local counter = { n = 0 }
            local a, b = { name = "A" }, { name = "B" }
            xray_data:stampFirstAppearance(a, 10, counter)
            xray_data:stampFirstAppearance(b, 20, counter)
            assert.are.equal(10, a.first_page)
            assert.are.equal(20, b.first_page)
            assert.is_true(b.first_seq > a.first_seq)
            -- re-stamp must not move an existing entity
            xray_data:stampFirstAppearance(a, 999, counter)
            assert.are.equal(10, a.first_page)
        end)
    end)
end)
