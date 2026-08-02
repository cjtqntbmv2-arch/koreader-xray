"""En/de prompt templates + placeholder substitution for the comprehensive X-Ray fetch.

Port of KOReader Lua's `prompts/en.lua` / `prompts/de.lua` (keys
`system_instruction`, `comprehensive_xray`) and the prompt-assembly half of
`xray_aihelper.lua:AIHelper:createPrompt` (~1250-1565): the CHARACTER
COMPLETENESS / NAME DISAMBIGUATION rules (:1480-1489), the SEGMENT
COMPLETENESS MODE addendum (:1490-1498), `context_footer` (:1502), the
`{BRACE}` cap substitution and timeline detail-guidance buckets
(:1505-1561), and the MERGE MODE instructions (:1301-1353) reused here for
`mode="enrich"`. Template text is transcribed verbatim from the Lua sources.

Two deliberate desktop-only divergences from the device version:
- A pretraining-spoiler guard sentence is appended to `system_instruction`
  (both languages) -- desktop runs bigger models in `detailed` mode, which
  are more prone to surfacing known-book facts from training data than the
  small on-device models the Lua prompts were tuned for.
- `NAME_RULES_*` / `SEGMENT_ADDENDUM_*` hold the same English text under
  both the `_EN` and `_DE` name: in the Lua source these two blocks are
  appended by `xray_aihelper.lua` code unconditionally, not sourced from the
  per-language `prompts/*.lua` tables -- so they are English regardless of
  `self.current_language` on device too. This is a faithful port, not a
  missed translation: they constrain WHICH entities to extract, and the
  German runs kept returning German values under them. The timeline
  detail-guidance did NOT survive that test and is now per-language (see
  `_TL_BUCKETS`); critical rule 3 of the German prompt backs it up for
  everything else that is still phrased in English.

Stdlib-only on purpose (see `xray_core/epub.py`).
"""
import re

DETAIL_CAPS = {
    "normal":   {"char": 200, "loc": 100, "tl": 80,  "hist": 100, "term": 100},
    "detailed": {"char": 500, "loc": 300, "tl": 200, "hist": 400, "term": 300},  # Lua very-detailed = clamp maxima
}

# Chunk-first framing (see build_prompt): [prefix + chunk + sep + instructions].
# extract and gleaning share [system + prefix + chunk + sep] byte-for-byte.
_SEGMENT_PREFIX = "BOOK TEXT CONTEXT:\n"
_INSTR_SEP = "\n\n---\n\n"

# EXTRACT_RESPONSE_SCHEMA lived here until 2026-07-25: a Gemini
# `responseSchema` (OpenAPI subset) that forced structured output. It went with
# the Gemini client -- the Claude extraction path states the shape in the prompt
# itself and clean_response (xray_core/merge.py) remains the actual contract.

SYSTEM_INSTRUCTION_EN = (
    r"""You are an expert literary researcher. Your response must be ONLY in valid JSON format. Ensure data is highly accurate and pertains strictly to the provided context."""
    " Use ONLY information present in the provided text. Do not add facts "
    "from your own knowledge of this book, its sequels, or its author."
)

SYSTEM_INSTRUCTION_DE = (
    r"""Sie sind ein präziser Literaturanalyst für eine E-Reader-X-Ray-Funktion. Für jede Antwort gilt:
1. AUSGABE: ausschließlich EIN gültiges JSON-Objekt. Kein Markdown, keine Codezäune (```), kein Text davor oder danach.
2. JSON-SICHERHEIT: Doppelte Anführungszeichen in Strings escapen (\"). Keine rohen Zeilenumbrüche in Strings (außer als \n).
3. QUELLE: Aussagen zu fiktiven Inhalten stützen Sie ausschließlich auf den mitgelieferten Buchkontext. Trainingswissen ist nur dort erlaubt, wo die Aufgabe es ausdrücklich freigibt (reale historische Personen, Serien-Metadaten).
4. SPOILER: Die angegebene Lesefortschritts-Grenze ist absolut. Inhalte danach existieren für Sie nicht."""
    "\n5. TRAININGSWISSEN: Verwenden Sie AUSSCHLIESSLICH Informationen aus dem "
    "bereitgestellten Text. Erg\u00e4nzen Sie keine Fakten aus Ihrem eigenen Wissen "
    "\u00fcber dieses Buch, dessen Fortsetzungen oder den Autor."
)

