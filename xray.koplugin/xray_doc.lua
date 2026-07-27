--[[
xray_doc.lua -- read xray.json (companion file or embedded zip member) and
answer the spoiler-staged questions the UI needs: which checkpoint applies at
the reader's current position, and what that checkpoint's snapshot/timeline
contain.

This is a read-only data module: it never touches the book file, and it has
no KOReader UI dependency beyond `docsettings` (for the .sdr extraction
target) -- xray_ui.lua/main.lua own everything about *displaying* what this
returns.

Ported patterns, not a dependency: the pure-Lua zip central-directory probe,
the mkdir-before-unzip BusyBox workaround, and manual shell-quoting all come
from xray.koplugin/xray_import.lua (read-only reference -- that plugin is
frozen, nothing here requires it). One deliberate divergence: the old
importer also gated on book title, because a mismatched fetch could silently
attach the wrong book's data; v2 drops that -- calibre now checks
book_fingerprint.text_hash before it ever embeds (docs/plans/2026-07-25-xray-
neuausrichtung.md, Phase 3), so the only gate left on-device is schema_version.
]]

local DocSettings = require("docsettings")
local _ = require("xray_i18n")

local XRayDoc = {}

-- Reading-position margin, in checkpoint-percent points: a checkpoint is
-- selected only once the reader's position has passed its percent by this
-- much, so device position noise can never surface a checkpoint's data
-- before the reader actually reached it. Calibration knob if on-device
-- measurement (plan, Phase 4/R2) finds the margin too tight or too loose.
XRayDoc.MARGIN = 2

local SUPPORTED_SCHEMA = 2
local DATA_PATH = "xray/xray.json"

-- doc/err per book_path, kept for the reading session -- without it, every
-- menu open would re-run the zip probe (cheap) and, on a book that does
-- carry embedded data, the mkdir/unzip/rm dance (not cheap) again.
local _cache = {}

-- ---------------------------------------------------------------------
-- Shell/zip plumbing, ported from xray_import.lua (see module docstring).
-- ---------------------------------------------------------------------

