"""Tests for xray_core.embed (Task 8): OPF manifest registration, ZipInfo-based
member copying (not filename-based -- duplicate-named entries would otherwise
silently corrupt on read), compress_type preservation, and re-embed idempotency.
"""
import hashlib
import warnings
import zipfile
from xml.etree import ElementTree as ET

import pytest

from epub_fixture import build_epub

from xray_core.embed import DATA_PATH, embed_xray, read_embedded


def _koreader_partial_md5(path):
    """Exact port of KOReader util.partialMD5: 12 x 1024-byte samples at the
    head-weighted offsets 1024*4^i, i=-1..10. This is the book identity its
    statistics/progress are keyed on."""
    m = hashlib.md5()
    with open(path, "rb") as f:
        for i in range(-1, 11):
            f.seek(int(1024 * 4.0 ** i))
            sample = f.read(1024)
            if not sample:
                break
            m.update(sample)
    return m.hexdigest()

_OPF_NS = "http://www.idpf.org/2007/opf"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_OPF_PATH = "OEBPS/content.opf"  # epub_fixture.py's fixed layout


def _manifest_items(opf_bytes):
    root = ET.fromstring(opf_bytes)
    return root.findall(f"{{{_OPF_NS}}}manifest/{{{_OPF_NS}}}item")


def _subjects(opf_bytes):
    root = ET.fromstring(opf_bytes)
    return [e.text for e in root.findall(f"{{{_OPF_NS}}}metadata/{{{_DC_NS}}}subject")]


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


def test_embed_preserves_opf_namespaced_attributes(tmp_path, minimal_doc):
    """calibre-authored EPUB2 OPFs carry opf:role/opf:file-as/opf:scheme.
    A prior implementation round-tripped the OPF through ET.tostring after
    ET.register_namespace("", OPF_NS), which strips these attributes' opf:
    prefix (an unprefixed attribute means "no namespace", not "default
    namespace"). The fix must splice bytes instead, leaving them untouched.
    """
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")], opf_attrs=True)
    out = tmp_path / "out.epub"

    embed_xray(book, minimal_doc, out)

    with zipfile.ZipFile(out) as zf:
        root = ET.fromstring(zf.read(_OPF_PATH))

    creator = root.find(f"{{{_OPF_NS}}}metadata/{{{_DC_NS}}}creator")
    identifier = root.find(f"{{{_OPF_NS}}}metadata/{{{_DC_NS}}}identifier")
    assert creator is not None
    assert identifier is not None

    assert creator.get(f"{{{_OPF_NS}}}role") == "aut"
    assert creator.get(f"{{{_OPF_NS}}}file-as") == "Author, Jane"
    assert identifier.get(f"{{{_OPF_NS}}}scheme") == "calibre"


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
    # Deliberately create a malformed (duplicate-named) archive to prove embed_xray
    # preserves both entries. zipfile.writestr warns on duplicate names; that warning
    # is expected here, so confine it to this setup+embed block and keep suite output clean.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Duplicate name")
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


def test_embed_append_preserves_koreader_partial_md5(tmp_path, minimal_doc):
    """append=True must leave the sampled head bytes untouched so a file
    replaced on-device keeps its KOReader statistics/progress (keyed on the
    head-weighted partialMD5)."""
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")])
    # Pad to a realistic novel size (stored, so deterministic) -- a real EPUB is
    # MBs, so its zip central directory sits far past every partialMD5 sample
    # offset (256 B .. 256 KB for a file this size). The guarantee only holds
    # when the source's content precedes the samples; a few-KB toy EPUB would
    # have its central dir before offset 1024 and append could shift a sample.
    with zipfile.ZipFile(book, "a") as zf:
        zf.writestr(zipfile.ZipInfo("OEBPS/bulk.bin"), b"A" * 400_000, zipfile.ZIP_STORED)
    before = _koreader_partial_md5(book)
    out = tmp_path / "out.epub"

    embed_xray(book, minimal_doc, out, append=True)

    assert _koreader_partial_md5(out) == before      # book identity preserved
    assert read_embedded(out) == minimal_doc          # still device-readable by name
    assert _koreader_partial_md5(book) == before      # source untouched


def test_embed_append_leaves_opf_unmodified(tmp_path, minimal_doc):
    """append mode does NOT register the xray in the OPF manifest -- that edit
    is exactly what moves head bytes. The importer reads DATA_PATH by name."""
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")])
    with zipfile.ZipFile(book) as zf:
        src_opf = zf.read(_OPF_PATH)
    out = tmp_path / "out.epub"

    embed_xray(book, minimal_doc, out, append=True)

    with zipfile.ZipFile(out) as zf:
        assert zf.read(_OPF_PATH) == src_opf          # byte-identical OPF
        assert DATA_PATH in zf.namelist()
        assert not [i for i in _manifest_items(zf.read(_OPF_PATH))
                    if i.get("href") == "../xray/xray.json"]


def test_embed_append_rejects_already_embedded_source(tmp_path, minimal_doc):
    """Appending onto an already-embedded EPUB would leave a duplicate member
    and void the md5 guarantee -- refuse loudly, point at full mode."""
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")])
    full = tmp_path / "full.epub"
    embed_xray(book, minimal_doc, full)               # full embed first

    with pytest.raises(ValueError):
        embed_xray(full, minimal_doc, tmp_path / "again.epub", append=True)


def test_embed_adds_xray_subject_tag(tmp_path, minimal_doc):
    """Full embed stamps a calibre-visible marker: dc:subject 'X-Ray' maps to a
    calibre Tag on read, so xray-capable books are visible and filterable."""
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")])
    out = tmp_path / "out.epub"

    embed_xray(book, minimal_doc, out)

    with zipfile.ZipFile(out) as zf:
        assert _subjects(zf.read(_OPF_PATH)) == ["X-Ray"]


def test_embed_xray_subject_idempotent(tmp_path, minimal_doc):
    """Re-embedding an already-marked EPUB must not add a duplicate subject."""
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")])
    once = tmp_path / "once.epub"
    twice = tmp_path / "twice.epub"

    embed_xray(book, minimal_doc, once)
    embed_xray(once, minimal_doc, twice)              # re-embed from marked copy

    with zipfile.ZipFile(twice) as zf:
        assert _subjects(zf.read(_OPF_PATH)) == ["X-Ray"]


def test_add_dc_subject_preserves_existing_and_dedups():
    """The helper appends the marker without disturbing existing subjects, and
    is idempotent case-insensitively (a re-run must not duplicate)."""
    from xray_core.embed import _add_dc_subject

    opf = (b'<package xmlns="http://www.idpf.org/2007/opf">'
           b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           b"<dc:title>T</dc:title><dc:subject>Fantasy</dc:subject>"
           b"</metadata></package>")

    once = _add_dc_subject(opf, "X-Ray")
    assert _subjects(once) == ["Fantasy", "X-Ray"]    # existing kept, new appended
    assert _subjects(_add_dc_subject(once, "X-Ray")) == ["Fantasy", "X-Ray"]
    assert _subjects(_add_dc_subject(once, "x-ray")) == ["Fantasy", "X-Ray"]  # ci dedup
