"""Tests for xray_core.embed (Task 8): OPF manifest registration, ZipInfo-based
member copying (not filename-based -- duplicate-named entries would otherwise
silently corrupt on read), compress_type preservation, and re-embed idempotency.
"""
import zipfile
from xml.etree import ElementTree as ET

from epub_fixture import build_epub

from xray_core.embed import DATA_PATH, embed_xray, read_embedded

_OPF_NS = "http://www.idpf.org/2007/opf"
_OPF_PATH = "OEBPS/content.opf"  # epub_fixture.py's fixed layout


def _manifest_items(opf_bytes):
    root = ET.fromstring(opf_bytes)
    return root.findall(f"{{{_OPF_NS}}}manifest/{{{_OPF_NS}}}item")


def test_embed_roundtrip(tmp_path, minimal_doc):
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")])
    out = tmp_path / "out.epub"

    embed_xray(book, minimal_doc, out)

    assert read_embedded(out) == minimal_doc


def test_mimetype_first_and_stored(tmp_path, minimal_doc):
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")])
    out = tmp_path / "out.epub"

    embed_xray(book, minimal_doc, out)

    with zipfile.ZipFile(out) as zf:
        infos = zf.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED


def test_manifest_entry_added(tmp_path, minimal_doc):
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")])
    out = tmp_path / "out.epub"

    embed_xray(book, minimal_doc, out)

    with zipfile.ZipFile(out) as zf:
        items = _manifest_items(zf.read(_OPF_PATH))

    matches = [i for i in items if i.get("href") == "../xray/xray.json"]
    assert len(matches) == 1
    assert matches[0].get("media-type") == "application/json"
    assert matches[0].get("id") == "xray-data"


def test_manifest_entry_idempotent(tmp_path, minimal_doc):
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")])
    once = tmp_path / "once.epub"
    twice = tmp_path / "twice.epub"

    embed_xray(book, minimal_doc, once)
    embed_xray(once, minimal_doc, twice)  # re-embed from the already-embedded copy

    with zipfile.ZipFile(twice) as zf:
        items = _manifest_items(zf.read(_OPF_PATH))
        namelist = zf.namelist()

    matches = [i for i in items if i.get("href") == "../xray/xray.json"]
    assert len(matches) == 1
    assert namelist.count(DATA_PATH) == 1


def test_preserves_compress_type(tmp_path, minimal_doc):
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")])
    with zipfile.ZipFile(book, "a") as zf:
        zf.writestr(zipfile.ZipInfo("OEBPS/cover.jpg"), b"fake-jpeg-bytes", zipfile.ZIP_STORED)
    out = tmp_path / "out.epub"

    embed_xray(book, minimal_doc, out)

    with zipfile.ZipFile(out) as zf:
        info = zf.getinfo("OEBPS/cover.jpg")
        assert info.compress_type == zipfile.ZIP_STORED
        assert zf.read(info) == b"fake-jpeg-bytes"


def test_reembed_replaces_old(tmp_path, minimal_doc):
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")])
    once = tmp_path / "once.epub"
    twice = tmp_path / "twice.epub"
    newer_doc = {**minimal_doc, "last_percent": 42}

    embed_xray(book, minimal_doc, once)
    embed_xray(once, newer_doc, twice)

    with zipfile.ZipFile(twice) as zf:
        assert zf.namelist().count(DATA_PATH) == 1
    assert read_embedded(twice) == newer_doc


def test_duplicate_named_entry_not_corrupted(tmp_path, minimal_doc):
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")])
    with zipfile.ZipFile(book, "a") as zf:
        zf.writestr("OEBPS/dup.txt", "first copy")
        zf.writestr("OEBPS/dup.txt", "second copy, much longer than the first")
    out = tmp_path / "out.epub"

    embed_xray(book, minimal_doc, out)

    with zipfile.ZipFile(out) as zf:
        dups = [i for i in zf.infolist() if i.filename == "OEBPS/dup.txt"]
        assert len(dups) == 2
        contents = {zf.read(i) for i in dups}  # read by ZipInfo, not by name

    assert contents == {b"first copy", b"second copy, much longer than the first"}


def test_read_embedded_returns_none_when_absent(tmp_path):
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")])
    assert read_embedded(book) is None
