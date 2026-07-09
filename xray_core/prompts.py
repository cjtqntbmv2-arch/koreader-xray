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
  both the `_EN` and `_DE` name: in the Lua source these two blocks (and the
  timeline detail-guidance text ported into `_timeline_guidance` below) are
  appended by `xray_aihelper.lua` code unconditionally, not sourced from the
  per-language `prompts/*.lua` tables -- so they are English regardless of
  `self.current_language` on device too (the Lua comment at the timeline
  guidance call site literally calls it "language-agnostic guidance"). This
  is a faithful port of that behavior, not a missed translation.

Stdlib-only on purpose (see `xray_core/epub.py`).
"""
import re

DETAIL_CAPS = {
    "normal":   {"char": 200, "loc": 100, "tl": 80,  "hist": 100, "term": 100},
    "detailed": {"char": 500, "loc": 300, "tl": 200, "hist": 400, "term": 300},  # Lua very-detailed = clamp maxima
}

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

TASK: Perform a complete X-Ray analysis. Output ONLY a valid JSON object.

CRITICAL ATTENTION PARTITIONING:
You are processing a massive document with two text blocks provided at the end of this prompt:
1. "CHAPTER SAMPLES": This is the macro-context of the book up to the reader's current location.
2. "BOOK TEXT CONTEXT": This is the micro-context of the most recent 20k characters.

ANTI-TRUNCATION PROTOCOL (CRITICAL):
You have a strict maximum output limit. If the "CHAPTER SAMPLES" contains MORE THAN 40 chapters (e.g., an omnibus edition):
1. You MUST reduce the characters list to ONLY the top 10 absolute most important characters.
2. You MUST reduce character descriptions to MAX {MAX_CHAR_DESC} characters.
3. You MUST reduce timeline event summaries to MAX {MAX_TIMELINE_EVENT} characters.
Failure to compress your output for massive books will cause the JSON to truncate and fail.

ALGORITHM FOR TIMELINE (HIGHEST PRIORITY):
To prevent skipping chapters or hallucinating events, you MUST execute this exact loop:
Step 1. Look ONLY at the "CHAPTER SAMPLES" block. Identify the narrative chapters.
Step 2. EXCLUDE all non-narrative frontmatter and backmatter (e.g., Cover, Title Page, Copyright, Table of Contents, Dedication, Acknowledgments, Also By).
Step 3. For each narrative chapter, starting from the very first one, create EXACTLY ONE event object in the `timeline` array.
Step 4. The `chapter` field MUST exactly match the chapter header in the sample. (Map them strictly in sequential order).
Step 5. Summarize that specific chapter in the `event` field. {TIMELINE_DETAIL_GUIDANCE} Do NOT group chapters.
Step 6. NO SPOILERS: Stop exactly at the %d%% mark. Do not include events past this progress.

ALGORITHM FOR CHARACTERS & HISTORICAL FIGURES:
Step 1. Extract important characters using both text blocks. ({NUM_CHARS} normal, MAX 10 if omnibus).
Step 2. You MUST use their FULL, formal names (e.g., "Abraham Van Helsing"). Do NOT use casual nicknames as the main name.
Step 3. Provide up to 3 alternative names, titles, or nicknames this character goes by in an `aliases` array. Include their common first name and last name if used. IMPORTANT: If a last name is shared by multiple characters (e.g., family members), DO NOT include it as an alias for either character.
Step 4. Actively scan for up to {NUM_HIST} NOTABLE REAL people from human history (e.g., Presidents, Authors, Generals). Add them to `historical_figures`.
CRITICAL for Characters & Historical Figures:
- DO NOT extract characters or historical figures mentioned ONLY in non-narrative frontmatter or backmatter (e.g., Acknowledgments, Author Bio, Dedications, Title Page, Copyright).
- Historical Figures MUST be verified real-world people with widespread historical recognition.
- DO NOT include purely fictional characters in the historical figures list, even if they interact with real historical events. Fictional characters MUST go in the `characters` array.
- For Historical Figures ONLY, you may use your internal knowledge to write their general `biography` and historical `role`, but you MUST use the book context for their `context_in_book`.
NO SPOILERS: Stop exactly at the %d%% mark.

ALGORITHM FOR LOCATIONS:
Step 1. Extract {NUM_LOCS} significant locations. NO SPOILERS: Stop exactly at the %d%% mark.

ALGORITHM FOR TERMS:
Step 0. Declare "book_type" as "fiction" or "non_fiction" at the JSON root.
Step 1. If non_fiction: extract {NUM_TERMS} significant technical terms, acronyms, jargon, or concepts readers would not know without specialized knowledge. Use appropriate categories like Acronym, Technical Term, Concept, or Jargon.
Step 2. If fiction: extract {NUM_TERMS} significant world-building elements that a new reader would need explained—such as invented factions, organizations, magic systems, technologies, creatures, languages, or in-universe lore.
   - Do NOT include character names or location names (those are tracked separately).
   - DO NOT extract real-world common words or concepts.
   - Use appropriate categories: Faction, Magic System, Technology, Creature, Organization, Lore, Language.
Step 3. Include what the acronym/phrase stands for in "expanded". If not an acronym/phrase, repeat the name.
Step 4. DO NOT include common everyday words.

STRICT SPOILER RULES:
- ABSOLUTELY NO information from after the current reading progress. Stop exactly at the %d%% mark.
- Descriptions must reflect the characters' state at this exact point in the book.

STRICT KNOWLEDGE SOURCE RULES (CRITICAL):
- For FICTIONAL CHARACTERS: Your descriptions MUST be based SOLELY on what is explicitly stated or clearly implied in the provided text. Do NOT supplement with knowledge from prior training, external sources, or general awareness of the book/series/author.
- If a character has only been briefly mentioned in the text so far, your description must reflect that limited information only. Do NOT infer, assume, or add any detail not grounded in the provided context.
- The ONLY exception is for REAL HISTORICAL FIGURES (placed in `historical_figures`): you may use internal knowledge for their general biography/role, but still rely on the book text for their `context_in_book`.

STRICT JSON SAFETY RULES:
- You MUST properly escape all double quotes (\") inside strings.
- Do NOT use unescaped line breaks inside strings.
- Output ONLY valid, parseable JSON.

REQUIRED JSON FORMAT:
{
  "book_type": "fiction",
  "characters": [
    {
      "name": "Full Formal Name",
      "aliases": ["Alias 1", "Alias 2"],
      "role": "Short archetype label (3-5 words, e.g. 'Antagonist', 'Protagonist', 'The Victim')",
      "gender": "Male / Female / Unknown",
      "occupation": "Job/Status",
      "description": "Description based STRICTLY on text provided. Do not infer or add external knowledge. NO SPOILERS. (Max {MAX_CHAR_DESC} chars)"
    }
  ],
  "historical_figures": [
    {
      "name": "Real Historical Person Name",
      "role": "Historical Role",
      "biography": "Short biography (MAX {MAX_HIST_BIO} chars)",
      "importance_in_book": "Significance up to current progress",
      "context_in_book": "How they are mentioned (MAX 100 chars)"
    }
  ],
  "locations": [
    {"name": "Place Name", "description": "Short desc (MAX {MAX_LOC_DESC} chars)"}
  ],
  "terms": [
    {
      "name": "Term or Acronym",
      "expanded": "Full expansion or same as name",
      "category": "Acronym / Technical Term / Concept / Jargon",
      "definition": "Concise definition in context (MAX {MAX_TERM_DEF} chars)"
    }
  ],
  "timeline": [
    {
      "chapter": "Exact Chapter Title from Samples",
      "event": "{TIMELINE_EXAMPLE}"
    }
  ]
} """

