# tests/test_claude_xray_e2e.py
import json
import os

from epub_fixture import build_epub  # NOT tests.epub_fixture
from xray_core.schema import validate
from tools.claude_xray_plan import write_plan
from tools.claude_xray_assemble import assemble


def test_plan_extract_assemble_end_to_end(tmp_path):
    body = "<p>" + " ".join(
        f"Chapter{i} Alice meets Bob and Carol in Eldras filler filler filler" for i in range(6000)
    ) + "</p>"
    epub = build_epub(tmp_path, chapters=[("Chapter", body)])  # title="Test Book" (fixture constant)
    workdir = str(tmp_path / "work")
    manifest = json.load(open(write_plan(epub, "detailed", workdir), encoding="utf-8"))

    # Stand-in for real subagents: deterministic canned extraction per chunk.
    for ch in manifest["chunks"]:
        with open(os.path.join(workdir, ch["raw_file"]), "w", encoding="utf-8") as f:
            json.dump({"book_type": "fiction",
                       "characters": [{"name": "Alice", "description": "Protagonist."},
                                      {"name": "Bob", "description": "Friend."}],
                       "locations": [{"name": "Eldras", "description": "A town."}],
                       "historical_figures": [], "terms": [], "timeline": []}, f)

    doc = assemble(epub, workdir, str(tmp_path / "out"))
    assert validate(doc) == []
    names = {c["name"] for c in doc["checkpoints"][-1]["snapshot"]["characters"]}
    assert {"Alice", "Bob"} <= names
    # D4: no character stamped past its checkpoint
    for cp in doc["checkpoints"]:
        for c in cp["snapshot"]["characters"]:
            assert c["first_pct"] <= cp["percent"]
