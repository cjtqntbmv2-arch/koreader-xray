"""Feature B (ego net) -- prompt construction.

The fold and its filter chain get their own tests once tools/claude_xray_relations.py
exists (plan T3); this file covers T2.
"""
import re

import pytest

from xray_core.prompts import (
    MAX_RELATIONS_PER_FIGURE,
    RELATIONS_DE,
    build_relations_prompt,
)


CHARACTERS = [
    {"name": "Eddard Stark", "aliases": ["Ned"], "description": "Lord von Winterfell."},
    {"name": "Robb Stark", "description": "Sein ältester Sohn."},
    {"name": "", "description": "namenlos, wird übersprungen"},
]

HISTORICAL = [
    {"name": "Aegon der Eroberer", "biography": "Einiger der Sieben Königslande."},
]


def build(language="de", **kw):
    kw.setdefault("title", "Die Gefährten")
    kw.setdefault("author", "J. R. R. Tolkien")
    kw.setdefault("characters", CHARACTERS)
    kw.setdefault("historical", HISTORICAL)
    return build_relations_prompt(language, **kw)


def test_no_placeholder_survives():
    """A forgotten .replace() ships the literal tag to the model. Cheap to
    check, and it is the failure this builder is most likely to have.

    Matched on the {UPPERCASE} tag shape rather than a bare '{': the prompt
    legitimately contains JSON examples like {"from": ...} that show the model
    the output form.
    """
    _system, instr = build()
    left = re.findall(r"\{[A-Z_]+\}", instr)
    assert left == [], f"unsubstituted placeholders: {left}"


def test_the_placeholder_check_can_actually_fail():
    """Guards the check above: if the tag shape ever stops matching what the
    templates use, test_no_placeholder_survives goes vacuously green."""
    assert re.findall(r"\{[A-Z_]+\}", RELATIONS_DE), "template has no tags to substitute"


def test_cap_reaches_the_prompt_as_a_real_number():
    """The cap must appear as its actual value.

    This is the falsifying case for routing through `_apply_percent_args`
    (prompts.py): that helper spreads `percent` across every specifier from the
    third onward, so a cap written as %d silently renders as 0 -- a prompt that
    reads "at most 0 relations per figure" and looks perfectly well-formed.
    A whole-book relations prompt has no percent to pass in the first place.
    """
    _system, instr = build()
    assert str(MAX_RELATIONS_PER_FIGURE) in instr
    assert MAX_RELATIONS_PER_FIGURE >= 1
    assert " 0 " not in instr.replace(str(MAX_RELATIONS_PER_FIGURE), "N")


def test_percent_signs_in_book_data_survive_verbatim():
    """No %-formatting anywhere in this builder, so a '%' in a title, a name or
    a description is just a character. With %-formatting it would either raise
    or eat the following character."""
    _system, instr = build(
        title="100% Winterfell",
        characters=[{"name": "Ser 50%", "description": "trägt %s im Wappen"}],
    )
    assert "100% Winterfell" in instr
    assert "Ser 50%" in instr
    assert "trägt %s im Wappen" in instr


def test_characters_and_aliases_are_listed():
    _system, instr = build()
    assert "Eddard Stark" in instr
    assert "Ned" in instr, "aliases must reach the model, or it cannot match names"
    assert "Lord von Winterfell." in instr


def test_nameless_entries_are_skipped():
    _system, instr = build()
    assert "namenlos, wird übersprungen" not in instr


def test_historical_figures_are_listed_with_their_aliases():
    """This shipped the other way round on a wrong premise. `clean_response`
    does build historical figures without an `aliases` key -- but the snapshot
    the pass reads is POST-MERGE, and `_add_alias` puts one there: measured,
    "Yssa the Elder" merged with "Queen Yssa the Elder" stores
    aliases ['Queen Yssa the Elder']. Withholding them from the model while the
    fold could resolve them only loses edges."""
    _system, instr = build(
        historical=[{"name": "Aegon der Eroberer", "aliases": ["der Drache"],
                     "biography": "Einiger der Sieben Königslande."}],
    )
    assert "Aegon der Eroberer" in instr
    assert "der Drache" in instr


