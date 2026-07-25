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
