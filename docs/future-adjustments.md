# Future adjustments

Low-priority, non-blocking quality items. Not correctness bugs — the data is
schema-valid and spoiler-safe — but the generated prose could be better. Both
concern the **X-Ray content**, whose prompts are shared in spirit between this
repo's `xray.koplugin/prompts/*.lua` and the calibre generator's `prompts.py`
(calibre-xray repo), so a prompt fix is a cross-repo change.

Observed during the first real end-to-end test (2026-07-11, calibre-generated
X-Ray for "Die Herren von Winterfell", target language `de`, on a Kobo).

## 1. Timeline events occasionally come back in English

In a `de` document, ~9 of 65 `timeline[].event` strings were English (e.g.
"Jon Schnee finds a sixth, albino direwolf pup left behind in the snow…"), while
every character/location/term `description` was consistently German. So the
drift is specific to the chronology/timeline generation, not the entity prompts.

Likely cause: the timeline/chronology prompt enforces the output language less
strictly than the entity prompts. Fix direction: strengthen the explicit
"write in <language>" instruction in the chronology prompt (and mirror it in
calibre's `prompts.py`), or add a light post-generation language check.

## 2. `historical_figures` comes back empty

For the same book, `historical_figures` was empty across all 12 checkpoints,
although the text has them (Aegon, Aerys, …); the model folded those into
`characters`/`terms` instead. An empty category is schema-valid, so this is a
classification/coverage nudge, not a defect.

Fix direction: clarify in the prompt what distinguishes a historical figure
(referenced-but-not-present, backstory) from an active character, or accept that
the split is model-dependent and drop the separate category if it rarely fills.
