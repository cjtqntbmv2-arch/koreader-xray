from xray_core.prompts import (
    CONTEXT_FOOTER_DE,
    CONTEXT_FOOTER_EN,
    DETAIL_CAPS,
    build_glean_prompt,
    build_prompt,
)

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


def test_de_timeline_guidance_is_german():
    """Beobachtet 2026-07-11 ("Die Herren von Winterfell", de): ~9 von 65
    `timeline[].event` kamen englisch zurueck, waehrend jede description
    deutsch war. Ursache: die Laengen-Anweisung fuer genau dieses eine Feld
    wurde sprachunabhaengig (englisch) in den deutschen Prompt gespritzt."""
    for detail in ("normal", "detailed"):
        _, user = build_prompt("de", detail, TITLE, AUTHOR, PERCENT, SEGMENT)

        assert "Schreiben Sie" in user
        assert "Write a" not in user
        assert "Write between" not in user


def test_de_prompt_demands_german_values():
    _, user = build_prompt("de", "normal", TITLE, AUTHOR, PERCENT, SEGMENT)

    assert "Sprache" in user
    assert "auf Deutsch" in user


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


def test_pretraining_guard_present_de():
    system, _ = build_prompt("de", "normal", TITLE, AUTHOR, PERCENT, SEGMENT)

    assert "TRAININGSWISSEN" in system
    assert "AUSSCHLIESSLICH Informationen aus dem bereitgestellten Text" in system


def test_segment_is_prefix_for_caching():
    """Chunk-first: the book text sits at the very front so the extract and
    gleaning calls share a byte-identical [system + chunk] prefix -> Gemini
    implicit-cache hit on the second call. Instructions follow the text."""
    prefix = "BOOK TEXT CONTEXT:\n" + SEGMENT + "\n\n---\n\n"
    _, extract = build_prompt("en", "normal", TITLE, AUTHOR, PERCENT, SEGMENT)
    _, glean = build_glean_prompt("en", "normal", TITLE, AUTHOR, PERCENT, SEGMENT, ["X"])
    assert extract.startswith(prefix)
    assert glean.startswith(prefix)


def test_no_inprompt_json_schema_block():
    """The bulky in-prompt JSON schema is gone -- structure now comes from the
    native responseSchema, saving ~35 lines of tokens per extract call."""
    for lang in ("en", "de"):
        _, user = build_prompt(lang, "normal", TITLE, AUTHOR, PERCENT, SEGMENT)
        assert "REQUIRED JSON FORMAT" not in user
        assert "JSON-SCHEMA" not in user


def test_no_phantom_chapter_samples_block():
    """The prompt must not reference a "CHAPTER SAMPLES" block or a "20k"
    recent-window: build_prompt only ever appends one real block
    (BOOK TEXT CONTEXT = the whole segment). Referencing a block that is
    never sent breaks the timeline algorithm and confuses extraction."""
    for lang in ("en", "de"):
        _, user = build_prompt(lang, "normal", TITLE, AUTHOR, PERCENT, SEGMENT)
        assert "CHAPTER SAMPLES" not in user
        assert "20k" not in user and "20.000" not in user
        assert "BOOK TEXT CONTEXT" in user


def test_no_top10_omnibus_cap():
    """The ANTI-TRUNCATION / omnibus 'reduce to top 10 characters' guidance is
    a recall killer and is inapplicable to the always-segment-scoped desktop
    pipeline -- it must not appear in the assembled prompt."""
    for lang in ("en", "de"):
        _, user = build_prompt(lang, "normal", TITLE, AUTHOR, PERCENT, SEGMENT)
        assert "top 10" not in user.lower()
        assert "ANTI-TRUNCATION" not in user
        assert "omnibus" not in user.lower()


def test_context_footer_is_last_for_en():
    """EN must mirror DE: a post-data instruction after the book text
    (official Gemini guidance: put instructions after the data context)."""
    _, user = build_prompt("en", "normal", TITLE, AUTHOR, PERCENT, SEGMENT)
    footer = CONTEXT_FOOTER_EN.strip()

    assert footer  # non-empty
    assert user.rstrip().endswith(footer)
    assert user.index(SEGMENT) < user.index(footer)


def test_context_footer_is_last_for_de():
    """context_footer must trail segment_text -- it tells the model to act on
    "the context above", so the book text has to actually be above it."""
    _, user = build_prompt("de", "normal", TITLE, AUTHOR, PERCENT, SEGMENT)
    footer = CONTEXT_FOOTER_DE.strip()

    assert user.rstrip().endswith(footer)
    assert user.index(SEGMENT) < user.index(footer)


def test_glean_prompt_lists_found_and_asks_additional():
    """The gleaning pass resends the segment plus already-found names and asks
    ONLY for entities not already in that list -- the research's top recall
    booster. The found names and the segment must both be present."""
    _, user = build_glean_prompt(
        "en", "normal", TITLE, AUTHOR, PERCENT, SEGMENT,
        found_names=["Jonathan Harker", "Count Dracula"],
    )
    assert "Jonathan Harker" in user
    assert "Count Dracula" in user
    assert SEGMENT in user
    lowered = user.lower()
    assert "additional" in lowered or "not already" in lowered or "missed" in lowered


def test_glean_prompt_is_language_aware():
    _, user = build_glean_prompt(
        "de", "normal", TITLE, AUTHOR, PERCENT, SEGMENT, found_names=["Mina"],
    )
    assert "Mina" in user
    assert SEGMENT in user
    # German instruction, not English boilerplate.
    assert "bereits" in user.lower() or "zusätzlich" in user.lower()


def test_glean_prompt_empty_found_names():
    """With no prior names the gleaning prompt is still valid (first chunk of a
    checkpoint could legitimately have contributed nothing yet)."""
    _, user = build_glean_prompt(
        "en", "normal", TITLE, AUTHOR, PERCENT, SEGMENT, found_names=[],
    )
    assert SEGMENT in user
    for leftover in ("{MAX_", "%s", "%d"):
        assert leftover not in user


def test_enrich_prompt_is_slim():
    """Enrich only patches descriptions of known characters -- it must not ship
    the full comprehensive spec (timeline loop, locations, terms), which the
    caller discards. Keeps the MERGE marker and the prior name."""
    prior = [("Mina Harker", "A schoolmistress.")]
    _, enrich_user = build_prompt(
        "en", "normal", TITLE, AUTHOR, PERCENT, SEGMENT,
        prior_names=prior, mode="enrich",
    )
    assert "MERGE MODE INSTRUCTIONS" in enrich_user
    assert "Mina Harker" in enrich_user
    # Slim: the full timeline algorithm and terms spec are gone.
    assert "ALGORITHM FOR TIMELINE" not in enrich_user
    assert "world-building" not in enrich_user


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
