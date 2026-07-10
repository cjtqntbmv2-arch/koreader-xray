from xray_core.merge import BookState, clean_response, is_more_complete_name, sort_entity_list


def test_clean_keeps_nameless_with_placeholder_and_truncates_role():
    raw = {
        "characters": [{"role": "x" * 50, "description": "a mystery"}],
        "locations": [{"description": "somewhere dark"}],
    }

    cleaned = clean_response(raw)

    assert cleaned["characters"][0]["name"] == "Unnamed character"
    assert cleaned["characters"][0]["role"] == "x" * 40
    assert cleaned["locations"][0]["name"] == "Unnamed location"


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
    # xray_aihelper.lua:2046 -- l.name or l.place or l.Lugar
    assert clean_response({"locations": [{"place": "Palermo"}]})["locations"][0]["name"] == "Palermo"
    assert clean_response({"locations": [{"lugar": "Vesuv"}]})["locations"][0]["name"] == "Vesuv"


def test_clean_location_never_uses_character_name_chain():
    # Regression: der Ort nutzte die Charakter-Kette (full_formal_name ...).
    # Ein Ort ohne name/place/lugar ist namenlos, egal was sonst dransteht.
    cleaned = clean_response({"locations": [{"full_formal_name": "Lord Farquaad"}]})
    assert cleaned["locations"][0]["name"] == "Unnamed location"


def test_clean_location_description_and_importance_fallbacks():
    # xray_aihelper.lua:2047-2048
    loc = clean_response({"locations": [{"name": "X", "desc": "d", "significance": "s"}]})["locations"][0]
    assert loc["description"] == "d"
    assert loc["importance"] == "s"
    loc2 = clean_response({"locations": [{"name": "X", "short_desc": "sd"}]})["locations"][0]
    assert loc2["description"] == "sd"


def test_clean_character_description_and_occupation_fallbacks():
    # xray_aihelper.lua:2017,2019
    c = clean_response({"characters": [{"name": "A", "bio": "b", "job": "j"}]})["characters"][0]
    assert c["description"] == "b"
    assert c["occupation"] == "j"
    assert clean_response({"characters": [{"name": "A", "history": "h"}]})["characters"][0]["description"] == "h"
    assert clean_response({"characters": [{"name": "A", "desc": "d"}]})["characters"][0]["description"] == "d"


def test_clean_historical_figure_fallbacks():
    # xray_aihelper.lua:2031-2035
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
