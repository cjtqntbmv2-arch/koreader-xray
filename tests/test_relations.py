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


def test_historical_figures_are_listed_separately_and_without_aliases():
    """clean_response builds historical figures with no `aliases` key at all
    (merge.py, unlike characters), so offering the model an alias line there
    would promise a matching rule the fold cannot honour."""
    _system, instr = build(
        historical=[{"name": "Aegon der Eroberer", "aliases": ["der Drache"],
                     "biography": "Einiger der Sieben Königslande."}],
    )
    assert "Aegon der Eroberer" in instr
    assert "der Drache" not in instr


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


def test_historical_figures_resolve_by_name_only(doc):
    """clean_response builds that category with no `aliases` key at all
    (merge.py), so an alias rule there would promise what cannot be honoured."""
    doc["checkpoints"][0]["snapshot"]["historical_figures"][0]["aliases"] = ["der Drache"]
    by_name, _warn = filter_relations(
        [edge("Robb Stark", "Aegon der Eroberer", "Ahn")], doc)
    assert len(by_name) == 1

    by_alias, _warn = filter_relations(
        [edge("Robb Stark", "der Drache", "Ahn")], doc)
    assert by_alias == []


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
    from xray_core.prompts import MAX_RELATIONS_PER_FIGURE

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
