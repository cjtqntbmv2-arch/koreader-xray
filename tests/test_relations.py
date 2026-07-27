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
