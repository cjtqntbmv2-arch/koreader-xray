--[[
X-Ray-Prompts – optimiert für Gemini-Modelle (2.5 / 3.x)
Drop-in-kompatibel: gleiche Keys, gleiche Reihenfolge der %s/%d-Platzhalter,
gleiche {TEMPLATE_VARS}. Nur die Prompt-Texte wurden umgebaut.

EMPFOHLENE API-ÄNDERUNGEN (größter Hebel, unabhängig vom Prompt-Text):
1. Strukturierte Ausgabe aktivieren statt JSON per Prompt zu erzwingen:
   generationConfig = { responseMimeType = "application/json",
                        responseSchema  = <Schema pro Prompt-Typ> }
   -> garantiert parsbares JSON, macht Escaping-/Fence-Regeln überflüssig.
   Wichtig: Schema NICHT zusätzlich im Prompt duplizieren, wenn responseSchema
   gesetzt ist (Google-Empfehlung). Solange nur der Prompt-Text genutzt wird,
   bleiben die JSON-Beispiele unten erhalten.
2. temperature = 0.0–0.2 für Extraktionsaufgaben.
3. maxOutputTokens ausreichend hoch setzen (z. B. 8192+), das entschärft das
   Trunkierungsproblem zuverlässiger als Prompt-Heuristiken.
4. Long-Context-Reihenfolge: Gemini arbeitet am besten, wenn große Kontexte
   ZUERST kommen und die Anweisung am ENDE steht. Da der Plugin-Code den
   Kontext ans Prompt-Ende hängt, unten das Feld `context_footer` NACH den
   Kontextblöcken anhängen (kleine Code-Änderung, großer Effekt).
]]

