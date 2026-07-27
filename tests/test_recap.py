"""Tests for the recap prompt and the recap pass (tools/claude_xray_recap.py).

The recap is prose, so nothing here can judge whether a generated text is
*good*. What is testable -- and what these tests hold -- is that the prompt
carries the right material, that stage selection spreads over the book, and
that folding puts the text in the one place the device reads it from.
"""
import json
import os
import re

from tools.claude_xray_recap import fold, run_fold, select_stages, write_plan
from xray_core.schema import validate
from xray_core.prompts import (
    RECAP_EN,
    RECAP_DE,
    RECAP_WEIGHTING_EN,
    RECAP_WEIGHTING_DE,
    _real_specifier_count,
    build_recap_prompt,
    recap_target_words,
)

EVENTS = [
    {"chapter": "Ch1", "event": "The city burns", "pct": 5},
    {"chapter": "Ch2", "event": "The heir is named", "pct": 30},
]
CHARACTERS = [
    {"name": "Alice Merrow", "description": "A cartographer."},
    {"name": "Ser Jaime", "description": ""},
]


def test_en_and_de_templates_take_the_same_arguments():
    """A DE template missing one specifier renders fine in every test that does
    not touch it and then dies on the first German book -- after planning, with
    the extraction budget already spent. _SPEC_RE does NOT catch this: it
    counts specifiers within one template to build that template's arg tuple,
    and never compares the two languages."""
    assert _real_specifier_count(RECAP_EN) == _real_specifier_count(RECAP_DE)
    assert _real_specifier_count(RECAP_WEIGHTING_EN) == _real_specifier_count(RECAP_WEIGHTING_DE)


def test_both_languages_render_without_raising():
    for language in ("en", "de"):
        system, prompt = build_recap_prompt(
            language, "Fire and Blood", "GRRM", 40, EVENTS, CHARACTERS)
        assert isinstance(system, str) and system
        assert isinstance(prompt, str) and prompt


def test_no_placeholder_survives_rendering():
    """A brace tag left behind reaches the model verbatim -- and a tag the DE
    template spells differently would never be substituted at all."""
    for language in ("en", "de"):
        _system, prompt = build_recap_prompt(
            language, "Fire and Blood", "GRRM", 40, EVENTS, CHARACTERS)
        assert not re.search(r"\{[A-Z_]+\}", prompt), prompt


def test_prompt_carries_the_events_and_characters_it_was_given():
    _system, prompt = build_recap_prompt(
        "en", "Fire and Blood", "GRRM", 40, EVENTS, CHARACTERS)
    assert "The city burns" in prompt
    assert "The heir is named" in prompt
    assert "Alice Merrow" in prompt
    # An entity whose description the extraction never filled must still be
    # named -- the recap needs to know it exists.
    assert "Ser Jaime" in prompt


def test_weighting_block_appears_only_once_there_is_a_past_to_weight():
    """Below 20% there is no "long ago" to give extra room to, so the recap
    just follows the chronology."""
    _s, early = build_recap_prompt("en", "T", "A", 15, EVENTS, CHARACTERS)
    _s, later = build_recap_prompt("en", "T", "A", 40, EVENTS, CHARACTERS)

    marker = RECAP_WEIGHTING_EN.split("%")[0].strip()[:40]
    assert marker not in early
    assert marker in later


def test_target_length_follows_the_material_not_the_position():
    """Measured on a real book: at 16% there were 11 events and the model still
    wrote 399 words, filling the gap with the founding of the Shire in 1601 and
    a history of pipe-weed. Length has to follow how much actually happened."""
    assert recap_target_words(0) == 150
    assert recap_target_words(11) == 150     # the real 16% stage
    assert recap_target_words(26) == 208     # the real 46% stage
    assert recap_target_words(51) == 400     # the real 89% stage
    assert recap_target_words(500) == 400


def test_prompt_states_the_target_length():
    _s, few = build_recap_prompt("en", "T", "A", 40, EVENTS[:1], CHARACTERS)
    _s, many = build_recap_prompt(
        "en", "T", "A", 40, [dict(EVENTS[0], pct=1) for _ in range(60)], CHARACTERS)
    assert str(recap_target_words(1)) in few
    assert str(recap_target_words(60)) in many


def test_weighting_bands_are_computed_from_the_reading_position():
    """0.5*P and 0.85*P, so the far band grows with the reader rather than
    sitting at a fixed percent."""
    _s, prompt = build_recap_prompt("en", "T", "A", 40, EVENTS, CHARACTERS)
    assert "20%" in prompt   # 0.5 * 40
    assert "34%" in prompt   # 0.85 * 40


# --------------------------------------------------------------------------
# Stage selection and planning
# --------------------------------------------------------------------------

