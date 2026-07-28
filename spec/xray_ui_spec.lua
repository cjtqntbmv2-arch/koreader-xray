require("spec.spec_helper")

local XRayUI = require("xray_ui")

-- Two stages; Jon Schnee exists only in the later one. Same shape as the
-- ego-net fixture in xray_doc_spec, kept separate so a change to one does not
-- silently retune the other.
local function makeDoc()
    local function cast(with_jon)
        local characters = {
            { name = "Eddard Stark", aliases = { "Ned" }, description = "Lord." },
            { name = "Robb Stark", description = "Sein Sohn." },
            { name = "Hodor", description = "Hodor." },
        }
        if with_jon then
            table.insert(characters, { name = "Jon Schnee", description = "Ziehsohn." })
        end
        return { characters = characters, historical_figures = {} }
    end
    return {
        checkpoints = {
            { percent = 20, snapshot = cast(false) },
            { percent = 60, snapshot = cast(true) },
        },
        relations = {
            { from = "Robb Stark",   to = "Eddard Stark", label = "Vater" },
            { from = "Eddard Stark", to = "Robb Stark",   label = "Sohn" },
            { from = "Eddard Stark", to = "Jon Schnee",   label = "Ziehsohn" },
            { from = "Robb Stark",   to = "Jon Schnee",   label = "Halbbruder" },
        },
    }
end

local function reset()
    _G.ui_tracker.shown = {}
    _G.ui_tracker.last_shown = nil
    _G.ui_tracker.closed = {}
end

local NED = { name = "Eddard Stark", aliases = { "Ned" }, description = "Lord." }
local HODOR = { name = "Hodor", description = "Hodor." }

