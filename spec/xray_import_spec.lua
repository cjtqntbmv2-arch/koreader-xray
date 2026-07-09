-- xray_import_spec.lua
require("spec.spec_helper")
local importer = require("xray_import")

-- Fresh deep copy per test: specs mutate entity tables (first_page stamping).
local function mock_doc()
    return dofile("spec/mocks/xray_import_doc.lua")
end

local function mock_plugin()
    local p = _G.createMockPlugin()
    for k, v in pairs(importer) do p[k] = v end
    return p
end

describe("xray_import", function()

    describe("_normTitle", function()
        it("lowercases, collapses whitespace and trims", function()
            assert.are.equal("test book", importer._normTitle("  Test   Book "))
        end)
        it("maps nil to the empty string", function()
            assert.are.equal("", importer._normTitle(nil))
        end)
    end)

    describe("_gateImport", function()
        local props = { title = "Test Book", authors = "Jane Author" }

        it("accepts a well-formed matching document", function()
            assert.is_nil(mock_plugin():_gateImport(mock_doc(), props))
        end)

        it("rejects a non-table document", function()
            assert.is_string(mock_plugin():_gateImport("not a table", props))
        end)

        it("rejects a newer schema version", function()
            local doc = mock_doc()
            doc.schema_version = 2
            assert.is_string(mock_plugin():_gateImport(doc, props))
        end)

        it("accepts an older schema version", function()
            local doc = mock_doc()
            doc.schema_version = 0
            assert.is_nil(mock_plugin():_gateImport(doc, props))
        end)

        it("rejects a document whose checkpoints key is missing", function()
            local doc = mock_doc()
            doc.checkpoints = nil
            assert.is_string(mock_plugin():_gateImport(doc, props))
        end)

        it("rejects a document with an empty checkpoints list", function()
            local doc = mock_doc()
            doc.checkpoints = {}
            assert.is_string(mock_plugin():_gateImport(doc, props))
        end)

        it("rejects a title mismatch", function()
            assert.is_string(mock_plugin():_gateImport(mock_doc(), { title = "A Different Book" }))
        end)

        it("ignores case and padding in the title comparison", function()
            assert.is_nil(mock_plugin():_gateImport(mock_doc(), { title = "  test   BOOK  " }))
        end)

        it("accepts when the device reports no title at all", function()
            assert.is_nil(mock_plugin():_gateImport(mock_doc(), {}))
        end)

        it("does not reject on an author mismatch", function()
            -- calibre writes "Martin, George R. R."; the EPUB says "George R. R. Martin".
            -- The JSON lives INSIDE this very EPUB, so title + schema are gate enough.
            assert.is_nil(mock_plugin():_gateImport(mock_doc(), { title = "Test Book", authors = "Author, Jane" }))
        end)
    end)

    -- A fake ui.document. `toc` is what getToc() returns; `find_hits` maps a
    -- snippet string to the list of hits findAllText should report.
    local function with_document(self_, page_count, toc, find_hits)
        self_.ui = self_.ui or {}
        self_.ui.document = {
            file = "/tmp/test_book.epub",
            getPageCount = function() return page_count end,
            getToc = function() return toc or {} end,
            findAllText = function(_, text, _, _, _, _)
                return (find_hits and find_hits[text]) or {}
            end,
            getProps = function() return { title = "Test Book" } end,
        }
        self_.ui.getCurrentPage = function() return 1 end
        -- Use the REAL classifier, not a hand-rolled stand-in: `_narrativeToc`
        -- must filter exactly as computeCheckpoints does, and a divergence
        -- between the two lists is precisely the bug worth catching.
        self_.isNonNarrativeChapter = require("xray_data").isNonNarrativeChapter
        return self_
    end

    -- findAllText returns a list of hit tables, each carrying .page
    local function hits(...)
        local out = {}
        for _, p in ipairs({ ... }) do table.insert(out, { page = p }) end
        return out
    end

    describe("_narrativeToc", function()
        it("drops non-narrative entries, keeps page order", function()
            local self_ = with_document(mock_plugin(), 100, {
                { title = "Cover", page = 1 },
                { title = "The Long Tide", page = 70 },
                { title = "The Harbor at Dawn", page = 10 },
                { title = "About the Author", page = 98 },
            })
            local n = self_:_narrativeToc()
            assert.are.equal(2, #n)
            assert.are.equal("The Harbor at Dawn", n[1].title)
            assert.are.equal("The Long Tide", n[2].title)
        end)

        it("drops entries with an out-of-range page", function()
            local self_ = with_document(mock_plugin(), 50, {
                { title = "One", page = 10 },
                { title = "Two", page = 500 },
                { title = "Three", page = 0 },
            })
            assert.are.equal(1, #self_:_narrativeToc())
        end)
    end)

    describe("_tocEndPage", function()
        local narrative = {
            { title = "One",   page = 10 },
            { title = "Two",   page = 40 },
            { title = "Three", page = 70 },
        }

        it("returns the page before the next chapter starts", function()
            local page, cursor = mock_plugin():_tocEndPage(narrative, "One", 0, 100)
            assert.are.equal(39, page)
            assert.are.equal(1, cursor)
        end)

        it("returns page_count for the last chapter", function()
            assert.are.equal(100, mock_plugin():_tocEndPage(narrative, "Three", 0, 100))
        end)

        it("searches only past the cursor, so duplicate titles resolve in order", function()
            local self_ = mock_plugin()
            local dup = {
                { title = "Chapter 1", page = 10 },
                { title = "Chapter 1", page = 40 },
                { title = "End",       page = 70 },
            }
            local p1, c1 = self_:_tocEndPage(dup, "Chapter 1", 0, 100)
            assert.are.equal(39, p1)
            assert.are.equal(69, self_:_tocEndPage(dup, "Chapter 1", c1, 100))
        end)

        it("returns nil for an unknown title", function()
            assert.is_nil(mock_plugin():_tocEndPage(narrative, "Nope", 0, 100))
        end)

        it("returns nil for an empty title", function()
            assert.is_nil(mock_plugin():_tocEndPage(narrative, "", 0, 100))
        end)

        it("clamps to page 1 when the next chapter starts on the same page", function()
            -- Two headings can render on the same physical page on coarse
            -- e-reader pagination; if that page is 1, next_start - 1 is 0,
            -- which would silently drop checkpoint 1 in
            -- _resolveCheckpointPages (xray_prefetch.lua's computeCheckpoints
            -- guards the analogous end_page with `>= 1`; this must too).
            local collide = {
                { title = "Part One",  page = 1 },
                { title = "Chapter 1", page = 1 },
                { title = "Chapter 2", page = 5 },
            }
            local page, cursor = mock_plugin():_tocEndPage(collide, "Part One", 0, 100)
            assert.are.equal(1, page)
            assert.are.equal(1, cursor)
        end)
    end)

    describe("_snippetPage", function()
        local SNIP = "a long enough snippet here"

        it("returns the page of a unique hit", function()
            local self_ = with_document(mock_plugin(), 100, {}, { [SNIP] = hits(42) })
            assert.are.equal(42, self_:_snippetPage(SNIP))
        end)

        it("rejects a snippet that matches more than once", function()
            -- A duplicate before the true boundary would activate a later
            -- snapshot early -- that is a spoiler, so refuse the anchor.
            local self_ = with_document(mock_plugin(), 100, {}, { [SNIP] = hits(12, 42) })
            assert.is_nil(self_:_snippetPage(SNIP))
        end)

        it("returns nil when nothing matches", function()
            local self_ = with_document(mock_plugin(), 100, {}, {})
            assert.is_nil(self_:_snippetPage(SNIP))
        end)

        it("returns nil for a snippet too short to be unique", function()
            local self_ = with_document(mock_plugin(), 100, {}, { ["tiny"] = hits(42) })
            assert.is_nil(self_:_snippetPage("tiny"))
        end)

        it("survives a document without findAllText", function()
            local self_ = with_document(mock_plugin(), 100, {})
            self_.ui.document.findAllText = nil
            assert.is_nil(self_:_snippetPage(SNIP))
        end)

        it("survives findAllText throwing", function()
            local self_ = with_document(mock_plugin(), 100, {})
            self_.ui.document.findAllText = function() error("crengine boom") end
            assert.is_nil(self_:_snippetPage(SNIP))
        end)
    end)

    describe("_resolveCheckpointPages", function()
        -- Mock chapter anchors: "The Harbor at Dawn" (cp1), none (cp2, densified),
        -- "The Long Tide" (cp3, last -> page_count).
        local toc = {
            { title = "The Harbor at Dawn", page = 1 },
            { title = "Salt and Ledgers",   page = 30 },
            { title = "The Long Tide",      page = 80 },
        }
        local SNIP2 = "the harbourmaster set down his pen and looked up."

        it("uses the TOC anchor when present and the snippet otherwise", function()
            local self_ = with_document(mock_plugin(), 100, toc, { [SNIP2] = hits(47) })
            local mapped = self_:_resolveCheckpointPages(mock_doc())
            assert.are.equal(3, #mapped)
            assert.are.equal(29, mapped[1].page)   -- TOC: 30 - 1
            assert.are.equal(47, mapped[2].page)   -- snippet
            assert.are.equal(100, mapped[3].page)  -- last -> page_count
        end)

        it("forces the last checkpoint of a complete document to page_count / 100%", function()
            local self_ = with_document(mock_plugin(), 100, toc, {})
            local mapped = self_:_resolveCheckpointPages(mock_doc())
            assert.are.equal(100, mapped[#mapped].page)
            assert.are.equal(100, mapped[#mapped].percent)
        end)

        it("treats a null chapter_anchor as absent without indexing it", function()
            -- json.decode may hand us a truthy null sentinel instead of nil.
            -- Use a boolean, not a string: Lua strings carry a metatable, so
            -- `("x").toc_title` is nil without erroring -- a weakened guard
            -- (`if anchor and anchor.toc_title`) would pass a string sentinel
            -- identically. A boolean is the sentinel some JSON decoders
            -- actually emit for null, and indexing it DOES raise "attempt to
            -- index a boolean value" -- the real failure this guard prevents.
            local doc = mock_doc()
            doc.checkpoints[2].chapter_anchor = true
            local self_ = with_document(mock_plugin(), 100, toc, { [SNIP2] = hits(47) })
            local mapped = self_:_resolveCheckpointPages(doc)  -- must not throw
            assert.are.equal(47, mapped[2].page)
        end)

        it("falls back to percent when both TOC and snippet miss", function()
            local self_ = with_document(mock_plugin(), 200, {}, {})
            local mapped = self_:_resolveCheckpointPages(mock_doc())
            assert.are.equal(28, mapped[1].page)  -- floor(200 * 14 / 100)
            assert.are.equal(94, mapped[2].page)  -- floor(200 * 47 / 100)
            assert.are.equal(200, mapped[3].page)
        end)

        it("drops the later of two checkpoints that collide on a page", function()
            -- Both anchors resolve to page 29. Keeping the EARLIER one is the
            -- spoiler-safe choice; the later, richer snapshot is discarded.
            local self_ = with_document(mock_plugin(), 100, toc, { [SNIP2] = hits(29) })
            local mapped = self_:_resolveCheckpointPages(mock_doc())
            assert.are.equal(2, #mapped)
            assert.are.equal(29, mapped[1].page)
            assert.are.equal(1, mapped[1].index)
            assert.are.equal(100, mapped[2].page)
            assert.are.equal(3, mapped[2].index)  -- original index survives the drop
        end)

        it("never drops the first checkpoint, so the smallest snapshot stays poorest", function()
            -- resolveSnapshotIndexForPage shows `smallest` before the first
            -- checkpoint; if cp1 were dropped a richer snapshot would take its
            -- slot. Route checkpoint 1 through the TOC tier (not the percent
            -- tier, which trivially can't collide) with two narrative entries
            -- colliding on page 1 -- the exact shape that made an unclamped
            -- _tocEndPage return 0 and drop checkpoint 1 entirely.
            local collide_toc = {
                { title = "The Harbor at Dawn", page = 1 },
                { title = "Salt and Ledgers",   page = 1 },
                { title = "The Long Tide",      page = 80 },
            }
            local self_ = with_document(mock_plugin(), 100, collide_toc, {})
            local mapped = self_:_resolveCheckpointPages(mock_doc())
            assert.are.equal(1, mapped[1].index)
            assert.are.equal(1, mapped[1].page)
        end)

        it("never lets a non-final checkpoint reach page_count", function()
            local self_ = with_document(mock_plugin(), 100, {},
                { ["prepared her for the real salt air."] = hits(100) })
            local mapped = self_:_resolveCheckpointPages(mock_doc())
            assert.are.equal(99, mapped[1].page)
        end)

        it("recomputes percent from the mapped page", function()
            local self_ = with_document(mock_plugin(), 100, toc, {})
            local mapped = self_:_resolveCheckpointPages(mock_doc())
            assert.are.equal(29, mapped[1].percent)
        end)

        it("returns nil for a book too short to stage", function()
            assert.is_nil(with_document(mock_plugin(), 2, {}, {}):_resolveCheckpointPages(mock_doc()))
        end)

        it("proceeds for page_count == 3, the minimum stageable length", function()
            -- Pins the boundary itself: only page_count == 2 -> nil was
            -- covered before, so an off-by-one (e.g. `< 4`) could slip through.
            assert.is_not_nil(with_document(mock_plugin(), 3, {}, {}):_resolveCheckpointPages(mock_doc()))
        end)

        it("does not pin the last checkpoint of an incomplete document to page_count", function()
            local doc = mock_doc()
            doc.complete = false
            table.remove(doc.checkpoints, 3)
            local self_ = with_document(mock_plugin(), 100, toc, { [SNIP2] = hits(47) })
            local mapped = self_:_resolveCheckpointPages(doc)
            assert.are.equal(2, #mapped)
            assert.are.equal(47, mapped[2].page)
            assert.are.equal(47, mapped[2].percent)
        end)

        it("calls yield_fn once per checkpoint", function()
            local self_ = with_document(mock_plugin(), 100, toc, {})
            local n = 0
            self_:_resolveCheckpointPages(mock_doc(), function() n = n + 1 end)
            assert.are.equal(3, n)
        end)
    end)
end)
