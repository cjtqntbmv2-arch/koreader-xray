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


# Every placeholder name across every language, lower-cased. clean_response
# stamps nameless entities with one of these (per-language), so at merge time
# two GENUINELY DISTINCT nameless entities would otherwise collide on the
# identical placeholder key and drop one via newest_wins. A nameless entity is
# un-dedupable by name, so -- like a truly-empty name (xray_data.lua:232-234) --
# a placeholder name must never collide. Built across all languages because a
# resumed workdir can carry chunks cleaned under a different language.
_PLACEHOLDER_NAMES = {v.lower() for lang in _FALLBACKS.values() for v in lang.values()}


# Leading title tokens that name one entity across surface forms ("Ser Jaime
# Lennister" == "Jaime Lennister"). Keyed by language like `fallback_strings`;
# unknown languages fall back to English. Extend by adding to a set. Lua has no
# equivalent (its `deduplicateByName` keys on the raw name), so this is a
# deliberate desktop-side extension of the NAME DISAMBIGUATION rules.
_HONORIFICS = {
    "en": {"ser", "lord", "lady", "king", "queen", "prince", "maester",
           "septa", "khal", "magister"},
    "de": {"ser", "lord", "lady", "könig", "königin", "prinz", "prinzessin",
           "maester", "septa", "septon", "khal", "magister", "meister"},
}

# Canonical Roman-numeral matcher (case-insensitive), used to spot regnal
# ordinals like "II"/"IV." in a name.
_ROMAN_RE = re.compile(r"m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})", re.I)


def _honorifics(language: str) -> set:
    return _HONORIFICS.get(language, _HONORIFICS["en"])


def _is_ordinal(token: str) -> bool:
    """A regnal ordinal ("II", "IV.") -- a Roman numeral of length >= 2, with
    an optional trailing dot. The length floor is the safety line: a lone
    "L."/"M." middle initial is a valid Roman numeral too, and stripping it
    would collapse "David L. Roth" and "David M. Roth" into one person.
    """
    t = token.rstrip(".")
    return len(t) >= 2 and _ROMAN_RE.fullmatch(t) is not None


def _strip_leading_honorifics(name: str, language: str) -> str:
    """Drop leading title tokens (Ser/Lord/König/...), preserving the case of
    the rest. Never strips to empty: a name that is *only* a title keeps it."""
    hon = _honorifics(language)
    toks = (name or "").split()
    i = 0
    while i < len(toks) - 1 and toks[i].rstrip(".").lower() in hon:
        i += 1
    return " ".join(toks[i:]) if toks else (name or "")


def _has_leading_honorific(name: str, language: str) -> bool:
    toks = (name or "").split()
    return len(toks) > 1 and toks[0].rstrip(".").lower() in _honorifics(language)


def _dedup_key(name: str, language: str) -> str:
    """Collision key for entity dedup: leading honorifics dropped and regnal
    ordinals removed, lower-cased, whitespace-collapsed. Cross-form variants
    of one entity ("Ser Jaime Lennister"/"Jaime Lennister", "Aerys II.
    Targaryen"/"Aerys Targaryen") map to the same key -- while a bare first
    name ("Robert") never collides with a full name ("Robert Baratheon") and
    two people differing only by ordinal ("Heinrich IV."/"Heinrich VIII.")
    keep distinct keys. The ordinal is only removed when >= 2 non-ordinal
    tokens remain, so a name distinguished *solely* by its ordinal is never
    reduced to a shared bare first name.

    Placeholder names ("Unnamed Character", ...) collapse to the EMPTY key so
    two genuinely distinct nameless entities never collide -- they are
    un-dedupable by name, exactly like a truly-empty name (xray_data.lua:
    232-234).
    """
    if (name or "").strip().lower() in _PLACEHOLDER_NAMES:
        return ""
    toks = _strip_leading_honorifics(name, language).lower().split()
    non_ord = [t for t in toks if not _is_ordinal(t)]
    if len(non_ord) >= 2:
        toks = non_ord
    return " ".join(toks)


def _pick_canonical(existing: str, incoming: str, language: str) -> str:
    """Display name to keep when two surface forms collide. Prefer the form
    WITHOUT a leading title ("Jaime Lennister" beats "Ser Jaime Lennister");
    among forms of equal title-status, keep the existing name unless the
    incoming one is strictly more complete."""
    if not incoming:
        return existing
    if not existing:
        return incoming
    ex_h = _has_leading_honorific(existing, language)
    in_h = _has_leading_honorific(incoming, language)
    if ex_h and not in_h:
        return incoming
    if in_h and not ex_h:
        return existing
    return incoming if is_more_complete_name(incoming, existing, language) else existing


