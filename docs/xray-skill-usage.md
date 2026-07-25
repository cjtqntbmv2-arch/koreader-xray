# Using the `xray` skill

The procedure lives in `.claude/skills/xray/SKILL.md` and is not repeated here.
Ask Claude for X-Ray data and point it at an EPUB; it plans the chunks, tells
you how many there are before spending anything, dispatches one subagent per
chunk, and assembles the result.

What you get is one JSON document, twice:

- `xray.json` — hand this to calibre: select the book, *Embed X-Ray*, pick the
  file. The plugin checks that the data belongs to this book, appends it
  without disturbing existing bytes, and verifies KOReader's `partialMD5`
  before and after so an already-read book keeps its reading statistics. Then
  send the book to the device the usual way.
- `<book>.epub.xray.json` — the same content under the name the device plugin
  looks for beside a book. Copy it there over USB when you want to iterate
  without re-sending the book.

This page exists for what the skill does not cover: what to expect from the
output, and where it is still rough.

## Known limits

From real runs, worth knowing before you blame the wrong thing:

- **Detail level costs size.** `detailed` (the default) is comprehensive but
  heavy — half a novel produced ~226 characters, ~120 terms and a ~1.7 MB
  document. `normal` gives a leaner one. Size is not a device problem
  (a 0.9 MB document parses in 0.042 s for +689 KB of heap, measured on a
  Kobo), it just makes the lists long.
- **Duplicate cards from name forms.** Dedup does not strip honorifics or
  ordinals, so "Ser Jaime Lennister" and "Jaime Lennister" survive as two
  cards, as do "Aerys II. Targaryen" and "Aerys Targaryen". Expect somewhat
  inflated character counts on dynasty-heavy books. The deliberate
  counter-rule: bare shared first names ("Robert") are never fuzzy-merged,
  because those books reuse first names across generations on purpose.
- **Chapter headings that fall between chunks.** When a chapter continues into
  a later chunk without repeating its heading, that chunk can under-emit
  timeline events for the headless part. It affects only the document-level
  timeline, not the per-checkpoint snapshots.
- **Descriptions are staged, not final.** An entry at 25 % is written from text
  up to 25 % only, so it is thinner than the same entry at 90 %. That is the
  spoiler guarantee doing its job.

## If something looks wrong on the device

- **"No X-Ray data" for a freshly generated book** — check the plugin version
  first. Documents are `schema_version: 2`; an older plugin rejects them
  outright rather than misreading them.
- **Nothing appears early in the book** — expected. Data becomes visible from
  the first checkpoint onward, typically 10–15 % in; before that the plugin
  says from which percentage it will have something.
