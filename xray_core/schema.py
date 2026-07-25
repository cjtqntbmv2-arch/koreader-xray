"""Validator for the xray.json interchange format (schema v1).

Hand-rolled and stdlib-only on purpose: `xray_core` must import cleanly
without `calibre` or third-party packages (no `jsonschema`), since it is
shared between the CLI, the calibre plugin, and plain pytest.

`schema/xray.schema.json` documents the same contract (draft-07 JSON Schema)
for humans/tooling and is kept in sync with this module by hand.
"""

from typing import TypeGuard

from xray_core.merge import _PLACEHOLDER_NAMES

# v2 (2026-07-25): snippet_anchor/chapter_anchor dropped from checkpoints --
# the device maps its reading position straight onto `percent` and needs no
# per-checkpoint marker to search for.
SCHEMA_VERSION = 2

# Top-level required keys and their expected Python type.
_TOP_LEVEL_TYPES = {
    "schema_version": int,
    "generator": str,
    "generator_version": str,
    "detail_level": str,
    "language": str,
    "book_fingerprint": dict,
    "complete": bool,
    "last_percent": int,
    "book_type": str,
    "timeline": list,
    "checkpoints": list,
}

_FINGERPRINT_TYPES = {
    "calibre_uuid": str,
    "title": str,
    "authors": list,
    "text_hash": str,
}

# Only characters/locations carry chronology fields (terms are alphabetical,
# historical figures are ordered by role weight) -- see project CLAUDE.md.
_CHRONOLOGY_LISTS = ("characters", "locations")
_CHRONOLOGY_FIELDS = ("name", "first_pct", "first_seq")
_SNAPSHOT_LISTS = ("characters", "locations", "terms", "historical_figures")