def _str(d: dict, key: str, default: str = "") -> str:
    """Deliberate divergence from Lua's `ensureString` (`xray_aihelper.lua:
    2014`: `(type(v) == "string" and #v > 0) and v or d or ""`), which only
    checks length and never strips: we also strip whitespace and treat a
    value that's empty afterwards as missing. Without this, `bool("   ")`
    being True in Python let a whitespace-only model value pass every
    truthy check downstream -- including `BookState._merge`'s `newest_wins`
    overwrite -- as if it were real content.
    """
    v = d.get(key)
    v = v.strip() if isinstance(v, str) else ""
    return v or default


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

    "Empty" includes whitespace-only, checked after stripping -- same
    divergence as `_str` and same reason (Lua's `ensureString` checks only
    `#v > 0` and never strips).
    """
    for key in keys:
        v = d.get(key)
        v = v.strip() if isinstance(v, str) else ""
        if v:
            return v
    return default


def _aliases(d: dict) -> list:
    # ponytail: filters only the exact empty string, not whitespace-only
    # (same root cause as _str/_first_nonempty above) -- left out of this
    # fix on purpose. Aliases union rather than overwrite (xray_data.lua
    # dedup), so a whitespace-only alias is cosmetic list noise, not the
    # data-loss overwrite this fix targets. Give it the same `.strip()`
    # treatment here if that noise ever turns out to matter.
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
            # rstrip AFTER the cut: a 40-char slice can end mid-space, and a
            # second pass over this output would strip that space away. Without
            # it clean_response is not a fixpoint -- generate.py re-cleans
            # cached chunks on resume, so a resumed run would differ from a
            # fresh one by exactly that trailing byte.
            "role": _str(c, "role")[:40].rstrip(),
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
            "role": _first_nonempty(h, _HIST_ROLE_KEYS, "")[:40].rstrip(),  # fixpoint, see above
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


def is_more_complete_name(new, old, language: str = "en") -> bool:
    """Port of `isMoreCompleteName` (`xray_data.lua:184-198`).

    Deliberate divergence: uses Python's Unicode-aware `\\w` rather than
    Lua's ASCII-only `%f[%w]` frontier pattern, so a German name bounded by
    an umlaut is still classified correctly.

    Second divergence (companion to the honorific-aware dedup key): leading
    title tokens are stripped from both sides first, so "Jaime Lennister" is
    not judged "more complete" than "Ser Jaime Lennister" merely by length.
    """
    new = _strip_leading_honorifics(new or "", language)
    old = _strip_leading_honorifics(old or "", language)
    if not new or not old or len(new) <= len(old):
        return False
    nl, ol = new.lower(), old.lower()
    if re.search(r"(?<!\w)" + re.escape(ol) + r"(?!\w)", nl):
        return True
    return nl.startswith(ol) or nl.endswith(ol)


def _add_alias(entity: dict, alias: str, alias_map: dict, language: str) -> None:
    """Record `alias` as an alternative surface form of `entity`.

    Generalizes `promoteName` (`xray_data.lua:200-221`, "old name -> aliases"):
    used both to demote a superseded display name and to keep a stripped
    title/ordinal variant a user might want shown. No-op on the display name or
    a literal duplicate; `alias_map` is keyed by `_dedup_key` so the variant
    still routes future segments to this entity.
    """
    if not alias:
        return
    al = alias.lower()
    if al != (entity.get("name") or "").lower():
        aliases = entity.setdefault("aliases", [])
        if not any((a or "").lower() == al for a in aliases):
            aliases.append(alias)
    alias_map[_dedup_key(alias, language)] = entity


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

    def __init__(self, language: str = "en"):
        # language selects the honorific set for cross-form name dedup
        # (_dedup_key); it must match the language clean_response ran under.
        self.language: str = language
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
        lang = self.language
        seen, alias_map = {}, {}
        for item in existing:
            k = _dedup_key(item.get("name") or "", lang)
            if not k:
                continue  # nameless entries never collide (xray_data.lua:232-234)
            seen[k] = item
            for alias in item.get("aliases") or []:
                if alias:
                    alias_map[_dedup_key(alias, lang)] = item

        for item in incoming:
            k = _dedup_key(item.get("name") or "", lang)
            match = seen.get(k) if k else None
            if match is None and k:
                match = alias_map.get(k)

            if match is None:
                existing.append(item)
                if k:
                    seen[k] = item
                    for alias in item.get("aliases") or []:
                        if alias:
                            alias_map[_dedup_key(alias, lang)] = item
                if stamp and item.get("first_pct") is None:
                    self._seq += 1
                    item["first_pct"] = checkpoint_pct
                    item["first_seq"] = self._seq
                continue

            # Same entity, different surface form. Choose the display name --
            # _pick_canonical prefers the honorific-stripped form -- and keep
            # every other form as an alias so a title/ordinal a user might want
            # shown is never silently dropped.
            new_name = item.get("name") or ""
            old_name = match.get("name") or ""
            canonical = _pick_canonical(old_name, new_name, lang)
            if canonical.lower() != old_name.lower():
                match["name"] = canonical  # set first: _add_alias skips the display name
                seen[_dedup_key(canonical, lang)] = match
                _add_alias(match, old_name, alias_map, lang)
            if new_name and new_name.lower() != canonical.lower():
                _add_alias(match, new_name, alias_map, lang)

            own_lower = (match.get("name") or "").lower()
            alias_lower_set = {a.lower() for a in match.get("aliases") or []}
            for alias in item.get("aliases") or []:
                al = alias.lower() if alias else ""
                if not al or al == own_lower or al in alias_lower_set:
                    continue
                match.setdefault("aliases", []).append(alias)
                alias_lower_set.add(al)
                alias_map[_dedup_key(alias, lang)] = match

            for field in newest_wins:
                if item.get(field):
                    match[field] = item[field]
            for field in fill_if_empty:
                if not match.get(field) and item.get(field):
                    match[field] = item[field]

    def merge_segment(self, cleaned: dict, checkpoint_pct: int) -> None:
        self._merge(
            self.characters, cleaned.get("characters") or [],
            # `role` newest-wins per xray_fetch.lua:587. Divergence: Lua
            # overwrites unconditionally; since we no longer default `role`
            # to a placeholder, an unconditional overwrite would let a
            # segment that never mentions the role erase a known one.
            newest_wins=("description", "role"), fill_if_empty=("gender", "occupation"),
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
            # xray_fetch.lua:660. Same non-empty guard -- and here Lua really
            # can blank a role: it defaults hist `role` to "" (AIHelper:
            # validateAndCleanData, xray_aihelper.lua, ca. line 2039) and
            # overwrites regardless. We keep the known value instead.
            newest_wins=("biography", "role"),
            fill_if_empty=("importance_in_book", "context_in_book"),
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
