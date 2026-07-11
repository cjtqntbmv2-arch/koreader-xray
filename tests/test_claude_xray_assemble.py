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


def test_assemble_refuses_out_dir_matching_source_epub_dir(tmp_path):
    # --out resolving to the source EPUB's own directory would make the
    # embedded-copy write path collide with the source path, truncating it
    # (embed_xray opens epub_path for reading and out_path for writing --
    # same file means the write side clobbers the read side mid-copy).
    epub, workdir = _prepare(tmp_path)
    src_before = open(epub, "rb").read()
    out_dir = os.path.dirname(epub)  # same directory the source EPUB lives in

    with pytest.raises(SystemExit):
        assemble(epub, workdir, out_dir)

    assert open(epub, "rb").read() == src_before


def test_assemble_refuses_out_dir_symlinked_to_source_epub_dir(tmp_path):
    # Same collision as the direct-path test above, but reached through a
    # symlink: os.path.abspath normalizes . / .. / trailing slashes but does
    # NOT resolve symlinks. On macOS /tmp -> /private/tmp, so a real-world
    # relative --out from cwd /tmp slips past an abspath-only guard even
    # though it is the same file. os.path.realpath resolves symlinks and
    # closes this gap.
    epub, workdir = _prepare(tmp_path)
    src_before = open(epub, "rb").read()
    link = tmp_path / "link"
    try:
        os.symlink(os.path.dirname(epub), link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")

    with pytest.raises(SystemExit):
        assemble(epub, workdir, str(link))

    assert open(epub, "rb").read() == src_before


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
