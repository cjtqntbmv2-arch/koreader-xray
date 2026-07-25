"""Embeds a generated xray.json into an EPUB so it survives calibre's Convert
Book: registered in the OPF manifest (an auxiliary resource, not in spine) at
a fixed zip path the device reads directly, no OPF parse needed on-device.

Stdlib-only on purpose (see xray_core/epub.py): zipfile, xml.etree.ElementTree,
json, os.
"""

import hashlib
import json
import os
import re
import shutil
import zipfile
from xml.etree import ElementTree as ET

from xray_core.epub import _find_opf_path as _opf_path  # same container.xml lookup

DATA_PATH = "xray/xray.json"

_OPF_NS = "http://www.idpf.org/2007/opf"

# Matches </manifest> or a prefixed </opf:manifest>-style close tag, whatever
# the source OPF actually uses.
_MANIFEST_CLOSE_RE = re.compile(rb"</\s*(?:[\w.-]+:)?manifest\s*>", re.IGNORECASE)


def _add_manifest_item(opf_bytes: bytes, href: str) -> bytes:
    """Append a manifest <item> pointing at `href` unless one already exists
    (idempotent -- a re-embed must not accumulate duplicate manifest entries).

    Splices the raw bytes instead of round-tripping through ET.tostring.
    Re-serializing the whole tree after ET.register_namespace("", OPF_NS)
    corrupts calibre's opf:role/opf:file-as/opf:scheme attributes: ElementTree
    maps that URI to the empty prefix for BOTH elements and attributes, but an
    unprefixed attribute means "no namespace", not "default namespace" -- so
    those attributes silently drop out of the OPF namespace on every embed of
    a calibre-authored EPUB2. Byte-splicing leaves every other byte untouched.
    """
    root = ET.fromstring(opf_bytes)  # read-only: existence check only, never reserialized
    manifest = root.find(f"{{{_OPF_NS}}}manifest")
    if manifest is not None:
        already_present = any(
            item.get("href") == href for item in manifest.findall(f"{{{_OPF_NS}}}item")
        )
        if already_present:
            return opf_bytes

    match = _MANIFEST_CLOSE_RE.search(opf_bytes)
    if not match:
        raise ValueError("OPF has no </manifest> closing tag to insert the xray item into")

    item_xml = f'<item id="xray-data" href="{href}" media-type="application/json"/>'.encode("utf-8")
    return opf_bytes[: match.start()] + item_xml + opf_bytes[match.start() :]


# First <dc:title> (or prefixed <opf:title>-style) element, capturing its inner
# text so we can replace just the content -- byte-splice, same reasoning as
# _add_manifest_item (never reserialize a calibre OPF through ElementTree).
_DC_TITLE_RE = re.compile(
    rb"(<\s*(?:[\w.-]+:)?title\b[^>]*>)(.*?)(</\s*(?:[\w.-]+:)?title\s*>)",
    re.IGNORECASE | re.DOTALL,
)


_DC_NS = "http://purl.org/dc/elements/1.1/"
_METADATA_CLOSE_RE = re.compile(rb"</\s*(?:[\w.-]+:)?metadata\s*>", re.IGNORECASE)


def _add_dc_subject(opf_bytes: bytes, value: str = "X-Ray") -> bytes:
    """Append <dc:subject>value</dc:subject> before </metadata> unless a subject
    with that (case-insensitive) text already exists -- idempotent, and existing
    subjects are left untouched. calibre maps dc:subject onto Tags, so this marks
    the book as X-Ray-capable and filterable in the library. Byte-splice, never
    reserialize (same reasoning as _add_manifest_item)."""
    root = ET.fromstring(opf_bytes)  # read-only: existence check only
    metadata = root.find(f"{{{_OPF_NS}}}metadata")
    if metadata is not None:
        want = value.strip().lower()
        for subj in metadata.findall(f"{{{_DC_NS}}}subject"):
            if (subj.text or "").strip().lower() == want:
                return opf_bytes
    match = _METADATA_CLOSE_RE.search(opf_bytes)
    if not match:
        raise ValueError("OPF has no </metadata> closing tag to insert dc:subject into")
    escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    item = f"<dc:subject>{escaped}</dc:subject>".encode("utf-8")
    return opf_bytes[: match.start()] + item + opf_bytes[match.start() :]


def _set_dc_title(opf_bytes: bytes, title: str) -> bytes:
    """Replace the inner text of the first <dc:title> with `title` (XML-escaped).

    The KOReader importer gates on title: it compares book_fingerprint.title
    against the OPF <dc:title> of the book as it lands on-device. Aligning the
    OPF here keeps the two in agreement regardless of whether calibre rewrites
    the OPF on send. No-op if the OPF has no title element."""
    escaped = (title.replace("&", "&amp;").replace("<", "&lt;")
               .replace(">", "&gt;")).encode("utf-8")
    return _DC_TITLE_RE.sub(lambda m: m.group(1) + escaped + m.group(3), opf_bytes, count=1)