COMPREHENSIVE_XRAY_EN = r"""Book: %s
Author: %s
Reading Progress: %d%%

TASK: Perform a complete X-Ray analysis of the BOOK TEXT CONTEXT above -- one bounded segment of the book, up to the reader's current progress. Base every extraction strictly on that text. Return ONLY a valid JSON object with keys: book_type, characters, historical_figures, locations, terms, timeline.

TIMELINE (highest priority):
- Identify the narrative chapters in the text; EXCLUDE non-narrative front/backmatter (Cover, Title Page, Copyright, Table of Contents, Dedication, Acknowledgments, Also By).
- Create EXACTLY ONE `timeline` event per narrative chapter, in reading order. `chapter` matches the heading as it appears; `event` summarizes only that chapter. {TIMELINE_DETAIL_GUIDANCE} Do NOT group or skip chapters. (event: max {MAX_TIMELINE_EVENT} chars.)

CHARACTERS & HISTORICAL FIGURES:
- Use each character's FULL, formal name (e.g. "Abraham Van Helsing"), not a casual nickname. Put up to 3 nicknames/titles in `aliases`. A last name shared by several characters (family members) is NOT an alias for any of them.
- Fields: `role` (short archetype label, <=40 chars), `gender` (Male/Female/Unknown), `occupation` (job/status), `description` (STRICTLY from the text, no inference or outside knowledge, max {MAX_CHAR_DESC} chars). A character only briefly mentioned gets a correspondingly brief description.
- Add up to {NUM_HIST} NOTABLE REAL historical people (Presidents, Authors, Generals) to `historical_figures` with `role`, `biography` (max {MAX_HIST_BIO} chars), `importance_in_book`, and `context_in_book` (max 100 chars). They must be verified, widely-recognized real people; for these you MAY use internal knowledge for biography/role, but `context_in_book` must come from the text. Purely fictional figures go in `characters`, never here.
- Do NOT extract anyone mentioned only in front/backmatter.

LOCATIONS:
- Extract {NUM_LOCS} significant locations, each with a `description` (max {MAX_LOC_DESC} chars).

TERMS:
- non_fiction: {NUM_TERMS} technical terms, acronyms, or concepts a layperson wouldn't know (category: Acronym / Technical Term / Concept / Jargon).
- fiction: {NUM_TERMS} world-building elements a new reader needs explained -- invented factions, organizations, magic systems, technologies, creatures, languages, lore (category: Faction / Magic System / Technology / Creature / Organization / Lore / Language).
- Not character or location names, not everyday words. `expanded`: the acronym's full form, else repeat the name. `definition`: max {MAX_TERM_DEF} chars.

SPOILERS: The %d%% mark is absolute -- nothing past it exists for you, and every description reflects each entity's state exactly at that mark. (The text above is already cut off there.)
KNOWLEDGE SOURCE: For fictional content use ONLY the text above -- no training, sequel, series, or author knowledge. The only exception is real historical figures' biography/role.
Output ONLY the JSON object: escape double quotes inside strings, no unescaped newlines, no code fences, no commentary."""

