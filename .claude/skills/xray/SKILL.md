---
name: xray
description: Generate KOReader X-Ray data (xray.json) from an EPUB — characters, locations, terms, historical figures and a timeline, staged per checkpoint so nothing past the reader's position leaks. Extraction runs on Claude subagents, one per chunk; there is no API key and no Gemini. Use this whenever the user points at an EPUB and wants X-Ray, wants to prepare a book for their e-reader, or asks for "the thing that tells me who this character is while I read" — including bare requests like "xray for <book>" or "generate the data for Fire and Blood". Do not use it for reading, converting or editing EPUBs generally.
---

# xray — Claude-backed X-Ray generation

Produces one JSON document per book. Delivery to the device is a separate,
deliberate step: the calibre plugin embeds the document into the book, and
calibre's own wireless connection carries it. This skill never touches the
source EPUB.

Run the commands from the repository root (they import `xray_core`).

## 1. Plan

```
python3 -m tools.claude_xray_plan "<EPUB>" --workdir "<WORKDIR>" --detail <normal|detailed>
```

`--detail` defaults to `detailed`. The command prints the path of a
`manifest.json` listing every chunk as `{cp_idx, chunk_idx, percent,
prompt_file, raw_file}`.

**Tell the user the chunk count before you start extracting.** Extraction is
the expensive part — one subagent per chunk — and a whole series can be several
hundred. A full novel is roughly 30–40. Giving them the number first lets them
stop you; discovering the cost afterwards does not.

## 2. Extract

Dispatch one subagent per chunk with the Agent/Task tool, **model `sonnet`**, in
waves of about 8–12 concurrent agents.

Sonnet rather than Opus is a measured decision, not thrift: recall here comes
from the prompt and the self-glean step, not the model tier, and on a real
37-chunk book Opus exhausted a MAX-plan quota partway through. Reserve Opus for
a book that demonstrably comes out badly.

Skip any chunk whose `raw_file` already exists — that is what makes a
interrupted run cheap to resume. For each remaining chunk, instruct the
subagent to:

- Read `<WORKDIR>/<prompt_file>`. It already contains the extraction
  instruction and that chunk's text; nothing needs to be added to it.
- Follow it exactly: every character, location, term, historical figure and
  timeline entry present in the chunk, then the self-glean re-scan for minor
  figures that the first pass missed — using **only** the provided text.
- Write the resulting JSON object, and nothing else, to `<WORKDIR>/<raw_file>`.
  Write it **directly** rather than generating a script that writes it;
  subagents that build `build_*.py` helpers leave them behind in the workdir
  and cost a round of cleanup for no benefit.

Report progress as n/total while the waves run.

## 3. Assemble

```
python3 -m tools.claude_xray_assemble "<EPUB>" --workdir "<WORKDIR>" --out "<OUTDIR>"
```

This cleans each raw extraction, merges them in book order, validates the
result against the schema, and writes two files with identical content:

- `<OUTDIR>/xray.json` — the file you hand to calibre.
- `<OUTDIR>/<book>.epub.xray.json` — the same document under the name the
  device plugin looks for next to a book, for delivery over USB.

If the assembler aborts listing missing or unparseable chunks, re-dispatch
subagents for exactly those `(cp_idx, chunk_idx)` pairs and run it again. It is
deliberately strict: a document that silently covered less of the book than it
claims would stage spoilers wrongly.

`--out` may be the book's own directory. Nothing here writes an EPUB, so
nothing can overwrite the source.

## 4. Report and hand over

Give the user both paths and the route that fits them:

**Via calibre (the normal way).** In calibre: select the book → *Embed X-Ray* →
pick `xray.json`. The plugin verifies the file belongs to this book
(`text_hash`), validates it, appends it to the EPUB without touching existing
bytes, and checks KOReader's `partialMD5` before and after so an already-read
book does not lose its reading statistics. It refuses rather than guesses. Then
send the book to the device as usual.

**Via USB (for testing).** Copy `<book>.epub.xray.json` next to the book on the
device. The plugin prefers a companion file over embedded data, which makes it
the fastest way to iterate without re-sending the book.

## What the document contains

`schema_version: 2`. One snapshot per checkpoint, each cumulative and frozen at
that point in the book, plus a document-level timeline whose entries carry the
percent at which they become visible. The device picks the highest checkpoint
its reading position has passed.

Two consequences worth knowing when something looks wrong:

- **A device plugin older than schema v2 rejects these documents.** If the
  reader says there is no X-Ray data for a freshly generated book, check the
  plugin version before suspecting the data.
- **Descriptions are staged, not final.** A character's description at 25 % is
  built only from text up to 25 %, so it is thinner than the same character's
  entry at 90 %. That is the point, not a defect.

## Constraints

Never modify the source EPUB. Never invent entities from knowledge of the book
outside the provided chunk text — the staging guarantee is only as good as that
rule. If a run is interrupted, resume it; do not start a fresh workdir, since
the finished chunks are the expensive part.
