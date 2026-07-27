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