def make_doc(n_stages, timeline=None):
    """A document shaped like a real one: `checkpoints` are per-chunk stages
    (there are ~57 on a novel, not the ~11 planned checkpoints), each holding a
    frozen snapshot, and `timeline` is flat at the top level."""
    step = 100 / n_stages
    checkpoints = []
    for i in range(n_stages):
        pct = max(1, round((i + 1) * step))
        checkpoints.append({
            "percent": 100 if i == n_stages - 1 else pct,
            "snapshot": {
                "characters": [{"name": f"Figure {i}", "description": f"Seen at {pct}%."}],
                "locations": [], "terms": [], "historical_figures": [],
            },
        })
    return {
        "schema_version": 2,
        "book_fingerprint": {"calibre_uuid": "u", "title": "Fire and Blood",
                             "authors": ["GRRM"], "text_hash": "abc123"},
        "timeline": timeline if timeline is not None else [
            {"chapter": "Ch1", "event": "The city burns", "pct": 5},
            {"chapter": "Ch2", "event": "The heir is named", "pct": 30},
            {"chapter": "Ch9", "event": "The tower falls", "pct": 95},
        ],
        "checkpoints": checkpoints,
    }


def test_selection_is_capped_and_ascending():
    idxs = select_stages(make_doc(57))
    assert len(idxs) <= 12
    assert idxs == sorted(set(idxs))


def test_selection_reaches_the_start_of_the_book():
    """Without this, "take the last 12" passes every other check here -- and
    the book has no recap at all until 80%."""
    doc = make_doc(57)
    idxs = select_stages(doc)
    assert doc["checkpoints"][idxs[0]]["percent"] <= 15


def test_selection_skips_the_final_stage():
    """The last stage is pinned at percent=100 (generate.py), and
    selectCheckpoint only reaches it at exactly 100% because its threshold is
    min(percent + MARGIN, 100). A recap there would be invisible all book."""
    doc = make_doc(57)
    idxs = select_stages(doc)
    assert idxs[-1] != len(doc["checkpoints"]) - 1


def test_selection_below_the_cap_takes_everything_but_the_last():
    assert select_stages(make_doc(3)) == [0, 1]


def test_selection_skips_stages_with_nothing_to_recap():
    """On the real book the earliest stage sits at 1% with zero timeline
    events. A "story so far" covering nothing is a wasted model call and a text
    no one at 1% would open anyway."""
    doc = make_doc(3, timeline=[{"chapter": "Ch2", "event": "Much later", "pct": 90}])
    assert select_stages(doc) == []

    doc = make_doc(3, timeline=[{"chapter": "Ch1", "event": "Early on", "pct": 5}])
    assert select_stages(doc) == [0, 1]


def test_selection_is_deterministic():
    doc = make_doc(57)
    assert select_stages(doc) == select_stages(doc)


def test_plan_writes_one_prompt_per_selected_stage(tmp_path):
    doc = make_doc(57)
    manifest = write_plan(doc, "/books/Fire and Blood.epub", str(tmp_path))

    assert len(manifest["stages"]) == len(select_stages(doc))
    for stage in manifest["stages"]:
        assert (tmp_path / stage["prompt_file"]).exists()


def test_prompt_carries_events_up_to_the_stage_and_none_beyond(tmp_path):
    doc = make_doc(57)
    manifest = write_plan(doc, "/books/b.epub", str(tmp_path))

    stage = next(s for s in manifest["stages"] if 30 <= s["percent"] < 95)
    text = (tmp_path / stage["prompt_file"]).read_text(encoding="utf-8")
    # Non-vacuity first: an empty timeline would make the "nothing beyond"
    # assertion true for a planner that writes no events at all.
    assert "The city burns" in text
    assert "The heir is named" in text
    assert "The tower falls" not in text


def test_schema_rejects_a_non_string_recap(minimal_doc):
    minimal_doc["checkpoints"][0]["recap"] = 12345
    problems = validate(minimal_doc)
    assert any("recap" in p for p in problems), problems


def test_schema_accepts_a_valid_recap(minimal_doc):
    """Vacuous before the rule exists -- validate() has no unknown-key check,
    so any shape passes. It starts earning its keep the moment the rule lands:
    an over-eager check that rejected every real document would fail here and
    nowhere else."""
    minimal_doc["checkpoints"][0]["recap"] = "The city burned and the heir was named."
    assert validate(minimal_doc) == []


def test_json_schema_copy_mentions_recap():
    """schema.py and schema/xray.schema.json are two hand-synced copies of one
    contract, and nothing else in the repo notices when they drift."""
    path = os.path.join(os.path.dirname(__file__), os.pardir, "schema", "xray.schema.json")
    with open(path, encoding="utf-8") as f:
        assert "recap" in f.read()


