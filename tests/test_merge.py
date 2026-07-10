import pytest

from xray_core.merge import (
    _CHAR_DESC_KEYS,
    _CHAR_NAME_KEYS,
    _CHAR_OCCUPATION_KEYS,
    _HIST_BIO_KEYS,
    _HIST_CONTEXT_KEYS,
    _HIST_IMPORTANCE_KEYS,
    _HIST_ROLE_KEYS,
    _LOC_DESC_KEYS,
    _LOC_IMPORTANCE_KEYS,
    _LOC_NAME_KEYS,
    _first_nonempty,
    BookState,
    clean_response,
    fallback_strings,
    is_more_complete_name,
    sort_entity_list,
)


def test_clean_keeps_nameless_with_placeholder_and_truncates_role():
    raw = {
        "characters": [{"role": "x" * 50, "description": "a mystery"}],
        "locations": [{"description": "somewhere dark"}],
    }

    cleaned = clean_response(raw)

    assert cleaned["characters"][0]["name"] == "Unnamed Character"
    assert cleaned["characters"][0]["role"] == "x" * 40
    assert cleaned["locations"][0]["name"] == "Unknown Place"


def test_clean_name_fallback_chain():
    cleaned = clean_response({"characters": [{"full_formal_name": "Lord Farquaad"}]})
    assert cleaned["characters"][0]["name"] == "Lord Farquaad"


def test_new_entities_stamped_first_pct_and_seq():
    state = BookState()

    state.merge_segment(
        clean_response({"characters": [{"name": "Alice"}, {"name": "Bob"}]}), checkpoint_pct=20
    )

    by_name = {c["name"]: c for c in state.characters}
    assert by_name["Alice"]["first_pct"] == 20
    assert by_name["Alice"]["first_seq"] == 1
    assert by_name["Bob"]["first_pct"] == 20
    assert by_name["Bob"]["first_seq"] == 2


def test_stamp_idempotent_across_segments():
    state = BookState()

    state.merge_segment(clean_response({"characters": [{"name": "Alice"}]}), checkpoint_pct=10)
    state.merge_segment(
        clean_response({"characters": [{"name": "Alice", "description": "later text"}]}),
        checkpoint_pct=50,
    )

    assert len(state.characters) == 1
    assert state.characters[0]["first_pct"] == 10
    assert state.characters[0]["first_seq"] == 1
    assert state.characters[0]["description"] == "later text"


def test_alias_collision_merges():
    state = BookState()

    state.merge_segment(
        clean_response({"characters": [{"name": "FitzChivalry", "aliases": ["Fitz"]}]}),
        checkpoint_pct=10,
    )
    state.merge_segment(
        clean_response({"characters": [{"name": "Fitz", "description": "the Bastard"}]}),
        checkpoint_pct=30,
    )

    assert len(state.characters) == 1
    assert state.characters[0]["name"] == "FitzChivalry"
    assert state.characters[0]["description"] == "the Bastard"


def test_name_promotion():
    state = BookState()

    # Segment 1: AI already knows the epithet as an alias of the short name.
    state.merge_segment(
        clean_response({"characters": [{"name": "Kvothe", "aliases": ["Kvothe Kingkiller"]}]}),
        checkpoint_pct=10,
    )
    # Segment 2: AI now reports the fuller name as canonical.
    state.merge_segment(
        clean_response({"characters": [{"name": "Kvothe Kingkiller"}]}), checkpoint_pct=40
    )

    assert len(state.characters) == 1
    assert state.characters[0]["name"] == "Kvothe Kingkiller"
    assert "Kvothe" in state.characters[0]["aliases"]
    assert state.characters[0]["first_pct"] == 10  # promotion must not restamp