def embed_xray(epub_path, doc: dict, out_path, append=False, title=None) -> None:
    """Rewrite the EPUB at `epub_path` into `out_path` with `doc` embedded at
    DATA_PATH and registered in the OPF manifest. Re-embed-safe: drops any
    prior DATA_PATH entry and leaves the manifest item deduplicated.

    With `append=True`, deliver `doc` by appending it to a byte-for-byte copy
    of the source instead: no OPF edit, no re-compression, existing bytes
    untouched. The xray is then NOT in the OPF manifest, so it does not survive
    calibre's Convert Book -- but the source's leading bytes are preserved
    exactly, which keeps KOReader's book identity stable. KOReader keys a
    book's statistics and progress on a head-weighted `partialMD5` (12 x 1 KB
    samples over the first ~1 MB); leaving the head untouched means replacing
    the file on-device does NOT reset reading statistics. The device importer
    reads DATA_PATH by name from the zip (not via the manifest), so it still
    finds it. Needs a pristine source (no existing DATA_PATH)."""
    if append:
        # append keeps every source byte intact (the partialMD5 guarantee), so
        # it cannot also rewrite the OPF title. title alignment therefore needs
        # full mode; for append, calibre's OPF-title-on-send does the aligning.
        _embed_append(epub_path, doc, out_path)
        return
    payload = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    with zipfile.ZipFile(epub_path) as zin:
        opf = _opf_path(zin)
        href = os.path.relpath(DATA_PATH, os.path.dirname(opf)).replace(os.sep, "/")
        with zipfile.ZipFile(out_path, "w") as zout:
            for item in zin.infolist():
                if item.filename == DATA_PATH:
                    continue  # drop any prior copy (re-embed)
                data = zin.read(item)  # by ZipInfo, not filename -- see module docstring
                if item.filename == opf:
                    data = _add_manifest_item(data, href)
                    data = _add_dc_subject(data)  # calibre-visible "X-Ray" tag
                    if title:
                        data = _set_dc_title(data, title)
                    comp = zipfile.ZIP_DEFLATED
                elif item.filename == "mimetype":
                    comp = zipfile.ZIP_STORED
                else:
                    comp = item.compress_type  # preserve source choice
                zout.writestr(item, data, comp)
            zout.writestr(DATA_PATH, payload, zipfile.ZIP_DEFLATED)


def _embed_append(epub_path, doc: dict, out_path) -> None:
    """Copy the source verbatim and append DATA_PATH as a new zip member,
    touching no existing byte (see embed_xray's append=True docstring). The
    md5-stability guarantee only holds for a pristine source, so refuse a
    source that already carries an xray rather than leave a duplicate member."""
    if read_embedded(epub_path) is not None:
        raise ValueError(
            "append mode needs a source EPUB without an existing xray/xray.json; "
            "use embed_xray() (full mode) to re-embed, or start from the original."
        )
    payload = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    shutil.copyfile(epub_path, out_path)  # SameFileError if out_path == epub_path
    with zipfile.ZipFile(out_path, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(DATA_PATH, payload)


def read_embedded(epub_path) -> dict | None:
    """The embedded xray.json, or None if this EPUB has none yet."""
    with zipfile.ZipFile(epub_path) as zf:
        if DATA_PATH not in zf.namelist():
            return None
        return json.loads(zf.read(DATA_PATH).decode("utf-8"))


def partial_md5(path) -> str:
    """KOReader's book identity: `util.partialMD5` -- 12 x 1024-byte samples at
    the head-weighted offsets 1024*4^i, i=-1..10 (256 B .. 1 MB). Statistics and
    reading progress are keyed on (title, authors, this hash), so a file whose
    partial_md5 changes is a NEW book to the device and starts from zero.

    Note what "head-weighted" does NOT mean: appending is not automatically
    safe. A sample only exists once the file is long enough to reach its
    offset, so growing a file ACROSS a sample boundary (1 KiB, 4 KiB, ...,
    256 KiB, 1 MiB, 4 MiB) adds a sample that previously sat past EOF and
    changes the hash -- a 0.7 MB novel plus a 1 MB xray does exactly that.
    Callers that promise to preserve statistics must compare this value before
    and after rather than reason about which mode they used.
    """
    m = hashlib.md5()
    with open(path, "rb") as f:
        for i in range(-1, 11):
            f.seek(int(1024 * 4.0 ** i))
            sample = f.read(1024)
            if not sample:
                break
            m.update(sample)
    return m.hexdigest()
