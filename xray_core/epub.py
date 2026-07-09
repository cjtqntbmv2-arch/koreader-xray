"""stdlib-only EPUB text extraction.

Turns an EPUB file into a `BookText`: spine-ordered plain text with char
offsets, a TOC, and a normalized text hash. Everything downstream
(checkpoint planning, generation) consumes `BookText`.

Stdlib-only on purpose (`zipfile`, `xml.etree.ElementTree`, `html.parser`) --
no `calibre`, no third-party packages -- so this runs identically in the
CLI and the calibre plugin and is testable with plain pytest.
"""

import hashlib
import posixpath
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

_OPF_NS = "http://www.idpf.org/2007/opf"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_EPUB_OPS_NS = "http://www.idpf.org/2007/ops"
_NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
_XMLENC_NS = "http://www.w3.org/2001/04/xmlenc#"

# Tags whose text we drop entirely: script/style are non-content; head/title
# is the document title, not book text (would otherwise leak into full_text).
_SKIP_TAGS = {"script", "style", "head", "title"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "blockquote", "section", "article", "table", "ul", "ol", "hr",
}

_WHITESPACE_RE = re.compile(r"\s+")
_SOFT_HYPHEN = "­"


@dataclass
class TocEntry:
    title: str
    spine_index: int
    offset: int  # char offset into full_text


@dataclass
class BookText:
    title: str
    authors: list
    language: str
    full_text: str  # spine-ordered plain text, "\n\n" between spine items
    spine_offsets: list  # char offset where each spine item begins
    toc: list  # list[TocEntry]
    text_hash: str  # "sha256:<hex>" of normalized full_text


class DrmError(Exception):
    """Raised when spine (readable) content is encrypted, not just fonts."""


def normalize_text(s: str) -> str:
    """Canonical text form for `text_hash` and downstream snippet matching.

    Collapses every whitespace run to a single space and strips soft
    hyphens (U+00AD). "Whitespace" = Python re's `\\s` (ASCII plus Unicode
    spaces, including NBSP) -- deliberately not identical to the KOReader
    importer's Lua `%s` (ASCII-only). That divergence is why
    book_fingerprint.text_hash is advisory only, never a refusal gate.
    """
    return _WHITESPACE_RE.sub(" ", s.replace(_SOFT_HYPHEN, "")).strip()


class _TextExtractor(HTMLParser):
    """Turns one spine item's (X)HTML markup into plain text."""

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    return parser.get_text().strip()


def _resolve(base_dir: str, href: str) -> str:
    """Resolve a relative href against `base_dir`, dropping any #fragment."""
    href = href.split("#", 1)[0]
    return posixpath.normpath(posixpath.join(base_dir, href))


def _find_opf_path(zf: zipfile.ZipFile) -> str:
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    rootfile = container.find(f".//{{{_CONTAINER_NS}}}rootfile")
    full_path = rootfile.get("full-path") if rootfile is not None else None
    if not full_path:
        raise ValueError("container.xml: no rootfile with full-path found")
    return full_path


def _parse_opf(zf: zipfile.ZipFile, opf_path: str):
    """Return (title, authors, language, spine_paths, toc_source).

    toc_source is ("nav", zip_path) | ("ncx", zip_path) | None.
    """
    opf_dir = posixpath.dirname(opf_path)
    root = ET.fromstring(zf.read(opf_path))

    title = ""
    authors: list[str] = []
    language = ""
    metadata = root.find(f"{{{_OPF_NS}}}metadata")
    if metadata is not None:
        title_el = metadata.find(f"{{{_DC_NS}}}title")
        if title_el is not None and title_el.text:
            title = title_el.text.strip()
        authors = [
            el.text.strip()
            for el in metadata.findall(f"{{{_DC_NS}}}creator")
            if el.text and el.text.strip()
        ]
        lang_el = metadata.find(f"{{{_DC_NS}}}language")
        if lang_el is not None and lang_el.text:
            language = lang_el.text.strip()

    manifest_by_id = {}
    for item in root.findall(f"{{{_OPF_NS}}}manifest/{{{_OPF_NS}}}item"):
        item_id = item.get("id")
        href = item.get("href")
        if item_id is None or href is None:
            continue
        manifest_by_id[item_id] = {
            "path": _resolve(opf_dir, href),
            "media_type": item.get("media-type", ""),
            "properties": item.get("properties") or "",
        }

    spine_el = root.find(f"{{{_OPF_NS}}}spine")
    spine_paths = []
    if spine_el is not None:
        for itemref in spine_el.findall(f"{{{_OPF_NS}}}itemref"):
            item = manifest_by_id.get(itemref.get("idref"))
            if item is not None:
                spine_paths.append(item["path"])

    # TOC source: EPUB3 nav item (manifest properties="nav") takes priority,
    # else EPUB2 NCX (spine's toc=idref, falling back to media-type lookup).
    toc_source = None
    nav_items = [m for m in manifest_by_id.values() if "nav" in m["properties"].split()]
    if nav_items:
        toc_source = ("nav", nav_items[0]["path"])
    else:
        ncx_id = spine_el.get("toc") if spine_el is not None else None
        ncx_item = manifest_by_id.get(ncx_id) if ncx_id else None
        if ncx_item is None:
            ncx_candidates = [
                m for m in manifest_by_id.values()
                if m["media_type"] == "application/x-dtbncx+xml"
            ]
            ncx_item = ncx_candidates[0] if ncx_candidates else None
        if ncx_item is not None:
            toc_source = ("ncx", ncx_item["path"])

    return title, authors, language, spine_paths, toc_source