def test_both_directions_are_demanded():
    """Every relationship twice, once per direction -- that is what lets the
    device filter on `from` alone, with no reversal logic."""
    _system, instr = build("en")
    assert "both directions" in instr.lower()


def test_language_selects_the_template():
    _system_de, de = build("de")
    _system_en, en = build("en")
    assert de != en
    assert "Beziehung" in de
    assert "relation" in en.lower()


def test_system_instruction_comes_from_the_shared_table():
    """Same JSON-only/no-pretraining system prompt as every other pass; a
    relations-specific one would drift from it."""
    from xray_core.prompts import SYSTEM_INSTRUCTION_DE

    system, _instr = build("de")
    assert system == SYSTEM_INSTRUCTION_DE


def test_unknown_language_raises():
    with pytest.raises(KeyError):
        build("fr")


def test_empty_lists_do_not_produce_an_empty_section():
    _system, instr = build(characters=[], historical=[])
    assert re.findall(r"\{[A-Z_]+\}", instr) == []
    assert "(none recorded)" in instr


# ---------------------------------------------------------------------------
# T3 -- the fold and its filter chain
# ---------------------------------------------------------------------------

import json
import os

import pytest as _pytest

from tools.claude_xray_relations import (
    filter_relations,
    fold,
    run_fold,
    write_plan,
)


def _character(name, seq, **kw):
    """first_pct/first_seq are required on snapshot characters (schema.py) --
    a fixture without them makes run_fold abort on validation rather than on
    whatever the test is actually about."""
    return {"name": name, "description": "", "first_pct": 12, "first_seq": seq, **kw}


@_pytest.fixture
def doc(minimal_doc):
    """minimal_doc with a cast worth relating. The last stage is the one the
    fold resolves against."""
    minimal_doc["checkpoints"][0]["snapshot"]["characters"] = [
        _character("Eddard Stark", 1, aliases=["Ned"], description="Lord."),
        _character("Robb Stark", 2, description="Sein Sohn."),
        _character("Jon Schnee", 3, description="Sein Ziehsohn."),
    ]
    minimal_doc["checkpoints"][0]["snapshot"]["historical_figures"] = [
        {"name": "Aegon der Eroberer", "biography": "Einiger."},
    ]
    return minimal_doc


def edge(f, t, label="Vater"):
    return {"from": f, "to": t, "label": label}


def test_a_clean_pair_survives_the_whole_chain(doc):
    """Non-vacuity first: without this every assertion below can be satisfied
    by a filter that discards everything."""
    kept, _warn = filter_relations(
        [edge("Robb Stark", "Eddard Stark", "Vater"),
         edge("Eddard Stark", "Robb Stark", "Sohn")], doc)
    assert len(kept) == 2
    assert {(r["from"], r["to"], r["label"]) for r in kept} == {
        ("Robb Stark", "Eddard Stark", "Vater"),
        ("Eddard Stark", "Robb Stark", "Sohn"),
    }


def test_unknown_endpoint_is_dropped(doc):
    kept, _warn = filter_relations(
        [edge("Robb Stark", "Tyrion Lennister")], doc)
    assert kept == []


def test_alias_endpoint_survives_and_is_canonicalised(doc):
    """The counter-probe to the case above. Without it a filter that drops
    everything passes both."""
    kept, _warn = filter_relations([edge("Robb Stark", "Ned", "Vater")], doc)
    assert len(kept) == 1
    assert kept[0]["to"] == "Eddard Stark", "must be rewritten to the canonical name"


def test_historical_figures_resolve_by_name_and_alias(doc):
    """Corrected 2026-07-28: the original rule here was name-only, justified by
    clean_response not building an `aliases` key. The snapshot is post-merge,
    where `_add_alias` does add one -- see
    test_historical_figures_resolve_by_alias_too."""
    doc["checkpoints"][0]["snapshot"]["historical_figures"][0]["aliases"] = ["der Drache"]
    by_name, _warn = filter_relations(
        [edge("Robb Stark", "Aegon der Eroberer", "Ahn")], doc)
    assert len(by_name) == 1

    by_alias, _warn = filter_relations(
        [edge("Robb Stark", "der Drache", "Ahn")], doc)
    assert len(by_alias) == 1
    assert by_alias[0]["to"] == "Aegon der Eroberer"


