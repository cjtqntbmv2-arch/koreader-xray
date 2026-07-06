-- xray_cachemanager_spec.lua
require("spec.spec_helper")
local cache_manager = require("xray_cachemanager"):new()

describe("xray_cachemanager", function()
    local test_book = "/tmp/test_book.epub"
    local test_cache = test_book .. ".sdr/xray_cache.lua"

    before_each(function()
        -- Ensure clean state
        os.execute("rm -rf /tmp/test_book.epub.sdr")
        os.execute("mkdir -p /tmp/test_book.epub.sdr")
    end)

    describe("getCachePath", function()
        it("returns correct sidecar path", function()
            local path = cache_manager:getCachePath(test_book)
            assert.are.equal(test_cache, path)
        end)
    end)

    describe("Serialization and Saving", function()
        it("saves and loads data correctly", function()
            local data = {
                characters = {
                    { name = "Alice", role = "Protagonist" }
                },
                last_fetch_page = 42
            }

            local success = cache_manager:saveCache(test_book, data)
            assert.is_true(success)

            local loaded = cache_manager:loadCache(test_book)
            assert.is_not_nil(loaded)
            assert.are.equal("Alice", loaded.characters[1].name)
            assert.are.equal(42, loaded.last_fetch_page)
            assert.are.equal("6.0", loaded.cache_version)
        end)

        it("saves and loads data correctly using asyncSaveCache fallback", function()
            local data = {
                characters = {
                    { name = "Bob", role = "Deuteragonist" }
                },
                last_fetch_page = 101
            }

            local done_called = false
            local success = cache_manager:asyncSaveCache(test_book, data, function(res)
                done_called = true
                assert.is_true(res)
            end)
            assert.is_true(success)
            assert.is_true(done_called)

            -- Allow any forked child process time to finish writing before reading
            os.execute("sleep 0.2")

            local loaded = cache_manager:loadCache(test_book)
            assert.is_not_nil(loaded)
            assert.are.equal("Bob", loaded.characters[1].name)
            assert.are.equal(101, loaded.last_fetch_page)
        end)

        it("handles circular references gracefully", function()
            local data = { name = "Alice" }
            data.self = data -- Circular reference

            local success = cache_manager:saveCache(test_book, data)
            assert.is_true(success)

            local loaded = cache_manager:loadCache(test_book)
            -- The circular reference is serialized as an empty table with a comment marker
            assert.is_table(loaded.self)
            assert.are.equal(0, #loaded.self)
        end)
    end)

    describe("Snapshot persistence", function()
        it("builds zero-padded snapshot paths", function()
            local path = cache_manager:getSnapshotPath(test_book, 3)
            assert.are.equal(test_book .. ".sdr/xray_snapshot_03.lua", path)
            assert.is_nil(cache_manager:getSnapshotPath(nil, 3))
            assert.is_nil(cache_manager:getSnapshotPath(test_book, nil))
        end)

        it("round-trips a snapshot and stamps version", function()
            local ok = cache_manager:saveSnapshot(test_book, 2, {
                page = 120, percent = 20,
                characters = { { name = "Rand", description = "A shepherd" } },
                locations = {}, terms = {}, historical_figures = {},
            })
            assert.is_true(ok)
            assert.is_true(cache_manager:snapshotExists(test_book, 2))
            local loaded = cache_manager:loadSnapshot(test_book, 2)
            assert.is_not_nil(loaded)
            assert.are.equal(1, loaded.snapshot_version)
            assert.are.equal(120, loaded.page)
            assert.are.equal(20, loaded.percent)
            assert.are.equal("Rand", loaded.characters[1].name)
        end)

        it("returns nil for missing or version-mismatched snapshots", function()
            assert.is_nil(cache_manager:loadSnapshot(test_book, 9))
            assert.is_false(cache_manager:snapshotExists(test_book, 9))
            -- Version mismatch: write a file with a foreign version
            local path = cache_manager:getSnapshotPath(test_book, 9)
            local f = io.open(path, "w")
            f:write("return { snapshot_version = 99 }\n")
            f:close()
            assert.is_nil(cache_manager:loadSnapshot(test_book, 9))
        end)

        it("deleteSnapshots removes all snapshot files", function()
            cache_manager:saveSnapshot(test_book, 1, { page = 10, characters = {} })
            cache_manager:saveSnapshot(test_book, 4, { page = 40, characters = {} })
            cache_manager:deleteSnapshots(test_book)
            assert.is_false(cache_manager:snapshotExists(test_book, 1))
            assert.is_false(cache_manager:snapshotExists(test_book, 4))
        end)

        it("clearCache also removes snapshots", function()
            cache_manager:saveSnapshot(test_book, 1, { page = 10, characters = {} })
            cache_manager:saveCache(test_book, { characters = {} })
            cache_manager:clearCache(test_book)
            assert.is_false(cache_manager:snapshotExists(test_book, 1))
            assert.is_nil(cache_manager:loadCache(test_book))
        end)
    end)
end)

describe("atomic writes", function()
    local CacheManager = require("xray_cachemanager")

    before_each(function()
        -- ponytail: spec_helper's mocked lfs.attributes reports any ".sdr" path
        -- as an existing directory regardless of the real filesystem, so
        -- ensureDirectory() never actually creates it here -- pre-create it
        -- the same way the outer describe block's before_each does.
        os.execute("rm -rf atomic_test.epub.sdr")
        os.execute("mkdir -p atomic_test.epub.sdr")
    end)

    it("keeps the previous cache intact when a save fails midway", function()
        local cm = CacheManager:new()
        local book = "atomic_test.epub"
        assert.is_true(cm:saveCache(book, { characters = { { name = "Alice" } } }))
        cm.serializeToFile = function() error("boom") end
        assert.is_false(cm:saveCache(book, { characters = { { name = "Bob" } } }))
        cm.serializeToFile = nil -- restore class method via __index
        local data = cm:loadCache(book)
        assert.is_not_nil(data)
        assert.are.equal("Alice", data.characters[1].name)
    end)

    it("keeps the previous snapshot intact when a snapshot save fails midway", function()
        local cm = CacheManager:new()
        local book = "atomic_test.epub"
        assert.is_true(cm:saveSnapshot(book, 1, { characters = { { name = "Alice" } } }))
        cm.serializeToFile = function() error("boom") end
        assert.is_false(cm:saveSnapshot(book, 1, { characters = { { name = "Bob" } } }))
        cm.serializeToFile = nil
        local snap = cm:loadSnapshot(book, 1)
        assert.is_not_nil(snap)
        assert.are.equal("Alice", snap.characters[1].name)
    end)
end)