describe("XRayUI.showEntry relations button", function()
    it("offers the button when the figure has neighbours", function()
        reset()
        XRayUI.showEntry(NED, "characters", makeDoc(), 2)
        local viewer = _G.ui_tracker.last_shown.args
        assert.is_not_nil(viewer)
        assert.is_table(viewer.buttons_table)
        assert.equals(1, #viewer.buttons_table)
        assert.equals(1, #viewer.buttons_table[1])
    end)

    it("keeps TextViewer's own buttons alongside it", function()
        -- Without add_default_buttons a caller's buttons_table REPLACES the
        -- default row, and the Close button disappears from exactly the cards
        -- this feature touches (textviewer.lua).
        reset()
        XRayUI.showEntry(NED, "characters", makeDoc(), 2)
        assert.is_true(_G.ui_tracker.last_shown.args.add_default_buttons)
    end)

    it("omits the button for a figure without neighbours", function()
        -- The counter-probe. Without it an implementation that always adds the
        -- button passes the case above.
        reset()
        XRayUI.showEntry(HODOR, "characters", makeDoc(), 2)
        assert.is_nil(_G.ui_tracker.last_shown.args.buttons_table)
    end)

    it("omits the button when the caller passed no document", function()
        reset()
        XRayUI.showEntry(NED, "characters")
        assert.is_not_nil(_G.ui_tracker.last_shown)
        assert.is_nil(_G.ui_tracker.last_shown.args.buttons_table)
    end)

    it("still renders the card itself in every case", function()
        reset()
        XRayUI.showEntry(HODOR, "characters", makeDoc(), 2)
        assert.equals("Hodor", _G.ui_tracker.last_shown.args.title)
    end)
end)

describe("XRayUI.showEgoNet", function()
    it("lists one row per neighbour, labelled with the relation", function()
        reset()
        XRayUI.showEgoNet(makeDoc(), 2, NED)
        local menu = _G.ui_tracker.last_shown.args
        assert.is_not_nil(menu)
        assert.equals(2, #menu.item_table)
        local labels = {}
        for _unused, row in ipairs(menu.item_table) do
            labels[row.text] = row.mandatory
        end
        -- Sorted by name: Jon Schnee before Robb Stark.
        assert.equals("Ziehsohn", labels["\226\128\162 Jon Schnee"])
        assert.equals("Sohn", labels["\226\128\162 Robb Stark"])
    end)

    it("shows nothing at all when there are no neighbours", function()
        reset()
        XRayUI.showEgoNet(makeDoc(), 2, HODOR)
        assert.is_nil(_G.ui_tracker.last_shown)
    end)

    it("never lists a figure the reader has not reached", function()
        -- The D4 case again, one layer up: XRayDoc.egoNet enforces it, this
        -- asserts the UI actually asks for the reader's stage and not another.
        reset()
        XRayUI.showEgoNet(makeDoc(), 1, NED)
        local menu = _G.ui_tracker.last_shown.args
        assert.equals(1, #menu.item_table)
        assert.equals("\226\128\162 Robb Stark", menu.item_table[1].text)
    end)

    it("opens the neighbour's own net when its row is tapped", function()
        reset()
        XRayUI.showEgoNet(makeDoc(), 2, NED)
        local rows = _G.ui_tracker.last_shown.args.item_table
        local robb
        for _unused, row in ipairs(rows) do
            if row.text == "\226\128\162 Robb Stark" then robb = row end
        end
        assert.is_not_nil(robb)

        reset()
        robb.callback()
        local menu = _G.ui_tracker.last_shown.args
        assert.is_not_nil(menu)
        -- At stage 2 Robb has both Eddard and Jon Schnee.
        assert.equals(2, #menu.item_table)
        assert.equals("\226\128\162 Eddard Stark", menu.item_table[1].text)
    end)

    it("survives junk input", function()
        reset()
        XRayUI.showEgoNet(nil, 1, NED)
        XRayUI.showEgoNet(makeDoc(), 2, nil)
        assert.is_nil(_G.ui_tracker.last_shown)
    end)
end)

-- ---------------------------------------------------------------------------
-- Review follow-ups (2026-07-28)
-- ---------------------------------------------------------------------------

describe("XRayUI ego net wiring", function()
    it("pins the button to the reader's stage, not the last one", function()
        -- The original fixture ran every case at cp_idx == #checkpoints, so a
        -- callback passing #doc.checkpoints instead of cp_idx was invisible.
        -- Robb has a neighbour that exists only in the later stage, so the two
        -- now answer differently.
        reset()
        XRayUI.showEntry({ name = "Robb Stark", description = "Sein Sohn." },
                         "characters", makeDoc(), 1)
        local button = _G.ui_tracker.last_shown.args.buttons_table[1][1]

        reset()
        button.callback()
        local rows = _G.ui_tracker.last_shown.args.item_table
        assert.equals(1, #rows)
        assert.equals("\226\128\162 Eddard Stark", rows[1].text)
    end)

    it("opens the net of the figure whose card it sits on", function()
        -- Without invoking the callback, a button wired to the wrong figure
        -- passes every assertion about buttons_table's shape.
        reset()
        XRayUI.showEntry(NED, "characters", makeDoc(), 2)
        local button = _G.ui_tracker.last_shown.args.buttons_table[1][1]
        assert.equals("Relations", button.text)

        reset()
        button.callback()
        assert.equals("Eddard Stark \226\128\148 Relations",
                      _G.ui_tracker.last_shown.args.title)
    end)

    it("keeps the reader's stage across a tap into a neighbour's net", function()
        reset()
        XRayUI.showEgoNet(makeDoc(), 1, NED)
        local rows = _G.ui_tracker.last_shown.args.item_table
        reset()
        rows[1].callback()  -- Robb Stark, the only neighbour at stage 1
        local next_rows = _G.ui_tracker.last_shown.args.item_table
        -- At stage 1 Robb has exactly one visible neighbour; at stage 2 he has
        -- two. A recursion that drifted to the last stage would show both.
        assert.equals(1, #next_rows)
        assert.equals("\226\128\162 Eddard Stark", next_rows[1].text)
    end)

    it("reaches the button through the category list", function()
        -- All four call sites that thread doc/cp_idx were added by this change
        -- and could be reverted with the suite green. This one covers the list
        -- path end to end.
        reset()
        XRayUI.showList(nil, makeDoc(), 2, "characters")
        local rows = _G.ui_tracker.last_shown.args.item_table
        local ned_row
        for _unused, row in ipairs(rows) do
            if row.text == "\226\128\162 Eddard Stark" then ned_row = row end
        end
        assert.is_not_nil(ned_row)

        reset()
        ned_row.callback()
        assert.is_not_nil(_G.ui_tracker.last_shown.args.buttons_table)
    end)

    it("shows the relation label beside the neighbour", function()
        -- `subtext` is not a Menu field -- 0 occurrences in KOReader's
        -- menu.lua in master, v2023.05 and v2025.04 -- so the label, which is
        -- this feature's entire payload, was rendered nowhere. `mandatory` is
        -- the real field for a short right-aligned value.
        reset()
        XRayUI.showEgoNet(makeDoc(), 2, NED)
        local labels = {}
        for _unused, row in ipairs(_G.ui_tracker.last_shown.args.item_table) do
            labels[row.text] = row.mandatory
        end
        assert.equals("Ziehsohn", labels["\226\128\162 Jon Schnee"])
        assert.equals("Sohn", labels["\226\128\162 Robb Stark"])
    end)

    it("offers no relations button on a term or location card", function()
        local doc = makeDoc()
        doc.checkpoints[2].snapshot.terms = { { name = "Eddard Stark",
                                                definition = "Ein Begriff." } }
        reset()
        XRayUI.showEntry({ name = "Eddard Stark", definition = "Ein Begriff." },
                         "terms", doc, 2)
        assert.is_nil(_G.ui_tracker.last_shown.args.buttons_table)
    end)
end)
