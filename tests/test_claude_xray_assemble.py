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
    assert os.path.exists(os.path.join(out, base + ".xray.json"))   # companion (device-side name)
    assert os.path.exists(os.path.join(out, "xray.json"))           # what calibre gets
    # No EPUB is written any more: embedding is the calibre plugin's job, and
    # it is the only path with the partial_md5/text_hash checks.
    assert not os.path.exists(os.path.join(out, base))
    # source EPUB untouched
    assert open(epub, "rb").read() == src_before
    # both deliverables are the same bytes
    assert open(os.path.join(out, base + ".xray.json"), encoding="utf-8").read() == \
           open(os.path.join(out, "xray.json"), encoding="utf-8").read()


def test_assemble_may_write_beside_the_source_book(tmp_path):
    """Writing --out into the book's own directory used to truncate the source
    (the embedded copy shared its name). With only JSON deliverables left, it
    is safe -- and it is how you deliver the companion file over USB."""
    epub, workdir = _prepare(tmp_path)
    src_before = open(epub, "rb").read()
    beside = os.path.dirname(epub)

    doc = assemble(epub, workdir, beside)

    assert validate(doc) == []
    assert open(epub, "rb").read() == src_before
    assert os.path.exists(os.path.join(beside, os.path.basename(epub) + ".xray.json"))


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


def test_assemble_detects_text_hash_drift(tmp_path):
    # The manifest records book.text_hash at plan time. If the EPUB changes
    # between planning and assembling, assemble must refuse rather than
    # silently reuse stale chunk extractions against the new text.
    epub, workdir = _prepare(tmp_path)
    manifest_path = os.path.join(workdir, "manifest.json")
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    manifest["book"]["text_hash"] = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f)

    with pytest.raises(SystemExit) as ei:
        assemble(epub, workdir, str(tmp_path / "out_hash_drift"))
    assert "hash" in str(ei.value).lower()


def test_assemble_localizes_nameless_placeholder_by_book_language(tmp_path):
    # A nameless entity in a non-English book must get the book-language
    # placeholder, not the English default -- the assembler must forward
    # book.language to clean_response. The resume re-clean cannot repair this
    # later (it only localizes a name that is STILL empty).
    body = "<p>" + " ".join(f"Wort{i}" for i in range(40000)) + "</p>"
    epub = build_epub(tmp_path, chapters=[("Kapitel", body)], language="de")
    workdir = str(tmp_path / "work")
    manifest = json.load(open(write_plan(epub, "detailed", workdir), encoding="utf-8"))
    for ch in manifest["chunks"]:
        raw = {"book_type": "fiction",
               "characters": [{"description": "eine namenlose Gestalt"}],  # no name key
               "locations": [], "historical_figures": [], "terms": [], "timeline": []}
        with open(os.path.join(workdir, ch["raw_file"]), "w", encoding="utf-8") as f:
            json.dump(raw, f)

    doc = assemble(epub, workdir, str(tmp_path / "out_de"))
    names = {c["name"] for c in doc["checkpoints"][-1]["snapshot"]["characters"]}
    assert "Unbenannter Charakter" in names
    assert "Unnamed Character" not in names


def test_assemble_never_writes_an_epub(tmp_path):
    """The embed modes moved to the calibre plugin, which checks partial_md5 and
    text_hash before it replaces anything. If an EPUB ever reappeared here, that
    unchecked second path would be back."""
    epub, workdir = _prepare(tmp_path)
    out = str(tmp_path / "out_noepub")

    assemble(epub, workdir, out)

    assert sorted(os.listdir(out)) == sorted(
        ["xray.json", os.path.basename(epub) + ".xray.json"])
