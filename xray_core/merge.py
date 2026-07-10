"""Entity cleaning + checkpoint merge/staging (Lua port).

Ports `xray_data.lua`'s dedup/promote/stamp/sort logic (`deduplicateByName`
~223-289, `isMoreCompleteName` ~184-198, `stampFirstAppearance` ~176-182,
`sortByFirstAppearance`/`sortByName`/`sortDataByFrequency` ~130-172), the
per-field defaulting from `AIHelper:validateAndCleanData` in
`xray_aihelper.lua` (function starts ca. line 2008), and the
checkpoint-merge field rules from `xray_fetch.lua` (description/definition
= newest non-empty, terms union aliases instead of overwriting).

Lua line numbers in this module are approximate ("ca.") and anchored to
KOReader-repo commit `42074d9` -- they drift as that repo moves, so prefer
the named function/file over the number when hunting for the source.

Stdlib-only on purpose (see xray_core/epub.py).
"""

import copy
import re

from xray_core.checkpoints import is_non_narrative

# Alternative keys the model sometimes emits, verbatim from the fallback
# chains inside `AIHelper:validateAndCleanData` (`xray_aihelper.lua`, ca.
# lines 2021-2054). Only `c.Name` is dead code there: a pure case-duplicate
# of `c.name`, already handled once `AIHelper:parseAIResponse` (ca. line
# 2003) lower-cases keys via `validateAndCleanData(normalizeKeys(data))`
# before this runs -- so it's dropped here without a Python equivalent.
# `l.Lugar` is different: a genuine third alternative key (Spanish for
# "place"), not a duplicate of anything else in its chain -- it lives on
# below as `"lugar"` in `_LOC_NAME_KEYS`. We rely on the same lower-casing
# precondition -- see clean_response's docstring.
_CHAR_NAME_KEYS = ("name", "full_formal_name", "full_name", "formal_name")
_CHAR_DESC_KEYS = ("description", "bio", "history", "desc")
_CHAR_OCCUPATION_KEYS = ("occupation", "job")
_LOC_NAME_KEYS = ("name", "place", "lugar")
_LOC_DESC_KEYS = ("description", "desc", "short_desc")
_LOC_IMPORTANCE_KEYS = ("importance", "significance")
_HIST_NAME_KEYS = ("name",)
_HIST_BIO_KEYS = ("biography", "bio", "description")
_HIST_ROLE_KEYS = ("role", "historical_role")
_HIST_IMPORTANCE_KEYS = ("importance_in_book", "significance")
_HIST_CONTEXT_KEYS = ("context_in_book", "context")

# `unnamed_character` / `unnamed_person` verbatim from the fallback-strings
# table in `prompts/en.lua` (ca. line 323) / `prompts/de.lua` (ca. line 362).
# `unknown_place` is NOT in that table -- `AIHelper:validateAndCleanData`
# hardcodes the English literal directly (ca. line 2052) even for German
# books; the German wording here is ours.
#
# Deliberate divergence: `validateAndCleanData` also defaults role,
# description, biography, importance_in_book and context_in_book to
# localized (or, for the latter two, hardcoded-English) placeholders. We
# leave those empty instead -- the device's card renderer in `xray_ui.lua`
# (e.g. ca. line 190) skips empty fields entirely, so a placeholder would
# only add visible noise to every card the model knew nothing about, and a
# non-empty value would block a later segment from filling the gap.
_FALLBACKS = {
    "en": {
        "unnamed_character": "Unnamed Character",
        "unnamed_person": "Unnamed Person",
        "unknown_place": "Unknown Place",
    },
    "de": {
        "unnamed_character": "Unbenannter Charakter",
        "unnamed_person": "Unbenannte Person",
        "unknown_place": "Unbekannter Ort",
    },
}


def fallback_strings(language: str) -> dict:
    """Localized placeholder names; unknown languages fall back to English."""
    return _FALLBACKS.get(language, _FALLBACKS["en"])


def _str(d: dict, key: str, default: str = "") -> str:
    v = d.get(key)
    return v if isinstance(v, str) and v else default


def _first_nonempty(d: dict, keys, default: str) -> str:
    """Return the first present-and-non-empty string value among `keys`.

    Deliberate divergence from Lua: `ensureString(c.description or c.bio or
    c.history or c.desc, default)` uses Lua's `or`, where the empty string
    is truthy (only `nil` and `false` are falsy in Lua) -- so that chain
    stops at the first key that merely *exists*, even if its value is `""`,
    and `ensureString` then returns the default for it. E.g. for
    `{"name": "A", "description": "", "bio": "echter Text"}`, Lua yields the
    placeholder even though "echter Text" is sitting right there in `bio`.
    Python treats "present but empty" as "missing" and keeps walking the
    chain instead, on purpose: an empty value from one key must not block a
    real value the model supplied under a later, alternative key.
    """
    for key in keys:
        v = d.get(key)
        if isinstance(v, str) and v:
            return v
    return default


def _aliases(d: dict) -> list:
    v = d.get("aliases")
    return [a for a in v if isinstance(a, str) and a] if isinstance(v, list) else []


