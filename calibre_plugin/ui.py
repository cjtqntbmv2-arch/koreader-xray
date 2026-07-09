"""The GUI InterfaceAction: wires a toolbar/menu action to a per-book
background job that reads the EPUB, generates X-Ray data via Gemini, embeds
it, validates the result, and (only if valid) replaces the library format.
"""
import os
import shutil
import tempfile
import zipfile

from calibre.gui2 import error_dialog, info_dialog, warning_dialog
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.threaded_jobs import ThreadedJob
from calibre.utils.config import cache_dir

from calibre_plugins.xray_generator.config import prefs
from xray_core.embed import embed_xray, read_embedded
from xray_core.epub import DrmError, read_epub
from xray_core.gemini import GeminiClient
from xray_core.generate import generate_xray


def _generate_and_embed(log, abort, notifications, epub_path, calibre_uuid, workdir,
                         api_key, model, language, detail_level, use_thinking, max_workers):
    """Runs on a background thread (calibre's ThreadedJob convention: the
    first three positional args are always log/abort/notifications). No
    cooperative-cancellation check on `abort` -- generate_xray has no abort
    hook (yet); a killed job just keeps running to completion in the
    background and its result is discarded by _job_done never being wired up
    for it. ponytail: add an abort-aware hook in xray_core if this matters."""
    def progress_cb(done, total):
        notifications.put((done / total if total else 0.0, f"{done}/{total} segments"))

    book = read_epub(epub_path)
    client = GeminiClient(api_key, model=model, use_thinking=use_thinking)
    doc = generate_xray(
        book, client, language, detail_level,
        calibre_uuid=calibre_uuid, progress_cb=progress_cb,
        workdir=workdir, max_workers=max_workers,
    )

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".epub")
    os.close(tmp_fd)
    embed_xray(epub_path, doc, tmp_path)
    return tmp_path, doc


def _validate_embedded_epub(tmp_path, doc):
    """Before a generated EPUB is ever allowed to replace the library copy:
    the zip must be structurally sound, the embedded doc must round-trip
    byte-for-byte equal, and read_epub must still be able to parse it. A
    bug in embed_xray must never turn into permanent data loss."""
    try:
        with zipfile.ZipFile(tmp_path) as zf:
            if zf.testzip() is not None:
                return False
        if read_embedded(tmp_path) != doc:
            return False
        read_epub(tmp_path)
    except Exception:
        return False
    return True


class XRayGeneratorAction(InterfaceAction):
    name = "X-Ray Generator"
    action_spec = ("X-Ray Generator", None,
                   _("Generate spoiler-staged X-Ray data for the selected book(s)"), None)
    action_type = "current"

    def genesis(self):
        self._running_jobs = {}  # ThreadedJob -> (book_id, title, workdir)
        self._active_book_ids = set()
        self.qaction.triggered.connect(self.generate_selected)

    def generate_selected(self):
        gui = self.gui
        db = gui.current_db.new_api
        book_ids = gui.library_view.get_selected_ids()
        if not book_ids:
            return error_dialog(gui, _("No books selected"),
                                 _("Select one or more books first."), show=True)

        if not prefs["api_key"]:
            return error_dialog(gui, _("No API key configured"),
                                 _("Set a Gemini API key in the plugin's configuration first."),
                                 show=True)

        skipped = []
        to_run = []
        for book_id in book_ids:
            title = db.field_for("title", book_id) or str(book_id)
            if book_id in self._active_book_ids:
                skipped.append(_("{0} (already running)").format(title))
                continue
            epub_path = db.format_abspath(book_id, "EPUB")
            if not epub_path:
                skipped.append(title)
                continue
            uuid = db.field_for("uuid", book_id) or f"book-{book_id}"
            to_run.append((book_id, title, epub_path, uuid))

        if skipped:
            info_dialog(
                gui, _("Books skipped"),
                _("{0} of {1} selected book(s) were skipped (no EPUB format, or already "
                  "running):").format(len(skipped), len(book_ids)),
                det_msg="\n".join(skipped), show=True)

        for book_id, title, epub_path, uuid in to_run:
            self._start_job(book_id, title, epub_path, uuid)

    def _start_job(self, book_id, title, epub_path, calibre_uuid):
        workdir = os.path.join(cache_dir(), "xray_generator", calibre_uuid)
        job = ThreadedJob(
            "xray_generator",
            _("Generate X-Ray: {0}").format(title),
            _generate_and_embed,
            (epub_path, calibre_uuid, workdir, prefs["api_key"], prefs["model"],
             prefs["language"], prefs["detail_level"], prefs["use_thinking"],
             prefs["max_workers"]),
            {},
            self._job_done,
            killable=False,  # ponytail: no abort hook to honor a kill request yet
        )
        self._running_jobs[job] = (book_id, title, workdir)
        self._active_book_ids.add(book_id)
        self.gui.job_manager.run_threaded_job(job)

    def _job_done(self, job):
        book_id, title, workdir = self._running_jobs.pop(job)
        self._active_book_ids.discard(book_id)
        gui = self.gui

        if job.failed:
            if isinstance(job.exception, DrmError):
                msg = _('"{0}" is DRM-protected; its text cannot be read.').format(title)
            else:
                msg = _('Generating X-Ray for "{0}" failed.').format(title)
            return error_dialog(gui, _("X-Ray generation failed"), msg,
                                 det_msg=job.details, show=True)

        tmp_path, doc = job.result
        if not _validate_embedded_epub(tmp_path, doc):
            _silently_remove(tmp_path)
            return error_dialog(
                gui, _("X-Ray generation failed"),
                _('The generated file for "{0}" failed validation; the library copy was '
                  "left untouched.").format(title),
                show=True)

        db = gui.current_db.new_api
        db.add_format(book_id, "EPUB", tmp_path, replace=True)
        _silently_remove(tmp_path)
        gui.library_view.model().refresh_ids([book_id])

        if doc.get("complete"):
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            warning_dialog(
                gui, _("X-Ray partially generated"),
                _('X-Ray data for "{0}" was prepared up to {1}% complete. Run "Generate '
                  'X-Ray" again later to continue from where it left off.')
                .format(title, doc.get("last_percent", 0)),
                show=True)


def _silently_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
