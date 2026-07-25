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
