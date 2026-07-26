"""calibre_plugin/ui.py without calibre.

calibre is not installed in the test environment and xray_core must never
import it (see CLAUDE.md), so ui.py is loaded straight from its file with a
stub `calibre.*` in sys.modules. Everything below the calibre boundary --
embed_xray, partial_md5, read_epub -- is the real thing, which is the point:
these tests are about what happens to the bytes on the way into the library.

The library itself is a FakeDb. Its add_format models the one behaviour that
broke the promise in the module docstring: with calibre's import hooks
enabled, what lands in the library is not what was handed over. Measured on
2026-07-26 against calibre 9.11.0 with DeDRM 10.0.9 installed -- DeDRM
registers as an on_import file type plugin for epub and runs its zipfix over
every EPUB, DRM or not, so db.add_format(..., run_hooks=True) stores a
structurally rewritten zip (same size, same content, rewritten local headers
and central directory) whose partial_md5 differs.
"""
import builtins
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import types

import pytest

from tests.epub_fixture import build_epub
from xray_core.embed import partial_md5, read_embedded
from xray_core.epub import read_epub

UI_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "calibre_plugin", "ui.py")


def _load_ui():
    """Import calibre_plugin/ui.py with a stubbed calibre.

    Loaded from the file rather than as calibre_plugin.ui: the package's
    __init__ builds calibre's plugin namespace, which only exists inside an
    installed plugin zip.
    """
    gui2 = types.ModuleType("calibre.gui2")
    for name in ("choose_files", "error_dialog", "info_dialog", "question_dialog"):
        setattr(gui2, name, lambda *a, **k: None)
    actions = types.ModuleType("calibre.gui2.actions")
    setattr(actions, "InterfaceAction", type("InterfaceAction", (), {}))
    calibre = types.ModuleType("calibre")
    setattr(calibre, "gui2", gui2)
    setattr(gui2, "actions", actions)
    sys.modules.update({"calibre": calibre, "calibre.gui2": gui2,
                        "calibre.gui2.actions": actions})
    setattr(builtins, "_", lambda s: s)  # calibre installs gettext into builtins

    spec = importlib.util.spec_from_file_location("xray_calibre_ui_under_test", UI_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ui = _load_ui()


class FakeDb:
    """Just the calibre db surface ui.py touches.

    rewrite_on_hooks mirrors what a real on_import file type plugin does to
    the bytes: when add_format runs the hooks, the file that lands in the
    library is a rewritten one.
    """

    def __init__(self, library_path, rewrite_on_hooks=True):
        self.library_path = library_path
        self.rewrite_on_hooks = rewrite_on_hooks
        self.add_format_calls = []
        self.fields = {"title": "Test Book", "formats": ("EPUB",), "tags": ()}

    def field_for(self, name, book_id):
        return self.fields.get(name)

    def set_field(self, name, mapping):
        self.fields[name] = next(iter(mapping.values()))

    def copy_format_to(self, book_id, fmt, dest):
        shutil.copyfile(self.library_path, dest)

    def add_format(self, book_id, fmt, path, replace=True, run_hooks=True):
        self.add_format_calls.append(
            {"path": path, "replace": replace, "run_hooks": run_hooks})
        shutil.copyfile(path, self.library_path)
        if run_hooks and self.rewrite_on_hooks:
            _mangle_zip_headers(self.library_path)

    def format_abspath(self, book_id, fmt):
        return self.library_path


def _mangle_zip_headers(path):
    """Flip the UTF-8 name flag in every local file header.

    Same size, same content, different bytes early in the file -- the shape a
    zip rewriter leaves behind, and enough to move partial_md5 (which samples
    at 256 B, 1 KiB, 4 KiB ... 1 MiB).
    """
    data = bytearray(open(path, "rb").read())
    for i in range(len(data) - 4):
        if data[i:i + 4] == b"PK\x03\x04":
            data[i + 7] ^= 0x08
    with open(path, "wb") as f:
        f.write(data)


class FakeGui:
    def __init__(self):
        self.library_view = types.SimpleNamespace(
            model=lambda: types.SimpleNamespace(refresh_ids=lambda ids: None))


@pytest.fixture
def dialogs(monkeypatch):
    """Record the dialogs ui.py raises; question_dialog answers yes."""
    seen = {"error": [], "info": [], "question": []}

    def record(kind, answer=None):
        def f(gui, title, msg, **kwargs):
            seen[kind].append((title, msg, kwargs.get("det_msg")))
            return answer
        return f

    monkeypatch.setattr(ui, "error_dialog", record("error"))
    monkeypatch.setattr(ui, "info_dialog", record("info"))
    monkeypatch.setattr(ui, "question_dialog", record("question", answer=True))
    return seen


@pytest.fixture
def book(tmp_path, minimal_doc):
    """A pristine EPUB in a fake library, plus a doc that belongs to it."""
    src = build_epub(tmp_path / "src", [("Kapitel 1", "<p>" + "Wort " * 400 + "</p>")])
    library_path = str(tmp_path / "library.epub")
    shutil.copyfile(src, library_path)
    doc = copy.deepcopy(minimal_doc)
    doc["book_fingerprint"]["text_hash"] = read_epub(library_path).text_hash
    return library_path, doc


def _embed(ui_module, db, doc, workdir, gui=None):
    os.makedirs(workdir, exist_ok=True)  # embed_selected's tempfile.mkdtemp
    action = ui.XRayGeneratorAction()
    return action._embed(gui or FakeGui(), db, 1, "Test Book", doc, str(workdir))


def test_import_hooks_stay_off_so_the_library_copy_is_the_checked_copy(
        tmp_path, book, dialogs):
    """calibre's import hooks may rewrite the file on its way into the library.

    The book is already in the library and its format has been through those
    hooks once; re-running them on a copy of it can only change bytes, and a
    changed byte in the first megabyte costs the reader's statistics.
    """
    library_path, doc = book
    db = FakeDb(library_path)

    _embed(ui, db, doc, tmp_path / "work")

    assert db.add_format_calls, "the library copy was never replaced"
    assert db.add_format_calls[0]["run_hooks"] is False


def test_the_library_copy_is_the_file_that_was_checked(tmp_path, book, dialogs):
    """Not "partial_md5 never changes" -- appending across a sample boundary
    may legitimately change it, which is what the question dialog before the
    write is for. The guarantee is that the file which lands is the file the
    check was run on."""
    library_path, doc = book
    db = FakeDb(library_path)

    _embed(ui, db, doc, tmp_path / "work")

    handed_over = db.add_format_calls[0]["path"]
    assert partial_md5(library_path) == partial_md5(handed_over)
    assert open(library_path, "rb").read() == open(handed_over, "rb").read()
    assert read_embedded(library_path) == doc


def test_a_library_copy_that_changed_anyway_is_reported(tmp_path, book, dialogs):
    """The docstring promises measured, not assumed. If something between us
    and the library still rewrites the file, the reader has to hear about it
    rather than be told the statistics survived."""
    library_path, doc = book
    db = FakeDb(library_path, rewrite_on_hooks=False)
    original_add_format = db.add_format

    def rewriting_add_format(*args, **kwargs):
        original_add_format(*args, **kwargs)
        _mangle_zip_headers(library_path)

    db.add_format = rewriting_add_format

    _embed(ui, db, doc, tmp_path / "work")

    assert dialogs["error"], "a silently changed library copy was not reported"
    assert not dialogs["info"], "success was reported although the file changed"


def test_the_happy_path_reports_success_once(tmp_path, book, dialogs):
    library_path, doc = book
    db = FakeDb(library_path)

    _embed(ui, db, doc, tmp_path / "work")

    assert len(dialogs["info"]) == 1
    assert not dialogs["error"]


def test_tagging_and_refresh_still_happen(tmp_path, book, dialogs):
    library_path, doc = book
    db = FakeDb(library_path)

    _embed(ui, db, doc, tmp_path / "work")

    assert ui.TAG in db.fields["tags"]


def test_wrong_book_is_refused_before_anything_is_written(tmp_path, book, dialogs):
    library_path, doc = book
    doc["book_fingerprint"]["text_hash"] = "sha256:" + hashlib.sha256(b"x").hexdigest()
    db = FakeDb(library_path)
    before = open(library_path, "rb").read()

    _embed(ui, db, doc, tmp_path / "work")

    assert not db.add_format_calls
    assert open(library_path, "rb").read() == before
    assert dialogs["error"]


def test_doc_survives_the_round_trip(tmp_path, book, dialogs):
    """read_embedded compares by value; make sure the bytes really are ours."""
    library_path, doc = book
    db = FakeDb(library_path)

    _embed(ui, db, doc, tmp_path / "work")

    import zipfile
    with zipfile.ZipFile(library_path) as zf:
        assert json.loads(zf.read("xray/xray.json").decode("utf-8")) == doc