COMPREHENSIVE_XRAY_DE = r"""# METADATEN
Buch: %s
Autor: %s
Spoiler-Grenze: %d%% Lesefortschritt

# KONTEXT
Oben steht EIN Textblock "BOOK TEXT CONTEXT" – ein abgegrenzter Buchabschnitt bis zur Spoiler-Grenze. Jede Extraktion stützt sich ausschließlich auf diesen Text.

# AUFGABE
Vollständige X-Ray-Analyse. Ausgabe: genau EIN JSON-Objekt nach dem Schema unten.

## 1. timeline (höchste Priorität)
- Datengrundlage: der "BOOK TEXT CONTEXT".
- Nur erzählende Kapitel verwenden. Vor- und Nachspann auslassen (Cover, Titelseite, Copyright, Inhaltsverzeichnis, Widmung, Danksagung, "Auch von").
- Pro erzählendem Kapitel GENAU EIN Objekt, in Lesereihenfolge. Kapitel einzeln behandeln, niemals gruppieren oder überspringen.
- "chapter" = exakte Kapitelüberschrift, wie sie im Text erscheint.
- "event" = Zusammenfassung NUR dieses Kapitels. {TIMELINE_DETAIL_GUIDANCE} (max. {MAX_TIMELINE_EVENT} Zeichen).

## 2. characters
- Extrahieren Sie Charaktere aus dem "BOOK TEXT CONTEXT".
- "name" = vollständiger formeller Name (z. B. "Abraham Van Helsing"). Spitznamen und Titel gehören in "aliases" (max. 3, inkl. gebräuchlichem Vor-/Nachnamen). Ein Nachname, den mehrere Charaktere teilen (z. B. Familienmitglieder), ist für keinen von ihnen ein Alias.
- "description": ausschließlich Fakten, die im gelieferten Text stehen oder dort eindeutig impliziert sind (max. {MAX_CHAR_DESC} Zeichen). Nur kurz erwähnte Charaktere erhalten entsprechend knappe Beschreibungen – ergänzen Sie sie nicht aus anderem Wissen.

## 3. historical_figures
- Bis zu {NUM_HIST} reale, allgemein anerkannte historische Personen (z. B. Präsidenten, Autoren, Generäle), die in erzählenden Teilen erwähnt werden.
- Fiktive Charaktere gehören immer in "characters" – auch wenn sie mit realen Ereignissen interagieren.
- "biography" (max. {MAX_HIST_BIO} Zeichen) und "role": internes Wissen erlaubt. "context_in_book" (max. 100 Zeichen): ausschließlich aus dem Buchkontext.

## 4. locations
- Extrahieren Sie {NUM_LOCS} bedeutende Orte aus dem Kontext (Beschreibung max. {MAX_LOC_DESC} Zeichen).

## 5. terms
- Setzen Sie zuerst "book_type" im JSON-Root auf "fiction" oder "non_fiction".
- non_fiction: {NUM_TERMS} Fachbegriffe, Akronyme oder Konzepte, die Laien erklärt werden müssten. Kategorien: Acronym, Technical Term, Concept, Jargon.
- fiction: {NUM_TERMS} World-Building-Elemente (Fraktionen, Magiesysteme, Technologien, Kreaturen, Organisationen, Lore, Sprachen). Kategorien: Faction, Magic System, Technology, Creature, Organization, Lore, Language.
- Charakter- und Ortsnamen sowie Alltagsbegriffe gehören nicht in "terms".
- "expanded" = ausgeschriebene Form des Akronyms; sonst Wiederholung von "name". "definition": max. {MAX_TERM_DEF} Zeichen.

# ELEMENTE AUSSERHALB DER ERZÄHLUNG
Charaktere, Personen und Begriffe, die NUR in Vor-/Nachspann vorkommen (Danksagung, Autorenbiografie, Widmung, Titelseite, Copyright), werden nicht extrahiert.

# AUSGABE-SCHLÜSSEL
Genau ein JSON-Objekt mit den Schlüsseln: book_type, characters, historical_figures, locations, terms, timeline. Charakter-Felder: name, aliases, role (max. 40 Zeichen), gender (Männlich/Weiblich/Unbekannt), occupation, description. historical_figures: name, role, biography, importance_in_book, context_in_book.

# KRITISCHE REGELN – ZULETZT PRÜFEN
1. Spoiler-Grenze %d%%: keinerlei Informationen aus späteren Abschnitten; Beschreibungen spiegeln exakt den Stand an dieser Marke. Der Text oben ist bereits dort abgeschnitten.
2. Quelle: Für alles Fiktive zählt nur der mitgelieferte Text – kein Serien-, Autoren- oder Trainingswissen (einzige Ausnahme: biography/role realer historischer Personen).
3. Sprache: Alle Textwerte im JSON stehen auf Deutsch – auch "event", auch dann, wenn eine Anweisung oben englisch formuliert ist. Eigennamen bleiben unverändert; die JSON-Schlüssel und die Kategorienamen bleiben englisch.
4. Ausgabe: nur das JSON-Objekt, ohne Codezäune und ohne Begleittext."""

# Desktop divergence from en.lua (which had no footer): a post-data instruction
# mirroring the DE one -- official Gemini guidance is to place instructions
# AFTER the data context, so both languages now end this way.
CONTEXT_FOOTER_EN = (
    "\n---\n"
    "Now, based solely on the BOOK TEXT CONTEXT above, perform the analysis "
    "described above. Respect the spoiler mark and output ONLY the required "
    "JSON object -- no code fences, no commentary."
)

CONTEXT_FOOTER_DE = r"""
---
Führen Sie jetzt, basierend ausschließlich auf dem BOOK TEXT CONTEXT oben, die oben definierte Aufgabe aus. Beachten Sie die Spoiler-Grenze und geben Sie nur das geforderte JSON-Objekt aus – ohne Codezäune, ohne Begleittext."""

# CHARACTER COMPLETENESS RULES + NAME DISAMBIGUATION RULES, verbatim from
# xray_aihelper.lua:1480-1489 (see module docstring: English in both variants
# by design, matching the Lua source's own language-agnostic injection).
NAME_RULES_EN = (
    "\n\nCHARACTER COMPLETENESS RULES:"
    "\n- A character is ANY figure who speaks or acts in the provided text, explicitly including minor characters with only a single scene."
    "\n- Do NOT create entries for figures that appear only in enumerations, genealogies, family trees, or passing mentions."
    "\n- If output space runs short, prioritize by importance and shorten minor characters' descriptions first."
    "\n\nNAME DISAMBIGUATION RULES:"
    "\n- Different characters may share the same name (dynasties, relatives). ALWAYS use a distinguishing canonical name for each (numeral, epithet, or seat, e.g. \"Aegon II Targaryen\", \"Walder Frey, Lord of the Crossing\")."
    "\n- NEVER list the bare shared name as an alias for any of these characters (this overrides the general guidance to include the common first name as an alias)."
    "\n- Treat a newly found character as an already-known one ONLY if the text clearly refers to the same person; otherwise create a separate, disambiguated entry."
)
NAME_RULES_DE = NAME_RULES_EN

