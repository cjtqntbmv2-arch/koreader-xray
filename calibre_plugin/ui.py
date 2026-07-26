"""The GUI InterfaceAction: embeds an already-generated xray.json into the
selected book's EPUB, so calibre's own wireless send carries it to the reader.

Generation happens on the desktop with the Claude skill (see
.claude/skills/xray/SKILL.md); calibre is only the delivery path. Appending
takes milliseconds, so there is no background job, no progress dialog and no
configuration page -- all of that belonged to the Gemini generator this
replaced.

Four things are checked before the library copy is replaced, each because it
would otherwise go wrong silently:

  1. Right book -- the document's text_hash against the EPUB's. The device no
     longer gates on the title, so a mis-picked file in the dialog would show
     another book's characters with nothing to warn you.
  2. Right shape -- schema.validate() on the document.
  3. Intact result -- zip integrity, byte-exact round-trip of the document,
     and read_epub() still parses the result.
  4. Same book to KOReader -- partial_md5, twice. Appending usually leaves it
     untouched, but not always (see xray_core.embed.partial_md5), and a changed
     hash means the device treats the book as new and drops its reading
     statistics and progress. Once before the write, because embedding anyway
     is the reader's call to make; once after it on the library copy itself,
     because "measured, not assumed" is only worth anything when it measures
     the file the device will actually get.
"""
import json
import os
import shutil
import tempfile
import zipfile

from calibre.gui2 import choose_files, error_dialog, info_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction

from xray_core.embed import embed_xray, partial_md5, read_embedded
from xray_core.epub import DrmError, read_epub
from xray_core.schema import validate

TAG = "X-Ray"


class XRayGeneratorAction(InterfaceAction):
    name = "X-Ray Generator"
    action_spec = (_("Embed X-Ray"), None,
                   _("Embed generated X-Ray data into the selected book's EPUB"), None)
    action_type = "current"

    def genesis(self):
        self.qaction.triggered.connect(self.embed_selected)

    def embed_selected(self):
        gui = self.gui
        db = gui.current_db.new_api
        book_ids = gui.library_view.get_selected_ids()

        # One book per xray.json -- a multi-selection has no sane mapping from
        # one chosen file to several books.
        if len(book_ids) != 1:
            return error_dialog(
                gui, _("Select one book"),
                _("Embedding takes one X-Ray file for one book. Select exactly "
                  "one book."), show=True)
        book_id = book_ids[0]
        title = db.field_for("title", book_id) or str(book_id)
        if "EPUB" not in (db.field_for("formats", book_id) or ()):
            return error_dialog(gui, _("No EPUB format"),
                                _('"{0}" has no EPUB format to embed into.').format(title),
                                show=True)

        paths = choose_files(
            gui, "xray_embed_json", _("Select the X-Ray data file"),
            filters=[(_("X-Ray data"), ["json"])], select_only_single_file=True)
        if not paths:
            return

        try:
            with open(paths[0], "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as e:
            return error_dialog(gui, _("Unreadable X-Ray file"), str(e), show=True)

        problems = validate(doc)
        if problems:
            return error_dialog(
                gui, _("Invalid X-Ray file"),
                _("This file is not a valid X-Ray document."),
                det_msg="\n".join(problems), show=True)

        workdir = tempfile.mkdtemp(prefix="xray_embed_")
        try:
            self._embed(gui, db, book_id, title, doc, workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _embed(self, gui, db, book_id, title, doc, workdir):
        source = os.path.join(workdir, "source.epub")
        # copy_format_to rather than format_abspath: handing a raw library path
        # to later processing breaks calibre's threadsafe promise (a concurrent
        # library operation could move or replace the file underneath us).
        db.copy_format_to(book_id, "EPUB", source)

        try:
            book = read_epub(source)
        except DrmError:
            return error_dialog(gui, _("DRM-protected"),
                                _('"{0}" is DRM-protected; its text cannot be read.')
                                .format(title), show=True)

        expected = (doc.get("book_fingerprint") or {}).get("text_hash")
        if expected != book.text_hash:
            return error_dialog(
                gui, _("Wrong book"),
                _('This X-Ray file was generated for a different text than "{0}". '
                  "Pick the file that belongs to this book.").format(title),
                det_msg="file: {0}\nbook: {1}".format(expected, book.text_hash),
                show=True)

        # Append mode leaves every existing byte alone. It refuses a source that
        # already carries an X-Ray, so a re-embed has to go through full mode --
        # which rewrites the head and can cost the reading statistics. That is
        # the reader's call, not ours.
        append = read_embedded(source) is None
        if not append and not question_dialog(
                gui, _("Replace existing X-Ray data?"),
                _('"{0}" already contains X-Ray data. Replacing it rewrites the '
                  "file rather than appending to it, which KOReader may see as a "
                  "different book and start its reading statistics over. "
                  "Continue?").format(title)):
            return

        out = os.path.join(workdir, "out.epub")
        embed_xray(source, doc, out, append=append)

        if not _result_is_sound(out, doc):
            return error_dialog(
                gui, _("Embedding failed"),
                _('The result for "{0}" failed its integrity check; the library '
                  "copy was left untouched.").format(title), show=True)

        if partial_md5(out) != partial_md5(source) and append and not question_dialog(
                gui, _("Reading statistics would reset"),
                _("Adding the X-Ray data pushes this file across one of the size "
                  "marks KOReader uses to recognise a book, so the reader would "
                  "treat it as new and start its statistics and reading position "
                  "over. Embed anyway?")):
            return

        # run_hooks=False: calibre otherwise runs every file type plugin that
        # registers for on_import over the file and stores whatever they hand
        # back. DeDRM (10.0.9, installed by most readers who own a Kobo) pushes
        # every EPUB through its zipfix, DRM or not, and returns the rewritten
        # copy -- same content, rewritten local headers and central directory,
        # different partial_md5. That is the library copy silently diverging
        # from the one checked above. The hooks belong to a file entering the
        # library from outside; this one is the library's own format with one
        # zip member appended, and it went through them when it was added.
        db.add_format(book_id, "EPUB", out, replace=True, run_hooks=False)
        _add_tag(db, book_id)
        gui.library_view.model().refresh_ids([book_id])

        # Measured on what the device will get, not on the temporary copy.
        landed = db.format_abspath(book_id, "EPUB")
        if landed and partial_md5(landed) != partial_md5(out):
            return error_dialog(
                gui, _("Reading statistics will reset"),
                _('The X-Ray data was embedded into "{0}", but calibre stored a '
                  "file that differs from the one that was checked. KOReader "
                  "recognises a book by its first bytes, so it will treat this "
                  "one as new and start its statistics and reading position "
                  "over.").format(title), show=True)

        info_dialog(
            gui, _("X-Ray embedded"),
            _('X-Ray data was embedded into "{0}". Send the book to your reader '
              "as usual.").format(title),
            show=True)


def _result_is_sound(path, doc):
    """A bug in embedding must never turn into permanent data loss: the zip has
    to be structurally sound, the document has to round-trip byte-for-byte, and
    the EPUB has to still parse."""
    try:
        with zipfile.ZipFile(path) as zf:
            if zf.testzip() is not None:
                return False
        if read_embedded(path) != doc:
            return False
        read_epub(path)
    except Exception:
        return False
    return True


def _add_tag(db, book_id):
    """Add the X-Ray tag, keeping the book's existing tags. set_field is a
    setter, not a merger -- passing just the new tag would silently wipe every
    other tag on the book."""
    tags = tuple(db.field_for("tags", book_id) or ())
    if TAG not in tags:
        db.set_field("tags", {book_id: tags + (TAG,)})
