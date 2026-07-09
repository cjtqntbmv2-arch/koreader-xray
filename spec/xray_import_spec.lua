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
end)
