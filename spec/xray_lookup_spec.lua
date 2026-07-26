-- spec/xray_lookup_spec.lua -- the dictionary-popup field-selection rule.
--
-- This exists because the rule has now been got wrong twice in one day: first
-- by reading `dict_popup.word or dict_popup.text` and then by "correcting" it
-- to `lookupword` after copying KOReader's own vocabulary-builder plugin,
-- which wants the OPPOSITE field (it collects dictionary entries; we match a
-- reader's selection against names in a book). The two field names look
-- interchangeable and are not.
require("spec.spec_helper")
local XRayLookup = require("xray_lookup")

describe("XRayLookup.wordFromDictPopup", function()
    it("takes the query, not the dictionary's matched headword", function()
        -- Measured on a device: long-pressing "Frodo" in a German book reached
        -- an English-German dictionary, which fuzzy-matched the entry "brood".
        -- KOReader displays both -- "brood" as the headword, "(query: Frodo)"
        -- underneath (dictquicklookup.lua:1204 substitutes `self.word`). Only
        -- the query can ever match an X-Ray entry.
        assert.equals("Frodo",
            XRayLookup.wordFromDictPopup({ word = "Frodo", lookupword = "brood" }))
    end)

    it("never falls back to the headword when the query is empty", function()
        -- A whitespace-only manual lookup trims to "" in ReaderDictionary's
        -- cleanSelection. Falling back to the headword there would search for a
        -- dictionary artefact and report a confident, wrong miss -- worse than
        -- reporting nothing.
        assert.is_nil(XRayLookup.wordFromDictPopup({ word = "   ", lookupword = "brood" }))
        assert.is_nil(XRayLookup.wordFromDictPopup({ lookupword = "brood" }))
    end)

    it("trims surrounding whitespace off the query", function()
        assert.equals("Frodo", XRayLookup.wordFromDictPopup({ word = "  Frodo  " }))
    end)

    it("survives a missing popup", function()
        assert.is_nil(XRayLookup.wordFromDictPopup(nil))
    end)
end)
