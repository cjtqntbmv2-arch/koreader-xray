# Future adjustments

Quality observations from the first real end-to-end test (2026-07-11,
generated X-Ray for "Die Herren von Winterfell", target language `de`, on a
Kobo). Both items are closed as of 2026-08-02; kept as the record of what was
seen and what it turned out to be.

## 1. Timeline events occasionally come back in English — FIXED 2026-08-02

In a `de` document, ~9 of 65 `timeline[].event` strings were English (e.g.
"Jon Schnee finds a sixth, albino direwolf pup left behind in the snow…"),
while every character/location/term `description` was consistently German.

Cause found: `timeline[].event` was the one field in the German prompt whose
instruction was English. The length/detail guidance was injected from code
rather than from the per-language template — a faithful port of what the old
Lua did — so the field was literally asked for in English and answered in
English. The other English blocks that remain (`NAME_RULES`,
`SEGMENT_ADDENDUM`) constrain *which* entities to extract rather than how to
phrase a value, and never produced drift.

Fix: `_TL_BUCKETS` / `_TL_LENGTH_RULE` in `xray_core/prompts.py` carry the
guidance per language, and critical rule 3 of the German prompt now demands
German values outright, which also covers anything still phrased in English.
Guarded by `tests/test_prompts.py::test_de_timeline_guidance_is_german`.

## 2. `historical_figures` comes back empty — NOT A DEFECT

For the same book, `historical_figures` was empty across all 12 checkpoints,
although the text has Aegon, Aerys and others.

That is the intended outcome. The category asks for **real, widely recognized
historical people** — presidents, authors, generals — and the prompt explicitly
sends fictional figures to `characters` "even when they interact with real
events". Aegon and Aerys are ancestors of an invented world, so `characters` is
where they belong. The category fills only for books that refer to our own
world: historical fiction, non-fiction, a novel that mentions Napoleon. A
secondary-world fantasy yielding an empty list is the rule working.

Worth knowing about the ceiling: `NUM_HIST = min(15, max(3, 800 // hist_cap))`
gives **3 entries in `detailed` and 8 in `normal`** — the more detailed mode has
the lower count, deliberately, because the cap is an output budget. If a
history-heavy book ever comes out thin here, that formula is the thing to
revisit, not the classification rule.
