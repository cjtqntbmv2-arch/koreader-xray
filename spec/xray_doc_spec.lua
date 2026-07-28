-- spec/xray_doc_spec.lua -- pure checkpoint-selection and timeline-filter
-- logic. No KOReader UI object is involved (selectCheckpoint/timeline take
-- plain doc tables); spec_helper's stubs are only needed because xray_doc.lua
-- requires docsettings/xray_i18n at module scope.
require("spec.spec_helper")
package.path = package.path .. ";xray_new.koplugin/?.lua"
local XRayDoc = require("xray_doc")

-- Three checkpoints; percents intentionally include the 100% edge case that
-- exercises XRayDoc.selectCheckpoint's clamp.
local function makeDoc()
    local empty_snapshot = { characters = {}, locations = {}, terms = {}, historical_figures = {} }
    return {
        checkpoints = {
            { percent = 10, snapshot = empty_snapshot },
            { percent = 50, snapshot = empty_snapshot },
            { percent = 100, snapshot = empty_snapshot },
        },
        timeline = {
            { chapter = "Ch1", event = "A", pct = 5 },
            { chapter = "Ch1", event = "B", pct = 30 },
            { chapter = "Ch1", event = "E", pct = 50 },  -- exactly at checkpoint 2's percent
            { chapter = "Ch2", event = "C", pct = 60 },
            { chapter = "Ch3", event = "D", pct = 95 },
        },
    }
end

describe("XRayDoc.nextPercent", function()
    it("returns the percent of the stage after the selected one", function()
        local doc = makeDoc()
        assert.equals(50, XRayDoc.nextPercent(doc, 1))
        assert.equals(100, XRayDoc.nextPercent(doc, 2))
    end)

    it("returns nil at the last stage -- nothing further can unlock", function()
        assert.is_nil(XRayDoc.nextPercent(makeDoc(), 3))
    end)

    it("returns the FIRST stage when none has been reached yet", function()
        -- cp_idx is nil before the first checkpoint clears, and that is exactly
        -- when the reader most wants to know from which percent data appears.
        assert.equals(10, XRayDoc.nextPercent(makeDoc(), nil))
    end)
end)

describe("XRayDoc.totals", function()
    it("counts the LAST snapshot -- what the whole book holds", function()
        local doc = makeDoc()
        doc.checkpoints[1].snapshot = {
            characters = {{name = "A"}}, locations = {}, terms = {}, historical_figures = {},
        }
        doc.checkpoints[3].snapshot = {
            characters = {{name = "A"}, {name = "B"}}, locations = {{name = "L"}},
            terms = {}, historical_figures = {},
        }

        local totals = XRayDoc.totals(doc)

        assert.equals(2, totals.characters)
        assert.equals(1, totals.locations)
        assert.equals(0, totals.terms)
    end)

    it("survives a document with no checkpoints at all", function()
        assert.equals(0, XRayDoc.totals({ checkpoints = {} }).characters)
    end)
end)

describe("XRayDoc.selectCheckpoint", function()
    it("returns nil when no checkpoint has been reached", function()
        local doc = makeDoc()
        -- first checkpoint's threshold is 10 + MARGIN = 12
        assert.is_nil(XRayDoc.selectCheckpoint(doc, 5))
    end)

    it("does not select a checkpoint exactly at its own percent (margin not yet cleared)", function()
        local doc = makeDoc()
        assert.is_nil(XRayDoc.selectCheckpoint(doc, 10))
    end)

    it("selects a checkpoint once its percent plus MARGIN is reached", function()
        local doc = makeDoc()
        assert.equals(1, XRayDoc.selectCheckpoint(doc, 10 + XRayDoc.MARGIN))
    end)

    it("clamps the last checkpoint's threshold to 100, so a reader at 100% reaches it", function()
        local doc = makeDoc()
        -- Without the clamp, the last checkpoint's threshold would be
        -- 100 + MARGIN = 102, which a 0..100 position could never reach.
        assert.equals(3, XRayDoc.selectCheckpoint(doc, 100))
    end)
end)