COMPREHENSIVE_XRAY_DE = r"""# METADATEN
Buch: %s
Autor: %s
Spoiler-Grenze: %d%% Lesefortschritt

# KONTEXT
Am Ende dieses Prompts folgen zwei Textblöcke:
1. "CHAPTER SAMPLES" – Kapitel-Stichproben bis zur Spoiler-Grenze (Makro-Kontext).
2. "BOOK TEXT CONTEXT" – die letzten ca. 20.000 Zeichen vor der Leseposition (Mikro-Kontext).

# AUFGABE
Vollständige X-Ray-Analyse. Ausgabe: genau EIN JSON-Objekt nach dem Schema unten.

## 1. timeline (höchste Priorität)
- Datengrundlage: ausschließlich "CHAPTER SAMPLES".
- Nur erzählende Kapitel verwenden. Vor- und Nachspann auslassen (Cover, Titelseite, Copyright, Inhaltsverzeichnis, Widmung, Danksagung, "Auch von").
- Pro erzählendem Kapitel GENAU EIN Objekt, in exakt der Reihenfolge der Stichproben, beginnend beim allerersten erzählenden Kapitel. Kapitel einzeln behandeln, niemals gruppieren oder überspringen.
- "chapter" = exakte Kapitelüberschrift aus der Stichprobe.
- "event" = Zusammenfassung NUR dieses Kapitels, {TIMELINE_DETAIL_GUIDANCE} (max. {MAX_TIMELINE_EVENT} Zeichen).

## 2. characters
- Extrahieren Sie {NUM_CHARS} wichtige Charaktere aus beiden Kontextblöcken.
- "name" = vollständiger formeller Name (z. B. "Abraham Van Helsing"). Spitznamen und Titel gehören in "aliases" (max. 3, inkl. gebräuchlichem Vor-/Nachnamen). Ein Nachname, den mehrere Charaktere teilen (z. B. Familienmitglieder), ist für keinen von ihnen ein Alias.
- "description": ausschließlich Fakten, die im gelieferten Text stehen oder dort eindeutig impliziert sind. Nur kurz erwähnte Charaktere erhalten entsprechend knappe Beschreibungen – ergänzen Sie sie nicht aus anderem Wissen.

## 3. historical_figures
- Bis zu {NUM_HIST} reale, allgemein anerkannte historische Personen (z. B. Präsidenten, Autoren, Generäle), die in erzählenden Teilen erwähnt werden.
- Fiktive Charaktere gehören immer in "characters" – auch wenn sie mit realen Ereignissen interagieren.
- "biography" und "role": internes Wissen erlaubt. "context_in_book": ausschließlich aus dem Buchkontext.

## 4. locations
- Extrahieren Sie {NUM_LOCS} bedeutende Orte aus dem Kontext.

## 5. terms
- Setzen Sie zuerst "book_type" im JSON-Root auf "fiction" oder "non_fiction".
- non_fiction: {NUM_TERMS} Fachbegriffe, Akronyme oder Konzepte, die Laien erklärt werden müssten. Kategorien: Acronym, Technical Term, Concept, Jargon.
- fiction: {NUM_TERMS} World-Building-Elemente (Fraktionen, Magiesysteme, Technologien, Kreaturen, Organisationen, Lore, Sprachen). Kategorien: Faction, Magic System, Technology, Creature, Organization, Lore, Language.
- Charakter- und Ortsnamen sowie Alltagsbegriffe gehören nicht in "terms".
- "expanded" = ausgeschriebene Form des Akronyms; sonst Wiederholung von "name".

# ELEMENTE AUSSERHALB DER ERZÄHLUNG
Charaktere, Personen und Begriffe, die NUR in Vor-/Nachspann vorkommen (Danksagung, Autorenbiografie, Widmung, Titelseite, Copyright), werden nicht extrahiert.

# KOMPRESSION BEI SAMMELAUSGABEN
Enthält "CHAPTER SAMPLES" mehr als 40 Kapitel (z. B. Sammelausgabe), gilt zwingend:
- "characters" auf die 10 wichtigsten begrenzen.
- Charakterbeschreibungen auf max. {MAX_CHAR_DESC} Zeichen kürzen.
- Timeline-Events auf max. {MAX_TIMELINE_EVENT} Zeichen kürzen.
So bleibt die Ausgabe vollständig und das JSON parsbar.

# JSON-SCHEMA (exakt einhalten, keine zusätzlichen Felder)
{
  "book_type": "fiction | non_fiction",
  "characters": [
    {
      "name": "Vollständiger formeller Name",
      "aliases": ["Alias 1", "Alias 2"],
      "role": "Rolle bis zur Spoiler-Grenze",
      "gender": "Männlich / Weiblich / Unbekannt",
      "occupation": "Beruf/Status",
      "description": "Nur aus dem gelieferten Text, Stand exakt an der Spoiler-Grenze (max. {MAX_CHAR_DESC} Zeichen)"
    }
  ],
  "historical_figures": [
    {
      "name": "Name der realen historischen Person",
      "role": "Historische Rolle",
      "biography": "Kurzbiografie (max. {MAX_HIST_BIO} Zeichen)",
      "importance_in_book": "Bedeutung bis zur Spoiler-Grenze",
      "context_in_book": "Wie sie im Buch erwähnt wird (max. 100 Zeichen)"
    }
  ],
  "locations": [
    { "name": "Name des Ortes", "description": "Kurzbeschreibung (max. {MAX_LOC_DESC} Zeichen)" }
  ],
  "terms": [
    {
      "name": "Begriff oder Akronym",
      "expanded": "Ausgeschriebene Form oder identisch mit name",
      "category": "Kategorie gemäß Aufgabe 5",
      "definition": "Präzise Definition im Buchkontext (max. {MAX_TERM_DEF} Zeichen)"
    }
  ],
  "timeline": [
    { "chapter": "Exakter Kapiteltitel aus den Stichproben", "event": "{TIMELINE_EXAMPLE}" }
  ]
}

# KRITISCHE REGELN – ZULETZT PRÜFEN
1. Spoiler-Grenze %d%%: keinerlei Informationen aus späteren Abschnitten; Beschreibungen spiegeln exakt den Stand an dieser Marke.
2. Quelle: Für alles Fiktive zählt nur der mitgelieferte Text – kein Serien-, Autoren- oder Trainingswissen (einzige Ausnahme: biography/role realer historischer Personen).
3. Ausgabe: nur das JSON-Objekt, ohne Codezäune und ohne Begleittext."""

