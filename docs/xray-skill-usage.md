# Using the `xray` skill

The `xray` skill (`.claude/skills/xray/SKILL.md`) generates KOReader X-Ray data
for an EPUB using Claude subagents instead of the Gemini API. Give Claude an
EPUB path; it drives three steps:

1. **Plan** — `python3 -m tools.claude_xray_plan "<EPUB>" --workdir "<WORKDIR>" --detail <normal|detailed>`
   writes one prompt file per text chunk plus `manifest.json` listing them.
2. **Extract** — Claude dispatches one subagent per chunk (in waves of
   ~8–12 concurrent), each reading its `<prompt_file>` and writing the
   extracted JSON to `<raw_file>`. Already-written `raw_file`s are skipped, so
   a rerun resumes instead of redoing finished chunks.
3. **Assemble** — `python3 -m tools.claude_xray_assemble "<EPUB>" --workdir "<WORKDIR>" --out "<OUTDIR>"`
   merges/validates the chunk outputs and writes `<book>.epub.xray.json`
   (companion), `<book>.epub` (embedded copy), and `xray.json` (raw) to
   `<OUTDIR>`.

## Which output to use

- **Already read this book on your device?** Copy the companion
  `<book>.epub.xray.json` next to the existing EPUB — this preserves your
  reading statistics/progress instead of replacing the file.
- **Haven't started the book yet?** Use the embedded `<book>.epub` copy from
  `<OUTDIR>` instead of the original.
- The original source EPUB is never modified by this process.
- `--out`/`OUTDIR` must be a directory other than the source EPUB's own
  directory, or the embedded copy would overwrite (and truncate) the source.

If assembly aborts listing missing or invalid chunks, re-dispatch subagents
only for those `(cp,idx)` pairs and re-run the assembler.

## Notes & known limits (from the first real e2e run)

- **Model:** dispatch the extraction subagents on **Sonnet**, not Opus. Recall
  comes from the prompt + self-glean, not the model tier, and at ~37 chunks Opus
  can exhaust a MAX-plan quota mid-run.
- **Detail level:** `detailed` (default) is comprehensive but heavy — a half-novel
  produced ~226 characters / ~120 terms and a ~1.7 MB `xray.json`. Use `normal`
  for a leaner document.
- **Duplicate cards from titles/forms:** name dedup does not (yet) strip leading
  honorifics, so "Ser Jaime Lennister" and "Jaime Lennister" survive as two cards
  (and ordinal/epithet variants like "Aerys II. Targaryen" vs "Aerys Targaryen").
  This is being addressed in `xray_core/merge.py`; until then expect some inflated
  character counts. Deliberate counter-rule: bare shared first names ("Robert")
  are never fuzzy-merged (dynasty books reuse first names).
- **Chapter headings across chunks:** when a chapter continues into a later chunk
  without its heading, that chunk's timeline events for the headless portion can be
  under-emitted. Minor; affects only the top-level `timeline`, not the snapshots.