def test_normalisation_runs_before_dedup(doc):
    """The measured failure of the first plan draft: normalising last let
    "Ned"->X and "Eddard Stark"->X through as two distinct edges, which were
    only then rewritten to the same name -- shipping two contradictory edges
    and letting one figure carry twice the cap, with validate() green."""
    kept, _warn = filter_relations(
        [edge("Ned", "Robb Stark", "Sohn"),
         edge("Eddard Stark", "Robb Stark", "Erbe")], doc)
    assert len(kept) == 1, f"alias spellings must collapse to one edge, got {kept}"


def test_normalisation_runs_before_the_self_edge_check(doc):
    """Same ordering, other victim: "Ned" -> "Eddard Stark" is a self-edge only
    once both names are canonical. It would draw the centre as its own
    neighbour."""
    kept, _warn = filter_relations([edge("Ned", "Eddard Stark", "er selbst")], doc)
    assert kept == []


def test_literal_self_edge_is_dropped(doc):
    kept, _warn = filter_relations(
        [edge("Robb Stark", "Robb Stark", "er selbst")], doc)
    assert kept == []


def test_duplicate_pair_keeps_the_first(doc):
    kept, _warn = filter_relations(
        [edge("Robb Stark", "Eddard Stark", "Vater"),
         edge("Robb Stark", "Eddard Stark", "Erzeuger")], doc)
    assert len(kept) == 1
    assert kept[0]["label"] == "Vater"


def test_cap_per_figure_is_enforced(doc):
    from tools.claude_xray_relations import HARD_CAP_PER_FIGURE as MAX_RELATIONS_PER_FIGURE

    targets = [f"Fig {i}" for i in range(MAX_RELATIONS_PER_FIGURE + 3)]
    doc["checkpoints"][0]["snapshot"]["characters"].extend(
        _character(n, 10 + i) for i, n in enumerate(targets))

    kept, _warn = filter_relations(
        [edge("Robb Stark", t, "kennt") for t in targets], doc)
    outgoing = [r for r in kept if r["from"] == "Robb Stark"]
    assert len(outgoing) == MAX_RELATIONS_PER_FIGURE
    assert [r["to"] for r in outgoing] == targets[:MAX_RELATIONS_PER_FIGURE]


def test_unreciprocated_edge_warns_but_is_kept(doc):
    kept, warnings = filter_relations(
        [edge("Robb Stark", "Eddard Stark", "Vater")], doc)
    assert len(kept) == 1, "the edge is correct in one net -- keep it"
    assert any("Robb Stark" in w and "Eddard Stark" in w for w in warnings)


def test_reciprocated_pair_does_not_warn(doc):
    """Counter-probe: without it, a fold that warns unconditionally passes."""
    _kept, warnings = filter_relations(
        [edge("Robb Stark", "Eddard Stark", "Vater"),
         edge("Eddard Stark", "Robb Stark", "Sohn")], doc)
    assert warnings == []


def test_malformed_entries_are_dropped_not_fatal(doc):
    kept, _warn = filter_relations(
        ["kein objekt",
         {"from": "Robb Stark"},
         {"from": "Robb Stark", "to": "", "label": "x"},
         edge("Robb Stark", "Eddard Stark", "Vater")], doc)
    assert len(kept) == 1


def test_fold_refuses_on_text_hash_drift(doc, tmp_path):
    (tmp_path / "relations.json").write_text(
        json.dumps({"relations": [edge("Robb Stark", "Eddard Stark")]}),
        encoding="utf-8")
    manifest = {"text_hash": "sha256:" + "9" * 64, "companion_name": "b.epub.xray.json"}
    with _pytest.raises(SystemExit, match="text_hash"):
        fold(doc, manifest, str(tmp_path))


def test_fold_writes_relations_onto_the_document(doc, tmp_path):
    (tmp_path / "relations.json").write_text(
        json.dumps({"relations": [
            edge("Robb Stark", "Eddard Stark", "Vater"),
            edge("Eddard Stark", "Robb Stark", "Sohn")]}),
        encoding="utf-8")
    manifest = {"text_hash": doc["book_fingerprint"]["text_hash"],
                "companion_name": "b.epub.xray.json"}

    fold(doc, manifest, str(tmp_path))
    assert len(doc["relations"]) == 2


