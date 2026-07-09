from xray_core.prompts import DETAIL_CAPS, build_prompt

TITLE, AUTHOR, PERCENT = "Dracula", "Bram Stoker", 45
SEGMENT = "Jonathan Harker arrives at the castle and meets Count Dracula."


def test_no_unresolved_tags():
    system, user = build_prompt("en", "normal", TITLE, AUTHOR, PERCENT, SEGMENT)

    for leftover in ("{MAX_", "%s", "%d"):
        assert leftover not in system, f"{leftover!r} leaked into system_instruction"
        assert leftover not in user, f"{leftover!r} leaked into user_prompt"


def test_literal_percent_escape():
    """"%d%%" (e.g. "Reading Progress: %d%%") must render as a plain "<n>%":
    the second "%" is a non-consuming literal-percent escape, not a second
    arg slot -- a naive str % args miscount would crash or misalign here."""
    _, user = build_prompt("en", "normal", TITLE, AUTHOR, PERCENT, SEGMENT)

    assert "Reading Progress: 45%" in user
    assert "45%%" not in user


def test_detail_level_changes_caps():
    _, normal = build_prompt("en", "normal", TITLE, AUTHOR, PERCENT, SEGMENT)
    _, detailed = build_prompt("en", "detailed", TITLE, AUTHOR, PERCENT, SEGMENT)

    assert "200" in normal
    assert "500" in detailed
    assert "500" not in normal


def test_de_prompt_is_german():
    _, user = build_prompt("de", "normal", TITLE, AUTHOR, PERCENT, SEGMENT)

    assert "Spoiler-Grenze" in user
    assert f"{PERCENT}%" in user


def test_segment_addendum_present():
    _, user = build_prompt("en", "normal", TITLE, AUTHOR, PERCENT, SEGMENT)

    assert "SEGMENT COMPLETENESS" in user


def test_name_rules_present():
    _, user = build_prompt("en", "normal", TITLE, AUTHOR, PERCENT, SEGMENT)

    assert "NAME DISAMBIGUATION" in user
    assert "CHARACTER COMPLETENESS" in user


def test_pretraining_guard_present():
    system, _ = build_prompt("en", "normal", TITLE, AUTHOR, PERCENT, SEGMENT)

    assert "ONLY information present in the provided text" in system


def test_enrich_mode_uses_prior():
    prior = [("Mina Harker", "A schoolmistress and Jonathan's fiancee.")]

    _, extract_user = build_prompt(
        "en", "normal", TITLE, AUTHOR, PERCENT, SEGMENT, mode="extract"
    )
    _, enrich_user = build_prompt(
        "en", "normal", TITLE, AUTHOR, PERCENT, SEGMENT,
        prior_names=prior, mode="enrich",
    )

    assert "Mina Harker" not in extract_user
    assert "Mina Harker" in enrich_user
    assert "A schoolmistress and Jonathan's fiancee." in enrich_user


def test_detail_caps_matches_contract():
    """DETAIL_CAPS is a public produced interface (Task 5/7 read it directly) --
    pin its exact shape from the brief's Global Constraints."""
    assert DETAIL_CAPS == {
        "normal":   {"char": 200, "loc": 100, "tl": 80,  "hist": 100, "term": 100},
        "detailed": {"char": 500, "loc": 300, "tl": 200, "hist": 400, "term": 300},
    }
