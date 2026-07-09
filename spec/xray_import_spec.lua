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

    describe("_buildImportedCache", function()
        local toc = {
            { title = "The Harbor at Dawn", page = 1 },
            { title = "Salt and Ledgers",   page = 30 },
            { title = "The Long Tide",      page = 80 },
        }
        local SNIP2 = "the harbourmaster set down his pen and looked up."

        local function build(page_count, doc_override)
            local self_ = with_document(mock_plugin(), page_count or 100, toc, { [SNIP2] = hits(47) })
            self_.computeCheckpoints = function() return {} end
            local doc = doc_override or mock_doc()
            local mapped = self_:_resolveCheckpointPages(doc)
            local book_data, snaps = self_:_buildImportedCache(doc, mapped)
            return book_data, snaps, self_
        end

        it("produces one snapshot per mapped checkpoint, indexed from 1", function()
            local _, snaps = build()
            assert.are.equal(3, #snaps)
            assert.are.equal(1, snaps[1].checkpoint_index)
            assert.are.equal(3, snaps[3].checkpoint_index)
        end)

        it("builds snapshot n from doc_json.checkpoints[mapped[n].index], not checkpoints[n]", function()
            -- Engineer the same collision the _resolveCheckpointPages spec uses:
            -- checkpoint 2's snippet resolves to page 29, colliding with
            -- checkpoint 1's TOC-derived 29, so it is dropped and
            -- mapped = {{index=1,page=29},{index=3,page=100}}. snapshots[2]
            -- must be built from checkpoints[3] (mapped[2].index), which alone
            -- carries "The Long Tide" and "Saint Bede" -- copying
            -- checkpoints[2] (the dropped one) or checkpoints[n] would produce
            -- a snapshot missing both.
            local self_ = with_document(mock_plugin(), 100, toc, { [SNIP2] = hits(29) })
            self_.computeCheckpoints = function() return {} end
            local doc = mock_doc()
            local mapped = self_:_resolveCheckpointPages(doc)
            assert.are.equal(2, #mapped)
            assert.are.equal(3, mapped[2].index)

            local _, snaps = self_:_buildImportedCache(doc, mapped)
            assert.are.equal(2, snaps[2].checkpoint_index)

            local has_location, has_figure = false, false
            for _, l in ipairs(snaps[2].locations) do
                if l.name == "The Long Tide" then has_location = true end
            end
            for _, f in ipairs(snaps[2].historical_figures) do
                if f.name == "Saint Bede" then has_figure = true end
            end
            assert.is_true(has_location)
            assert.is_true(has_figure)
        end)

        it("carries page and percent onto each snapshot", function()
            local _, snaps = build()
            assert.are.equal(29, snaps[1].page)
            assert.are.equal(29, snaps[1].percent)
            assert.are.equal(100, snaps[3].page)
            assert.are.equal(100, snaps[3].percent)
        end)

        it("never puts a timeline into a snapshot", function()
            local _, snaps = build()
            for _, s in ipairs(snaps) do assert.is_nil(s.timeline) end
        end)

        it("never stamps snapshot_version or cache_version itself", function()
            local book_data, snaps = build()
            assert.is_nil(book_data.cache_version)
            assert.is_nil(snaps[1].snapshot_version)
        end)

        it("preserves device field names verbatim", function()
            local _, snaps = build()
            local last = snaps[3]
            assert.are.equal("Alice Merrow", last.characters[1].name)
            assert.are.equal("protagonist", last.characters[1].role)
            assert.is_string(last.characters[1].description)
            assert.is_string(last.terms[1].definition)             -- not `description`
            assert.is_string(last.historical_figures[1].biography) -- not `description`
            assert.are.equal("climax", last.locations[2].importance)
        end)

        it("stamps first_page from first_pct against page_count, not the checkpoint page", function()
            -- snaps[1].page is 29, page_count is 100: an implementation that
            -- passed the checkpoint page as the total would produce 4, not 14.
            local _, snaps = build(100)
            assert.are.equal(14, snaps[1].characters[1].first_page)
            assert.are.equal(47, snaps[3].characters[2].first_page)
            assert.are.equal(92, snaps[3].locations[2].first_page)
            assert.are.equal(1, snaps[3].characters[1].first_seq)
        end)

        it("recomputes first_page on a second call instead of keeping a stale value", function()
            -- A retried import (e.g. after the user changes font size, which
            -- changes KOReader's own pagination) must not leave first_page
            -- pinned to the page_count of the first attempt.
            local doc = mock_doc()
            local self_ = with_document(mock_plugin(), 100, toc, { [SNIP2] = hits(47) })
            self_.computeCheckpoints = function() return {} end
            local mapped = self_:_resolveCheckpointPages(doc)

            local _, snaps1 = self_:_buildImportedCache(doc, mapped)
            assert.are.equal(14, snaps1[1].characters[1].first_page)  -- pctToPage(14, 100)

            self_.ui.document.getPageCount = function() return 200 end
            local _, snaps2 = self_:_buildImportedCache(doc, mapped)
            assert.are.equal(28, snaps2[1].characters[1].first_page)  -- pctToPage(14, 200)
        end)

        it("leaves terms without first_page (they sort alphabetically)", function()
            local _, snaps = build()
            assert.is_nil(snaps[3].terms[1].first_page)
        end)

        it("writes no history arrays", function()
            local _, snaps = build()
            for _, c in ipairs(snaps[3].characters) do assert.is_nil(c.history) end
        end)

        it("stamps sort_order so main.lua's restore pass has a stable key", function()
            -- Without it every entity ties at 9999 and Lua's unstable sort may
            -- permute the chronological order on the next open of the book.
            local book_data, snaps = build()
            assert.are.equal(1, snaps[3].characters[1].sort_order)
            assert.are.equal(2, snaps[3].characters[2].sort_order)
            assert.are.equal(1, book_data.historical_figures[1].sort_order)
        end)

        it("coerces non-string entity fields at the trust boundary", function()
            -- The JSON arrives inside a user-supplied EPUB. xray_ui.lua calls
            -- :sub() on term.definition; a number there would crash the reader.
            local doc = mock_doc()
            doc.checkpoints[3].snapshot.terms[1].definition = 42
            doc.checkpoints[3].snapshot.characters[1].role = 7
            local self_ = with_document(mock_plugin(), 100, toc, { [SNIP2] = hits(47) })
            self_.computeCheckpoints = function() return {} end
            local _, snaps = self_:_buildImportedCache(doc, self_:_resolveCheckpointPages(doc))
            assert.are.equal("42", snaps[3].terms[1].definition)
            assert.are.equal("7", snaps[3].characters[1].role)
        end)

        -- The real D4 property, in producer space (see Global Constraints).
        it("never puts an entity into a snapshot whose first_pct exceeds that checkpoint", function()
            local doc = mock_doc()
            local _, snaps = build(100, doc)
            for n, snap in ipairs(snaps) do
                local cp_pct = doc.checkpoints[n].percent
                for _, list in ipairs({ snap.characters, snap.locations }) do
                    for _, e in ipairs(list) do
                        assert.is_true(e.first_pct <= cp_pct)
                    end
                end
            end
        end)

        it("grows monotonically: every snapshot contains its predecessor's entities", function()
            local _, snaps = build()

            local seen1 = {}
            for _, c in ipairs(snaps[1].characters) do seen1[c.name] = true end
            assert.is_nil(seen1["Corwin Vale"])  -- first appears at checkpoint 2

            local grew = false
            for n = 2, #snaps do
                local seen = {}
                for _, c in ipairs(snaps[n].characters) do seen[c.name] = true end
                for _, c in ipairs(snaps[n - 1].characters) do
                    assert.is_true(seen[c.name] == true)
                end
                if #snaps[n].characters > #snaps[n - 1].characters then grew = true end
            end
            assert.is_true(grew)
        end)

        it("gives every timeline event a page and keeps them in the main cache", function()
            local book_data, snaps = build(100)
            assert.are.equal(3, #book_data.timeline)
            assert.are.equal(14, book_data.timeline[1].page)
            assert.are.equal(92, book_data.timeline[3].page)
            assert.is_string(book_data.timeline[1].event)
            assert.is_string(book_data.timeline[1].chapter)
            assert.is_nil(snaps[1].timeline)
        end)

        it("hides a timeline event with a missing or non-numeric pct instead of defaulting to page 1", function()
            -- The device's own rule (xray_prefetch.lua visibleTimeline) hides
            -- this-book events with no page anchor; defaulting to page 1 would
            -- show an unplaceable event to the reader from checkpoint 1 onward.
            local doc = mock_doc()
            doc.timeline[1].pct = nil
            doc.timeline[2].pct = "not-a-number"
            local book_data = build(100, doc)
            assert.is_nil(book_data.timeline[1].page)
            assert.is_nil(book_data.timeline[2].page)
            assert.are.equal(92, book_data.timeline[3].page)  -- sibling keeps its page
        end)

        it("mirrors the last snapshot into the main cache", function()
            local book_data, snaps = build()
            assert.are.equal(#snaps[3].characters, #book_data.characters)
            assert.are.equal("Test Book", book_data.book_title)
            assert.are.equal("Jane Author", book_data.author)
            assert.are.equal("fiction", book_data.book_type)
        end)

        it("joins multiple authors with a comma", function()
            local doc = mock_doc()
            doc.book_fingerprint.authors = { "Jane Author", "John Writer" }
            local book_data = build(100, doc)
            assert.are.equal("Jane Author, John Writer", book_data.author)
        end)

        it("treats a bare authors string as a single author instead of raising", function()
            local doc = mock_doc()
            doc.book_fingerprint.authors = "Jane Author"
            local book_data = build(100, doc)
            assert.are.equal("Jane Author", book_data.author)
        end)

        it("coerces a non-string author element instead of raising", function()
            local doc = mock_doc()
            doc.book_fingerprint.authors = { "Jane Author", { name = "John Writer" } }
            local book_data = build(100, doc)
            assert.is_string(book_data.author)
            assert.is_true(book_data.author:find("Jane Author", 1, true) ~= nil)
        end)

        it("sets last_fetch_page to the last mapped checkpoint page", function()
            assert.are.equal(100, (build(100)).last_fetch_page)
        end)

        it("builds a manifest that mirrors the mapped checkpoints", function()
            local book_data = build(100)
            assert.are.equal(3, #book_data.prefetch.checkpoints)
            assert.are.equal(29, book_data.prefetch.checkpoints[1].page)
            assert.are.equal(100, book_data.prefetch.checkpoints[3].page)
            assert.is_number(book_data.prefetch.created_at)
        end)

        it("marks a complete document as a completed prefetch", function()
            assert.is_true((build(100)).prefetch.completed)
        end)

        it("appends the device's own checkpoints beyond an incomplete import", function()
            local doc = mock_doc()
            doc.complete = false
            doc.last_percent = 47
            table.remove(doc.checkpoints, 3)   -- generated only up to 47%
            table.remove(doc.timeline, 3)      -- table.remove, not `= nil`: no holes

            local self_ = with_document(mock_plugin(), 100, toc, { [SNIP2] = hits(47) })
            self_.computeCheckpoints = function()
                return { { page = 29, percent = 29 }, { page = 70, percent = 70 }, { page = 100, percent = 100 } }
            end
            local mapped = self_:_resolveCheckpointPages(doc)
            local book_data, snaps = self_:_buildImportedCache(doc, mapped)

            assert.are.equal(2, #snaps)                            -- only the imported ones
            assert.are.equal(4, #book_data.prefetch.checkpoints)   -- 2 imported + 2 appended (70, 100)
            assert.are.equal(70, book_data.prefetch.checkpoints[3].page)
            assert.are.equal(100, book_data.prefetch.checkpoints[4].page)
            assert.is_nil(book_data.prefetch.completed)            -- device must finish the job
            assert.are.equal(47, book_data.last_fetch_page)
        end)

        it("marks an incomplete document complete when nothing is left to fetch", function()
            -- An incomplete doc's last checkpoint is clamped to page_count - 1
            -- (99), so the device fake must report nothing above that.
            local doc = mock_doc()
            doc.complete = false
            local self_ = with_document(mock_plugin(), 100, toc, {})
            self_.computeCheckpoints = function() return { { page = 99, percent = 99 } } end
            local mapped = self_:_resolveCheckpointPages(doc)
            local book_data = self_:_buildImportedCache(doc, mapped)
            assert.are.equal(99, book_data.last_fetch_page)
            assert.is_true(book_data.prefetch.completed)
        end)
    end)

    describe("importEmbeddedXray", function()
        local toc = {
            { title = "The Harbor at Dawn", page = 1 },
            { title = "Salt and Ledgers",   page = 30 },
            { title = "The Long Tide",      page = 80 },
        }
        local SNIP2 = "the harbourmaster set down his pen and looked up."

        local function fake_cache_manager()
            return {
                saved = nil, snaps = {}, deleted = false,
                saveCache = function(cm, _, data) cm.saved = data; return true end,
                saveSnapshot = function(cm, _, i, data) cm.snaps[i] = data; return true end,
                loadSnapshot = function(cm, _, i) return cm.snaps[i] end,
                snapshotExists = function(cm, _, i) return cm.snaps[i] ~= nil end,
                deleteSnapshots = function(cm) cm.snaps = {}; cm.deleted = true; return true end,
            }
        end

        local function prepared(page_count)
            local self_ = with_document(mock_plugin(), page_count or 100, toc, { [SNIP2] = hits(47) })
            self_.cache_manager = fake_cache_manager()
            self_.computeCheckpoints = function() return {} end
            self_.invalidated = false
            self_.invalidateSnapshotExistsCache = function(s) s.invalidated = true end
            self_.viewed_page = nil
            self_.updateSnapshotViewForPage = function(s, p) s.viewed_page = p end
            _G.ui_tracker.shown = {}
            return self_
        end

        it("writes the main cache and one file per snapshot", function()
            local self_ = prepared()
            self_:importEmbeddedXray(mock_doc())
            assert.is_not_nil(self_.cache_manager.saved)
            assert.are.equal(3, #self_.cache_manager.snaps)
            assert.are.equal(29, self_.cache_manager.snaps[1].page)
        end)

        it("adopts the imported cache as book_data and the timeline as self.timeline", function()
            local self_ = prepared()
            self_:importEmbeddedXray(mock_doc())
            assert.are.equal("Test Book", self_.book_data.book_title)
            assert.are.equal(3, #self_.book_data.prefetch.checkpoints)
            -- applySnapshot never swaps the timeline (D2), so the importer must
            -- mirror it onto self the way autoLoadCache does (main.lua:718).
            assert.are.equal(3, #self_.timeline)
        end)

        it("busts the snapshot-existence memo and refreshes the view", function()
            local self_ = prepared()
            self_:importEmbeddedXray(mock_doc())
            assert.is_true(self_.invalidated)
            assert.are.equal(1, self_.viewed_page)
        end)

        it("clears prefetch_active so the auto-prefetch is not blocked forever", function()
            local self_ = prepared()
            self_:importEmbeddedXray(mock_doc())
            assert.falsy(self_.prefetch_active)
        end)

        it("aborts without adopting book_data when a snapshot write fails", function()
            -- saveSnapshot returns false; it does not throw. Adopting the
            -- whole-book main cache with a missing gating snapshot would show a
            -- richer snapshot (or the full book) to a reader at page 5.
            local self_ = prepared()
            self_.cache_manager.saveSnapshot = function(cm, _, i, data)
                if i == 2 then return false end
                cm.snaps[i] = data; return true
            end
            self_:importEmbeddedXray(mock_doc())
            assert.is_nil(self_.cache_manager.saved)
            assert.is_nil(self_.book_data)
            assert.is_true(self_.cache_manager.deleted)
            assert.falsy(self_.prefetch_active)
        end)

        it("aborts without adopting book_data when the main cache write fails", function()
            local self_ = prepared()
            self_.cache_manager.saveCache = function() return false end
            self_:importEmbeddedXray(mock_doc())
            assert.is_nil(self_.book_data)
            assert.is_true(self_.cache_manager.deleted)
            assert.falsy(self_.prefetch_active)
        end)

        it("clears prefetch_active even when a write throws", function()
            local self_ = prepared()
            self_.cache_manager.saveCache = function() error("disk full") end
            self_:importEmbeddedXray(mock_doc())
            assert.is_nil(self_.book_data)
            assert.falsy(self_.prefetch_active)
        end)

        it("bails out on a book too short to stage without writing anything", function()
            local self_ = prepared(2)
            self_:importEmbeddedXray(mock_doc())
            assert.is_nil(self_.cache_manager.saved)
            assert.is_nil(self_.book_data)
            assert.falsy(self_.prefetch_active)
        end)

        it("deletes orphan snapshots when destroyed mid-import, without saving or adopting", function()
            -- The reader closes the book right after the first snapshot lands
            -- on disk but before saveCache ever runs. Left alone, a later
            -- native prefetch's _nextPendingCheckpoint (xray_prefetch.lua:
            -- 202-212) would see that orphan file and, since it only checks
            -- snapshotExists(path, i) and never the file's actual page, treat
            -- the checkpoint as already done under a freshly computed (and
            -- possibly later-anchored) manifest -- a spoiler leak.
            local self_ = prepared()
            local real_saveSnapshot = self_.cache_manager.saveSnapshot
            self_.cache_manager.saveSnapshot = function(cm, path, i, data)
                local ok = real_saveSnapshot(cm, path, i, data)
                if i == 1 then self_.destroyed = true end
                return ok
            end
            self_:importEmbeddedXray(mock_doc())
            assert.is_true(self_.cache_manager.deleted)
            assert.is_nil(self_.cache_manager.saved)
            assert.is_nil(self_.book_data)
            assert.falsy(self_.prefetch_active)
        end)

        it("mirrors book_type onto self after a successful import, but not after an aborted one", function()
            local self_ = prepared()
            self_:importEmbeddedXray(mock_doc())
            assert.are.equal("fiction", self_.book_type)

            local aborted = prepared()
            aborted.cache_manager.saveCache = function() return false end
            aborted:importEmbeddedXray(mock_doc())
            assert.is_nil(aborted.book_type)
        end)
    end)
end)