def test_manifest_carries_what_fold_needs(tmp_path):
    """text_hash and companion_name are not decoration. Stage indices shift
    with the chunk count, and the companion filename exists nowhere in the
    document -- assemble derives it from the EPUB path."""
    doc = make_doc(57)
    manifest = write_plan(doc, "/books/Fire and Blood.epub", str(tmp_path))

    assert manifest["text_hash"] == "abc123"
    assert manifest["companion_name"] == "Fire and Blood.epub.xray.json"
    for stage in manifest["stages"]:
        assert set(stage) == {"stage_idx", "percent", "prompt_file", "out_file"}


# --------------------------------------------------------------------------
# Folding the prose back in
# --------------------------------------------------------------------------

def write_recaps(tmp_path, manifest, texts):
    """texts: {stage_idx: prose}. Stages left out stay unwritten, which is what
    an interrupted subagent wave looks like."""
    for stage in manifest["stages"]:
        text = texts.get(stage["stage_idx"])
        if text is not None:
            (tmp_path / stage["out_file"]).write_text(text, encoding="utf-8")


def test_fold_puts_the_prose_where_the_device_reads_it(tmp_path):
    """The place is the contract: `recap` must sit beside `snapshot`, not
    inside it and not at the document root. validate() cannot tell the
    difference -- it returns [] for all three -- so this assertion is what
    holds the shape."""
    doc = make_doc(57)
    manifest = write_plan(doc, "/books/b.epub", str(tmp_path))
    target = manifest["stages"][3]
    write_recaps(tmp_path, manifest, {target["stage_idx"]: "The city burned."})

    warnings = fold(doc, manifest, str(tmp_path))

    assert doc["checkpoints"][target["stage_idx"]]["recap"] == "The city burned."
    assert "recap" not in doc["checkpoints"][target["stage_idx"]]["snapshot"]
    assert "recap" not in doc
    assert warnings == []


def test_fold_skips_stages_whose_prose_was_never_written(tmp_path):
    """Partial coverage is the normal outcome of an interrupted run, not an
    error worth aborting a whole pass for."""
    doc = make_doc(57)
    manifest = write_plan(doc, "/books/b.epub", str(tmp_path))
    written = manifest["stages"][2]
    write_recaps(tmp_path, manifest, {written["stage_idx"]: "Only this one."})

    fold(doc, manifest, str(tmp_path))

    assert doc["checkpoints"][written["stage_idx"]]["recap"] == "Only this one."
    for stage in manifest["stages"]:
        if stage["stage_idx"] != written["stage_idx"]:
            assert "recap" not in doc["checkpoints"][stage["stage_idx"]]


def test_fold_drops_a_recap_that_names_someone_from_a_later_stage(tmp_path):
    """The one leak class a machine can catch. Dropping means removing the
    key, never writing "" -- "" is truthy in Lua and would show an empty
    viewer instead of walking back to the previous stage."""
    doc = make_doc(57)
    manifest = write_plan(doc, "/books/b.epub", str(tmp_path))
    target = manifest["stages"][1]
    later_name = doc["checkpoints"][50]["snapshot"]["characters"][0]["name"]
    write_recaps(tmp_path, manifest,
                 {target["stage_idx"]: f"All was quiet until {later_name} arrived."})

    warnings = fold(doc, manifest, str(tmp_path))

    assert "recap" not in doc["checkpoints"][target["stage_idx"]]
    assert any(later_name in w for w in warnings), warnings


def test_fold_keeps_a_recap_that_only_names_people_already_met(tmp_path):
    doc = make_doc(57)
    manifest = write_plan(doc, "/books/b.epub", str(tmp_path))
    target = manifest["stages"][4]
    known = doc["checkpoints"][target["stage_idx"]]["snapshot"]["characters"][0]["name"]
    prose = f"{known} had made it this far."
    write_recaps(tmp_path, manifest, {target["stage_idx"]: prose})

    warnings = fold(doc, manifest, str(tmp_path))

    assert doc["checkpoints"][target["stage_idx"]]["recap"] == prose
    assert warnings == []


def test_fold_does_not_fire_on_a_later_name_embedded_in_another_word(tmp_path):
    """A plain substring search flags "Robb" inside "Robbers" and throws away a
    perfectly good recap, with a warning that reads like a real leak."""
    doc = make_doc(3)
    doc["checkpoints"][2]["snapshot"]["characters"] = [
        {"name": "Robb", "description": "Arrives late."}]
    manifest = write_plan(doc, "/books/b.epub", str(tmp_path))
    target = manifest["stages"][0]
    prose = "Robbers plundered three villages that winter."
    write_recaps(tmp_path, manifest, {target["stage_idx"]: prose})

    warnings = fold(doc, manifest, str(tmp_path))

    assert doc["checkpoints"][target["stage_idx"]]["recap"] == prose
    assert warnings == []