def test_location_alias_collision_fills_still_empty_field():
    state = BookState()

    state.merge_segment(
        clean_response({"locations": [{"name": "The Shire", "aliases": ["Hobbiton region"]}]}),
        checkpoint_pct=5,
    )
    state.merge_segment(
        clean_response({"locations": [{"name": "Hobbiton region", "importance": "home"}]}),
        checkpoint_pct=15,
    )

    assert len(state.locations) == 1
    assert state.locations[0]["name"] == "The Shire"
    assert state.locations[0]["importance"] == "home"  # filled: was still-empty
    assert state.locations[0]["first_pct"] == 5  # not restamped


def test_no_first_name_fuzzy_match():
    state = BookState()

    state.merge_segment(
        clean_response({"characters": [{"name": "Robert Baratheon"}, {"name": "Robert Arryn"}]}),
        checkpoint_pct=10,
    )

    assert len(state.characters) == 2
    names = {c["name"] for c in state.characters}
    assert names == {"Robert Baratheon", "Robert Arryn"}


def test_terms_accumulate_aliases():
    state = BookState()

    state.merge_segment(
        clean_response({"terms": [{"name": "Skinchanger", "aliases": ["Warg"]}]}),
        checkpoint_pct=10,
    )
    state.merge_segment(
        clean_response({"terms": [{"name": "Skinchanger", "definition": "a magic user"}]}),
        checkpoint_pct=40,
    )

    assert len(state.terms) == 1
    assert state.terms[0]["aliases"] == ["Warg"]
    assert state.terms[0]["definition"] == "a magic user"


def test_historical_figures_field_merge_split():
    state = BookState()

    state.merge_segment(
        clean_response(
            {
                "historical_figures": [
                    {"name": "Napoleon", "biography": "Corsican-born military leader"}
                ]
            }
        ),
        checkpoint_pct=10,
    )
    # Segment 2 omits biography but supplies the previously-empty importance_in_book.
    state.merge_segment(
        clean_response(
            {"historical_figures": [{"name": "Napoleon", "importance_in_book": "central to Act II"}]}
        ),
        checkpoint_pct=40,
    )

    assert len(state.historical_figures) == 1
    fig = state.historical_figures[0]
    assert fig["biography"] == "Corsican-born military leader"  # newest-non-empty: not blanked
    assert fig["importance_in_book"] == "central to Act II"  # fill-if-empty


def test_snapshot_is_deep_copy():
    state = BookState()
    state.merge_segment(
        clean_response({"characters": [{"name": "Alice", "aliases": ["Al"]}]}), checkpoint_pct=10
    )

    snap = state.snapshot()
    state.characters[0]["description"] = "mutated after snapshot"
    state.characters[0]["aliases"].append("mutated alias")

    assert snap["characters"][0]["description"] == ""
    assert snap["characters"][0]["aliases"] == ["Al"]


def test_sort_characters_chronological_terms_alpha():
    chars = [
        {"name": "Zed", "first_pct": 10, "first_seq": 1},
        {"name": "Amy", "first_pct": 50, "first_seq": 2},
    ]
    assert [c["name"] for c in sort_entity_list(chars, "character")] == ["Zed", "Amy"]

    terms = [{"name": "Zebra"}, {"name": "apple"}]
    assert [t["name"] for t in sort_entity_list(terms, "term")] == ["apple", "Zebra"]


def test_sort_historical_figures_by_role_weight():
    figures = [
        {"name": "Bit Player", "role": "background extra"},
        {"name": "The Great Leader", "role": "protagonist"},
        {"name": "Sidekick", "role": "secondary character"},
    ]

    ordered = [f["name"] for f in sort_entity_list(figures, "historical_figure")]

    assert ordered == ["The Great Leader", "Sidekick", "Bit Player"]


def test_non_narrative_timeline_events_dropped():
    state = BookState()
    cleaned = clean_response(
        {
            "timeline": [
                {"chapter": "Copyright", "event": "should be dropped"},
                {"chapter": "Chapter One", "event": "kept"},
            ]
        }
    )

    state.merge_segment(cleaned, checkpoint_pct=20)

    assert len(state.timeline) == 1
    assert state.timeline[0]["chapter"] == "Chapter One"
    assert state.timeline[0]["pct"] == 20


