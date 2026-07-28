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
            labels[row.text] = row.subtext
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
        assert.equals(1, #menu.item_table)
        assert.equals("\226\128\162 Eddard Stark", menu.item_table[1].text)
    end)

    it("survives junk input", function()
        reset()
        XRayUI.showEgoNet(nil, 1, NED)
        XRayUI.showEgoNet(makeDoc(), 2, nil)
        assert.is_nil(_G.ui_tracker.last_shown)
    end)
end)
