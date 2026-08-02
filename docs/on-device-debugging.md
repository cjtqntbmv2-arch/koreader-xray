# On-device debugging (Kobo + KOReader, from a Mac)

How to find out what the plugin is doing on a real device. Written for a Kobo
connected to a Mac; Kindle paths differ (`/mnt/us` instead of `/mnt/onboard`).

The plugin has no log of its own — it also has no config file, no network calls
while reading, and no AI. What it has instead are two screens that report their
own state, and one of them writes itself to a file you can read over USB.

## Start here: Status and Diagnostics

Both live under **X-Ray → More**.

**Status** answers "why am I seeing this much": where the data came from
(companion file or embedded), the reading position, which stage is in effect,
how far the next one is, and per category how many entries are visible out of
the book's total. If the generator ran out partway, it also says
`Data incomplete: generated up to N%`.

**Diagnostics** is the technical dump — plugin and KOReader version, book path,
source, byte size and load time, then `schema_version`, `generator`,
`generator_version`, `detail_level`, `language`, stage count, `complete`,
`last_percent`, a `text_hash` prefix, both position axes (text and page),
the selected stage index and `MARGIN`, and which of the two dictionary-button
mechanisms is live on this build.

That last line matters more than it looks: both button installations sit inside
`pcall` guards, so without it a missing X-Ray button in the dictionary popup is
indistinguishable from a button nobody pressed. That ambiguity once cost a full
debugging round on a released device.

**Save diagnostics** writes the same text into `DataStorage:getSettingsDir()`
as `xray_diagnostics.txt`, so you can read it over USB instead of retyping it
off a 6-inch screen. The confirmation dialog prints the exact path it used —
trust that over the expansion given below, which is what the settings directory
resolves to on a stock Kobo install.

## Paths on a Kobo

Over USB the device mounts as the `KOBOeReader` volume; `.adds` is hidden, so
reveal it in Finder with **Cmd+Shift+.**

```
/Volumes/KOBOeReader/.adds/koreader/plugins/xray.koplugin/
/Volumes/KOBOeReader/.adds/koreader/settings/xray_diagnostics.txt
/Volumes/KOBOeReader/.adds/koreader/crash.log
```

The same places as absolute on-device paths (what you use over SSH):

```
/mnt/onboard/.adds/koreader/plugins/xray.koplugin/
/mnt/onboard/.adds/koreader/settings/xray_diagnostics.txt
/mnt/onboard/.adds/koreader/crash.log
```

## Two ways in from the Mac

**a) USB mass storage — simple, not live.** While the Kobo is mounted as
storage, KOReader is *not* running, so this is strictly a before/after
workflow: copy files in, eject, run the test on the device, plug in again and
read the result.

**b) KOReader's SSH server — live.** Menu → **Network → SSH server** (dropbear).
It displays the exact connect command including the port:

```sh
ssh -p <port> <user>@<kobo-ip>
```

From there you can watch `crash.log` while the device keeps running, and look
at a book's sidecar directory without unplugging anything.

## When no X-Ray data shows up

The two error messages are not interchangeable — they tell you which half of
the load path failed:

- **"No X-Ray data for this book."** Nothing was found at all: no companion
  file beside the book, and no `xray/xray.json` member inside the EPUB.
- **"The X-Ray data for this book could not be read."** Something *was* found
  and could not be used — unparseable JSON, a `schema_version` other than 2, or
  an extraction that produced nothing.

For the first message, check in this order:

1. **The companion file's name.** It is the book's full file name plus
   `.xray.json` — `Wintermärchen.epub.xray.json` beside `Wintermärchen.epub`,
   not `Wintermärchen.xray.json`. This is checked first and needs no unzip.
2. **AppleDouble litter.** Copying from a Mac leaves `._`-prefixed twins on the
   FAT volume. Run `dot_clean /Volumes/KOBOeReader` before ejecting.
3. **Whether the book is even on the exported partition.** On the Clara BW used
   for testing here, the library lives on `/mnt/Buecher` — a second partition
   that USB does *not* export. Only `/mnt/onboard` appears as
   `/Volumes/KOBOeReader`, so a book copied "to the Kobo" from the Mac and a
   book KOReader is reading can be two different places. Copy book *and*
   companion to the exported volume for a USB test.
4. **A full KOReader restart.** KOReader caches plugin code; editing a `.lua`
   on the device and reopening the book is not enough.

For the second message, the schema gate is the usual suspect: the plugin accepts
`schema_version == 2` and nothing else (`xray_doc.lua:34`). Diagnostics prints
the value it read.

## The embedded path is the fragile one

Reading a companion file is a plain `io.open`. Reading the embedded copy shells
out, and the device's BusyBox `unzip` is not the `unzip` on your Mac:

- `unzip -d <dir>` does **not** create `<dir>` on BusyBox — the extraction
  silently yields nothing. `xray_doc.lua` runs `mkdir -p` first.
- There is no `-t`, so archive integrity is checked by hand-parsing the zip
  magic bytes and the central directory instead.
- Some builds ignore the member argument, so a failed targeted extraction
  retries as a whole-archive extraction.

None of this is covered by a test: pytest and the Lua specs both run under
Info-ZIP, which masks the difference entirely. Anything touching
`os.execute("unzip …")` is only verified on real hardware.

## crash.log

The plugin wraps its entry points in `pcall` — a failure must never take the
book down with it. The flip side is that failures are quiet by design, so a real
Lua error surfaces in KOReader's own `crash.log` rather than on screen. If a
view opens empty and Diagnostics looks sane, that file is the next place to
look.
