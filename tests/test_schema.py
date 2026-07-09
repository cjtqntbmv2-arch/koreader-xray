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