# SEGMENT COMPLETENESS MODE addendum, verbatim from xray_aihelper.lua:1490-1498.
SEGMENT_ADDENDUM_EN = (
    "\n\nSEGMENT COMPLETENESS MODE:"
    "\n- This fetch covers ONE bounded text segment of the book. Extract EVERY character who speaks or acts within the provided text, including minor and single-scene ones. Do NOT omit anyone."
    "\n- Apply the SAME exhaustive rule to LOCATIONS and to TERMS/world-building elements: list EVERY location and EVERY term that appears in this segment, including minor ones, with short definitions."
    "\n- Give minor characters short descriptions -- but never drop a character to save space."
)
SEGMENT_ADDENDUM_DE = SEGMENT_ADDENDUM_EN

# Gleaning pass (research's top recall booster): resend the same segment plus
# the names already found and ask ONLY for entities not yet listed. {FOUND_NAMES}
# is substituted AFTER %-formatting so names containing '%' can't break it.
GLEAN_EN = r"""Book: %s
Author: %s
Reading Progress: %d%%

The following characters have ALREADY been extracted from the BOOK TEXT CONTEXT above:
{FOUND_NAMES}

TASK: Find EVERY additional character, location, and world-building term that appears in the text but is NOT already in the list above -- especially minor and single-scene ones. Do NOT repeat anything already listed. If you find none, return empty arrays.
Use the SAME JSON schema and field rules as a full extraction (characters, locations, historical_figures, terms). NO SPOILERS: nothing past the %d%% mark.
Output ONLY the JSON object."""

GLEAN_DE = r"""Buch: %s
Autor: %s
Spoiler-Grenze: %d%% Lesefortschritt

Folgende Charaktere wurden aus dem "BOOK TEXT CONTEXT" oben BEREITS extrahiert:
{FOUND_NAMES}

AUFGABE: Finden Sie JEDEN zusätzlichen Charakter, Ort und World-Building-Begriff, der im Text vorkommt, aber NICHT in obiger Liste steht -- besonders Neben- und Einzelszenen-Figuren. Wiederholen Sie nichts bereits Gelistetes. Falls nichts, geben Sie leere Arrays zurück.
Verwenden Sie dasselbe JSON-Schema und dieselben Feldregeln wie bei einer Vollextraktion (characters, locations, historical_figures, terms). SPOILER: nichts nach der %d%%-Marke.
Ausgabe: NUR das JSON-Objekt."""

# Slim enrich header: enrich only rewrites descriptions of already-known
# characters, so it ships neither the timeline loop nor locations/terms specs
# (the caller discards them). The MERGE MODE marker comes from _enrich_block.
ENRICH_HEADER_EN = r"""Book: %s
Author: %s
Reading Progress: %d%%

Output ONLY a JSON object of the form {"characters": [{"name": "...", "description": "..."}]}. Rewrite the description of each character listed below using the BOOK TEXT CONTEXT at the end. Add no new characters and no other fields. NO SPOILERS: use only information up to the %d%% mark."""

ENRICH_HEADER_DE = r"""Buch: %s
Autor: %s
Spoiler-Grenze: %d%% Lesefortschritt

Ausgabe: NUR ein JSON-Objekt der Form {"characters": [{"name": "...", "description": "..."}]}. Schreiben Sie die Beschreibung jedes unten gelisteten Charakters neu, basierend auf dem "BOOK TEXT CONTEXT" am Ende. Keine neuen Charaktere, keine weiteren Felder. SPOILER: nur Informationen bis zur %d%%-Marke."""

RECAP_EN = r"""Book: %s
Author: %s

Write a "story so far" recap for a reader who has read %d%% of this book and is picking it up again after weeks away.

Use ONLY the events and characters listed below. They are everything the reader has read; anything beyond them would be a spoiler. Do not speculate about what comes next, and do not hint that something is still to be revealed.

Write about {WORDS} words of flowing prose. No headings, no bullet lists, no cast list at the end -- one continuous piece that gives the reader back the thread.
{WEIGHTING}
EVENTS SO FAR:
{TIMELINE}

CHARACTERS THE READER HAS MET:
{CHARACTERS}"""