def test_timeline_drops_events_with_blank_chapter():
    # xray_fetch.lua:534 filtert Timeline-Ereignisse durch denselben Helper.
    state = BookState()

    state.merge_segment(
        clean_response(
            {
                "timeline": [
                    {"chapter": "", "event": "kapitellos"},
                    {"chapter": "Kapitel 1", "event": "echt"},
                    {"chapter": "Copyright", "event": "frontmatter"},
                ]
            }
        ),
        checkpoint_pct=10,
    )

    assert [ev["event"] for ev in state.timeline] == ["echt"]


def test_is_more_complete_name_unicode_word_boundary():
    # "Müller" as its own space-bounded word promotes correctly.
    assert is_more_complete_name("Doktor Müller", "Müller")
    # "Müller" immediately continued by more Unicode word chars ("übung") is
    # NOT a word-boundary match -- \w must treat "ü" as a word char (the
    # deliberate Unicode-vs-ASCII divergence), and the match sits in the
    # middle of the string so neither prefix nor suffix applies either.
    assert not is_more_complete_name("the Müllerübung club", "Müller")
    assert not is_more_complete_name("Al", "Alice")  # shorter never promotes


def test_clean_location_name_falls_back_to_place_and_lugar():
    # AIHelper:validateAndCleanData (xray_aihelper.lua, ca. line 2052) -- l.name or l.place or l.Lugar
    assert clean_response({"locations": [{"place": "Palermo"}]})["locations"][0]["name"] == "Palermo"
    assert clean_response({"locations": [{"lugar": "Vesuv"}]})["locations"][0]["name"] == "Vesuv"


def test_clean_location_never_uses_character_name_chain():
    # aus Task 2, Platzhalter ist jetzt lokalisiert
    cleaned = clean_response({"locations": [{"full_formal_name": "Lord Farquaad"}]})
    assert cleaned["locations"][0]["name"] == "Unknown Place"


def test_clean_location_description_and_importance_fallbacks():
    # AIHelper:validateAndCleanData (xray_aihelper.lua, ca. lines 2053-2054)
    loc = clean_response({"locations": [{"name": "X", "desc": "d", "significance": "s"}]})["locations"][0]
    assert loc["description"] == "d"
    assert loc["importance"] == "s"
    loc2 = clean_response({"locations": [{"name": "X", "short_desc": "sd"}]})["locations"][0]
    assert loc2["description"] == "sd"


def test_clean_character_description_and_occupation_fallbacks():
    # AIHelper:validateAndCleanData (xray_aihelper.lua, ca. lines 2023 and 2025)
    c = clean_response({"characters": [{"name": "A", "bio": "b", "job": "j"}]})["characters"][0]
    assert c["description"] == "b"
    assert c["occupation"] == "j"
    assert clean_response({"characters": [{"name": "A", "history": "h"}]})["characters"][0]["description"] == "h"
    assert clean_response({"characters": [{"name": "A", "desc": "d"}]})["characters"][0]["description"] == "d"


def test_clean_historical_figure_fallbacks():
    # AIHelper:validateAndCleanData (xray_aihelper.lua, ca. lines 2038-2041)
    h = clean_response(
        {
            "historical_figures": [
                {
                    "name": "N",
                    "description": "d",
                    "historical_role": "r",
                    "significance": "s",
                    "context": "c",
                }
            ]
        }
    )["historical_figures"][0]
    assert h["biography"] == "d"
    assert h["role"] == "r"
    assert h["importance_in_book"] == "s"
    assert h["context_in_book"] == "c"