def clean_response(raw: dict, language: str = "en") -> dict:
    """Port of `validateAndCleanData`'s per-field defaulting (essentials).

    PRECONDITION: `raw`'s keys are already lower-cased by
    `gemini.normalize_keys` (`gemini.py:192`), exactly as Lua couples the
    two in `AIHelper:parseAIResponse` (`xray_aihelper.lua`, ca. line 2003:
    `validateAndCleanData(normalizeKeys(data))`). Calling this with raw
    model output that skipped that step will silently miss upper-case keys.

    Nameless characters/locations are KEPT with a placeholder name (the
    `name` fallback chains inside `AIHelper:validateAndCleanData`,
    `xray_aihelper.lua`, ca. lines 2021 and 2052) -- never dropped, so a
    character or place the AI described but couldn't name never silently
    disappears.
    """
    strings = fallback_strings(language)
    characters = [
        {
            "name": _first_nonempty(c, _CHAR_NAME_KEYS, strings["unnamed_character"]),
            "role": _str(c, "role")[:40],
            "description": _first_nonempty(c, _CHAR_DESC_KEYS, ""),
            "gender": _str(c, "gender"),
            "occupation": _first_nonempty(c, _CHAR_OCCUPATION_KEYS, ""),
            "aliases": _aliases(c),
        }
        for c in raw.get("characters") or []
        if isinstance(c, dict)
    ]

    locations = [
        {
            "name": _first_nonempty(loc, _LOC_NAME_KEYS, strings["unknown_place"]),
            "description": _first_nonempty(loc, _LOC_DESC_KEYS, ""),
            "importance": _first_nonempty(loc, _LOC_IMPORTANCE_KEYS, ""),
            "aliases": _aliases(loc),
        }
        for loc in raw.get("locations") or []
        if isinstance(loc, dict)
    ]

    historical_figures = [
        {
            "name": _first_nonempty(h, _HIST_NAME_KEYS, strings["unnamed_person"]),
            "biography": _first_nonempty(h, _HIST_BIO_KEYS, ""),
            "role": _first_nonempty(h, _HIST_ROLE_KEYS, "")[:40],
            "importance_in_book": _first_nonempty(h, _HIST_IMPORTANCE_KEYS, ""),
            "context_in_book": _first_nonempty(h, _HIST_CONTEXT_KEYS, ""),
        }
        for h in raw.get("historical_figures") or []
        if isinstance(h, dict)
    ]

    # No name-fallback chain/placeholder here: Lua's validateAndCleanData
    # doesn't clean terms at all (they pass through raw), and an unnamed
    # glossary entry has nothing to look up by -- unlike a nameless
    # character/location, there's no entity to protect from being dropped.
    terms = [
        {
            "name": _str(t, "name"),
            "aliases": _aliases(t),
            "expanded": _str(t, "expanded"),
            "category": _str(t, "category"),
            "definition": _str(t, "definition"),
        }
        for t in raw.get("terms") or []
        if isinstance(t, dict)
    ]

    timeline = [
        {"chapter": _str(ev, "chapter"), "event": _str(ev, "event")}
        for ev in raw.get("timeline") or []
        if isinstance(ev, dict)
    ]

    return {
        "characters": characters,
        "locations": locations,
        "historical_figures": historical_figures,
        "terms": terms,
        "timeline": timeline,
        "book_type": "non_fiction" if raw.get("book_type") == "non_fiction" else "fiction",
    }


def is_more_complete_name(new, old) -> bool:
    """Port of `isMoreCompleteName` (`xray_data.lua:184-198`).

    Deliberate divergence: uses Python's Unicode-aware `\\w` rather than
    Lua's ASCII-only `%f[%w]` frontier pattern, so a German name bounded by
    an umlaut is still classified correctly.
    """
    if not new or not old or len(new) <= len(old):
        return False
    nl, ol = new.lower(), old.lower()
    if re.search(r"(?<!\w)" + re.escape(ol) + r"(?!\w)", nl):
        return True
    return nl.startswith(ol) or nl.endswith(ol)


def _promote_name(entity: dict, new_name: str, alias_map: dict) -> None:
    """Port of `promoteName` (`xray_data.lua:200-221`): old name -> aliases."""
    old_name = entity.get("name") or ""
    aliases = entity.setdefault("aliases", [])
    if not any(a.lower() == old_name.lower() for a in aliases):
        aliases.append(old_name)
    entity["name"] = new_name
    alias_map[old_name.lower()] = entity


_ROLE_WEIGHT_RULES = (
    (("protagonist",), 100),
    (("main", "lead", "hero", "detective"), 90),
    (("deuteragonist",), 80),
    (("major", "antagonist", "villain", "primary"), 70),
    (("secondary", "supporting"), 30),
    (("minor", "background"), 5),
)


def _role_weight(role) -> int:
    """Port of `sortDataByFrequency`'s `getRoleScore` (`xray_data.lua:52-62`),
    weights only -- no text-frequency signal (desktop has no cheap source
    and the importer re-sorts anyway, see brief)."""
    r = (role or "").lower()
    for keywords, weight in _ROLE_WEIGHT_RULES:
        if any(kw in r for kw in keywords):
            return weight
    return 15


