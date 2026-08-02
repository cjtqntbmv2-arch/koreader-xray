# X-Ray Plugin for KOReader

![version](https://img.shields.io/badge/version-26.8.0-blue)
![Platform](https://img.shields.io/badge/platform-KOReader-green.svg)
![License](https://img.shields.io/badge/license-MIT-yellow.svg)

Kindle-style X-Ray for KOReader: who is this character, where is this place,
what does this word mean — answered from the book you are holding, without
spoiling anything you have not read yet.

The X-Ray data for a book is prepared once on your computer and travels with
the book. The plugin on the e-reader reads it and shows it. It needs no API
key and no connection to look anything up, and it never modifies the book.

## What you get

Open a book and pick **X-Ray** from the reader menu:

- **Characters**, **Locations**, **Terms**, **Historical Figures**, **Timeline**
  — five lists, one tap each. Character and location lists are ordered by first
  appearance, terms alphabetically.
- **Detail cards** with role, occupation, aliases and a description, plus a
  **Relations** view: who this character is connected to, and a tap to jump
  straight to that neighbour's card.
- **Story so far** — a recap of the book up to your position, if the data was
  generated with one.
- **X-Ray in the dictionary** — long-press a name while reading and the
  dictionary popup carries an X-Ray button. Can be switched off under *More*.
- **Status** — which stage of the data you are seeing, how far the next one is,
  and how many entries are still hidden ahead of you.

Everything you see is capped at your reading position. The data is stored as a
series of stages, and the plugin picks the highest one you have actually
reached — a character who turns out to be someone else in chapter 30 still
reads as their chapter-3 self while you are in chapter 3.

## Installation

1. Download `xray.koplugin.zip` from the
   [latest release](https://github.com/cjtqntbmv2-arch/koreader-xray/releases/latest).
2. Extract it into KOReader's `plugins/` folder, so you end up with
   `plugins/xray.koplugin/`.
3. Restart KOReader.

KOReader puts third-party menu entries at the end of *Tools*, which can be a
page turn away. If you use X-Ray often, bind the **X-Ray** action to a gesture
(KOReader's *Gesture Manager*) and skip the menu entirely.

**Upgrading from a version older than 26.7.26?** Those releases generated data
on the device and left an API-key file and per-book caches behind. Plugin
updates never delete files, so use **More → Remove old X-Ray data** once; it
lists what it found and asks before deleting anything.

## Getting X-Ray data onto the device

Two routes, both fine — pick by whether you want the EPUB touched.

**Companion file (recommended for testing).** Copy the generated
`<book>.epub.xray.json` next to the book, so the two sit side by side:

```
Wintermärchen.epub
Wintermärchen.epub.xray.json
```

The EPUB stays byte-identical, which means KOReader's reading statistics and
progress for that book survive. The plugin looks for this file first.

**Embedded in the EPUB (recommended for a library).** Install the calibre
plugin from `dist/xray-generator-<version>.zip`
(*Preferences → Plugins → Load plugin from file*), select the book, and use its
**Embed X-Ray** action. The data becomes part of the EPUB and reaches the device
over any transfer route you already use. Note that replacing the book file in
calibre resets that book's reading statistics on the device.

Either way: reopen the book on the device afterwards. If nothing shows up,
**More → Status** tells you whether a file was found and which one.

## Generating the data

Generation runs on your computer with [Claude
Code](https://claude.com/claude-code) — one subagent per chunk of the book,
no API key of your own and no third-party AI service. In a checkout of this
repository, point Claude Code at an EPUB and ask for X-Ray; the bundled `xray`
skill drives it end to end. Under the hood it is three steps:

```bash
python3 -m tools.claude_xray_plan "BOOK.epub" --workdir /tmp/xray --detail detailed
# Claude dispatches subagents over the planned chunks
python3 -m tools.claude_xray_assemble "BOOK.epub" --workdir /tmp/xray --out .
```

The result is `xray.json` plus `BOOK.epub.xray.json` — the same document under
the name the plugin looks for next to a book. Optional extra passes add the
"Story so far" recap and the relationship net. A full novel is roughly 30–40
chunks; the skill tells you the count before it starts.

## Updates

The plugin checks GitHub for a new release once a week, in the background, and
only when you are online — no telemetry, nothing is sent. You can also trigger
it under **More → Check for updates**. Updates are downloaded and unpacked in
place; KOReader has to be restarted to load the new code, and it offers to do
that for you.

## Languages

English and German. The interface follows KOReader's language setting.

## Credits

Based on [koreader-xray-plugin](https://github.com/ultimatejimmy/koreader-xray-plugin)
by Jimmy Pautz (MIT).