RECAP_DE = r"""Buch: %s
Autor: %s

Schreiben Sie einen Rückblick („Was bisher geschah") für eine Leserin, die %d%% dieses Buchs gelesen hat und es nach Wochen wieder aufschlägt.

Verwenden Sie AUSSCHLIESSLICH die unten aufgeführten Ereignisse und Figuren. Sie sind alles, was die Leserin gelesen hat; alles darüber hinaus wäre ein Spoiler. Spekulieren Sie nicht über das Kommende und deuten Sie nicht an, dass noch etwas enthüllt wird.

Schreiben Sie etwa {WORDS} Wörter Fließtext. Keine Überschriften, keine Aufzählungen, keine Figurenliste am Ende -- ein zusammenhängender Text, der den Faden zurückgibt.
{WEIGHTING}
BISHERIGE EREIGNISSE:
{TIMELINE}

FIGUREN, DIE DIE LESERIN KENNT:
{CHARACTERS}"""

# Weighted AGAINST reading order, which is the whole point of this recap: you
# forget the opening and remember last night's chapter. Appended only once
# there is a past worth weighting -- see build_recap_prompt.
RECAP_WEIGHTING_EN = r"""
Weight the retelling AGAINST the reading order: the reader still remembers the most recent chapters and has forgotten the opening. Spend about 55%% of the words on everything up to the %d%% mark, about 30%% on the stretch from %d%% to %d%%, and only about 15%% on the most recent part -- that one needs a reminder, not a retelling.
"""

RECAP_WEIGHTING_DE = r"""
Gewichten Sie die Nacherzählung GEGEN die Leserichtung: die letzten Kapitel hat die Leserin noch im Kopf, den Anfang hat sie vergessen. Verwenden Sie etwa 55%% der Wörter auf alles bis zur %d%%-Marke, etwa 30%% auf den Abschnitt von %d%% bis %d%%, und nur etwa 15%% auf den jüngsten Teil -- der braucht eine Erinnerung, keine Nacherzählung.
"""

_COMPREHENSIVE = {"en": COMPREHENSIVE_XRAY_EN, "de": COMPREHENSIVE_XRAY_DE}
_GLEAN = {"en": GLEAN_EN, "de": GLEAN_DE}
_ENRICH_HEADER = {"en": ENRICH_HEADER_EN, "de": ENRICH_HEADER_DE}
_RECAP = {"en": RECAP_EN, "de": RECAP_DE}
_RECAP_WEIGHTING = {"en": RECAP_WEIGHTING_EN, "de": RECAP_WEIGHTING_DE}
_SYSTEM = {"en": SYSTEM_INSTRUCTION_EN, "de": SYSTEM_INSTRUCTION_DE}
_NAME_RULES = {"en": NAME_RULES_EN, "de": NAME_RULES_DE}
_SEGMENT_ADDENDUM = {"en": SEGMENT_ADDENDUM_EN, "de": SEGMENT_ADDENDUM_DE}
_CONTEXT_FOOTER = {"en": CONTEXT_FOOTER_EN, "de": CONTEXT_FOOTER_DE}

# `%%` is a non-consuming literal-percent escape (same semantics as C/Lua
# string.format); count only real %s/%d specifiers so the arg tuple lines up
# -- see Global Constraints / task brief for the "%d%%" (Reading Progress)
# case that motivates this.
_SPEC_RE = re.compile(r"%%|%[sd]")


def _real_specifier_count(template: str) -> int:
    return sum(1 for tok in _SPEC_RE.findall(template) if tok != "%%")


def _apply_percent_args(template: str, title: str, author: str, percent: int) -> str:
    """title, author fill the first two %s; every remaining %d is `percent`."""
    n = _real_specifier_count(template)
    args = (title, author) + (percent,) * (n - 2)
    return template % args


# Bucket ladder text per language. The Lua source injected the English
# wording into the German prompt too (`xray_aihelper.lua` built this in code,
# not from `prompts/de.lua`), and this module faithfully ported that -- until
# the first real German book came back with ~9 of 65 `timeline[].event`
# strings in English while every `description` was German. `event` was the
# one field in the German prompt whose instruction was English, so the model
# answered it in the language it was asked in. The device-side Lua prompts no
# longer exist, so there is nothing left to stay faithful to.
_TL_BUCKETS = {
    "en": (
        "Write a brief one-phrase summary.",
        "Write a concise single-sentence summary.",
        "Write a detailed summary including context and key consequences.",
        "Write a rich, full narrative description including character actions, key events, and their consequences.",
    ),
    "de": (
        "Schreiben Sie eine knappe Zusammenfassung in einem Satzteil.",
        "Schreiben Sie eine knappe Zusammenfassung in genau einem Satz.",
        "Schreiben Sie eine ausführliche Zusammenfassung mit Zusammenhang und wesentlichen Folgen.",
        "Schreiben Sie eine dichte, vollständige Schilderung mit Handlungen der Figuren, den wesentlichen Ereignissen und deren Folgen.",
    ),
}
_TL_LENGTH_RULE = {
    "en": (
        " Write between {min_len} and {tl_len} characters. Do NOT write a shorter "
        "summary unless the chapter has almost no content."
    ),
    "de": (
        " Schreiben Sie zwischen {min_len} und {tl_len} Zeichen. Fassen Sie sich NICHT "
        "kürzer, es sei denn, das Kapitel hat fast keinen Inhalt."
    ),
}