def sort_entity_list(lst: list, kind: str) -> list:
    """Port of `sortEntityList`'s dispatch (`xray_data.lua:164-172`)."""
    if kind in ("character", "location"):
        big = 10**9
        return sorted(lst, key=lambda e: (e.get("first_pct", big), e.get("first_seq", big)))
    if kind == "term":
        return sorted(lst, key=lambda e: (e.get("name") or "").lower())
    return sorted(lst, key=lambda e: _role_weight(e.get("role")), reverse=True)


class BookState:
    """Accumulates cleaned segments across checkpoints into staged snapshots."""

    def __init__(self):
        self.characters: list = []
        self.locations: list = []
        self.terms: list = []
        self.historical_figures: list = []
        self.timeline: list = []
        self.book_type: str = "fiction"
        self._seq: int = 0

    def _merge(self, existing, incoming, *, newest_wins, fill_if_empty, stamp, checkpoint_pct):
        """Port of `deduplicateByName` (`xray_data.lua:223-289`) fused with
        the checkpoint-merge field rules (brief rules 3/4/6). Mutates
        `existing` in place; `incoming` items become the stored objects for
        whichever names are brand new this call.
        """
        seen, alias_map = {}, {}
        for item in existing:
            k = (item.get("name") or "").lower()
            if not k:
                continue  # nameless entries never collide (xray_data.lua:232-234)
            seen[k] = item
            for alias in item.get("aliases") or []:
                if alias:
                    alias_map[alias.lower()] = item

        for item in incoming:
            k = (item.get("name") or "").lower()
            match = seen.get(k) if k else None
            if match is None and k:
                match = alias_map.get(k)

            if match is None:
                existing.append(item)
                if k:
                    seen[k] = item
                    for alias in item.get("aliases") or []:
                        if alias:
                            alias_map[alias.lower()] = item
                if stamp and item.get("first_pct") is None:
                    self._seq += 1
                    item["first_pct"] = checkpoint_pct
                    item["first_seq"] = self._seq
                continue

            new_name = item.get("name") or ""
            if new_name and is_more_complete_name(new_name, match.get("name") or ""):
                _promote_name(match, new_name, alias_map)
                seen[new_name.lower()] = match

            own_lower = (match.get("name") or "").lower()
            alias_lower_set = {a.lower() for a in match.get("aliases") or []}
            for alias in item.get("aliases") or []:
                al = alias.lower() if alias else ""
                if not al or al == own_lower or al in alias_lower_set:
                    continue
                match.setdefault("aliases", []).append(alias)
                alias_lower_set.add(al)
                alias_map[al] = match

            for field in newest_wins:
                if item.get(field):
                    match[field] = item[field]
            for field in fill_if_empty:
                if not match.get(field) and item.get(field):
                    match[field] = item[field]

    def merge_segment(self, cleaned: dict, checkpoint_pct: int) -> None:
        self._merge(
            self.characters, cleaned.get("characters") or [],
            newest_wins=("description",), fill_if_empty=("role", "gender", "occupation"),
            stamp=True, checkpoint_pct=checkpoint_pct,
        )
        self._merge(
            self.locations, cleaned.get("locations") or [],
            newest_wins=("description",), fill_if_empty=("importance",),
            stamp=True, checkpoint_pct=checkpoint_pct,
        )
        # Terms union aliases on an exact-name hit (brief rule 6) -- a
        # deliberate divergence from Lua's wholesale alias overwrite, which
        # would drop an alias a later segment simply doesn't repeat.
        self._merge(
            self.terms, cleaned.get("terms") or [],
            newest_wins=("definition",), fill_if_empty=("expanded", "category"),
            stamp=False, checkpoint_pct=checkpoint_pct,
        )
        self._merge(
            self.historical_figures, cleaned.get("historical_figures") or [],
            newest_wins=("biography",),
            fill_if_empty=("role", "importance_in_book", "context_in_book"),
            stamp=False, checkpoint_pct=checkpoint_pct,
        )

        for ev in cleaned.get("timeline") or []:
            if is_non_narrative(ev.get("chapter")):
                continue
            self.timeline.append({**ev, "pct": checkpoint_pct})

        # ponytail: last cleaned segment's book_type wins (no majority vote
        # across segments) -- clean_response always yields a value so this
        # is an unconditional overwrite; add tie-breaking if a book ever
        # flips classification mid-merge in practice.
        if cleaned.get("book_type"):
            self.book_type = cleaned["book_type"]

    def snapshot(self) -> dict:
        """Deep-copied, sorted snapshot -- later mutation of this BookState
        must never leak into a snapshot already handed out (D4)."""
        return copy.deepcopy(
            {
                "characters": sort_entity_list(self.characters, "character"),
                "locations": sort_entity_list(self.locations, "location"),
                "terms": sort_entity_list(self.terms, "term"),
                "historical_figures": sort_entity_list(
                    self.historical_figures, "historical_figure"
                ),
            }
        )