def test_fold_places_relations_beside_checkpoints_not_inside(doc, tmp_path):
    """Placement assertion, standing in for the device-side test that cannot
    run without a JSON module (design)."""
    (tmp_path / "relations.json").write_text(
        json.dumps({"relations": [edge("Robb Stark", "Eddard Stark")]}),
        encoding="utf-8")
    fold(doc, {"text_hash": doc["book_fingerprint"]["text_hash"],
               "companion_name": "b.epub.xray.json"}, str(tmp_path))
    assert "relations" in doc
    assert "relations" not in doc["checkpoints"][0]
    assert "relations" not in doc["checkpoints"][0]["snapshot"]


def test_missing_answer_file_is_not_fatal(doc, tmp_path):
    """An interrupted wave leaves no relations.json -- same tolerance the recap
    fold has for a missing stage file."""
    fold(doc, {"text_hash": doc["book_fingerprint"]["text_hash"],
               "companion_name": "b.epub.xray.json"}, str(tmp_path))
    assert doc.get("relations", []) == []


def test_plan_writes_prompt_and_manifest(doc, tmp_path):
    manifest = write_plan(doc, "/books/Die Gefährten.epub", str(tmp_path))
    assert os.path.exists(tmp_path / "relations.prompt.txt")
    assert manifest["companion_name"] == "Die Gefährten.epub.xray.json"
    assert manifest["text_hash"] == doc["book_fingerprint"]["text_hash"]
    prompt = (tmp_path / "relations.prompt.txt").read_text(encoding="utf-8")
    assert "Eddard Stark" in prompt and "Aegon der Eroberer" in prompt


def test_run_fold_writes_both_filenames(doc, tmp_path):
    work = tmp_path / "work"
    out = tmp_path / "out"
    work.mkdir()
    doc_path = tmp_path / "xray.json"
    doc_path.write_text(json.dumps(doc), encoding="utf-8")

    manifest = write_plan(doc, "/books/b.epub", str(work))
    (work / "relations.json").write_text(
        json.dumps({"relations": [
            edge("Robb Stark", "Eddard Stark", "Vater"),
            edge("Eddard Stark", "Robb Stark", "Sohn")]}),
        encoding="utf-8")

    written, _warn = run_fold(str(doc_path), str(work), str(out))
    assert len(written["relations"]) == 2
    for name in ("xray.json", manifest["companion_name"]):
        assert (out / name).exists()
        assert json.loads((out / name).read_text(encoding="utf-8"))["relations"]


# ---------------------------------------------------------------------------
# Review follow-ups (2026-07-28): findings from four independent reviewers
# ---------------------------------------------------------------------------

def test_label_naming_a_figure_drops_the_edge(doc):
    """The label is the one string on the ego-net screen that the device's D4
    filter never inspects -- it is rendered verbatim beside the neighbour. A
    label like "Vater, auch von Jon Schnee" therefore leaks a name the reader
    may not have reached, however correct both endpoints are. Caught here,
    because only the desktop knows the full cast."""
    doc["checkpoints"][0]["snapshot"]["characters"].append(
        _character("Jon Schnee", 9, description="Ziehsohn."))
    kept, warnings = filter_relations(
        [edge("Robb Stark", "Eddard Stark", "Vater, auch von Jon Schnee")], doc)
    assert kept == []
    assert any("Jon Schnee" in w for w in warnings)


def test_an_ordinary_label_is_untouched(doc):
    """Counter-probe: without it a scanner that drops every label passes."""
    kept, _warn = filter_relations(
        [edge("Robb Stark", "Eddard Stark", "Vater")], doc)
    assert len(kept) == 1
    assert kept[0]["label"] == "Vater"


def test_the_endpoints_own_names_do_not_trip_the_scan(doc):
    """Both figures are on screen anyway, so naming them is not a leak."""
    kept, _warn = filter_relations(
        [edge("Robb Stark", "Eddard Stark", "Sohn von Eddard Stark")], doc)
    assert len(kept) == 1


