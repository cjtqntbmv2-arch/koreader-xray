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

Dispatch subagents with the Agent/Task tool, **model `sonnet`**, **3 to 5 chunks
per subagent**, in waves of about 8–12 concurrent agents. Skip any chunk whose
`raw_file` already exists — that is what makes an interrupted run cheap to
resume.

Two things about this shape are measured, not preference, and both matter more
than anything you could tune in the prompt:

**Give each subagent several chunks.** A subagent costs roughly 97k tokens
before it does any work at all — scaffolding, tool schemas, the agentic loop —
against about 8k tokens for the chunk it is there to process. One agent per
chunk means paying that fixed cost 68 times on a full novel. Batching three
measured 41% cheaper end to end, five about half; the context that accumulates
inside one agent is a rounding error against the bootstrap it saves. Do not
batch so far that a failure loses a lot of finished work — 3 to 5 is the range
where both effects stay comfortable.

**Tell it exactly where the files are and to do nothing else.** Pass the
**absolute** workdir path, never `$TMPDIR` — inside a subagent that resolves to
a sandbox override and sends it hunting through directories that do not exist.
An agent that spent 12 tool calls on a two-call job cost four times one that
spent two, because every call re-sends the whole accumulated context.

Instruct each subagent, for each of its chunks:

- Read `<ABSOLUTE-WORKDIR>/<prompt_file>`. It already contains the extraction
  instruction and that chunk's text; nothing needs to be added to it.
- Follow it exactly: every character, location, term, historical figure and
  timeline entry present in the chunk, then the self-glean re-scan for minor
  figures the first pass missed — using **only** the provided text. Names that
  appear once still count; those minor figures are most of what separates a
  good document from a thin one.
- Write the resulting JSON object, and nothing else, to
  `<ABSOLUTE-WORKDIR>/<raw_file>`, **directly** with the write tool. Agents that
  generate a `build_*.py` to write it leave the helper behind and cost a round
  of cleanup for no benefit.
- Treat the chunks as independent: nothing from one may appear in another's
  output.

Sonnet rather than Opus is also measured: recall comes from the prompt and the
self-glean step, not the model tier, and on a real 37-chunk book Opus exhausted
a MAX-plan quota partway through. Reserve Opus for a book that demonstrably
comes out badly.

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

## 4. Recap (optional)

A "story so far" prose recap the reader can open mid-book, staged like
everything else so it never describes anything past their position. Skipping
this step leaves a perfectly valid document — the device just does not offer
the entry.

**Decide before handing the book over in §5.** Adding recaps afterwards means
embedding into a book that already carries `xray/xray.json`: the append path
refuses that, the calibre plugin falls back to a full rewrite, and it warns —
correctly — that KOReader may then see the book as a different one and reset
its reading statistics.

```
python3 -m tools.claude_xray_recap plan "<EPUB>" --doc "<OUTDIR>/xray.json" --workdir "<WORKDIR>"
```

This writes at most 12 prompt files plus `recap_manifest.json`. A document
carries ~57 per-chunk stages; one recap each would add ~20k words of prose to a
file the device unzips on e-ink hardware, so the pass spreads a dozen over the
book and the device walks back to the newest one at or below the reader.

Dispatch subagents exactly as in §2 — model `sonnet`, several stages per agent,
**absolute** workdir path — and instruct each, for each of its stages:

- Read `<ABSOLUTE-WORKDIR>/<prompt_file>`. It already carries the instruction
  and all the material; nothing needs to be added to it.
- Write the prose, and nothing else, to `<ABSOLUTE-WORKDIR>/<out_file>`,
  **directly** with the write tool. Plain text — no JSON, no headings, no
  preamble like "Here is the recap".
- Use only the events and characters the prompt lists. Anything else is a
  spoiler, and the fold step will throw the recap away for it.

```
python3 -m tools.claude_xray_recap fold --doc "<OUTDIR>/xray.json" --workdir "<WORKDIR>" --out "<OUTDIR>"
```

Folding validates and rewrites both filenames. It drops any recap that names a
character who only appears in a later stage, and prints which — re-dispatch
that stage's subagent if the recap is wanted back. Stages whose prose was never
written are skipped; partial coverage is fine.

**After any re-run of §3, run `fold` again.** `assemble` rebuilds the
checkpoints from the chunk cache alone and overwrites both files, so a repeated
assemble removes every recap without saying so. The prose is still in the
workdir, so re-folding costs nothing.

## 5. Report and hand over

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