return {
    -- System instruction (per systemInstruction-Feld der API übergeben)
    system_instruction = [[Sie sind ein präziser Literaturanalyst für eine E-Reader-X-Ray-Funktion. Für jede Antwort gilt:
1. AUSGABE: ausschließlich EIN gültiges JSON-Objekt. Kein Markdown, keine Codezäune (```), kein Text davor oder danach.
2. JSON-SICHERHEIT: Doppelte Anführungszeichen in Strings escapen (\"). Keine rohen Zeilenumbrüche in Strings (außer als \n).
3. QUELLE: Aussagen zu fiktiven Inhalten stützen Sie ausschließlich auf den mitgelieferten Buchkontext. Trainingswissen ist nur dort erlaubt, wo die Aufgabe es ausdrücklich freigibt (reale historische Personen, Serien-Metadaten).
4. SPOILER: Die angegebene Lesefortschritts-Grenze ist absolut. Inhalte danach existieren für Sie nicht.]],

    -- Author-only prompt (For quick bio lookup)
    author_only = [[# METADATEN
Buchtitel: "%s"
Autor laut Metadaten: "%s"

# AUFGABE
Verifizieren Sie den Autor anhand des BOOK TEXT CONTEXT (falls am Ende dieses Prompts vorhanden) und erstellen Sie eine Kurzbiografie. Weicht der tatsächliche Autor von den Metadaten ab, verwenden Sie den korrekten Namen aus dem Kontext.

# REGELN
- Schwerpunkt der Biografie: literarische Karriere und Hauptwerke.
- Daten im lokalen Datumsformat (z. B. TT.MM.JJJJ). Unbekannte Daten: leerer String "".

# JSON-FORMAT
{
  "author": "Vollständiger korrekter Name",
  "author_bio": "Kompakte Biografie (Karriere, Hauptwerke)",
  "author_birth": "Geburtsdatum oder \"\"",
  "author_death": "Sterbedatum oder \"\""
}]],

    -- Single Comprehensive Fetch (Combined Characters, Locations, Timeline)
    comprehensive_xray = [[# METADATEN
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
3. Ausgabe: nur das JSON-Objekt, ohne Codezäune und ohne Begleittext.]],

    -- Fetch More Characters (AI Limit Bypass)
    more_characters = [[# METADATEN
Buch: %s
Autor: %s
Spoiler-Grenze: %d%% Lesefortschritt

# AUFGABE
Extrahieren Sie GENAU 10 ZUSÄTZLICHE wichtige Charaktere aus dem Kontext am Ende dieses Prompts. Ausgabe: genau EIN JSON-Objekt.

# BEREITS EXTRAHIERT – diese Charaktere (inkl. ihrer Aliase) auslassen:
%s

# REGELN
- "name" = vollständiger formeller Name; Spitznamen in "aliases" (max. 3).
- "description" ausschließlich aus dem gelieferten Text; max. {MAX_CHAR_DESC} Zeichen, damit die Ausgabe vollständig bleibt.
- Spoiler-Grenze %d%%: Beschreibungen spiegeln exakt den Stand an dieser Marke; nichts aus späteren Abschnitten.

# JSON-FORMAT
{
  "characters": [
    {
      "name": "Vollständiger formeller Name",
      "aliases": ["Alias 1", "Alias 2"],
      "role": "Rolle bis zur Spoiler-Grenze",
      "gender": "Männlich / Weiblich / Unbekannt",
      "occupation": "Beruf/Status",
      "description": "Nur aus dem gelieferten Text (max. {MAX_CHAR_DESC} Zeichen)"
    }
  ]
}]],

    -- Fetch More Terms (Glossary Support)
    more_terms = [[# METADATEN
Buch: %s
Autor: %s
Spoiler-Grenze: %d%% Lesefortschritt

# AUFGABE
Extrahieren Sie GENAU 15 ZUSÄTZLICHE bedeutende Begriffe aus dem Kontext am Ende dieses Prompts. Ausgabe: genau EIN JSON-Objekt.
- Sachbuch: Fachbegriffe, Konzepte, Akronyme, Jargon.
- Belletristik: World-Building-Elemente (Fraktionen, Organisationen, Magiesysteme, Technologien, Kreaturen, Sprachen, Lore).

# BEREITS EXTRAHIERT – diese Begriffe auslassen:
%s

# REGELN
- Definitionen max. {MAX_TERM_DEF} Zeichen, damit die Ausgabe vollständig bleibt.
- Keine Charakter-/Ortsnamen, keine Alltagsbegriffe.
- Spoiler-Grenze %d%%: keine Informationen aus späteren Abschnitten.

# JSON-FORMAT
{
  "terms": [
    {
      "name": "Begriff oder Akronym",
      "expanded": "Ausgeschriebene Form oder identisch mit name",
      "category": "Faction / Magic System / Technology / Creature / Organization / Lore / Language / Acronym / Technical Term / Concept / Jargon",
      "definition": "Präzise Definition im Buchkontext (max. {MAX_TERM_DEF} Zeichen)"
    }
  ]
}]],

    -- Targeted Single Word Lookup
    single_word_lookup = [[Der Benutzer hat das Wort "%s" hervorgehoben.

# AUFGABE
Bestimmen Sie anhand des BOOK TEXT CONTEXT am Ende dieses Prompts, ob dieses Wort im Buch ein Charakter, ein Ort, eine historische Figur oder ein Fachbegriff/Akronym ist, und liefern Sie den passenden Eintrag.

# ENTSCHEIDUNGSREGELN
1. FIKTIVE CHARAKTERE UND ORTE: ausschließlich der mitgelieferte BOOK TEXT CONTEXT zählt. Beschreiben Sie nur, was dieser Text offenbart – auch wenn Sie den Charakter aus einer bekannten Serie wiedererkennen. Bei nur kurzer Erwähnung bleibt die Beschreibung entsprechend knapp.
2. REALE HISTORISCHE PERSONEN: Identität, Biografie und historische Rolle dürfen Sie aus internem Wissen liefern – aber nur bei realen, bedeutenden Personen. Die Relevanz im Buch ("role"/Kontext) stammt aus dem Textkontext.
3. FACHBEGRIFFE (v. a. bei Sachbüchern): Prüfen Sie, ob das Wort ein Fachbegriff, Akronym oder Schlüsselkonzept ist, und definieren Sie es im Buchkontext.
4. Trifft nichts davon zu, setzen Sie "is_valid" auf false und begründen Sie kurz.

# JSON-FORMAT (bei is_valid = true)
{
  "is_valid": true,
  "type": "character | location | historical_figure | term",
  "item": {
    "name": "Vollständiger Name",
    "aliases": ["Alias 1", "Alias 2"],
    "role": "Rolle",
    "gender": "Männlich/Weiblich/Unbekannt",
    "occupation": "Beruf",
    "description": "Kurze Beschreibung (max. 250 Zeichen)"
  },
  "error_message": ""
}
Feldvarianten je nach "type":
- location: "item" enthält "name" und "description".
- historical_figure: "item" enthält "name", "biography" und "role".
- term: "item" enthält "name", "expanded", "category" und "definition".

# JSON-FORMAT (bei is_valid = false)
{
  "is_valid": false,
  "error_message": "Kurze Erklärung, warum dies kein gültiger Eintrag ist."
}]],

    -- Multi-Book Series Context Prompts
    series_detect = [[Buchtitel: %s
Autor: %s

# AUFGABE
Bestimmen Sie, ob dieses Buch Teil einer benannten Serie ist. Antworten Sie nur auf Basis gesicherten Wissens; wenn Sie nicht sicher sind, geben Sie { "is_series": false } zurück – erfinden Sie keine Serie.

# JSON-FORMAT (Teil einer Serie)
{
  "is_series": true,
  "series_name": "Das Rad der Zeit",
  "book_index": 3,
  "total_books_known": 14
}

# JSON-FORMAT (keine Serie oder unsicher)
{ "is_series": false }]],

    prior_book_list = [[Serie: %s
Aktueller Buchindex: %d
Aktueller Buchtitel: %s

# AUFGABE
Listen Sie die Titel (und Autoren, falls abweichend von "%s") der Bücher 1 bis %d auf, die in dieser Serie VOR dem aktuellen Buch erschienen sind.

# REGELN
- Nur Bücher aufführen, deren Existenz und Reihenfolge Sie sicher kennen. Erfinden Sie keine Titel; lassen Sie unsichere Einträge weg.
- Reihenfolge: aufsteigend nach Serienindex.

# JSON-FORMAT
{
  "prior_books": [
    { "index": 1, "title": "Das Auge der Welt", "author": "Robert Jordan" }
  ]
}]],

    series_book_summary = [[Buch: %s
Autor: %s
Dies ist Buch %d der Serie "%s".

# AUFGABE
Erstellen Sie eine VOLLSTÄNDIGE Zusammenfassung dieses gesamten Buches für einen Leser, der als Nächstes den Folgeband beginnt. Spoiler für dieses Buch sind ausdrücklich erwünscht; Spoiler für spätere Bände sind verboten.

# INHALT
- Hauptcharaktere: Name, Rolle, Status am Buchende.
- Hauptorte.
- Kritische Handlungsereignisse inklusive Ende.
- Wichtige eingeführte World-Building-Begriffe.

# JSON-FORMAT
{
  "characters": [
    { "name": "Vollständiger Name", "aliases": [], "role": "...", "description": "Status am Ende dieses Buches (max. 300 Zeichen)" }
  ],
  "locations": [
    { "name": "...", "description": "..." }
  ],
  "terms": [
    { "name": "...", "aliases": ["Alias 1", "Alias 2"], "expanded": "...", "category": "...", "definition": "..." }
  ],
  "timeline": [
    { "chapter": "Buchzusammenfassung", "event": "Eine einzelne, hochdetaillierte Zusammenfassung von Handlung, Hauptereignissen und Ende des gesamten Buches (max. 2000 Zeichen). Gliedern Sie sie zwingend in mehrere Absätze, getrennt durch doppelte Zeilenumbrüche (\\n\\n) – kein einzelner Textblock." }
  ]
}]],

    -- Find Duplicates
    find_duplicates = [[# METADATEN
Buch: %s
Autor: %s
Spoiler-Grenze: %d%% Lesefortschritt

# AUFGABE
Prüfen Sie die folgende Liste von %s aus diesem Buch und identifizieren Sie Einträge, die dieselbe Entität unter verschiedenen Namen bezeichnen.

# LISTE
%s

# REGELN
- Duplikat = zwei Einträge bezeichnen eindeutig dieselbe Entität (z. B. "Die Große Bibliothek" / "Große Bibliothek", "John" / "John Doe").
- Melden Sie nur Paare, bei denen Sie sich sehr sicher sind. Verwandte oder ähnliche, aber verschiedene Entitäten sind keine Duplikate.
- Keine Duplikate vorhanden: leeres Array zurückgeben.
- Spoiler-Grenze %d%%: keine Informationen aus späteren Abschnitten verwenden.

# JSON-FORMAT
{
  "duplicate_pairs": [
    {
      "primary": "Zu BEHALTENDER Eintrag (vollständigerer/formellerer Name)",
      "secondary": "Zu ENTFERNENDER Eintrag",
      "reason": "Kurzer Grund (max. 100 Zeichen)"
    }
  ]
}]],

    -- Merge Descriptions
    merge_descriptions = [[# AUFGABE
Kombinieren Sie die folgenden zwei Beschreibungen derselben Entität (Charakter oder Ort) zu einer einzigen, prägnanten und natürlich fließenden Zusammenfassung. Entfernen Sie Redundanzen; behalten Sie alle einzigartigen Fakten beider Beschreibungen.

Hauptbeschreibung: %s
Sekundärbeschreibung: %s

# JSON-FORMAT
{
  "merged_description": "Kombinierte, überarbeitete Beschreibung (max. {MAX_CHAR_DESC} Zeichen)"
}]],

    -- OPTIONAL: Nach den Kontextblöcken anhängen (Gemini Long-Context-Best-Practice:
    -- Anweisung ans Prompt-Ende, hinter die Daten). Erfordert eine kleine Anpassung
    -- im aufrufenden Code: prompt .. context .. context_footer
    context_footer = [[

---
Führen Sie jetzt, basierend ausschließlich auf dem gesamten Kontext oben, die eingangs definierte Aufgabe aus. Beachten Sie die Spoiler-Grenze und geben Sie nur das geforderte JSON-Objekt aus – ohne Codezäune, ohne Begleittext.]],

    -- Fallback strings (unverändert)
    fallback = {
        unknown_book = "Unbekanntes Buch",
        unknown_author = "Unbekannter Autor",
        unnamed_character = "Unbenannter Charakter",
        not_specified = "Nicht angegeben",
        no_description = "Keine Beschreibung",
        unnamed_person = "Unbenannte Person",
        no_biography = "Keine Biografie verfügbar"
    }
}