def test_a_sentence_shaped_label_is_rejected(doc):
    """Catches the leak class the name scan cannot see: a label naming a place,
    a house or an event that is not in the cast. The prompt asks for one or two
    words; anything sentence-shaped is a rule violation, not a role."""
    kept, warnings = filter_relations(
        [edge("Robb Stark", "Eddard Stark",
              "sein Vater und der Herr von Winterfell im Norden")], doc)
    assert kept == []
    assert warnings


def test_historical_figures_resolve_by_alias_too(doc):
    """Corrects an assumption this module shipped with. `clean_response` builds
    historical figures without an `aliases` key, but the SNAPSHOT is post-merge,
    and `_add_alias` adds one there: measured, "Yssa the Elder" merged with
    "Queen Yssa the Elder" stores aliases ['Queen Yssa the Elder']. Resolving
    those by name only silently drops edges."""
    doc["checkpoints"][0]["snapshot"]["historical_figures"][0]["aliases"] = ["Der Eroberer"]
    kept, _warn = filter_relations(
        [edge("Robb Stark", "Der Eroberer", "Ahn")], doc)
    assert len(kept) == 1
    assert kept[0]["to"] == "Aegon der Eroberer"


def test_answer_with_prose_around_a_fence_still_parses(doc, tmp_path):
    """The shape a subagent most often writes. Raising here would lose the pass
    after its budget is spent -- the trap this fold exists to avoid."""
    (tmp_path / "relations.json").write_text(
        'Here are the relations:\n\n```json\n'
        '{"relations": [{"from": "Robb Stark", "to": "Eddard Stark", "label": "Vater"}]}\n'
        '```\n\nHope that helps!',
        encoding="utf-8")
    fold(doc, {"text_hash": doc["book_fingerprint"]["text_hash"],
               "companion_name": "b.epub.xray.json"}, str(tmp_path))
    assert len(doc["relations"]) == 1


def test_unparseable_answer_fails_with_a_readable_message(doc, tmp_path):
    (tmp_path / "relations.json").write_text("I could not do this.", encoding="utf-8")
    with _pytest.raises(SystemExit, match="relations.json"):
        fold(doc, {"text_hash": doc["book_fingerprint"]["text_hash"],
                   "companion_name": "b.epub.xray.json"}, str(tmp_path))


def test_an_empty_result_clears_stale_relations(doc, tmp_path):
    """SKILL.md makes --doc and --out the same file, so a re-fold is
    read-modify-write. Keeping the previous run's edges when the new answer
    yields none reports them as fresh and hides that this run found nothing."""
    doc["relations"] = [{"from": "Robb Stark", "to": "Eddard Stark", "label": "STALE"}]
    (tmp_path / "relations.json").write_text(
        json.dumps({"relations": [edge("Robb Stark", "Niemand")]}), encoding="utf-8")
    fold(doc, {"text_hash": doc["book_fingerprint"]["text_hash"],
               "companion_name": "b.epub.xray.json"}, str(tmp_path))
    assert doc["relations"] == []


def test_a_missing_answer_file_leaves_the_document_alone(doc, tmp_path):
    """The other half of the case above: an interrupted wave must not wipe
    what a previous run produced."""
    doc["relations"] = [{"from": "Robb Stark", "to": "Eddard Stark", "label": "Vater"}]
    fold(doc, {"text_hash": doc["book_fingerprint"]["text_hash"],
               "companion_name": "b.epub.xray.json"}, str(tmp_path))
    assert len(doc["relations"]) == 1


def test_the_cap_applies_per_source_not_globally(doc):
    """Two source figures must each get their own allowance; a global counter
    would give the first figure everything and the second nothing."""
    from tools.claude_xray_relations import HARD_CAP_PER_FIGURE as MAX_RELATIONS_PER_FIGURE

    targets = [f"Fig {i}" for i in range(MAX_RELATIONS_PER_FIGURE)]
    doc["checkpoints"][0]["snapshot"]["characters"].extend(
        _character(n, 20 + i) for i, n in enumerate(targets))

    raw = ([edge("Robb Stark", t, "kennt") for t in targets]
           + [edge("Eddard Stark", t, "kennt") for t in targets])
    kept, _warn = filter_relations(raw, doc)
    assert len([r for r in kept if r["from"] == "Robb Stark"]) == MAX_RELATIONS_PER_FIGURE
    assert len([r for r in kept if r["from"] == "Eddard Stark"]) == MAX_RELATIONS_PER_FIGURE


