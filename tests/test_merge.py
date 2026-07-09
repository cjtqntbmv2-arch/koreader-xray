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


def test_is_more_complete_name_unicode_word_boundary():
    # "Müller" as its own space-bounded word promotes correctly.
    assert is_more_complete_name("Doktor Müller", "Müller")
    # "Müller" immediately continued by more Unicode word chars ("übung") is
    # NOT a word-boundary match -- \w must treat "ü" as a word char (the
    # deliberate Unicode-vs-ASCII divergence), and the match sits in the
    # middle of the string so neither prefix nor suffix applies either.
    assert not is_more_complete_name("the Müllerübung club", "Müller")
    assert not is_more_complete_name("Al", "Alice")  # shorter never promotes