describe("XRayDoc.timeline", function()
    it("filters against the selected checkpoint's percent, not the raw reading position", function()
        local doc = makeDoc()
        -- Reader position is 80%, but MARGIN keeps checkpoint 3 (100%)
        -- unreached, so only checkpoint 2 (50%) is selected.
        local idx = XRayDoc.selectCheckpoint(doc, 80)
        assert.equals(2, idx)

        local events = XRayDoc.timeline(doc, idx)
        -- Event C (pct=60) is before the raw position (80) but after the
        -- selected checkpoint's percent (50) -- it must stay hidden. Event E
        -- (pct=50) sits exactly on the checkpoint's own percent and must be
        -- included (the bound is inclusive).
        assert.equals(3, #events)
        assert.equals("A", events[1].event)
        assert.equals("B", events[2].event)
        assert.equals("E", events[3].event)
    end)

    it("returns an empty list when no checkpoint has been reached", function()
        local doc = makeDoc()
        local idx = XRayDoc.selectCheckpoint(doc, 5)
        assert.is_nil(idx)
        assert.equals(0, #XRayDoc.timeline(doc, idx))
    end)
end)

-- Seven stages so an index can sit *between* two staged recaps. Recaps only
-- exist on the stages a test asks for -- partial coverage is the normal state
-- of a document, not a defect: the generation pass is one model call per
-- stage and an interrupted run leaves the later ones unwritten.
local function makeStagedDoc(recaps)
    local checkpoints = {}
    for i = 1, 7 do
        checkpoints[i] = { percent = i * 12, snapshot = {} }
    end
    for idx, text in pairs(recaps or {}) do
        checkpoints[idx].recap = text
    end
    return { checkpoints = checkpoints }
end

describe("XRayDoc.recap", function()
    it("returns the recap of the selected stage", function()
        local doc = makeStagedDoc({ [5] = "recap at five" })
        assert.equals("recap at five", XRayDoc.recap(doc, 5))
    end)

    it("walks back to the nearest earlier stage that has one", function()
        local doc = makeStagedDoc({ [2] = "recap at two", [5] = "recap at five" })
        assert.equals("recap at five", XRayDoc.recap(doc, 7))
    end)

    -- The D4 bound. Without this case an implementation that ignores idx and
    -- returns the last non-empty recap in the document passes every other
    -- assertion here -- and hands a reader at 30% the recap written for the
    -- ending.
    it("never returns a recap from a stage beyond the reader", function()
        local doc = makeStagedDoc({ [2] = "recap at two", [5] = "recap at five" })
        assert.equals("recap at two", XRayDoc.recap(doc, 3))
    end)

    -- "" is truthy in Lua, so `cp.recap or nil` would stop here and hand the
    -- viewer an empty page instead of the perfectly good earlier recap.
    it("treats an empty string as no recap and keeps walking back", function()
        local doc = makeStagedDoc({ [2] = "recap at two", [5] = "" })
        assert.equals("recap at two", XRayDoc.recap(doc, 5))
    end)

    it("returns nil when no stage carries a recap", function()
        assert.is_nil(XRayDoc.recap(makeStagedDoc(), 7))
    end)

    it("returns nil when no checkpoint has been reached", function()
        local doc = makeStagedDoc({ [2] = "recap at two" })
        assert.is_nil(XRayDoc.recap(doc, nil))
    end)

    it("survives malformed documents", function()
        assert.is_nil(XRayDoc.recap(nil, 3))
        assert.is_nil(XRayDoc.recap({}, 3))
        assert.is_nil(XRayDoc.recap({ checkpoints = "nope" }, 3))
    end)
end)

-- ---------------------------------------------------------------------------
-- Ego net (feature B)
-- ---------------------------------------------------------------------------

-- Two stages. Jon Schnee exists ONLY in the later one -- that is what makes
-- the D4 case below a real staging test rather than a "name is nowhere"
-- test, which a filter resolving against the LAST snapshot would also pass.
local function makeNetDoc()
    local function cast(with_jon)
        local characters = {
            { name = "Eddard Stark", aliases = { "Ned" } },
            { name = "Robb Stark" },
            { name = "Hodor" },  -- in the cast, in no relation
        }
        if with_jon then
            table.insert(characters, { name = "Jon Schnee" })
        end
        return {
            characters = characters,
            historical_figures = { { name = "Aegon der Eroberer" } },
        }
    end
    return {
        checkpoints = {
            { percent = 20, snapshot = cast(false) },
            { percent = 60, snapshot = cast(true) },
        },
        relations = {
            { from = "Robb Stark",   to = "Eddard Stark",       label = "Vater" },
            { from = "Eddard Stark", to = "Robb Stark",         label = "Sohn" },
            { from = "Eddard Stark", to = "Jon Schnee",         label = "Ziehsohn" },
            { from = "Robb Stark",   to = "Aegon der Eroberer", label = "Ahn" },
            { from = "Jon Schnee",   to = "Eddard Stark",       label = "Ziehvater" },
            { from = "Aegon der Eroberer", to = "Robb Stark",   label = "Nachfahre" },
        },
    }
end

local function nameSet(net)
    local names = {}
    for _unused, item in ipairs(net or {}) do
        names[item.entry.name] = item.label
    end
    return names
end

describe("XRayDoc.resolve", function()
    local snapshot = makeNetDoc().checkpoints[1].snapshot

    it("finds an entry by name", function()
        local hits = XRayDoc.resolve(snapshot, "Robb Stark")
        assert.equals(1, #hits)
        assert.equals("characters", hits[1].category)
    end)

    it("finds an entry by alias and reports its canonical entry", function()
        local hits = XRayDoc.resolve(snapshot, "Ned")
        assert.equals(1, #hits)
        assert.equals("Eddard Stark", hits[1].entry.name)
    end)

    it("tolerates case and surrounding punctuation", function()
        assert.equals(1, #XRayDoc.resolve(snapshot, "robb stark."))
    end)

    it("finds historical figures too", function()
        local hits = XRayDoc.resolve(snapshot, "Aegon der Eroberer")
        assert.equals(1, #hits)
        assert.equals("historical_figures", hits[1].category)
    end)

    it("returns an empty list for an unknown name", function()
        assert.equals(0, #XRayDoc.resolve(snapshot, "Tyrion Lennister"))
    end)
end)

describe("XRayDoc.egoNet", function()
    it("returns the neighbours of the centre figure", function()
        local doc = makeNetDoc()
        local net = XRayDoc.egoNet(doc, 2, { name = "Robb Stark" })
        assert.equals(2, #net)
        local names = nameSet(net)
        assert.equals("Vater", names["Eddard Stark"])
        assert.equals("Ahn", names["Aegon der Eroberer"])
    end)

    it("never shows a figure the reader has not reached", function()
        -- THE D4 case. Jon Schnee is a valid target at stage 2 and does not
        -- exist at stage 1. A filter that resolves against the last snapshot
        -- instead of the visible one passes every other case in this file --
        -- and shows a reader at 20% a name from the end of the book.
        local doc = makeNetDoc()
        local early = nameSet(XRayDoc.egoNet(doc, 1, { name = "Eddard Stark" }))
        assert.is_nil(early["Jon Schnee"])
        assert.equals("Sohn", early["Robb Stark"])

        local late = nameSet(XRayDoc.egoNet(doc, 2, { name = "Eddard Stark" }))
        assert.equals("Ziehsohn", late["Jon Schnee"])
    end)

    it("resolves an edge target that the snapshot knows only as an alias", function()
        local doc = makeNetDoc()
        doc.relations = { { from = "Robb Stark", to = "Ned", label = "Vater" } }
        local net = XRayDoc.egoNet(doc, 1, { name = "Robb Stark" })
        assert.equals(1, #net)
        -- Labelled with the snapshot entry's own name, not the edge's spelling:
        -- otherwise the node carries a name the reader cannot find in the list.
        assert.equals("Eddard Stark", net[1].entry.name)
    end)

    it("filters on `from`, so each direction keeps its own label", function()
        -- Without this, an egoNet filtering on `to` returns the same
        -- neighbours with every label swapped.
        local doc = makeNetDoc()
        assert.equals("Vater", nameSet(XRayDoc.egoNet(doc, 2, { name = "Robb Stark" }))["Eddard Stark"])
        assert.equals("Sohn", nameSet(XRayDoc.egoNet(doc, 2, { name = "Eddard Stark" }))["Robb Stark"])
    end)

    it("leaves out edges that belong to other figures", function()
        -- Absence counter-probe, with the count asserted: without it an egoNet
        -- returning every edge in the document passes the cases above. Aegon
        -- has exactly one outgoing edge, so this stays a real filter test
        -- rather than a trivially empty one.
        local doc = makeNetDoc()
        local net = XRayDoc.egoNet(doc, 2, { name = "Aegon der Eroberer" })
        assert.equals(1, #net)
        assert.equals("Robb Stark", net[1].entry.name)
    end)

    it("recognises the centre figure by one of its aliases", function()
        local doc = makeNetDoc()
        doc.relations = { { from = "Ned", to = "Robb Stark", label = "Sohn" } }
        assert.equals(1, #XRayDoc.egoNet(doc, 1, { name = "Eddard Stark", aliases = { "Ned" } }))
    end)

    it("carries the category so a tap knows which card to open", function()
        local doc = makeNetDoc()
        local net = XRayDoc.egoNet(doc, 2, { name = "Robb Stark" })
        local kinds = {}
        for _unused, item in ipairs(net) do kinds[item.entry.name] = item.category end
        assert.equals("characters", kinds["Eddard Stark"])
        assert.equals("historical_figures", kinds["Aegon der Eroberer"])
    end)

    it("sorts by displayed name so the view is deterministic", function()
        local doc = makeNetDoc()
        local net = XRayDoc.egoNet(doc, 2, { name = "Robb Stark" })
        assert.equals("Aegon der Eroberer", net[1].entry.name)
        assert.equals("Eddard Stark", net[2].entry.name)
    end)

    it("returns an empty list for a document without relations", function()
        local doc = makeNetDoc()
        doc.relations = nil
        assert.equals(0, #XRayDoc.egoNet(doc, 2, { name = "Robb Stark" }))
    end)

    it("returns an empty list for a figure with no edges", function()
        -- Hodor is in the cast and in no relation -- this is the case the UI
        -- uses to decide the "Relations" button must not appear.
        local doc = makeNetDoc()
        assert.equals(0, #XRayDoc.egoNet(doc, 2, { name = "Hodor" }))
    end)

    it("survives junk input", function()
        assert.equals(0, #XRayDoc.egoNet(nil, 1, { name = "x" }))
        assert.equals(0, #XRayDoc.egoNet(makeNetDoc(), 1, nil))
        assert.equals(0, #XRayDoc.egoNet(makeNetDoc(), 99, { name = "Robb Stark" }))
    end)
end)
