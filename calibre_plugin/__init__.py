"""InterfaceActionBase entry point for the X-Ray Generator calibre plugin.

Registered/loaded by calibre at startup for every installed plugin (this is
how calibre discovers `actual_plugin` and builds the Preferences entry),
well before the GUI action itself (`ui.py`) is ever imported.

xray_core's own modules use plain top-level absolute imports (e.g.
"from xray_core.epub import BookText") so the exact same package works
unmodified for the CLI, pytest, AND this plugin (see xray_core/epub.py's
module docstring). Inside an installed calibre plugin zip, everything is
only reachable through calibre's multi-file-plugin namespace,
`calibre_plugins.xray_generator.*` (see plugin-import-name-xray_generator.txt)
-- there is no bare top-level `xray_core` module. `tools/build_plugin.py`
bundles the whole xray_core/ package at the zip root alongside this
package's own files, so `calibre_plugins.xray_generator.xray_core` DOES
exist and import correctly (calibre explicitly documents that nested
packages under the plugin root work like normal Python packages).

So: alias that already-real package to the bare name `xray_core` in
sys.modules before anything else can import it. Registering a package in
sys.modules before/during its own parent's init is standard Python
self-import behavior (every package does this for `from . import sibling`),
so this works regardless of calibre's internal zip-loading details.
"""
import sys

if "xray_core" not in sys.modules:
    from . import xray_core
    sys.modules["xray_core"] = xray_core

from calibre.customize import InterfaceActionBase


class XRayGeneratorPlugin(InterfaceActionBase):
    name = "X-Ray Generator"
    description = "Generate spoiler-staged X-Ray data via Gemini and embed it into the EPUB"
    supported_platforms = ["windows", "osx", "linux"]
    author = "Daniel Niehof"
    version = (0, 1, 0)
    minimum_calibre_version = (6, 0, 0)
    # NB: brief said "calibre_plugin.ui:...", but calibre's real multi-file
    # plugin namespace (confirmed against calibre's own plugin dev manual)
    # is calibre_plugins.<plugin-import-name>.<module> -- see
    # plugin-import-name-xray_generator.txt.
    actual_plugin = "calibre_plugins.xray_generator.ui:XRayGeneratorAction"

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.xray_generator.config import ConfigWidget
        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()