CONTEXT_FOOTER_EN = ""  # en.lua has no context_footer key; Lua falls back to "" at the call site

CONTEXT_FOOTER_DE = r"""
---
Führen Sie jetzt, basierend ausschließlich auf dem gesamten Kontext oben, die eingangs definierte Aufgabe aus. Beachten Sie die Spoiler-Grenze und geben Sie nur das geforderte JSON-Objekt aus – ohne Codezäune, ohne Begleittext."""

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
    "\n- This fetch covers ONE bounded text segment of the book. Extract EVERY character who speaks or acts within the provided samples, including minor ones."
    "\n- For this segment fetch, these rules take precedence over the ANTI-TRUNCATION PROTOCOL and the character count guidance in Step 1 above."
    "\n- The character count target of {NUM_CHARS} applies to NEW characters found in this segment, NOT to the total list."
    "\n- Apply the SAME exhaustive rule to LOCATIONS and to TERMS/world-building elements: list EVERY location and EVERY term that appears in this segment, including minor ones, with short definitions, counting only NEW entries for this segment."
    "\n- Give minor characters short descriptions. If output space runs short, drop the least important characters first."
)
SEGMENT_ADDENDUM_DE = SEGMENT_ADDENDUM_EN

_COMPREHENSIVE = {"en": COMPREHENSIVE_XRAY_EN, "de": COMPREHENSIVE_XRAY_DE}
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


