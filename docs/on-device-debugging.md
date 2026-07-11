# On-device debugging (Kobo + KOReader, from a Mac)

How to watch what the plugin does on a real device. Written for a Kobo connected
to a Mac; the Kindle paths differ (`/mnt/us` instead of `/mnt/onboard`).

## The plugin log

The plugin has its own logger (`xray.koplugin/xray_logger.lua`). It is **off by
default** — on e-ink every log line is a full flash open/append/close cycle, so
it must never run unattended. There is deliberately no menu toggle.

Enable it by editing the on-device config and setting:

```lua
debug_logging = true,
```

in `xray_config.lua` (or by adding a `debug_logging` key to `settings.json`).

The log is written **inside the plugin directory**, next to the code:

```
plugins/xray.koplugin/xray.log        (rotates to xray.log.old at 512 KB)
```

Turn it back off (`debug_logging = false`) when you are done.

## Paths on a Kobo

Over USB (the Kobo mounts as the `KOBOeReader` volume; `.adds` is hidden — reveal
it in Finder with **Cmd+Shift+.**):

```
/Volumes/KOBOeReader/.adds/koreader/plugins/xray.koplugin/xray_config.lua
/Volumes/KOBOeReader/.adds/koreader/plugins/xray.koplugin/xray.log
/Volumes/KOBOeReader/.adds/koreader/crash.log
```

The same locations as absolute on-device paths (what you use over SSH):

```
/mnt/onboard/.adds/koreader/plugins/xray.koplugin/xray.log
/mnt/onboard/.adds/koreader/crash.log
```

## Two ways to read the logs from the Mac

### a) USB mass storage — simple, not live

Plug in the cable and the Kobo becomes the `KOBOeReader` volume. **While it is
mounted as storage, KOReader is not running** (the Kobo is in USB mode), so this
is a before/after workflow:

1. plug in → set `debug_logging = true` in `xray_config.lua` → eject
2. run your test on the device
3. plug in again → read `xray.log`

### b) KOReader's built-in SSH server — live

KOReader can serve SSH/SFTP over Wi-Fi (menu → **Network → SSH server**, it uses
dropbear). It displays the exact connect command, including the port. From the
Mac:

```sh
ssh -p <port> <user>@<kobo-ip>
tail -f /mnt/onboard/.adds/koreader/plugins/xray.koplugin/xray.log
```

This streams the log while the device keeps running — no plugging/unplugging.

## KOReader's own log

Separate from the plugin log, KOReader writes Lua errors and crashes to
`crash.log` in its install directory. This is the right place to watch the
**calibre import** path: the import is wrapped in `pcall`, so a failure never
takes the book down — but it surfaces in `crash.log` (and, with `debug_logging`
on, in `xray.log`). If tapping *Import calibre X-Ray* against a real
calibre-prepared EPUB leaves both logs clean and data appears, the on-device
import worked.

## Verifying the AI-fetching main switch is off

The hardest proof that "switch off ⇒ no network call" needs no log reading at
all: **turn Wi-Fi off on the Kobo**, set the switch to off, then walk every view
and button. If no "Connecting…" dialog ever appears and `xray.log` shows no fetch
attempt, the guarantee holds.
