"""Validator for the xray.json interchange format (schema v1).

Hand-rolled and stdlib-only on purpose: `xray_core` must import cleanly
without `calibre` or third-party packages (no `jsonschema`), since it is
shared between the CLI, the calibre plugin, and plain pytest.

`schema/xray.schema.json` documents the same contract (draft-07 JSON Schema)
for humans/tooling and is kept in sync with this module by hand.
"""

SCHEMA_VERSION = 1

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


def _is_strict_int(value) -> bool:
    """True for a real int, false for bool (bool is a subclass of int)."""
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

        snippet_anchor = cp.get("snippet_anchor")
        if not isinstance(snippet_anchor, str) or not snippet_anchor.strip():
            problems.append(f"{label}.snippet_anchor must be a non-empty string")

        snapshot = cp.get("snapshot")
        if not isinstance(snapshot, dict):
            problems.append(f"{label}.snapshot must be an object")
            continue
        for list_name in _SNAPSHOT_LISTS:
            entries = snapshot.get(list_name)
            if not isinstance(entries, list):
                problems.append(f"{label}.snapshot.{list_name} must be a list")
                continue
            if list_name in _CHRONOLOGY_LISTS:
                for j, entry in enumerate(entries):
                    problems.extend(
                        _validate_chronology_entry(
                            entry, f"{label}.snapshot.{list_name}[{j}]"
                        )
                    )

    if checkpoints and prev_percent is not None and prev_percent != last_percent:
        problems.append(
            f"last checkpoint percent ({prev_percent}) must equal "
            f"last_percent ({last_percent!r})"
        )

    return problems


def _validate_chronology_entry(entry, label: str) -> list[str]:
    if not isinstance(entry, dict):
        return [f"{label} must be an object"]

    problems = [
        f"{label} missing required field: {field}"
        for field in _CHRONOLOGY_FIELDS
        if field not in entry
    ]
    if "name" in entry and (not isinstance(entry["name"], str) or not entry["name"].strip()):
        problems.append(f"{label}.name must be a non-empty string")
    for field in ("first_pct", "first_seq"):
        if field in entry and not _is_strict_int(entry[field]):
            problems.append(f"{label}.{field} must be an int")
    return problems
