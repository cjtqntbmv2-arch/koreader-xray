"""Builds minimal, valid EPUB files for tests -- stdlib `zipfile` only.

Fixed internal layout (relied on by tests, e.g. to attach a DRM
encryption.xml against a known spine path):

    mimetype                (stored, first entry)
    META-INF/container.xml
    OEBPS/content.opf
    OEBPS/chapter0.xhtml, OEBPS/chapter1.xhtml, ...
    OEBPS/nav.xhtml         (epub3=True, toc=True)
    OEBPS/toc.ncx           (epub3=False, toc=True)
"""

import zipfile
from pathlib import Path

_CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>{title}</title></head>
<body>
{body}
</body>
</html>
"""

_NAV_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Table of Contents</title></head>
<body>
<nav epub:type="toc">
<ol>
{items}
</ol>
</nav>
</body>
</html>
"""

_NCX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head></head>
<docTitle><text>{title}</text></docTitle>
<navMap>
{items}
</navMap>
</ncx>
"""

_ENCRYPTION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes256-cbc"/>
    <enc:CipherData>
      <enc:CipherReference URI="{uri}"/>
    </enc:CipherData>
  </enc:EncryptedData>
</encryption>
"""

_TITLE = "Test Book"
_AUTHORS = ("Jane Author",)
_LANGUAGE = "en"


def build_epub(tmp_path, chapters, toc=True, epub3=True, encryption_uri=None):
    """Write a minimal valid EPUB under `tmp_path` and return its Path.

    chapters: list of (title, html_body) tuples -> one xhtml spine item each.
    toc: whether to include a nav (epub3) / ncx (epub2) TOC document.
    epub3: EPUB3 `nav` document vs EPUB2 `toc.ncx`.
    encryption_uri: if set, writes META-INF/encryption.xml with a single
        CipherReference to this URI -- a spine path (e.g. "OEBPS/chapter0.xhtml")
        to simulate DRM-encrypted content, or a non-spine path (e.g.
        "OEBPS/fonts/x.otf") to simulate font-only obfuscation.
    """
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    path = Path(tmp_path) / "book.epub"
    chapter_files = [f"chapter{i}.xhtml" for i in range(len(chapters))]

    manifest_items = [
        f'<item id="chap{i}" href="{fname}" media-type="application/xhtml+xml"/>'
        for i, fname in enumerate(chapter_files)
    ]
    spine_items = [f'<itemref idref="chap{i}"/>' for i in range(len(chapters))]
    spine_attrs = ""

    if toc and epub3:
        manifest_items.append(
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        )
    elif toc and not epub3:
        manifest_items.append(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        )
        spine_attrs = ' toc="ncx"'

    creators = "".join(f"<dc:creator>{a}</dc:creator>" for a in _AUTHORS)
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{'3.0' if epub3 else '2.0'}" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{_TITLE}</dc:title>
    {creators}
    <dc:language>{_LANGUAGE}</dc:language>
    <dc:identifier id="bookid">urn:uuid:00000000-0000-0000-0000-000000000000</dc:identifier>
  </metadata>
  <manifest>
    {"".join(manifest_items)}
  </manifest>
  <spine{spine_attrs}>
    {"".join(spine_items)}
  </spine>
</package>
"""

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", opf)
        for i, (chap_title, body) in enumerate(chapters):
            zf.writestr(
                f"OEBPS/{chapter_files[i]}",
                _CHAPTER_XHTML.format(title=chap_title, body=body),
            )
        if toc and epub3:
            items = "\n".join(
                f'<li><a href="{chapter_files[i]}">{chap_title}</a></li>'
                for i, (chap_title, _) in enumerate(chapters)
            )
            zf.writestr("OEBPS/nav.xhtml", _NAV_XHTML.format(items=items))
        elif toc and not epub3:
            items = "\n".join(
                f'<navPoint id="np{i}" playOrder="{i + 1}">'
                f"<navLabel><text>{chap_title}</text></navLabel>"
                f'<content src="{chapter_files[i]}"/></navPoint>'
                for i, (chap_title, _) in enumerate(chapters)
            )
            zf.writestr("OEBPS/toc.ncx", _NCX_XML.format(title=_TITLE, items=items))
        if encryption_uri is not None:
            zf.writestr("META-INF/encryption.xml", _ENCRYPTION_XML.format(uri=encryption_uri))

    return path
