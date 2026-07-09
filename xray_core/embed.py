"""Embeds a generated xray.json into an EPUB so it survives calibre's Convert
Book: registered in the OPF manifest (an auxiliary resource, not in spine) at
a fixed zip path the device reads directly, no OPF parse needed on-device.

Stdlib-only on purpose (see xray_core/epub.py): zipfile, xml.etree.ElementTree,
json, os.
"""

import json
import os
import zipfile
from xml.etree import ElementTree as ET

from xray_core.epub import _find_opf_path as _opf_path  # same container.xml lookup

DATA_PATH = "xray/xray.json"

_OPF_NS = "http://www.idpf.org/2007/opf"
_DC_NS = "http://purl.org/dc/elements/1.1/"


def _add_manifest_item(opf_bytes: bytes, href: str) -> bytes:
    """Append a manifest <item> pointing at `href` unless one already exists
    (idempotent -- a re-embed must not accumulate duplicate manifest entries).
    """
    ET.register_namespace("", _OPF_NS)  # preserve the OPF's default namespace
    ET.register_namespace("dc", _DC_NS)  # ...and the conventional dc: prefix
    root = ET.fromstring(opf_bytes)
    manifest = root.find(f"{{{_OPF_NS}}}manifest")
    if manifest is None:
        return opf_bytes

    already_present = any(
        item.get("href") == href for item in manifest.findall(f"{{{_OPF_NS}}}item")
    )
    if not already_present:
        item = ET.SubElement(manifest, f"{{{_OPF_NS}}}item")
        item.set("id", "xray-data")
        item.set("href", href)
        item.set("media-type", "application/json")

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def embed_xray(epub_path, doc: dict, out_path) -> None:
    """Rewrite the EPUB at `epub_path` into `out_path` with `doc` embedded at
    DATA_PATH and registered in the OPF manifest. Re-embed-safe: drops any
    prior DATA_PATH entry and leaves the manifest item deduplicated."""
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
                    comp = zipfile.ZIP_DEFLATED
                elif item.filename == "mimetype":
                    comp = zipfile.ZIP_STORED
                else:
                    comp = item.compress_type  # preserve source choice
                zout.writestr(item, data, comp)
            zout.writestr(DATA_PATH, payload, zipfile.ZIP_DEFLATED)


def read_embedded(epub_path) -> dict | None:
    """The embedded xray.json, or None if this EPUB has none yet."""
    with zipfile.ZipFile(epub_path) as zf:
        if DATA_PATH not in zf.namelist():
            return None
        return json.loads(zf.read(DATA_PATH).decode("utf-8"))
