#!/usr/bin/env python3
"""Builds the installable calibre plugin zip: dist/xray-generator-<VERSION>.zip.

Layout calibre requires: the plugin's own files (calibre_plugin/*) go at the
zip ROOT (calibre's multi-file-plugin convention -- see
calibre_plugin/plugin-import-name-xray_generator.txt), flattened out of the
calibre_plugin/ directory they live in on disk. The whole xray_core/ package
and VERSION are bundled alongside them at that same root: xray_core.generate
reads VERSION as "../VERSION" relative to itself, i.e. a zip-root sibling,
and calibre_plugin/__init__.py aliases calibre_plugins.xray_generator.xray_core
(this same bundled copy) to the bare name "xray_core" so xray_core's own
top-level absolute imports resolve unmodified inside the plugin.
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "calibre_plugin"
CORE_DIR = ROOT / "xray_core"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
OUT_PATH = ROOT / "dist" / f"xray-generator-{VERSION}.zip"


def _iter_files(pkg_dir):
    for path in sorted(pkg_dir.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc" or path.name.startswith("."):
            continue
        yield path


def build():
    OUT_PATH.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in _iter_files(PLUGIN_DIR):
            zf.write(path, path.relative_to(PLUGIN_DIR).as_posix())  # flattened to zip root
        for path in _iter_files(CORE_DIR):
            zf.write(path, path.relative_to(ROOT).as_posix())  # keeps xray_core/... prefix
        zf.write(ROOT / "VERSION", "VERSION")
    return OUT_PATH


if __name__ == "__main__":
    out = build()
    print(f"built {out}")