def _is_strict_int(value) -> TypeGuard[int]:
    """True for a real int, false for bool (bool is a subclass of int).

    Typed as a TypeGuard so `_is_strict_int(x) and x < 0` narrows `x` for the
    type checker -- callers rely on that short-circuit to compare bounds.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def validate(doc: dict) -> list[str]:
    """Check `doc` against the xray.json v1 contract.

    Returns a list of human-readable problem descriptions; an empty list
    means the document is valid.
    """
    if not isinstance(doc, dict):
        return ["document must be an object"]

    problems: list[str] = []

    for key, expected_type in _TOP_LEVEL_TYPES.items():
        if key not in doc:
            problems.append(f"missing required field: {key}")
            continue
        value = doc[key]
        if expected_type is int:
            if not _is_strict_int(value):
                problems.append(f"{key} must be an int")
        elif not isinstance(value, expected_type):
            problems.append(f"{key} must be of type {expected_type.__name__}")

    if "schema_version" in doc and doc["schema_version"] != SCHEMA_VERSION:
        problems.append(
            f"schema_version must be {SCHEMA_VERSION}, got {doc['schema_version']!r}"
        )

    fingerprint = doc.get("book_fingerprint")
    if isinstance(fingerprint, dict):
        for key, expected_type in _FINGERPRINT_TYPES.items():
            if key not in fingerprint:
                problems.append(f"book_fingerprint missing required field: {key}")
            elif not isinstance(fingerprint[key], expected_type):
                problems.append(
                    f"book_fingerprint.{key} must be of type {expected_type.__name__}"
                )

    checkpoints = doc.get("checkpoints")
    if isinstance(checkpoints, list):
        problems.extend(_validate_checkpoints(checkpoints, doc.get("last_percent")))

    if isinstance(fingerprint, dict) and isinstance(fingerprint.get("authors"), list):
        problems.extend(
            f"book_fingerprint.authors[{i}] must be a string"
            for i, a in enumerate(fingerprint["authors"])
            if not isinstance(a, str)
        )

    timeline = doc.get("timeline")
    if isinstance(timeline, list):
        problems.extend(_validate_timeline(timeline))

    return problems


def _validate_checkpoints(checkpoints: list, last_percent) -> list[str]:
    problems: list[str] = []
    prev_percent = None

    for i, cp in enumerate(checkpoints):
        label = f"checkpoints[{i}]"
        if not isinstance(cp, dict):
            problems.append(f"{label} must be an object")
            continue

        percent = cp.get("percent")
        if not isinstance(percent, int) or isinstance(percent, bool) or not (1 <= percent <= 100):
            problems.append(f"{label}.percent must be an int between 1 and 100")
        else:
            if prev_percent is not None and percent <= prev_percent:
                problems.append(
                    f"{label}.percent must strictly ascend across checkpoints "
                    f"(got {percent} after {prev_percent})"
                )
            prev_percent = percent

        snapshot = cp.get("snapshot")
        if not isinstance(snapshot, dict):
            problems.append(f"{label}.snapshot must be an object")
            continue
        for list_name in _SNAPSHOT_LISTS:
            entries = snapshot.get(list_name)
            if not isinstance(entries, list):
                problems.append(f"{label}.snapshot.{list_name} must be a list")
                continue

            # Contract guardrail, not a currently-reachable bug: BookState._merge
            # (xray_core/merge.py) updates its `seen` name map the instant an item
            # is appended -- not just once up front -- so same-named entries
            # already collapse into one within a single merge call, and
            # characters/locations/historical_figures always get a non-empty
            # placeholder name when the model omits one. Today's pipeline can't
            # actually emit a duplicate here. Kept anyway, same bet as the D4
            # first_pct guard in _validate_chronology_entry: cheap insurance
            # against a future _merge regression or any other producer of this
            # format. Nameless entries (e.g. an unnamed term) are exempt --
            # merge.py never collides those either, so we don't invent a
            # stricter rule than the pipeline itself relies on. Placeholder
            # names (Unnamed Character, ...) are exempt for the same reason:
            # two distinct nameless entities legitimately share one placeholder
            # and merge.py keeps them separate rather than drop a description.
            seen_names = set()
            for j, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                key = (entry.get("name") or "").strip().lower()
                if key in _PLACEHOLDER_NAMES:
                    key = ""
                if key and key in seen_names:
                    problems.append(
                        f"{label}.snapshot.{list_name}[{j}] duplicate name: {entry['name']!r}"
                    )
                seen_names.add(key)

            if list_name in _CHRONOLOGY_LISTS:
                for j, entry in enumerate(entries):
                    problems.extend(
                        _validate_chronology_entry(
                            entry, f"{label}.snapshot.{list_name}[{j}]", percent
                        )
                    )

    if checkpoints and prev_percent is not None and prev_percent != last_percent:
        problems.append(
            f"last checkpoint percent ({prev_percent}) must equal "
            f"last_percent ({last_percent!r})"
        )

    return problems


def _validate_chronology_entry(entry, label: str, checkpoint_percent) -> list[str]:
    if not isinstance(entry, dict):
        return [f"{label} must be an object"]

    problems = [
        f"{label} missing required field: {field}"
        for field in _CHRONOLOGY_FIELDS
        if field not in entry
    ]
    if "name" in entry and (not isinstance(entry["name"], str) or not entry["name"].strip()):
        problems.append(f"{label}.name must be a non-empty string")
    for field, minimum in (("first_pct", 0), ("first_seq", 1)):
        if field not in entry:
            continue
        value = entry[field]
        if not _is_strict_int(value):
            problems.append(f"{label}.{field} must be an int")
        elif value < minimum:
            problems.append(f"{label}.{field} must be >= {minimum}")

    # D4 structural guardrail: an entity must never be stamped as first
    # appearing AFTER the checkpoint it's snapshotted in -- that's exactly
    # the shape of a future-entity spoiler leak (see xray_core/generate.py
    # _enrich_checkpoint history). Turns any future regression of that kind
    # into a hard validation error instead of a silent leak.
    if _is_strict_int(entry.get("first_pct")) and _is_strict_int(checkpoint_percent):
        if entry["first_pct"] > checkpoint_percent:
            name = entry.get("name", "<unnamed>")
            problems.append(
                f"{label} ({name!r}) first_pct ({entry['first_pct']}) must be "
                f"<= checkpoint percent ({checkpoint_percent})"
            )
    return problems


def _validate_timeline(timeline: list) -> list[str]:
    """The device reads `timeline` top-level and gates each event on `pct`
    (`xray_import.lua:326-336`); an event without a valid `pct` is silently
    hidden there. Catch it here instead of shipping data the reader never sees."""
    problems: list[str] = []
    for i, ev in enumerate(timeline):
        label = f"timeline[{i}]"
        if not isinstance(ev, dict):
            problems.append(f"{label} must be an object")
            continue
        for field in ("chapter", "event"):
            if not isinstance(ev.get(field), str):
                problems.append(f"{label}.{field} must be a string")
        pct = ev.get("pct")
        # pct=0 is rejected, not just out-of-range values: in the KOReader
        # importer, tonumber(0) is truthy in Lua, so pctToPage(0, ...) runs
        # and clamps to page 1 -- showing the event from checkpoint 1 onward
        # instead of hiding it like a missing pct would (xray_import.lua).
        if not (_is_strict_int(pct) and 1 <= pct <= 100):
            problems.append(f"{label}.pct must be an int between 1 and 100")
    return problems
