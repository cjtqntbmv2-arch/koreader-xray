import zipfile

import pytest
from epub_fixture import build_epub

from xray_core.epub import DrmError, normalize_text, read_epub

_ENCRYPTION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes256-cbc"/>
    <enc:CipherData>
      <enc:CipherReference URI="OEBPS/chapter0.xhtml"/>
    </enc:CipherData>
  </enc:EncryptedData>
</encryption>
"""


def test_reads_spine_order_and_text(tmp_path):
    chapters = [
        ("One", "<p>The first chapter begins here.</p>"),
        ("Two", "<p>The second chapter continues the story.</p>"),
        ("Three", "<p>The third chapter concludes it.</p>"),
    ]
    book = read_epub(build_epub(tmp_path, chapters))

    assert "The first chapter begins here." in book.full_text
    assert "The second chapter continues the story." in book.full_text
    assert "The third chapter concludes it." in book.full_text
    assert (
        book.full_text.index("first chapter")
        < book.full_text.index("second chapter")
        < book.full_text.index("third chapter")
    )
    assert len(book.spine_offsets) == 3


def test_toc_entries_have_ascending_offsets(tmp_path):
    chapters = [
        ("Chapter One", "<p>Text one.</p>"),
        ("Chapter Two", "<p>Text two.</p>"),
        ("Chapter Three", "<p>Text three.</p>"),
    ]
    book = read_epub(build_epub(tmp_path, chapters))

    assert len(book.toc) == 3
    offsets = [entry.offset for entry in book.toc]
    assert offsets == sorted(offsets)
    assert len(set(offsets)) == len(offsets)


def test_epub2_ncx_toc(tmp_path):
    chapters = [
        ("Chapter One", "<p>Text one.</p>"),
        ("Chapter Two", "<p>Text two.</p>"),
    ]
    book = read_epub(build_epub(tmp_path, chapters, epub3=False))

    assert [entry.title for entry in book.toc] == ["Chapter One", "Chapter Two"]
    assert [entry.spine_index for entry in book.toc] == [0, 1]


def test_no_toc(tmp_path):
    chapters = [("Chapter One", "<p>Text one.</p>")]
    book = read_epub(build_epub(tmp_path, chapters, toc=False))

    assert book.toc == []


def test_text_hash_stable_across_whitespace(tmp_path):
    hash_a = read_epub(build_epub(tmp_path, [("One", "<p>Hello world.</p>")])).text_hash
    hash_b = read_epub(
        build_epub(tmp_path, [("One", "<p>Hello   \n   world.</p>")])
    ).text_hash

    assert hash_a == hash_b


def test_strips_tags_and_soft_hyphens(tmp_path):
    body = "<p>State-of-the-art exam­ple with <b>bold</b> and <i>italic</i>.</p>"
    book = read_epub(build_epub(tmp_path, [("One", body)]))

    assert "<p>" not in book.full_text
    assert "<b>" not in book.full_text
    assert "bold" in book.full_text
    assert "italic" in book.full_text
    assert "­" not in normalize_text(book.full_text)
    assert "example" in normalize_text(book.full_text)


def test_normalize_text_preserves_nbsp(tmp_path):
    """Locks in the ASCII-only _WHITESPACE_RE fix: NBSP must survive
    normalize_text (it's also applied to snippet anchors, where collapsing
    NBSP to a plain space would break literal-text matching on device)."""
    nbsp_book = read_epub(build_epub(tmp_path / "nbsp", [("One", "<p>A&#160;B</p>")]))
    space_book = read_epub(build_epub(tmp_path / "space", [("One", "<p>A B</p>")]))

    assert " " in nbsp_book.full_text
    assert " " in normalize_text(nbsp_book.full_text)
    assert nbsp_book.text_hash != space_book.text_hash


def test_drm_raises(tmp_path):
    path = build_epub(tmp_path, [("One", "<p>Secret text.</p>")])
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("META-INF/encryption.xml", _ENCRYPTION_XML)

    with pytest.raises(DrmError):
        read_epub(path)


def test_drm_font_obfuscation_ok(tmp_path):
    """Font-only obfuscation (CipherReference to a non-spine resource) is
    not DRM on the readable content and must not raise."""
    path = build_epub(
        tmp_path,
        [("One", "<p>Not secret text.</p>")],
        encryption_uri="OEBPS/fonts/x.otf",
    )

    book = read_epub(path)

    assert "Not secret text." in book.full_text