-- string.format("%q", s) is LUA quoting, not shell quoting: it leaves $, `,
-- ( and ) live for the shell to expand. Single quotes suppress all shell
-- expansion; only the quote character itself needs escaping.
local function shellQuote(s)
    return "'" .. (tostring(s):gsub("'", "'\\''")) .. "'"
end

local function u16le(s, i)
    local a, b = s:byte(i, i + 1)
    if not b then return nil end
    return a + b * 256
end

local function u32le(s, i)
    local a, b, c, d = s:byte(i, i + 3)
    if not d then return nil end
    return a + b * 256 + c * 65536 + d * 16777216
end

-- Is `name` listed in the zip's central directory? Pure Lua, no shell-out:
-- locate the End-Of-Central-Directory record by validating candidates (a
-- trailing archive comment can itself contain "PK\5\6", so "last
-- occurrence" is not a safe locator), then walk the central directory
-- record by record (ZIP spec 4.3.12) comparing filenames for exact
-- equality -- a member named "not-xray/xray.json.bak" contains
-- "xray/xray.json" as a raw substring, so this must not be a substring
-- search. Running this before any mkdir/unzip means a book without X-Ray
-- data never shells out at all.
--
-- ponytail: no zip64 support -- the bounds check below simply fails closed
-- and we report "absent". EPUBs are never zip64; revisit only if that changes.
local function zipHasEntry(zip_path, name)
    local fh = io.open(zip_path, "rb")
    if not fh then return false end
    local size = fh:seek("end")
    if not size or size < 22 then fh:close(); return false end

    local tail_len = math.min(size, 65557)  -- 22-byte EOCD + up to 65535 comment
    fh:seek("end", -tail_len)
    local tail = fh:read(tail_len) or ""
    local tail_start = size - tail_len

    local candidates = {}
    local pos = 1
    while true do
        local hit = tail:find("PK\5\6", pos, true)
        if not hit then break end
        table.insert(candidates, hit)
        pos = hit + 1
    end

    local eocd = nil
    for i = #candidates, 1, -1 do
        local hit = candidates[i]
        local comment_len = u16le(tail, hit + 20)
        if comment_len then
            local abs = tail_start + (hit - 1)
            if size - (abs + 22) == comment_len then
                eocd = hit
                break
            end
        end
    end
    if not eocd then fh:close(); return false end

    local cd_size = u32le(tail, eocd + 12)
    local cd_off = u32le(tail, eocd + 16)
    if not cd_size or not cd_off or cd_size == 0 or cd_off + cd_size > size then
        fh:close(); return false
    end

    fh:seek("set", cd_off)
    local cd = fh:read(cd_size) or ""
    fh:close()

    local i = 1
    while i <= #cd do
        local sig_a, sig_b, sig_c, sig_d = cd:byte(i, i + 3)
        if sig_a ~= 0x50 or sig_b ~= 0x4B or sig_c ~= 0x01 or sig_d ~= 0x02 then
            return false
        end
        local n = u16le(cd, i + 28)
        local m = u16le(cd, i + 30)
        local k = u16le(cd, i + 32)
        if not n or not m or not k then return false end
        local name_start = i + 46
        if name_start + n - 1 > #cd then return false end
        if cd:sub(name_start, name_start + n - 1) == name then return true end
        i = i + 46 + n + m + k
    end
    return false
end

-- ---------------------------------------------------------------------
-- JSON decoding.
-- ---------------------------------------------------------------------

-- rapidjson decodes measurably faster on-device but is not guaranteed
-- present on every KOReader build; json always is. Probe both with pcall and
-- confirm .decode is actually a function before trusting either -- a bare
-- `require` would crash a device that lacks the optional one.
local function pickJsonModule()
    for _unused, name in ipairs({ "rapidjson", "json" }) do
        local ok, mod = pcall(require, name)
        if ok and type(mod) == "table" and type(mod.decode) == "function" then
            return mod
        end
    end
    return nil
end

local function decodeJson(raw)
    local mod = pickJsonModule()
    if not mod then return nil end
    local ok, decoded = pcall(mod.decode, raw)
    if not ok or type(decoded) ~= "table" then return nil end
    return decoded
end

local function schemaOk(doc)
    return type(doc) == "table" and doc.schema_version == SUPPORTED_SCHEMA
end

-- ---------------------------------------------------------------------
-- Source readers. Each returns (doc, broken):
--   doc,  false  -- success
--   nil,  false  -- source absent -- not an error, just not there
--   nil,  true   -- source present but unusable (bad JSON / wrong schema /
--                   extraction failed)
-- ---------------------------------------------------------------------

-- Companion file next to the book: `<book_path>.xray.json`. Plain file read,
-- no unzip/BusyBox hazard -- and it is tried first, so re-running the desktop
-- skill (which only rewrites the companion) is picked up without having to
-- re-send the whole EPUB over WiFi.
local function readCompanion(book_path)
    local fh = io.open(book_path .. ".xray.json", "r")
    if not fh then return nil, false end
    local raw = fh:read("*a")
    fh:close()
    if not raw or raw == "" then return nil, false end
    local doc = decodeJson(raw)
    if not doc or not schemaOk(doc) then return nil, true end
    return doc, false, #raw
end

-- Embedded copy: `xray/xray.json` inside the EPUB zip.
local function readEmbedded(book_path)
    if not zipHasEntry(book_path, DATA_PATH) then return nil, false end

    local sidecar = DocSettings:getSidecarDir(book_path)
    if not sidecar then return nil, true end
    local tmp_dir = sidecar .. "/xray_doc_tmp"
    local extracted = tmp_dir .. "/" .. DATA_PATH

    -- BusyBox unzip (Kobo/Kindle) does not create -d's target directory the
    -- way Info-ZIP does -- mkdir -p first, or extraction silently yields
    -- nothing and a book that genuinely carries the data reports as if it
    -- didn't.
    os.execute("mkdir -p " .. shellQuote(tmp_dir))
    os.execute(string.format("unzip -o -q %s %s -d %s 2>/dev/null",
        shellQuote(book_path), shellQuote(DATA_PATH), shellQuote(tmp_dir)))
    local fh = io.open(extracted, "r")
    if not fh then
        -- Some BusyBox unzip builds ignore the member argument and extract
        -- nothing instead of everything, rather than erroring -- retry as a
        -- whole-archive extraction before giving up.
        os.execute(string.format("unzip -o -q %s -d %s 2>/dev/null",
            shellQuote(book_path), shellQuote(tmp_dir)))
        fh = io.open(extracted, "r")
    end

    local raw = fh and fh:read("*a")
    if fh then fh:close() end
    -- Clean up on both the success and failure path -- this is scratch
    -- space in the book's own .sdr dir, not somewhere stale files belong.
    os.execute("rm -rf " .. shellQuote(tmp_dir))

    if not raw or raw == "" then return nil, true end
    local doc = decodeJson(raw)
    if not doc or not schemaOk(doc) then return nil, true end
    return doc, false, #raw
end

-- Third return value is diagnostic metadata, kept because the answers are
-- unrecoverable afterwards: which of the two sources actually won, how big the
-- document was and what reading it cost. The status screen used to GUESS the
-- source ("embedded" as a hardcoded fallback) and was therefore wrong every
-- time a companion file won -- exactly the case one debugs a device over.
local function loadUncached(book_path)
    local started = os.clock()
    local doc, broken, bytes = readCompanion(book_path)
    local source = "companion"
    if not doc then
        local embedded_doc, embedded_broken, embedded_bytes = readEmbedded(book_path)
        doc, bytes, source = embedded_doc, embedded_bytes, "embedded"
        broken = broken or embedded_broken
    end

    local meta = {
        book_path = book_path,
        source = doc and source or nil,
        bytes = bytes,
        load_ms = (os.clock() - started) * 1000,
    }

    if doc then return doc, nil, meta end
    if broken then
        return nil, _("The X-Ray data for this book could not be read."), meta
    end
    return nil, _("No X-Ray data for this book."), meta
end

-- ---------------------------------------------------------------------
-- Public interface.
-- ---------------------------------------------------------------------

function XRayDoc.load(ui)
    local book_path = ui and ui.document and ui.document.file
    if not book_path then return nil, _("No book is open.") end

    local cached = _cache[book_path]
    if cached then return cached.doc, cached.err, cached.meta end

    -- Belt-and-suspenders on top of the per-source error handling above:
    -- this runs on every cacheless book open, unconditionally, so a Lua
    -- error here must never be allowed to take the reader down with it
    -- (xray.koplugin/main.lua:406-411 is the precedent this follows).
    local ok, doc, err, meta = pcall(loadUncached, book_path)
    if not ok then
        doc, err, meta = nil, tostring(doc), nil
    end

    _cache[book_path] = { doc = doc, err = err, meta = meta }
    return doc, err, meta
end

-- Diagnostic metadata for the book currently open, or nil if it has not been
-- loaded yet. Read from the cache rather than recomputed: re-reading to answer
-- "where did this come from" would measure the wrong load.
function XRayDoc.meta(ui)
    local book_path = ui and ui.document and ui.document.file
    local cached = book_path and _cache[book_path]
    return cached and cached.meta or nil
end

-- The percent at which the NEXT stage unlocks, or nil at the last one. With
-- cp_idx nil -- nothing reached yet -- this is the first stage, which is the
-- moment a reader most wants the number.
function XRayDoc.nextPercent(doc, cp_idx)
    local cps = doc and doc.checkpoints
    if type(cps) ~= "table" then return nil end
    local nxt = cps[(cp_idx or 0) + 1]
    return nxt and nxt.percent or nil
end

-- What the FINISHED book holds, taken from the last snapshot. Shown beside the
-- current stage's counts so that "not much here" splits at a glance into
-- "staged, keep reading" and "the extraction found little".
function XRayDoc.totals(doc)
    local cps = doc and doc.checkpoints
    local last = type(cps) == "table" and cps[#cps] or nil
    local snapshot = (last and last.snapshot) or {}
    return {
        characters = #(snapshot.characters or {}),
        locations = #(snapshot.locations or {}),
        terms = #(snapshot.terms or {}),
        historical_figures = #(snapshot.historical_figures or {}),
        timeline = #((doc and doc.timeline) or {}),
    }
end

-- Position on the text axis, 0..100. getFullHeight() does not exist on
-- measured hardware (neither ui.rolling nor ui.document); this is the
-- working substitute: resolve the current XPointer's position, and the last
-- page's XPointer position as the axis total, on the same scale. Preferred
-- over page-percent, which is quantized by page count and was measured to
-- drift from character-percent by up to ~2.7 points on a real book -- enough
-- to blow through MARGIN on its own before device noise even enters.
function XRayDoc.position(ui)
    local ok, result = pcall(function()
        local document = ui and ui.document
        if not document then return nil end
        local pos = document:getPosFromXPointer(document:getXPointer())
        local total = document:getPosFromXPointer(
            document:getPageXPointer(document:getPageCount()))
        if type(pos) ~= "number" or type(total) ~= "number" or total == 0 then
            return nil
        end
        return pos / total * 100
    end)
    if not ok then return nil end
    return result
end

-- Highest-index checkpoint the reader has actually passed, with MARGIN
-- points of slack. The clamp to 100 is required, not cosmetic: the last
-- checkpoint is pinned at percent=100, so without clamping, MARGIN would
-- push its threshold past 100 and it could never be selected -- the reader
-- would never see the complete data even having finished the book.
-- No checkpoint reached yet -> nil, deliberately, not the earliest one: the
-- earliest snapshot still holds data up to its own (10-15%) percent, which a
-- reader at 3% has not read yet.
function XRayDoc.selectCheckpoint(doc, pct)
    if type(doc) ~= "table" or type(doc.checkpoints) ~= "table" then return nil end
    if type(pct) ~= "number" then return nil end

    -- checkpoints[].percent strictly ascends (schema.py), so the last index
    -- whose threshold clears `pct` is also the highest one -- no need to
    -- track a running max.
    local selected = nil
    for i, cp in ipairs(doc.checkpoints) do
        if type(cp) == "table" and type(cp.percent) == "number" then
            local threshold = math.min(cp.percent + XRayDoc.MARGIN, 100)
            if threshold <= pct then
                selected = i
            end
        end
    end
    return selected
end

function XRayDoc.snapshot(doc, idx)
    if type(doc) ~= "table" or type(doc.checkpoints) ~= "table" then return nil end
    local cp = doc.checkpoints[idx]
    return cp and cp.snapshot or nil
end

-- Recaps exist only on the stages the generation pass covered (~11 of the ~57
-- a document carries), so any other reading position walks BACK to the newest
-- recap at or below it -- never forward, which would describe the book past
-- the reader. Partial coverage is normal, not a defect: the pass is one model
-- call per stage and an interrupted run leaves the later ones unwritten.
-- `""` counts as absent. The fold pass omits the key rather than writing an
-- empty string, but a hand-edited document can carry one, and `""` is truthy
-- in Lua -- `cp.recap or nil` would stop here and show an empty page.
function XRayDoc.recap(doc, idx)
    if type(doc) ~= "table" or type(doc.checkpoints) ~= "table" then return nil end
    if type(idx) ~= "number" then return nil end

    for i = idx, 1, -1 do
        local cp = doc.checkpoints[i]
        local text = type(cp) == "table" and cp.recap or nil
        if type(text) == "string" and text ~= "" then return text end
    end
    return nil
end

-- The timeline is a flat, whole-book list at the document level, NOT nested
-- inside each snapshot (generate.py/merge.py) -- rendering it unfiltered
-- would show a reader at 5% the end of the book. Filtering against the
-- *selected checkpoint's* percent, not the raw reading position, means it
-- inherits MARGIN automatically and stays consistent with the entity lists.
function XRayDoc.timeline(doc, idx)
    if type(doc) ~= "table" or type(doc.timeline) ~= "table" then return {} end
    local cp = type(doc.checkpoints) == "table" and doc.checkpoints[idx] or nil
    if not cp or type(cp.percent) ~= "number" then return {} end

    local filtered = {}
    for _unused, ev in ipairs(doc.timeline) do
        if type(ev) == "table" and type(ev.pct) == "number" and ev.pct <= cp.percent then
            table.insert(filtered, ev)
        end
    end
    return filtered
end

function XRayDoc.firstAvailablePercent(doc)
    if type(doc) ~= "table" or type(doc.checkpoints) ~= "table" then return 0 end
    local first = doc.checkpoints[1]
    if type(first) ~= "table" or type(first.percent) ~= "number" then return 0 end
    return first.percent
end

return XRayDoc
