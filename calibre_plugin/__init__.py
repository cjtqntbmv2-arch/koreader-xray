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

So: install a meta_path finder that maps the whole bare `xray_core` prefix
onto the already-real `calibre_plugins.xray_generator.xray_core` package,
then alias the top-level package too. Aliasing only the top package is not
enough -- calibre's importer resolves only `calibre_plugins.*` names, so
`from xray_core.checkpoints import ...` (in xray_core's own modules and in
ui.py) would otherwise raise ModuleNotFoundError for every submodule.
"""
import importlib
import importlib.abc
import importlib.util
import sys


class _XrayCoreRedirect:
    """meta_path finder: bare `xray_core[.sub]` -> the bundled real package."""

    _PREFIX = "xray_core"
    _REAL = "calibre_plugins.xray_generator.xray_core"

    def find_spec(self, name, path=None, target=None):
        if name != self._PREFIX and not name.startswith(self._PREFIX + "."):
            return None
        real = importlib.import_module(self._REAL + name[len(self._PREFIX):])
        sys.modules[name] = real
        return importlib.util.spec_from_loader(name, _AlreadyLoaded(real))


class _AlreadyLoaded(importlib.abc.Loader):
    """Loader that hands back an already-imported module unchanged."""

    def __init__(self, module):
        self._module = module

    def create_module(self, spec):
        return self._module

    def exec_module(self, module):
        pass


if "xray_core" not in sys.modules:
    sys.meta_path.insert(0, _XrayCoreRedirect())
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