def _timeline_guidance(tl_len: int, language: str) -> str:
    """Port of the tl_len bucket ladder in createPrompt (xray_aihelper.lua
    ~1524-1547), per language (see `_TL_BUCKETS` above)."""
    buckets = _TL_BUCKETS.get(language, _TL_BUCKETS["en"])
    if tl_len <= 50:
        guidance = buckets[0]
    elif tl_len <= 80:
        guidance = buckets[1]
    elif tl_len <= 150:
        guidance = buckets[2]
    else:
        guidance = buckets[3]
    min_len = tl_len * 3 // 4  # Lua: math.floor(tl_len * 0.75); *3//4 is exact for ints, no float rounding
    rule = _TL_LENGTH_RULE.get(language, _TL_LENGTH_RULE["en"])
    return guidance + rule.format(min_len=min_len, tl_len=tl_len)


def _apply_caps(text: str, caps: dict, language: str) -> str:
    """Replace the `{BRACE}` tags -- ported count formulas from
    xray_aihelper.lua:1516-1523 (floor via `//`, same as Lua's math.floor
    since all operands are positive)."""
    char_len, loc_len, tl_len = caps["char"], caps["loc"], caps["tl"]
    hist_len, term_len = caps["hist"], caps["term"]
    # No {NUM_CHARS} target any more: characters are extracted exhaustively
    # (SEGMENT COMPLETENESS MODE), so a numeric cap would only suppress recall.
    num_locs = min(20, max(3, 8 * 100 // loc_len))
    num_hist = min(15, max(3, 8 * 100 // hist_len))
    num_terms = min(20, max(5, 15 * 100 // term_len))
    # No {TIMELINE_EXAMPLE} substitution: the tag appears in no template.
    for tag, value in {
        "{MAX_CHAR_DESC}": char_len,
        "{MAX_LOC_DESC}": loc_len,
        "{NUM_LOCS}": num_locs,
        "{MAX_TIMELINE_EVENT}": tl_len,
        "{TIMELINE_DETAIL_GUIDANCE}": _timeline_guidance(tl_len, language),
        "{MAX_HIST_BIO}": hist_len,
        "{NUM_HIST}": num_hist,
        "{MAX_TERM_DEF}": term_len,
        "{NUM_TERMS}": num_terms,
    }.items():
        text = text.replace(tag, str(value))
    return text


def _enrich_block(prior_names, char_len: int) -> str:
    """MERGE MODE instructions + existing-knowledge lines, ported from
    xray_aihelper.lua:1301-1353 (device parity: re-synthesize one cohesive
    description per recurring entity from accumulated + new text)."""
    lines = "\n".join(f"- {name}: {desc}" for name, desc in prior_names)
    return (
        "\n\nMERGE MODE INSTRUCTIONS:\nYou are UPDATING an existing X-Ray.\n"
        "- For entities (Characters, Locations, Historical Figures) that already exist, "
        "synthesize a completely rewritten, cohesive summary combining the EXISTING "
        "KNOWLEDGE with any new information found in the text.\n"
        "- Write a solid summary that is not repetitive.\n"
        f"- Descriptions MUST NOT exceed {char_len} characters.\n"
        "- If there is no new information, return the existing description (or a "
        f"refined version of it under {char_len} characters)."
        "\n\nEXISTING CHARACTER KNOWLEDGE (Context Optimized):\n" + lines
    )


def build_prompt(language, detail_level, title, author, percent, segment_text,
                  prior_names=None, mode="extract"):
    """Build (system_instruction, user_prompt) for the comprehensive X-Ray fetch.

    mode="extract": phase-A independent chunk extraction, `prior_names` unused.
    mode="enrich": phase-C re-synthesis pass, `prior_names` is a list of
    (name, description) pairs for entities already known before this segment.
    Enrich uses a slim description-only prompt, not the full comprehensive spec.
    """
    caps = DETAIL_CAPS[detail_level]
    system = _SYSTEM[language]

    if mode == "enrich":
        instr = _apply_percent_args(_ENRICH_HEADER[language], title, author, percent)
        instr += _enrich_block(prior_names or [], caps["char"])
    else:
        instr = _apply_percent_args(_COMPREHENSIVE[language], title, author, percent)
        instr += _NAME_RULES[language] + _SEGMENT_ADDENDUM[language]
        instr = _apply_caps(instr, caps, language)

    # Chunk-first: the book text leads so the extract and gleaning calls share a
    # byte-identical [system + chunk] prefix -> Gemini implicit-cache hit on the
    # second call. Instructions follow the data (also official Gemini guidance).
    # The instruction part is %-formatted BEFORE prepending the chunk, so a '%'
    # inside the book text can never be mistaken for a format specifier.
    return system, _SEGMENT_PREFIX + segment_text + _INSTR_SEP + instr + _CONTEXT_FOOTER[language]


def build_glean_prompt(language, detail_level, title, author, percent,
                        segment_text, found_names):
    """Build (system, user) for the gleaning pass: resend `segment_text` plus
    the already-found `found_names` and ask ONLY for entities not yet listed.
    Chunk-first with the SAME prefix as build_prompt, so the gleaning call hits
    the extract call's implicit cache. `detail_level` is accepted for call-site
    symmetry with build_prompt."""
    system = _SYSTEM[language]
    instr = _apply_percent_args(_GLEAN[language], title, author, percent)
    names = ", ".join(found_names) if found_names else "(none yet)"
    instr = instr.replace("{FOUND_NAMES}", names)
    return system, _SEGMENT_PREFIX + segment_text + _INSTR_SEP + instr + _CONTEXT_FOOTER[language]


def _recap_events_block(events) -> str:
    if not events:
        return "(none recorded)"
    return "\n".join(
        f"- {ev.get('pct', 0)}%: {(ev.get('event') or '').strip()}"
        for ev in events
    )


def _recap_characters_block(characters) -> str:
    """Names carry even without a description -- the recap needs to know an
    entity exists in order to name it correctly, and extraction leaves
    `description` empty rather than inventing a placeholder (see merge.py)."""
    lines = []
    for ch in characters or []:
        name = (ch.get("name") or "").strip()
        if not name:
            continue
        desc = (ch.get("description") or "").strip()
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    return "\n".join(lines) or "(none recorded)"


# Length follows the material, not the reading position. Measured on a real
# book: at 16% there were 11 timeline events, and against a flat "250 to 400
# words" the model still wrote 399 -- padding with the founding of the Shire in
# 1601 and a history of pipe-weed. A densely plotted book at 20% has more to
# report than a slow one at 40%, and the event count is what knows that.
RECAP_WORDS_PER_EVENT = 8
RECAP_WORDS_MIN = 150
RECAP_WORDS_MAX = 400


def recap_target_words(event_count: int) -> int:
    return max(RECAP_WORDS_MIN,
               min(RECAP_WORDS_MAX, RECAP_WORDS_PER_EVENT * event_count))


def build_recap_prompt(language, title, author, percent, events, characters):
    """Build (system, user) for one stage's "story so far" recap.

    Deliberately NOT routed through build_prompt: that one is built for chunk
    extraction and unconditionally prepends `_SEGMENT_PREFIX + segment_text`
    plus the context footer. A recap has no book-text segment at all -- its
    input is the frozen snapshot of the stage it belongs to, which is exactly
    what keeps it spoiler-safe.

    `events` and `characters` must already be cut to this stage. That cut is
    the D4 boundary and belongs to the caller (tools/claude_xray_recap.py), not
    here -- a prompt builder that silently filtered would hide where the
    guarantee lives.
    """
    instr = _apply_percent_args(_RECAP[language], title, author, percent)
    instr = instr.replace("{WORDS}", str(recap_target_words(len(events or []))))

    # Below 20% there is no "long ago" worth extra room; the bands would carve
    # up a handful of chapters. Chronological order is the better answer there,
    # and that is what the base template already asks for.
    if percent >= 20:
        far, mid = round(percent * 0.5), round(percent * 0.85)
        weighting = _RECAP_WEIGHTING[language] % (far, far, mid)
    else:
        weighting = "\n"
    instr = instr.replace("{WEIGHTING}", weighting)

    # Substituted AFTER %-formatting: a '%' inside an event text or a character
    # description would otherwise be read as a format specifier -- the same
    # reason build_prompt formats the instruction before prepending the chunk.
    instr = instr.replace("{TIMELINE}", _recap_events_block(events))
    instr = instr.replace("{CHARACTERS}", _recap_characters_block(characters))
    return _SYSTEM[language], instr


# ---------------------------------------------------------------------------
# Ego-net relations (feature B). One call per book, over the finished document.
# ---------------------------------------------------------------------------

# Narrow on purpose. On a 6" screen sparseness is the quality: with a handful
# of structural bonds per figure the neighbours always fit on one screen, and
# every edge shown carries information. "Meets" and "knows" do not.
MAX_RELATIONS_PER_FIGURE = 5

RELATIONS_EN = r"""Book: {TITLE}
Author: {AUTHOR}

TASK: List the relationships between the figures below. Use ONLY the figures listed; never invent a name, and never name anyone who is not in the lists.

Record ONLY these kinds of bond: family, rule/service, alliance, enmity, love/marriage, mentorship. Leave out passing encounters -- that two figures met, travelled together or know each other is not a relationship here.

At most {MAX} relationships per figure. If a figure has more, keep the ones that matter most to the plot.

Give EVERY relationship in BOTH directions, as two separate entries. `label` names the role `to` has for `from`: for a father Eddard and his son Robb, write both {"from": "Robb Stark", "to": "Eddard Stark", "label": "father"} and {"from": "Eddard Stark", "to": "Robb Stark", "label": "son"}. A missing counterpart makes the relationship disappear from one of the two figures.

Keep `label` short -- a word or two, no sentence.

Output ONLY a JSON object of the form {"relations": [{"from": "...", "to": "...", "label": "..."}]}.

FIGURES:
{CHARACTERS}

HISTORICAL FIGURES (real people referenced in the book; valid targets too):
{HISTORICAL}"""

RELATIONS_DE = r"""Buch: {TITLE}
Autor: {AUTHOR}

AUFGABE: Führen Sie die Beziehungen zwischen den unten genannten Figuren auf. Verwenden Sie AUSSCHLIESSLICH die aufgeführten Figuren; erfinden Sie keinen Namen und nennen Sie niemanden, der nicht in den Listen steht.

Erfassen Sie NUR diese Arten von Bindung: Verwandtschaft, Herrschaft/Dienst, Bündnis, Feindschaft, Liebe/Ehe, Mentorschaft. Flüchtige Begegnungen bleiben weg -- dass zwei Figuren sich getroffen haben, zusammen gereist sind oder einander kennen, ist hier keine Beziehung.

Höchstens {MAX} Beziehungen je Figur. Hat eine Figur mehr, behalten Sie die für die Handlung wichtigsten.

Geben Sie JEDE Beziehung in BEIDEN Richtungen an, als zwei getrennte Einträge. `label` benennt die Rolle, die `to` für `from` hat: für den Vater Eddard und seinen Sohn Robb also sowohl {"from": "Robb Stark", "to": "Eddard Stark", "label": "Vater"} als auch {"from": "Eddard Stark", "to": "Robb Stark", "label": "Sohn"}. Fehlt die Gegenrichtung, verschwindet die Beziehung bei einer der beiden Figuren.

Halten Sie `label` kurz -- ein bis zwei Wörter, kein Satz.

Ausgabe: NUR ein JSON-Objekt der Form {"relations": [{"from": "...", "to": "...", "label": "..."}]}.

FIGUREN:
{CHARACTERS}

HISTORISCHE PERSONEN (reale Personen, auf die das Buch verweist; ebenfalls gültige Ziele):
{HISTORICAL}"""

_RELATIONS = {"en": RELATIONS_EN, "de": RELATIONS_DE}


def _relations_figures_block(figures, description_key: str, with_aliases: bool) -> str:
    lines = []
    for figure in figures or []:
        name = (figure.get("name") or "").strip()
        if not name:
            continue
        line = f"- {name}"
        if with_aliases:
            aliases = [a.strip() for a in figure.get("aliases") or [] if (a or "").strip()]
            if aliases:
                line += f" (also: {', '.join(aliases)})"
        desc = (figure.get(description_key) or "").strip()
        if desc:
            line += f": {desc}"
        lines.append(line)
    return "\n".join(lines) or "(none recorded)"


def build_relations_prompt(language, title, author, characters, historical):
    """Build (system, user) for the one relations pass of a book.

    No %-formatting anywhere -- not `_apply_percent_args`, not a bare `%`.
    Two reasons, both measured. That helper takes a `percent` this whole-book
    prompt does not have, and it spreads it across every specifier from the
    third onward, so a cap written as %d renders as 0: a prompt asking for "at
    most 0 relations per figure" that still looks well-formed. And a book whose
    title or a character description contains a '%' would either raise or eat
    the next character. Plain .replace() has neither failure mode.

    Not routed through build_prompt either: that one prepends
    `_SEGMENT_PREFIX + segment_text`, and this pass has no book-text segment --
    it reads the finished document.

    Both categories get their aliases. This shipped as characters-only on
    2026-07-27, justified by `clean_response` building historical figures with
    no `aliases` key (merge.py) -- but the snapshot this pass reads is
    POST-merge, where `_add_alias` does add one. Measured: "Yssa the Elder"
    merged with "Queen Yssa the Elder" stores aliases ['Queen Yssa the Elder'].
    Withholding those from the model only cost edges.
    """
    instr = _RELATIONS[language]
    instr = instr.replace("{TITLE}", title or "")
    instr = instr.replace("{AUTHOR}", author or "")
    instr = instr.replace("{MAX}", str(MAX_RELATIONS_PER_FIGURE))
    instr = instr.replace(
        "{CHARACTERS}", _relations_figures_block(characters, "description", True)
    )
    instr = instr.replace(
        "{HISTORICAL}", _relations_figures_block(historical, "biography", True)
    )
    return _SYSTEM[language], instr