def test_name_scan_allows_what_the_prompt_itself_supplied(tmp_path):
    """A recap may name anything the prompt fed it, and timeline events are
    part of that material. An entity often appears in an event long before
    extraction records it as a character -- on a real book the Balrog turns up
    in a Moria event well before it lands in any snapshot. Flagging that throws
    away a correct recap and reports it as a spoiler.
    """
    doc = make_doc(3, timeline=[
        {"chapter": "Moria", "event": "Gandalf faces the Balrog", "pct": 20},
    ])
    doc["checkpoints"][2]["snapshot"]["characters"] = [
        {"name": "Balrog", "description": "A demon of the ancient world."}]
    manifest = write_plan(doc, "/books/b.epub", str(tmp_path))
    target = manifest["stages"][0]
    prose = "Gandalf faced the Balrog on the bridge and fell."
    write_recaps(tmp_path, manifest, {target["stage_idx"]: prose})

    warnings = fold(doc, manifest, str(tmp_path))

    assert doc["checkpoints"][target["stage_idx"]]["recap"] == prose
    assert warnings == []


def test_name_scan_allows_names_that_only_a_description_supplied(tmp_path):
    """The prompt feeds character descriptions too, and those name people who
    are not entities yet. On a real book Bilbo's entry mentions Thorin
    Oakenshield at 16%, while Thorin only becomes a character of his own much
    later -- a recap repeating that is quoting its own material.
    """
    doc = make_doc(3, timeline=[{"chapter": "Ch1", "event": "A journey begins", "pct": 5}])
    doc["checkpoints"][0]["snapshot"]["characters"] = [
        {"name": "Bilbo", "description": "Travelled to Erebor with Thorin Oakenshield."}]
    doc["checkpoints"][2]["snapshot"]["characters"] = [
        {"name": "Thorin Oakenshield", "description": "A dwarf king."}]
    manifest = write_plan(doc, "/books/b.epub", str(tmp_path))
    target = manifest["stages"][0]
    prose = "Bilbo had once travelled to Erebor with Thorin Oakenshield."
    write_recaps(tmp_path, manifest, {target["stage_idx"]: prose})

    warnings = fold(doc, manifest, str(tmp_path))

    assert doc["checkpoints"][target["stage_idx"]]["recap"] == prose
    assert warnings == []


def test_fold_refuses_when_the_document_is_not_the_one_that_was_planned(tmp_path):
    """Stage indices are a function of the chunk count. Folding recap_5 into a
    renumbered document hangs prose covering 0-67% onto a stage claiming 51% --
    a D4 violation the name scan cannot see."""
    doc = make_doc(57)
    manifest = write_plan(doc, "/books/b.epub", str(tmp_path))
    write_recaps(tmp_path, manifest, {manifest["stages"][1]["stage_idx"]: "Prose."})

    doc["book_fingerprint"]["text_hash"] = "a-different-book"

    try:
        fold(doc, manifest, str(tmp_path))
    except SystemExit as e:
        assert "text_hash" in str(e)
    else:
        raise AssertionError("fold accepted a document it did not plan against")


def test_fold_refuses_when_a_stage_no_longer_sits_at_its_planned_percent(tmp_path):
    doc = make_doc(57)
    manifest = write_plan(doc, "/books/b.epub", str(tmp_path))
    write_recaps(tmp_path, manifest, {manifest["stages"][1]["stage_idx"]: "Prose."})

    shifted = manifest["stages"][1]["stage_idx"]
    doc["checkpoints"][shifted]["percent"] = doc["checkpoints"][shifted]["percent"] + 7

    try:
        fold(doc, manifest, str(tmp_path))
    except SystemExit as e:
        assert "percent" in str(e)
    else:
        raise AssertionError("fold accepted a document whose stages moved")


def test_run_fold_writes_both_filenames_with_identical_bytes(tmp_path, minimal_doc):
    """xray.json is what calibre gets; <book>.epub.xray.json is the name the
    device looks for beside a book. The USB companion route has no second
    validation after this, so both have to be written here."""
    workdir = tmp_path / "work"
    out_dir = tmp_path / "out"
    workdir.mkdir()
    out_dir.mkdir()

    doc_path = tmp_path / "xray.json"
    doc_path.write_text(json.dumps(minimal_doc), encoding="utf-8")

    manifest = write_plan(minimal_doc, "/books/Beispiel.epub", str(workdir))
    write_recaps(workdir, manifest, {manifest["stages"][0]["stage_idx"]: "Bisher geschah dies."})

    run_fold(str(doc_path), str(workdir), str(out_dir))

    a = (out_dir / "xray.json").read_bytes()
    b = (out_dir / "Beispiel.epub.xray.json").read_bytes()
    assert a == b
    assert json.loads(a)["checkpoints"][0]["recap"] == "Bisher geschah dies."
