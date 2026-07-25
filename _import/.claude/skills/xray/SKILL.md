---
name: xray
description: Generate KOReader X-Ray data (xray.json) from an EPUB using Claude subagents as the extraction backend (no Gemini API). Use when the user gives an EPUB and wants embedded/companion X-Ray output.
---

# xray — Claude-backed X-Ray generation

Given an EPUB path (and optional `--detail normal|detailed`, default `detailed`):

1. **Plan.** Run:
   `python3 -m tools.claude_xray_plan "<EPUB>" --workdir "<WORKDIR>" --detail <detail>`
   Read the printed `manifest.json`. It lists every chunk as `{cp_idx, chunk_idx, percent, prompt_file, raw_file}`.
   **Before launching the extraction, tell the user the chunk count as a rough cost signal** — extraction is the expensive part (one subagent per chunk). A full novel is ~30–40 chunks; a whole series is far more. Because each `raw_file` is written independently, hitting a usage/quota limit mid-run only costs the *unfinished* chunks (rerun resumes).

2. **Extract (one subagent per chunk, in parallel batches, no cap).** Dispatch with the **Agent/Task tool** (one subagent per chunk), **model `sonnet`** — Opus is not worth it here: recall comes from the prompt + self-glean, not the model tier, and at ~37 chunks Opus can exhaust a MAX-plan quota mid-run (measured on a real book). Send them in waves of ~8–12 concurrent subagents (a realistic batch size — a 77-chunk book is ~7 waves), each processing one chunk. For each chunk whose `raw_file` does not yet exist in `<WORKDIR>` (resume-safe), the subagent is told to:
   - Read `<WORKDIR>/<prompt_file>` (it contains the full extraction instruction + that chunk's text).
   - Follow it exactly: extract EVERY character/location/term/historical-figure/timeline entry present in the chunk, then self-glean (re-scan for missed minor figures), using ONLY the provided text.
   - Write the resulting JSON object (only the JSON, matching the schema described in the prompt) to `<WORKDIR>/<raw_file>` — write the JSON **directly**, and leave no helper scripts (`build_*.py`, `gen_*.py`) behind in the workdir.
   Show progress (n/total). Because each result is a file, re-running skips finished chunks.

3. **Assemble.** Run:
   `python3 -m tools.claude_xray_assemble "<EPUB>" --workdir "<WORKDIR>" --out "<OUTDIR>" [--embed-mode full|append] [--title "<calibre library title>"]`
   This cleans the raw outputs into the resume cache, runs the deterministic merge/validate, and writes `<OUTDIR>/<book>.epub.xray.json` (companion), `<OUTDIR>/<book>.epub` (embedded copy), and `<OUTDIR>/xray.json`.
   - `--title "<calibre library title>"`: **pass this whenever the book comes from a calibre library and will be sent to the device via calibre.** The KOReader importer gates on the title (`book_fingerprint.title` vs the OPF title of the book as it lands on-device), and calibre rewrites the OPF to its *library* title on send — which often differs from the EPUB's own OPF title (e.g. a German EPUB whose OPF says "Feuer und Blut" under a calibre library entry titled "Fire and Blood"). `--title` aligns **both** the fingerprint **and** the embedded EPUB's own `<dc:title>` to the value you pass, so all three (fingerprint / embedded OPF / calibre-on-send OPF) agree and the import is accepted. Without it the data is silently rejected as *"does not match this book."* Get the exact title from `select title from books where ...` in `<library>/metadata.db`, or from the calibre GUI. (Full embed mode only — `append` mode leaves source bytes untouched, so calibre's OPF-title-on-send does the aligning there.)
   - `--embed-mode full` (default): registers the xray in the OPF manifest, so it survives calibre's *Convert Book*.
   - `--embed-mode append`: leaves the source bytes untouched and only appends the xray. Use this when the book has **already been read on the device** and you want to replace the file (e.g. via calibre wireless) *without* resetting KOReader reading statistics — see below.

4. **Report** the three output paths. Guidance on which to use:
   - **Embedded copy is fine even for already-read books.** KOReader keys a book's statistics/progress on a *head-weighted* `partialMD5` (12×1 KB samples over the first ~1 MB), so embedding the xray does **not** change the book's identity and does **not** reset statistics — verified on a real multi-MB book. `--embed-mode append` **guarantees** this (source head bytes are byte-identical); the default `full` mode also preserved it in testing but isn't guaranteed for unusually small books.
   - **Companion `.xray.json`** is the zero-risk option (the book file is never touched at all), but calibre wireless sends only book formats, not sidecar files — you'd copy it next to the book on the device manually.
   - When replacing a read book and relying on stats preservation, also disable calibre's *"Update metadata in book files when sending to device"* so calibre doesn't rewrite the head on send. The original source EPUB is never modified by this tool.
   - **Calibre "X-Ray" tag:** full mode also stamps `<dc:subject>X-Ray</dc:subject>` into the OPF, which calibre maps to a filterable **Tag** "X-Ray". calibre only reads this when it *reads* the OPF — reliably on **add-as-new** (not on plain format-replace, where calibre keeps its library metadata). Append mode does not add it (it touches no bytes). This is a visibility aid only; it does not affect the device.

Constraints: never modify the source EPUB; if the assembler aborts listing missing/invalid chunks, re-dispatch subagents for exactly those `(cp,idx)` and re-run the assembler. `--out`/`OUTDIR` must be a directory other than the source EPUB's own directory, or the embedded copy would overwrite (and truncate) the source.