def _timeline_guidance(tl_len: int) -> tuple[str, str]:
    """Port of the tl_len bucket ladder in createPrompt (xray_aihelper.lua
    ~1524-1547). Guidance/example text is language-agnostic in the Lua
    source (see module docstring) -- used for both en and de prompts."""
    if tl_len <= 50:
        guidance = "Write a brief one-phrase summary."
        example = "The hero escapes the burning city."
    elif tl_len <= 80:
        guidance = "Write a concise single-sentence summary."
        example = "The hero escapes the burning city and reunites with his companions at the river crossing."
    elif tl_len <= 150:
        guidance = "Write a detailed summary including context and key consequences."
        example = (
            "The hero escapes the burning city, pursued by guards, and reunites with "
            "companions at the river crossing, where they plan their next move against the antagonist."
        )
    else:
        guidance = "Write a rich, full narrative description including character actions, key events, and their consequences."
        example = (
            "The hero escapes the burning city under cover of darkness, pursued by the king's "
            "guards. After a harrowing chase, he reunites with companions at the river crossing, "
            "where they learn the antagonist has seized the eastern fortress and begin planning a counterattack."
        )
    min_len = tl_len * 3 // 4  # Lua: math.floor(tl_len * 0.75); *3//4 is exact for ints, no float rounding
    guidance += (
        f" Write between {min_len} and {tl_len} characters. Do NOT write a shorter "
        "summary unless the chapter has almost no content."
    )
    return guidance, example


def _apply_caps(text: str, caps: dict) -> str:
    """Replace the `{BRACE}` tags -- ported count formulas from
    xray_aihelper.lua:1516-1523 (floor via `//`, same as Lua's math.floor
    since all operands are positive)."""
    char_len, loc_len, tl_len = caps["char"], caps["loc"], caps["tl"]
    hist_len, term_len = caps["hist"], caps["term"]
    num_chars = min(60, max(10, 50 * 200 // char_len))
    num_locs = min(20, max(3, 8 * 100 // loc_len))
    num_hist = min(15, max(3, 8 * 100 // hist_len))
    num_terms = min(20, max(5, 15 * 100 // term_len))
    tl_guidance, tl_example = _timeline_guidance(tl_len)

    for tag, value in {
        "{MAX_CHAR_DESC}": char_len,
        "{NUM_CHARS}": num_chars,
        "{MAX_LOC_DESC}": loc_len,
        "{NUM_LOCS}": num_locs,
        "{MAX_TIMELINE_EVENT}": tl_len,
        "{TIMELINE_DETAIL_GUIDANCE}": tl_guidance,
        "{TIMELINE_EXAMPLE}": tl_example,
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
    """
    caps = DETAIL_CAPS[detail_level]
    system = _SYSTEM[language]

    prompt = _apply_percent_args(_COMPREHENSIVE[language], title, author, percent)
    if mode == "enrich" and prior_names:
        prompt += _enrich_block(prior_names, caps["char"])
    prompt += _NAME_RULES[language] + _SEGMENT_ADDENDUM[language]
    prompt += _CONTEXT_FOOTER[language]
    prompt = _apply_caps(prompt, caps)
    prompt += "\n\nBOOK TEXT CONTEXT:\n" + segment_text

    return system, prompt
