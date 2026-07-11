from xray_core.schema import validate, SCHEMA_VERSION


def test_minimal_valid_doc(minimal_doc):
    assert validate(minimal_doc) == []


def test_missing_snippet_anchor(minimal_doc):
    del minimal_doc["checkpoints"][0]["snippet_anchor"]
    assert any("snippet_anchor" in p for p in validate(minimal_doc))


def test_empty_snippet_anchor_is_valid(minimal_doc):
    """make_snippet_anchor() legitimately returns "" for a textless zone
    (e.g. image-only front matter); the KEY must still be required (see
    test_missing_snippet_anchor above), but an empty string is not an error
    -- the device falls back to chapter/percent anchors."""
    minimal_doc["checkpoints"][0]["snippet_anchor"] = ""
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


def test_chapter_anchor_type_is_checked(minimal_doc):
    minimal_doc["checkpoints"][0]["chapter_anchor"] = "Kapitel 12"
    assert any("chapter_anchor" in p for p in validate(minimal_doc))

    minimal_doc["checkpoints"][0]["chapter_anchor"] = None
    assert validate(minimal_doc) == []  # null ist erlaubt


def test_chapter_anchor_missing_toc_title_is_rejected(minimal_doc):
    minimal_doc["checkpoints"][0]["chapter_anchor"] = {"spine_index": 5}
    assert any("toc_title" in p for p in validate(minimal_doc))


def test_chapter_anchor_toc_title_wrong_type_is_rejected(minimal_doc):
    minimal_doc["checkpoints"][0]["chapter_anchor"] = {"toc_title": 42, "spine_index": 5}
    assert any("toc_title" in p for p in validate(minimal_doc))


def test_chapter_anchor_negative_spine_index_is_rejected(minimal_doc):
    minimal_doc["checkpoints"][0]["chapter_anchor"] = {"toc_title": "K1", "spine_index": -1}
    assert any("spine_index" in p for p in validate(minimal_doc))


def test_chapter_anchor_bool_spine_index_is_rejected(minimal_doc):
    """bool is a subclass of int in Python; spine_index=True must not slip
    through as 1 -- _is_strict_int() exists to catch exactly this."""
    minimal_doc["checkpoints"][0]["chapter_anchor"] = {"toc_title": "K1", "spine_index": True}
    assert any("spine_index" in p for p in validate(minimal_doc))


def test_chapter_anchor_missing_spine_index_is_rejected(minimal_doc):
    minimal_doc["checkpoints"][0]["chapter_anchor"] = {"toc_title": "K1"}
    assert any("spine_index" in p for p in validate(minimal_doc))


def test_chapter_anchor_spine_index_zero_is_accepted(minimal_doc):
    """spine_index is an index, not a percent -- 0 is a valid first spine
    item and must validate cleanly (unlike timeline pct=0, see
    test_timeline_pct_zero_is_rejected)."""
    minimal_doc["checkpoints"][0]["chapter_anchor"] = {"toc_title": "K1", "spine_index": 0}
    assert validate(minimal_doc) == []


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