_CHAIN_PRIORITY_CASES = [
    pytest.param(_CHAR_NAME_KEYS, ["name", "full_formal_name", "full_name", "formal_name"], id="_CHAR_NAME_KEYS"),
    pytest.param(_CHAR_DESC_KEYS, ["description", "bio", "history", "desc"], id="_CHAR_DESC_KEYS"),
    pytest.param(_CHAR_OCCUPATION_KEYS, ["occupation", "job"], id="_CHAR_OCCUPATION_KEYS"),
    pytest.param(_LOC_NAME_KEYS, ["name", "place", "lugar"], id="_LOC_NAME_KEYS"),
    pytest.param(_LOC_DESC_KEYS, ["description", "desc", "short_desc"], id="_LOC_DESC_KEYS"),
    pytest.param(_LOC_IMPORTANCE_KEYS, ["importance", "significance"], id="_LOC_IMPORTANCE_KEYS"),
    pytest.param(_HIST_BIO_KEYS, ["biography", "bio", "description"], id="_HIST_BIO_KEYS"),
    pytest.param(_HIST_ROLE_KEYS, ["role", "historical_role"], id="_HIST_ROLE_KEYS"),
    pytest.param(_HIST_IMPORTANCE_KEYS, ["importance_in_book", "significance"], id="_HIST_IMPORTANCE_KEYS"),
    pytest.param(_HIST_CONTEXT_KEYS, ["context_in_book", "context"], id="_HIST_CONTEXT_KEYS"),
]


@pytest.mark.parametrize("keys, expected_order", _CHAIN_PRIORITY_CASES)
def test_first_nonempty_chain_priority(keys, expected_order):
    # Every fallback test above sets exactly one alternative key, so an
    # accidental reorder in merge.py (e.g. swapping ("description", "bio",
    # ...) to ("bio", "description", ...)) would go unnoticed. `expected_order`
    # is written out independently here rather than derived from `keys`, so
    # a reorder of the real chain actually flips which value wins below.
    d = {key: f"value-from-{key}" for key in expected_order}
    for winner in expected_order[:-1]:
        assert _first_nonempty(d, keys, "MUST-NOT-WIN") == f"value-from-{winner}"
        del d[winner]


def test_fallback_strings_are_localized():
    # prompts/de.lua:361-364, prompts/en.lua:322-325
    assert fallback_strings("de")["unnamed_character"] == "Unbenannter Charakter"
    assert fallback_strings("en")["unnamed_character"] == "Unnamed Character"
    # Unbekannte Sprache faellt auf Englisch zurueck, nie auf KeyError.
    assert fallback_strings("fr")["unnamed_character"] == "Unnamed Character"


def test_clean_response_localizes_name_placeholders():
    de = clean_response({"characters": [{"role": "x"}]}, language="de")
    assert de["characters"][0]["name"] == "Unbenannter Charakter"

    de_loc = clean_response({"locations": [{"description": "d"}]}, language="de")
    assert de_loc["locations"][0]["name"] == "Unbekannter Ort"

    de_hist = clean_response({"historical_figures": [{"biography": "b"}]}, language="de")
    assert de_hist["historical_figures"][0]["name"] == "Unbenannte Person"


def test_clean_response_leaves_content_fields_empty():
    # Bewusste Divergenz zu Lua: der Viewer blendet leere Felder aus
    # (xray_ui.lua:190,214), ein Platzhalter waere sichtbares Rauschen.
    c = clean_response({"characters": [{"name": "A"}]}, language="de")["characters"][0]
    assert c["role"] == ""
    assert c["description"] == ""

    h = clean_response({"historical_figures": [{"name": "H"}]}, language="de")["historical_figures"][0]
    assert h["biography"] == ""
    assert h["importance_in_book"] == ""
    assert h["context_in_book"] == ""


def test_empty_field_is_still_fillable_by_a_later_segment():
    # Der Kern der Entscheidung: leere Felder lassen Luecken zuwachsen.
    state = BookState()
    state.merge_segment(clean_response({"characters": [{"name": "Alice"}]}), 10)
    state.merge_segment(
        clean_response({"characters": [{"name": "Alice", "occupation": "Forscherin"}]}, "de"), 50
    )

    assert state.characters[0]["occupation"] == "Forscherin"
