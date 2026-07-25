-- xray_updater.lua — OTA updater: checks GitHub Releases for a newer
-- xray.koplugin build, downloads it, and unzips it over the running install.
-- Ported from xray.koplugin/xray_updater.lua (docs/plans/
-- 2026-07-25-xray-neuausrichtung.md, Phase 4 / Updater). Dropped: the beta
-- channel and the API-key backup/re-injection dance (_injectValue,
-- restoreConfigBackup) — that logic only ever existed to carry the six
-- API-key fields of xray_config.lua across an update, and that file does
-- not exist in this plugin.

local UIManager   = require("ui/uimanager")
local InfoMessage = require("ui/widget/infomessage")
local ConfirmBox  = require("ui/widget/confirmbox")
local logger      = require("logger")
local _ = require("xray_i18n")

-- Hoisted (source pcall'd these per call site); same failure handling either way.
local ok_json, json        = pcall(require, "json")
local ok_ds,   DataStorage = pcall(require, "datastorage")
local ok_tr,   Trapper     = pcall(require, "ui/trapper")

-- ---------------------------------------------------------------------------
-- Configuration
-- ---------------------------------------------------------------------------
local GITHUB_OWNER = "cjtqntbmv2-arch"
local GITHUB_REPO  = "koreader-xray"
local ASSET_NAME   = "xray.koplugin.zip"

-- Cache validity time in seconds for the fetched release info. 0 = disable.
local CACHE_TTL = 3600 -- 1 hour

local API_URL = string.format(
    "https://api.github.com/repos/%s/%s/releases/latest",
    GITHUB_OWNER, GITHUB_REPO
)

local Updater = {}

-- ---------------------------------------------------------------------------
-- Release-info cache (avoids hammering the GitHub API on repeated checks)
-- ---------------------------------------------------------------------------

local function _cacheFile()
    if ok_ds and DataStorage then
        return DataStorage:getSettingsDir() .. "/xray_update_cache.json"
    end
    return "/tmp/xray_update_cache.json"
end

local function _loadCache()
    if CACHE_TTL <= 0 then return nil end
    local fh = io.open(_cacheFile(), "r")
    if not fh then return nil end
    local raw = fh:read("*a")
    fh:close()
    if not ok_json then return nil end
    local ok_d, data = pcall(json.decode, raw)
    if not ok_d or type(data) ~= "table" then return nil end
    if (os.time() - (data.timestamp or 0)) > CACHE_TTL then return nil end
    return data.payload
end

local function _saveCache(payload)
    if CACHE_TTL <= 0 then return end
    if not ok_json then return end
    local ok_e, encoded = pcall(json.encode, { timestamp = os.time(), payload = payload })
    if not ok_e then return end
    local fh = io.open(_cacheFile(), "w")
    if fh then
        fh:write(encoded)
        fh:close()
    end
end

local function _clearCache()
    pcall(os.remove, _cacheFile())
end

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

local function _currentVersion(plugin_path)
    local ok, meta = pcall(dofile, plugin_path .. "/_meta.lua")
    if ok and type(meta) == "table" and meta.version then
        return meta.version
    end
    return "0.0.0"
end

-- Compares CalVer tags (e.g. "26.7.25") by extracting every digit group and
-- comparing left to right; a missing trailing group counts as 0.
--
-- Tags MUST stay strictly three-part numeric. gmatch pulls every digit group
-- out of the string, so a suffixed tag like "26.7.25-hotfix2" would parse to
-- {26, 7, 25, 2} and compare as newer than plain "26.7.25". The old code had
-- a stable-beats-beta special case at equal numeric parts; with the beta
-- channel gone there is no special case left anywhere that would catch a
-- stray suffix like that. Keep release tags clean — this is an operational
-- rule for whoever cuts the release, not something this function guards.
local function _versionLessThan(a, b)
    local function parts(v)
        local t_parts = {}
        if not v then return t_parts end
        for n in v:gmatch("(%d+)") do
            t_parts[#t_parts + 1] = tonumber(n)
        end
        return t_parts
    end

    local pa, pb = parts(a), parts(b)
    for i = 1, math.max(#pa, #pb) do
        local va = pa[i] or 0
        local vb = pb[i] or 0
        if va < vb then return true end
        if va > vb then return false end
    end
    return false
end

local function _toast(msg, timeout)
    local w = InfoMessage:new{ text = msg, timeout = timeout or 4 }
    UIManager:show(w)
    return w
end

local function _closeWidget(w)
    if w then UIManager:close(w) end
end

-- ---------------------------------------------------------------------------
-- HTTP — socket/http etc. stay lazy per call, unlike the hoist above: these
-- are unconditional requires, kept scoped to an actual network op.
-- ---------------------------------------------------------------------------

local function _httpGet(url)
    local ok_su, socketutil = pcall(require, "socketutil")
    local http   = require("socket/http")
    local ltn12  = require("ltn12")
    local socket = require("socket")

    if ok_su then
        socketutil:set_timeout(
            socketutil.LARGE_BLOCK_TIMEOUT,
            socketutil.LARGE_TOTAL_TIMEOUT
        )
    end

    local chunks = {}
    local code, headers, status = socket.skip(1, http.request({
        url      = url,
        method   = "GET",
        headers  = {
            ["User-Agent"] = "KOReader-XRay-Updater/1.0",
            ["Accept"]     = "application/vnd.github.v3+json",
        },
        sink     = ltn12.sink.table(chunks),
        redirect = true,
    }))

    if ok_su then socketutil:reset_timeout() end

    if ok_su and (
        code == socketutil.TIMEOUT_CODE or
        code == socketutil.SSL_HANDSHAKE_CODE or
        code == socketutil.SINK_TIMEOUT_CODE
    ) then
        return nil, "timeout (" .. tostring(code) .. ")"
    end

    if headers == nil then
        return nil, "network error (" .. tostring(code or status) .. ")"
    end

    if code == 200 then
        return table.concat(chunks)
    end
    return nil, string.format("HTTP %s", tostring(code))
end

local function _httpGetToFile(url, dest_path)
    local ok_su, socketutil = pcall(require, "socketutil")
    local http   = require("socket/http")
    local ltn12  = require("ltn12")
    local socket = require("socket")

    local fh, err_open = io.open(dest_path, "wb")
    if not fh then
        return nil, "Could not create file: " .. tostring(err_open)
    end

    if ok_su then
        socketutil:set_timeout(
            socketutil.FILE_BLOCK_TIMEOUT,
            socketutil.FILE_TOTAL_TIMEOUT
        )
    end

    local code, headers, status = socket.skip(1, http.request({
        url      = url,
        method   = "GET",
        headers  = { ["User-Agent"] = "KOReader-XRay-Updater/1.0" },
        sink     = ltn12.sink.file(fh),
        redirect = true,
    }))

    if ok_su then socketutil:reset_timeout() end

    if ok_su and (
        code == socketutil.TIMEOUT_CODE or
        code == socketutil.SSL_HANDSHAKE_CODE or
        code == socketutil.SINK_TIMEOUT_CODE
    ) then
        pcall(os.remove, dest_path)
        return nil, "timeout (" .. tostring(code) .. ")"
    end

    if headers == nil then
        pcall(os.remove, dest_path)
        return nil, "network error (" .. tostring(code or status) .. ")"
    end

    if code == 200 then return true end
    pcall(os.remove, dest_path)
    return nil, string.format("HTTP %s", tostring(code))
end

-- ---------------------------------------------------------------------------
-- JSON parsing
-- ---------------------------------------------------------------------------

local function _parseRelease(body)
    if not ok_json then
        logger.warn("xray updater: json module not available, using fallback regex")
        local tag = body:match('"tag_name"%s*:%s*"([^"]*)"')
        if not tag then return nil, "could not parse tag_name" end
        local download_url = body:match(
            '"browser_download_url"%s*:%s*"([^"]*'
            .. ASSET_NAME:gsub("%.", "%%.") .. '[^"]*)"'
        )
        local notes = body:match('"body"%s*:%s*"(.-)"[,}]')
        if notes then
            notes = notes:gsub("\\n", "\n"):gsub("\\r", ""):gsub('\\"', '"'):gsub("\\\\", "\\")
        end
        return {
            version      = tag:match("v?(.*)"),
            download_url = download_url,
            notes        = (notes and notes ~= "") and notes or nil,
        }
    end

    local ok_d, data = pcall(json.decode, body)
    if not ok_d or type(data) ~= "table" then
        return nil, "JSON parse error: " .. tostring(data)
    end

    -- GitHub error responses carry a 'message' field instead of a release.
    if data.message and not data.tag_name then
        return nil, "GitHub API error: " .. tostring(data.message)
    end

    local tag = data.tag_name
    if not tag then return nil, "tag_name missing from API response" end

    local download_url = nil
    for _unused, asset in ipairs(data.assets or {}) do
        if type(asset.name) == "string" and asset.name == ASSET_NAME then
            download_url = asset.browser_download_url
            break
        end
    end

    local notes = data.body
    if notes and notes ~= "" then
        notes = notes:gsub("#+%s*", "")
        notes = notes:gsub("%*%*(.-)%*%*", "%1")
        notes = notes:gsub("`(.-)`", "%1")
        notes = notes:gsub("\r\n", "\n"):gsub("\r", "\n")
        if #notes > 600 then notes = notes:sub(1, 597) .. "..." end
        notes = notes:match("^%s*(.-)%s*$")
    end

    return {
        version      = tag:match("v?(.*)"),
        download_url = download_url,
        notes        = (notes and notes ~= "") and notes or nil,
        html_url     = data.html_url,
    }
end

-- ---------------------------------------------------------------------------
-- Unzip
-- ---------------------------------------------------------------------------

local function _unzip(zip_path, dest_dir)
    local cmd = string.format("unzip -o -q %q -d %q", zip_path, dest_dir)
    local ret = os.execute(cmd)
    if ret ~= 0 and ret ~= true then
        return nil, "unzip failed (exit " .. tostring(ret) .. ")"
    end
    return true
end

-- Portable "is this a complete zip?" check, run before extracting over the
-- live install. We can't shell out to `unzip -t`: BusyBox unzip (Kindle and
-- most e-reader firmwares) has no -t option, so that test false-fails EVERY
-- update on those devices. Instead verify the local-file-header magic
-- (PK\3\4, or PK\5\6 for an empty archive) and that the End-Of-Central-
-- Directory signature (PK\5\6) is present near the end -> catches empty,
-- truncated, and HTML-error-page downloads without any external tool.
-- Extraction still uses `unzip -o` (which BusyBox does support).
function Updater._zipLooksValid(path)
    local fh = io.open(path, "rb")
    if not fh then return false end
    local head = fh:read(4) or ""
    if head ~= "PK\3\4" and head ~= "PK\5\6" then
        fh:close()
        return false
    end
    local size = fh:seek("end")
    if not size or size < 22 then
        fh:close()
        return false
    end
    local tail_len = math.min(size, 65557) -- 22-byte EOCD + up to 65535 comment
    fh:seek("end", -tail_len)
    local tail = fh:read(tail_len) or ""
    fh:close()
    return tail:find("PK\5\6", 1, true) ~= nil
end

-- ---------------------------------------------------------------------------
-- Download & Install
-- ---------------------------------------------------------------------------

local function _tmpZipPath(plugin_path)
    if ok_ds and DataStorage then
        return DataStorage:getSettingsDir() .. "/xray_update.zip"
    end
    local probe = "/tmp/.xray_probe"
    local fh = io.open(probe, "w")
    if fh then fh:close(); os.remove(probe); return "/tmp/xray_update.zip" end
    return plugin_path .. "/xray_update.zip"
end

local function _applyUpdate(plugin, download_url, new_version)
    local tmp_zip    = _tmpZipPath(plugin.path)
    local parent_dir = plugin.path:match("^(.+)/[^/]+$") or plugin.path

    local progress_msg = _toast(
        string.format(_("Downloading X-Ray %s..."), new_version), 120
    )

    local function doDownloadAndInstall()
        local dl_ok, dl_err = _httpGetToFile(download_url, tmp_zip)
        if not dl_ok then
            return { success = false, stage = "download", err = dl_err }
        end

        -- Reject truncated/empty downloads before extracting over the live
        -- install (see Updater._zipLooksValid above for why this can't be
        -- `unzip -t`).
        -- ponytail: no staged install; this check covers the realistic
        -- failure mode (partial download). Upgrade path: unzip to a staging
        -- dir + directory swap if half-written installs ever show up.
        if not Updater._zipLooksValid(tmp_zip) then
            os.remove(tmp_zip)
            return { success = false, stage = "zip", err = "corrupted download (zip integrity check failed)" }
        end

        local uz_ok, uz_err = _unzip(tmp_zip, parent_dir)
        os.remove(tmp_zip)
        if not uz_ok then
            return { success = false, stage = "unzip", err = uz_err }
        end

        return { success = true }
    end

    local function handleInstallResult(result)
        _closeWidget(progress_msg)
        if not result or not result.success then
            local stage = result and result.stage or "unknown"
            local err   = result and result.err   or "unknown error"
            logger.err("xray updater: failed at", stage, "-", err)
            if stage == "download" then
                _toast(string.format(_("Download error: %s"), tostring(err)))
            elseif stage == "zip" then
                _toast(string.format(_("Corrupted update file: %s"), tostring(err)))
            else
                _toast(string.format(_("Extraction error: %s"), tostring(err)))
            end
            return
        end
        _clearCache()
        UIManager:show(ConfirmBox:new{
            text = string.format(
                _("X-Ray %s successfully installed.\n\nRestart KOReader to apply the update?"),
                new_version
            ),
            ok_text     = _("Restart"),
            cancel_text = _("Later"),
            ok_callback = function() UIManager:restartKOReader() end,
        })
    end

    if ok_tr and Trapper and Trapper.dismissableRunInSubprocess then
        local completed, result = Trapper:dismissableRunInSubprocess(
            doDownloadAndInstall,
            progress_msg,
            function(res) handleInstallResult(res) end
        )
        if completed and result then
            UIManager:scheduleIn(0.2, function() handleInstallResult(result) end)
        elseif completed == false then
            _closeWidget(progress_msg)
            pcall(os.remove, tmp_zip)
            _toast(_("Update cancelled."))
        end
    else
        UIManager:scheduleIn(0.3, function()
            handleInstallResult(doDownloadAndInstall())
        end)
    end
end

-- ---------------------------------------------------------------------------
-- Version check
-- ---------------------------------------------------------------------------

local function _showUpdateDialog(plugin, release, current)
    local latest       = release.version
    local download_url = release.download_url
    local notes        = release.notes

    if not _versionLessThan(current, latest) then
        logger.info("xray updater: up to date (" .. current .. ")")
        _toast(string.format(_("X-Ray is up to date (%s)."), current))
        return
    end

    logger.info("xray updater: new version available:", latest)

    local header = string.format(_("X-Ray %s is available!\nYou have %s."), latest, current)
    local footer = _("\n\nDownload and install now?")
    local notes_block = notes
        and ("\n\n" .. _("What's new:") .. "\n" .. notes)
        or  ""

    if not download_url then
        UIManager:show(ConfirmBox:new{
            text = header .. notes_block .. "\n\n"
                .. _("No automatic update file was found.\n\nOpen the releases page on GitHub?"),
            ok_text     = _("Open in browser"),
            cancel_text = _("Cancel"),
            ok_callback = function()
                local Device = require("device")
                if Device:canOpenLink() then
                    Device:openLink(string.format(
                        "https://github.com/%s/%s/releases/latest",
                        GITHUB_OWNER, GITHUB_REPO
                    ))
                end
            end,
        })
        return
    end

    UIManager:show(ConfirmBox:new{
        text        = header .. notes_block .. footer,
        ok_text     = _("Download and install"),
        cancel_text = _("Cancel"),
        ok_callback = function() _applyUpdate(plugin, download_url, latest) end,
    })
end

local function _doFetch()
    local cached = _loadCache()
    if cached then
        logger.info("xray updater: using cached release info")
        return cached
    end
    local body, err = _httpGet(API_URL)
    if not body then return { error = err } end
    local release, parse_err = _parseRelease(body)
    if not release then return { error = "parse error: " .. tostring(parse_err) } end
    _saveCache(release)
    return release
end

local function _doCheckForUpdates(plugin, current)
    local checking_msg = _toast(_("Checking for updates..."), 15)

    local function handleCheckResult(release)
        _closeWidget(checking_msg)
        if not release then
            _toast(_("Error checking for updates."))
            return
        end
        if release.error then
            logger.err("xray updater: check error:", release.error)
            _toast(string.format(_("Error checking for updates: %s"), tostring(release.error)))
            return
        end
        _showUpdateDialog(plugin, release, current)
    end

    if ok_tr and Trapper and Trapper.dismissableRunInSubprocess then
        local completed, result = Trapper:dismissableRunInSubprocess(
            _doFetch,
            checking_msg,
            function(res) handleCheckResult(res) end
        )
        if completed and result then
            UIManager:scheduleIn(0.2, function() handleCheckResult(result) end)
        elseif completed == false then
            _closeWidget(checking_msg)
            _toast(_("Update check cancelled."))
        end
    else
        UIManager:scheduleIn(0.3, function()
            handleCheckResult(_doFetch())
        end)
    end
end

-- ---------------------------------------------------------------------------
-- Public API
-- ---------------------------------------------------------------------------

-- Manual check, triggered from the menu. Always reports back to the user,
-- including "already up to date" and network/parse errors.
function Updater.checkNow(plugin)
    local current = _currentVersion(plugin.path)
    local ok_nm, NetworkMgr = pcall(require, "ui/network/manager")
    if ok_nm and NetworkMgr and NetworkMgr.runWhenOnline then
        NetworkMgr:runWhenOnline(function()
            _doCheckForUpdates(plugin, current)
        end)
        return
    end
    _doCheckForUpdates(plugin, current)
end

-- Background check: stays completely silent unless a newer version is
-- actually available (no "checking..." toast, no error toast). No cadence
-- or online-check gating in here by design, matching the ported original —
-- the caller decides when it's time to check and whether the network is up.
function Updater.checkSilently(plugin)
    local current = _currentVersion(plugin.path)
    local release = _doFetch()
    if release and not release.error then
        if _versionLessThan(current, release.version) then
            _showUpdateDialog(plugin, release, current)
        end
    end
end

return Updater
