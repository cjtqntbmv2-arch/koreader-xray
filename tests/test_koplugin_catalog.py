"""The device plugin's German catalog must cover every string the code asks for.

xray_i18n.lua looks translations up by their English source string and falls
back to that string when a key is missing -- silently. The first build of the
rewrite shipped 46 of 64 strings untranslated that way and still "worked", so
the drift is invisible without a check. This is that check; it runs in the same
CI job as the rest of the suite.

Lua-side test in the Python suite on purpose: it is the only test runner CI
executes, and a rule nobody runs is not a rule.
"""
import re
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent / "xray.koplugin"
_CATALOG = _PLUGIN_DIR / "languages" / "de.po"

# _("...") with double quotes; escaped quotes inside are kept intact.
_CALL_RE = re.compile(r'_\(\s*"((?:[^"\\]|\\.)*)"\s*\)')
_MSGID_RE = re.compile(r'^msgid\s+"((?:[^"\\]|\\.)*)"', re.MULTILINE)


def _keys_used():
    keys = set()
    for lua in sorted(_PLUGIN_DIR.glob("*.lua")):
        keys |= set(_CALL_RE.findall(lua.read_text(encoding="utf-8")))
    return keys


def _keys_translated():
    ids = set(_MSGID_RE.findall(_CATALOG.read_text(encoding="utf-8")))
    ids.discard("")  # the PO header entry
    return ids


def test_every_string_has_a_german_translation():
    missing = sorted(_keys_used() - _keys_translated())
    assert not missing, (
        "these strings would silently show in English:\n  "
        + "\n  ".join(repr(m) for m in missing)
    )


def test_catalog_has_no_dead_entries():
    """A leftover entry is a renamed string that lost its translation somewhere
    else -- worth the same attention as a missing one."""
    dead = sorted(_keys_translated() - _keys_used())
    assert not dead, (
        "these catalog entries are not used by any _() call:\n  "
        + "\n  ".join(repr(d) for d in dead)
    )


def test_placeholders_survive_translation():
    """A translation that drops or adds a %s/%d breaks string.format at runtime,
    on the device, in the error path that was trying to tell you something."""
    text = _CATALOG.read_text(encoding="utf-8")
    pairs = re.findall(
        r'^msgid\s+"((?:[^"\\]|\\.)*)"\s*\nmsgstr\s+"((?:[^"\\]|\\.)*)"',
        text, re.MULTILINE)
    spec = re.compile(r"%[sd%%]")
    mismatched = [
        (src, dst) for src, dst in pairs
        if sorted(spec.findall(src)) != sorted(spec.findall(dst))
    ]
    assert not mismatched, "placeholder mismatch:\n  " + "\n  ".join(
        f"{src!r} -> {dst!r}" for src, dst in mismatched)
