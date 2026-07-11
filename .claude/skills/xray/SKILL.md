---
name: xray
description: Generate KOReader X-Ray data (xray.json) from an EPUB using Claude subagents as the extraction backend (no Gemini API). Use when the user gives an EPUB and wants embedded/companion X-Ray output.
---

# xray — Claude-backed X-Ray generation

Given an EPUB path (and optional `--detail normal|detailed`, default `detailed`):

1. **Plan.** Run:
   `python3 -m tools.claude_xray_plan "<EPUB>" --workdir "<WORKDIR>" --detail <detail>`
   Read the printed `manifest.json`. It lists every chunk as `{cp_idx, chunk_idx, percent, prompt_file, raw_file}`.

2. **Extract (one subagent per chunk, in parallel batches, no cap).** Dispatch with the **Agent/Task tool** (one subagent per chunk). Send them in waves of ~8–12 concurrent subagents (a realistic batch size — a 77-chunk book is ~7 waves), each processing one chunk. For each chunk whose `raw_file` does not yet exist in `<WORKDIR>` (resume-safe), the subagent is told to:
   - Read `<WORKDIR>/<prompt_file>` (it contains the full extraction instruction + that chunk's text).
   - Follow it exactly: extract EVERY character/location/term/historical-figure/timeline entry present in the chunk, then self-glean (re-scan for missed minor figures), using ONLY the provided text.
   - Write the resulting JSON object (only the JSON, matching the schema described in the prompt) to `<WORKDIR>/<raw_file>`.
   Show progress (n/total). Because each result is a file, re-running skips finished chunks.

3. **Assemble.** Run:
   `python3 -m tools.claude_xray_assemble "<EPUB>" --workdir "<WORKDIR>" --out "<OUTDIR>"`
   This cleans the raw outputs into the resume cache, runs the deterministic merge/validate, and writes `<OUTDIR>/<book>.epub.xray.json` (companion), `<OUTDIR>/<book>.epub` (embedded copy), and `<OUTDIR>/xray.json`.

4. **Report** the three output paths and tell the user: use the companion `.xray.json` (drop next to the book on the device) to preserve reading statistics on already-read books; use the embedded copy for new books before first read. The original EPUB is never modified.

Constraints: never modify the source EPUB; if the assembler aborts listing missing/invalid chunks, re-dispatch subagents for exactly those `(cp,idx)` and re-run the assembler. `--out`/`OUTDIR` must be a directory other than the source EPUB's own directory, or the embedded copy would overwrite (and truncate) the source.