def test_names_are_matched_case_insensitively(doc):
    """A model re-typing names it was handed drifts in case; dropping the edge
    for that would be silent loss."""
    kept, _warn = filter_relations(
        [{"from": "ROBB STARK", "to": "eddard stark", "label": "Vater"}], doc)
    assert len(kept) == 1
    assert (kept[0]["from"], kept[0]["to"]) == ("Robb Stark", "Eddard Stark")


def test_padded_names_resolve_and_a_blank_label_does_not(doc):
    kept, _warn = filter_relations(
        [{"from": " Robb Stark ", "to": "Eddard Stark", "label": "Vater"},
         {"from": "Eddard Stark", "to": "Robb Stark", "label": "   "}], doc)
    assert len(kept) == 1
    assert kept[0]["from"] == "Robb Stark"


def test_the_fold_cap_is_a_safety_net_not_a_selection(doc):
    """Measured on "Die Gefährten": with the cap at the prompt's 5, Frodo kept
    5 outgoing edges while 13 figures pointed at him -- the protagonist got the
    poorest net in the book, and which 5 survived was decided by the order the
    model happened to write them in. The prompt's limit is the editorial one;
    the fold's exists only to stop a derailed answer."""
    from tools.claude_xray_relations import HARD_CAP_PER_FIGURE
    from xray_core.prompts import MAX_RELATIONS_PER_FIGURE

    assert HARD_CAP_PER_FIGURE > MAX_RELATIONS_PER_FIGURE

    targets = [f"Fig {i}" for i in range(MAX_RELATIONS_PER_FIGURE + 3)]
    doc["checkpoints"][0]["snapshot"]["characters"].extend(
        _character(n, 30 + i) for i, n in enumerate(targets))
    raw = []
    for t in targets:
        raw.append(edge("Robb Stark", t, "kennt"))
        raw.append(edge(t, "Robb Stark", "kennt"))

    kept, warnings = filter_relations(raw, doc)
    assert len([r for r in kept if r["from"] == "Robb Stark"]) == len(targets)
    assert warnings == [], f"nothing should be capped or unreciprocated here: {warnings}"


def test_the_cap_drops_whole_pairs(doc):
    """When the cap does bite, it must take the counterpart with it. Capping
    one direction only leaves B's card showing A while A's card does not show
    B -- and reports it as an unreciprocated edge, which is a defect the fold
    manufactured rather than found."""
    from tools.claude_xray_relations import HARD_CAP_PER_FIGURE

    targets = [f"Fig {i}" for i in range(HARD_CAP_PER_FIGURE + 2)]
    doc["checkpoints"][0]["snapshot"]["characters"].extend(
        _character(n, 40 + i) for i, n in enumerate(targets))
    raw = []
    for t in targets:
        raw.append(edge("Robb Stark", t, "kennt"))
        raw.append(edge(t, "Robb Stark", "kennt"))

    kept, warnings = filter_relations(raw, doc)
    pairs = {(r["from"], r["to"]) for r in kept}
    assert len([r for r in kept if r["from"] == "Robb Stark"]) == HARD_CAP_PER_FIGURE
    for source, target in pairs:
        assert (target, source) in pairs, f"{source} -> {target} lost its counterpart"
    assert not any("unreciprocated" in w for w in warnings)
    assert any("cap" in w.lower() for w in warnings), "a silent drop is the thing to avoid"


def test_a_genuinely_one_sided_edge_is_still_kept_and_reported(doc):
    """Counter-probe to the case above: the pair-drop must not swallow the
    asymmetry the model itself produced, which is the one worth warning about."""
    kept, warnings = filter_relations(
        [edge("Robb Stark", "Eddard Stark", "Vater")], doc)
    assert len(kept) == 1
    assert any("unreciprocated" in w for w in warnings)
