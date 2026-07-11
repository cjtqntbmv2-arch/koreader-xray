import json
import os

import pytest

from epub_fixture import build_epub  # NOT tests.epub_fixture
from xray_core.schema import validate
from tools.claude_xray_plan import write_plan
from tools.claude_xray_assemble import assemble


def _prepare(tmp_path):
    body = "<p>" + " ".join(f"AliceBob{i}" for i in range(40000)) + "</p>"
    epub = build_epub(tmp_path, chapters=[("Chapter", body)])  # title/authors/language are fixture constants
    workdir = str(tmp_path / "work")
    manifest_path = write_plan(epub, "detailed", workdir)
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    # Simulate subagents: write a raw.json per chunk.
    for ch in manifest["chunks"]:
        raw = {"book_type": "fiction",
               "characters": [{"name": "Alice", "description": "A person."}],
               "locations": [], "historical_figures": [], "terms": [], "timeline": []}
        with open(os.path.join(workdir, ch["raw_file"]), "w", encoding="utf-8") as f:
            json.dump(raw, f)
    return epub, workdir


def test_assemble_produces_valid_doc_and_deliverables(tmp_path):
    epub, workdir = _prepare(tmp_path)
    out = str(tmp_path / "out")
    src_before = open(epub, "rb").read()

    doc = assemble(epub, workdir, out)

    assert validate(doc) == []
    assert any(c["name"] == "Alice" for c in doc["checkpoints"][-1]["snapshot"]["characters"])
    base = os.path.basename(epub)
    assert os.path.exists(os.path.join(out, base + ".xray.json"))   # companion (append-form)
    assert os.path.exists(os.path.join(out, base))                  # embedded copy (same name)
    assert os.path.exists(os.path.join(out, "xray.json"))           # raw
    # source EPUB untouched
    assert open(epub, "rb").read() == src_before
    # companion == raw doc bytes
    assert open(os.path.join(out, base + ".xray.json"), encoding="utf-8").read() == \
           open(os.path.join(out, "xray.json"), encoding="utf-8").read()


def test_assemble_fails_loud_on_missing_chunk(tmp_path):
    epub, workdir = _prepare(tmp_path)
    # remove one raw.json
    manifest = json.load(open(os.path.join(workdir, "manifest.json"), encoding="utf-8"))
    os.remove(os.path.join(workdir, manifest["chunks"][0]["raw_file"]))
    with pytest.raises(SystemExit) as ei:
        assemble(epub, workdir, str(tmp_path / "out2"))
    assert "missing" in str(ei.value)


def test_assemble_never_hits_network_at_detailed(tmp_path):
    # A complete cache at detail=detailed must NOT invoke the stub client
    # (guards the mandatory enrich=False). If enrich ran, _NoNetworkClient
    # would raise RuntimeError and this test would error.
    epub, workdir = _prepare(tmp_path)  # detail=detailed
    doc = assemble(epub, workdir, str(tmp_path / "out3"))
    assert doc["complete"] is True


def test_assemble_is_reproducible(tmp_path):
    epub, workdir = _prepare(tmp_path)
    d1 = assemble(epub, workdir, str(tmp_path / "a"))
    d2 = assemble(epub, workdir, str(tmp_path / "b"))
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)
    assert open(tmp_path / "a" / "xray.json").read() == open(tmp_path / "b" / "xray.json").read()
