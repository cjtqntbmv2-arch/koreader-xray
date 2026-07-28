from xray_core.schema import validate, SCHEMA_VERSION


def test_minimal_valid_doc(minimal_doc):
    assert validate(minimal_doc) == []


def test_anchor_fields_are_ignored_not_rejected(minimal_doc):
    """schema v2 dropped snippet_anchor/chapter_anchor. A leftover field from
    a v1-era document is simply not looked at -- validate() rejects on the
    declared schema_version, which is the check that actually matters."""
    minimal_doc["checkpoints"][0]["snippet_anchor"] = "irgendein Resttext"
    minimal_doc["checkpoints"][0]["chapter_anchor"] = {"toc_title": "K1", "spine_index": 0}
    assert validate(minimal_doc) == []


def test_wrong_schema_version(minimal_doc):
    minimal_doc["schema_version"] = 99
    assert any("schema_version" in p for p in validate(minimal_doc))


def test_checkpoints_must_ascend(minimal_doc):
    cp = dict(minimal_doc["checkpoints"][0])
    cp["percent"] = 5
    minimal_doc["checkpoints"].append(cp)
    assert any("ascend" in p for p in validate(minimal_doc))


def test_valid_doc_still_passes(minimal_doc):
    assert validate(minimal_doc) == []


def test_timeline_entries_are_validated(minimal_doc):
    minimal_doc["timeline"] = [{"chapter": "K1", "event": "e", "pct": 150}]
    assert any("timeline[0].pct" in p for p in validate(minimal_doc))

    minimal_doc["timeline"] = ["kein objekt"]
    assert any("timeline[0]" in p for p in validate(minimal_doc))

    minimal_doc["timeline"] = [{"chapter": "K1", "event": "e"}]
    assert any("pct" in p for p in validate(minimal_doc))


def test_timeline_pct_zero_is_rejected(minimal_doc):
    """pct=0 must be rejected, not just out-of-range values: in the KOReader
    importer, `tonumber(0)` is truthy in Lua, so pctToPage(0, ...) runs and
    clamps to page 1 -- showing the event from checkpoint 1 onward instead
    of hiding it like a missing pct would (xray_import.lua)."""
    minimal_doc["timeline"] = [{"chapter": "K1", "event": "e", "pct": 0}]
    assert any("timeline[0].pct" in p for p in validate(minimal_doc))


def test_authors_must_be_strings(minimal_doc):
    minimal_doc["book_fingerprint"]["authors"] = ["ok", 42]
    assert any("authors[1]" in p for p in validate(minimal_doc))


def test_negative_first_pct_and_seq_are_rejected(minimal_doc):
    minimal_doc["checkpoints"][0]["snapshot"]["characters"][0]["first_pct"] = -1
    assert any("first_pct" in p for p in validate(minimal_doc))

    minimal_doc["checkpoints"][0]["snapshot"]["characters"][0]["first_pct"] = 12
    minimal_doc["checkpoints"][0]["snapshot"]["characters"][0]["first_seq"] = 0
    assert any("first_seq" in p for p in validate(minimal_doc))


def test_duplicate_names_in_a_snapshot_list_are_rejected(minimal_doc):
    chars = minimal_doc["checkpoints"][0]["snapshot"]["characters"]
    chars.append(dict(chars[0], first_seq=2))
    assert any("duplicate" in p.lower() for p in validate(minimal_doc))


def test_duplicate_names_are_case_insensitive(minimal_doc):
    chars = minimal_doc["checkpoints"][0]["snapshot"]["characters"]
    chars.append(dict(chars[0], name="jane doe", first_seq=2))
    assert any("duplicate" in p.lower() for p in validate(minimal_doc))


def test_empty_names_never_collide(minimal_doc):
    """Mirrors BookState._merge (xray_core/merge.py): nameless entries never
    collide there ("nameless entries never collide", xray_data.lua:232-234),
    so the validator must not flag multiple empty names as duplicates."""
    minimal_doc["checkpoints"][0]["snapshot"]["terms"] = [
        {"name": ""},
        {"name": ""},
    ]
    assert not any("duplicate" in p.lower() for p in validate(minimal_doc))


# ---------------------------------------------------------------------------
# relations (ego net, feature B) -- shape only. That both endpoints resolve to
# a real figure is enforced constructively by the fold in
# tools/claude_xray_relations.py, never here: generate_xray raises on any
# validation problem *after* the whole extraction budget is spent, so a single
# bad edge would cost the entire run (design, "D4 gilt konstruktiv").
# ---------------------------------------------------------------------------


def test_wellformed_relations_are_accepted(minimal_doc):
    """The positive case is load-bearing, not decoration: without it a rule
    that rejects every document passes the whole negative battery. Measured --
    a `reject_everything` mutant ran 10/10 green against the acceptance list
    while this case was missing."""
    minimal_doc["relations"] = [
        {"from": "Jane Doe", "to": "John Doe", "label": "sister"},
        {"from": "John Doe", "to": "Jane Doe", "label": "brother"},
    ]
    assert validate(minimal_doc) == []


def test_relations_must_be_a_list(minimal_doc):
    minimal_doc["relations"] = "x"
    assert validate(minimal_doc) == ["relations must be a list"]


def test_relation_entry_must_be_an_object(minimal_doc):
    minimal_doc["relations"] = ["Jane Doe -> John Doe"]
    assert validate(minimal_doc) == ["relations[0] must be an object"]


def test_relation_requires_every_field(minimal_doc):
    minimal_doc["relations"] = [{"from": "Jane Doe", "label": "sister"}]
    assert validate(minimal_doc) == ["relations[0].to must be a non-empty string"]


def test_relation_fields_must_be_non_empty_strings(minimal_doc):
    minimal_doc["relations"] = [{"from": "Jane Doe", "to": "John Doe", "label": 12345}]
    assert validate(minimal_doc) == ["relations[0].label must be a non-empty string"]

    minimal_doc["relations"] = [{"from": "Jane Doe", "to": "John Doe", "label": ""}]
    assert validate(minimal_doc) == ["relations[0].label must be a non-empty string"]


def test_relation_fields_of_pure_whitespace_are_rejected(minimal_doc):
    """Same reasoning as _str/_first_nonempty in xray_core/merge.py, which
    strip and treat a then-empty string as a missing field (project CLAUDE.md,
    "bewusste Divergenzen vom Lua"): bool("   ") is true in Python, so without
    stripping a whitespace-only name passes every truthiness check and then
    resolves to no figure at all on the device."""
    minimal_doc["relations"] = [{"from": "   ", "to": "John Doe", "label": "brother"}]
    assert validate(minimal_doc) == ["relations[0].from must be a non-empty string"]


def test_absent_relations_are_not_required(minimal_doc):
    """Feature-presence gating, not version gating: a document generated
    before this feature existed stays valid (design, "Kein Schema-Bump")."""
    assert "relations" not in minimal_doc
    assert validate(minimal_doc) == []