def _parse_nav_toc(zf: zipfile.ZipFile, nav_path: str, spine_index_by_path: dict):
    nav_dir = posixpath.dirname(nav_path)
    root = ET.fromstring(zf.read(nav_path))

    toc_nav = None
    for nav in root.iter():
        if nav.tag.rsplit("}", 1)[-1] == "nav" and nav.get(f"{{{_EPUB_OPS_NS}}}type") == "toc":
            toc_nav = nav
            break
    if toc_nav is None:
        return []

    entries = []
    for a in toc_nav.iter():
        if a.tag.rsplit("}", 1)[-1] != "a":
            continue
        href = a.get("href")
        if not href:
            continue
        spine_index = spine_index_by_path.get(_resolve(nav_dir, href))
        if spine_index is not None:
            entries.append(("".join(a.itertext()).strip(), spine_index))
    return entries


def _parse_ncx_toc(zf: zipfile.ZipFile, ncx_path: str, spine_index_by_path: dict):
    ncx_dir = posixpath.dirname(ncx_path)
    root = ET.fromstring(zf.read(ncx_path))

    entries = []
    for navpoint in root.iter(f"{{{_NCX_NS}}}navPoint"):
        content_el = navpoint.find(f"{{{_NCX_NS}}}content")
        src = content_el.get("src") if content_el is not None else None
        if not src:
            continue
        spine_index = spine_index_by_path.get(_resolve(ncx_dir, src))
        if spine_index is None:
            continue
        label_el = navpoint.find(f"{{{_NCX_NS}}}navLabel/{{{_NCX_NS}}}text")
        title = (label_el.text or "").strip() if label_el is not None else ""
        entries.append((title, spine_index))
    return entries


def _check_drm(zf: zipfile.ZipFile, spine_paths: list) -> None:
    if "META-INF/encryption.xml" not in zf.namelist():
        return
    root = ET.fromstring(zf.read("META-INF/encryption.xml"))
    spine_set = set(spine_paths)
    for ref in root.iter(f"{{{_XMLENC_NS}}}CipherReference"):
        uri = ref.get("URI")
        # Only spine (readable) content triggers DrmError -- font
        # obfuscation (a CipherReference to a font file, not in the spine)
        # is not a DRM book and must stay readable.
        if uri and posixpath.normpath(uri) in spine_set:
            raise DrmError(f"spine content is encrypted: {uri}")


def read_epub(path) -> BookText:
    with zipfile.ZipFile(path) as zf:
        opf_path = _find_opf_path(zf)
        title, authors, language, spine_paths, toc_source = _parse_opf(zf, opf_path)

        _check_drm(zf, spine_paths)

        texts = [
            _html_to_text(zf.read(p).decode("utf-8", errors="replace"))
            for p in spine_paths
        ]

        spine_offsets = []
        offset = 0
        for text in texts:
            spine_offsets.append(offset)
            offset += len(text) + 2  # + len("\n\n") separator
        full_text = "\n\n".join(texts)

        toc_entries = []
        if toc_source is not None:
            kind, toc_path = toc_source
            spine_index_by_path = {p: i for i, p in enumerate(spine_paths)}
            raw_toc = (
                _parse_nav_toc(zf, toc_path, spine_index_by_path)
                if kind == "nav"
                else _parse_ncx_toc(zf, toc_path, spine_index_by_path)
            )
            toc_entries = [
                TocEntry(title=t, spine_index=si, offset=spine_offsets[si])
                for t, si in raw_toc
            ]

        text_hash = "sha256:" + hashlib.sha256(
            normalize_text(full_text).encode("utf-8")
        ).hexdigest()

        return BookText(
            title=title,
            authors=authors,
            language=language,
            full_text=full_text,
            spine_offsets=spine_offsets,
            toc=toc_entries,
            text_hash=text_hash,
        )
