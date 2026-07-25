# calibre X-Ray Desktop Generation — Design

Datum: 2026-07-09 · Status: vom User freigegeben (Brainstorming-Session)

## Ziel

X-Ray-Generierung (Charaktere, Orte, Begriffe, Ereignisse; spoiler-gestaffelt) vom
E-Reader auf den Mac verlagern: ein **calibre-Plugin** generiert die Daten, bettet sie
in die EPUB ein, calibres Wireless-Versand bringt sie aufs Gerät; das KOReader-Plugin
importiert sie einmalig in native `.sdr`-Snapshots. Danach verhält sich alles exakt wie
nativ prefetcht (inkl. Einzelwort-Nachladen, `propagateEntityForward`).

Motivation: Kobo Clara BW ist beim Prefetch langsam/blockiert; Desktop erlaubt
Parallelisierung, schont Akku, ermöglicht höheren Detailgrad.

## Entscheidungen (User-Auswahl)

- **Volles calibre-Plugin** (nicht CLI-first).
- **Transport: in EPUB einbetten** (`xray/xray.json` als Zip-Eintrag, ohne Manifest-Eintrag).
- **Provider: nur Gemini** im ersten Wurf (Port des Gemini-Pfads aus `xray_aihelper.lua`).
- **Detailgrad: Mehr-Detail-Modus von Anfang an** → KOReader-Render-Caps werden
  parametrisiert (`detail_level`), native Generierung behält heutige Werte.

## Projektzuschnitt

- **Neues Repo `calibre-xray`** (Python, SemVer ab 0.1.0): calibre InterfaceAction-Plugin.
- **Dieses Repo**: nur Importer + parametrisierte Detail-Caps + Specs.
- **Vertrag**: Austauschformat `xray.json` (schema-versioniert). Prompts werden einmalig
  aus `prompts/en.lua`/`de.lua` nach Python portiert; Änderungen ab dann bewusst doppelt pflegen.

## calibre-Seite

1. **Textextraktion**: reine Python-Stdlib (zipfile/ElementTree/html.parser), Spine-
   Reihenfolge, Kapitel als Klartext mit Zeichen-Offsets — NICHT calibres Container-API,
   damit derselbe Extraktor in CLI und Plugin läuft und ohne calibre testbar ist.
   (Implementierungsstand siehe `docs/plans/2026-07-09-calibre-xray-plugin.md` + grill-findings.)
2. **Checkpoint-Planer**: 1:1-Port von `computeCheckpoints` (xray_prefetch.lua) —
   gleiche Konstanten (MAX_CHECKPOINTS, MAX_INTERVAL_PCT, HARD_CAP), gleicher
   `isNonNarrativeChapter`-Filter, gleiche Fallback-Regel (<2 narrative Anker → fixe
   10%-Schritte). Einheit: Zeichen-Offset statt Render-Seite.
3. **Gemini-Client**: generationConfig/JSON-Schema-Output wie im Lua-Original,
   Volltext pro Segment (26.7.13-Design). **Segmente parallel** (größter Speed-Gewinn).
4. **Detail-Modus**: Stufe Normal/Detailliert in der Config; höhere Beschreibungs-Caps,
   mehr Timeline-Ereignisse, dichtere Checkpoints; Wert wandert als `detail_level` ins JSON.
5. **Snapshot-Builder**: Port der Merge-Logik (Aliase, Namenskollisionsschutz,
   First-Appearance, chronologisch/alphabetisch, Vorwärts-Propagierung). Fachlich
   heikelster Teil — importierte Daten müssen von nativen ununterscheidbar sein.
6. **GUI/Einbettung**: InterfaceAction „X-Ray generieren" (calibre-Background-Job),
   Config-Dialog (API-Key, Sprache en/de, Detailgrad). Einbettung beim Senden (Hook)
   oder auf Knopfdruck.

## Anker-Strategie (robust, KOReader-synchron)

Checkpoint-**Auswahl** = identischer Algorithmus wie das Gerät (s. o.), daher gleiche
Positionen auch bei kapitellosen Büchern (10%-Raster auf Zeichenlänge).

Positions-**Mapping** pro Checkpoint, dreistufige Ankerkette:

1. **Text-Snippet-Anker (primär, immer vorhanden)**: letzte ~80–120 Zeichen des an die
   KI gesendeten Texts, ASCII-whitespace-normalisiert (NBSP bleibt erhalten), Soft-Hyphens
   entfernt, an Satzgrenze geschnitten und bei Bedarf **verlängert, bis eindeutig** im Buch.
   Importer lokalisiert per Volltextsuche (`findText` → `results[1].page`, verifiziert 2026-07-09).
   Kein Mehrfachtreffer-Ranking nötig: die Eindeutigkeit macht den ersten Treffer zum richtigen —
   und verhindert, dass ein früheres Duplikat einen späteren Snapshot zu früh aktiviert (Spoiler).
   Grenze = exakt die Textstelle, bis zu der die KI las — unabhängig von Pagination/Konvertierung.
2. **TOC-Anker (sekundär)**: Kapiteltitel + Spine-Index (für Kapitelend-Checkpoints),
   Plausibilisierung und Fallback.
3. **Prozent (tertiär)**: Zeichen-Prozent, beim Mapping **abgerundet** (im Zweifel zu
   wenig zeigen statt Spoiler).

Randfälle: Offset in textloser Zone → Anker zur nächsten *vorangehenden* Textstelle.
Alte KOReader ohne Suche → automatisch Stufe 2/3.

## Austauschformat `xray.json` v1

```
schema_version, generator + version, detail_level, language,
book_fingerprint { calibre_uuid, title, authors, text_hash },
complete (bool; false bei Abbruch, mit last_percent), book_type,
timeline: [ { chapter, event, pct } ]  (top-level, ein Eintrag pro Ereignis — nicht pro
                                        Checkpoint im Snapshot),
checkpoints: [ { percent, snippet_anchor, chapter_anchor { toc_title, spine_index },
                 snapshot { characters, locations, terms, historical_figures } } ]
```

Snapshot-Strukturen Feld für Feld wie das heutige `.sdr`-Cache-Format, nur JSON. `timeline`
liegt bewusst top-level statt — wie hier ursprünglich skizziert — pro Checkpoint im Snapshot:
jedes Ereignis trägt sein eigenes `pct`, der KOReader-Importer mappt `pct → Seite` und blendet
Ereignisse ohne gültiges `pct` aus. Das verletzt D4 nicht, weil die Spoiler-Staffelung beim
Lesen auf dem Gerät passiert — anhand des `pct` pro Ereignis — und nicht davon abhängt, wie die
Ereignisse in der Datei angeordnet sind.

## KOReader-Seite (dieses Repo)

1. **Importer**: bei `onReaderReady` ohne vorhandenen Cache EPUB auf `xray/xray.json`
   prüfen (Zip-Lesen über vorhandenen Updater-/26.7.6-Pfad). Schema-Version prüfen,
   Fingerprint plausibilisieren, Ankerkette auf Seiten mappen, native `.sdr`-Snapshots
   schreiben. Einmalig.
2. **Detail-Caps parametrisieren** über `detail_level`.
3. **`complete=false`** → Hinweis „vorbereitet bis X%", Rest-Checkpoints bleiben für
   normalen Geräte-Prefetch offen.

## Fehlerbehandlung & Edge Cases

- DRM/leerer Text → klare Ablehnung im calibre-Job.
- API-Abbruch → Job wiederaufnehmbar (fertige Checkpoints bleiben), `complete=false`.
- Buch-Identität: Gate = Titel/Autor (case-insensitiv, gleiches OPF beide Seiten) +
  Schema-Version + Struktur-Sanity. `text_hash` nur **beratend/gespeichert**, NIE
  Ablehnungsgrund (Python-`\s`/Lua-`%s`-NBSP-Divergenz macht exakten Vergleich unbrauchbar).
- Auslieferung überlebt calibre „Convert Book": eingebettete `xray/xray.json` wird ins
  OPF-Manifest eingetragen (Hilfsressource, nicht Spine).
- Einbetten überschreibt die Bibliotheks-EPUB: Temp-EPUB vor `add_format(replace=True)`
  validieren (testzip + read_epub), sonst Original unangetastet lassen.
- Altes Plugin trifft neueres Schema → Versions-Gate; neueres Plugin trifft älteres
  `schema_version` → akzeptieren (`<= SUPPORTED`), fehlende neue Felder als absent behandeln.
- Mehrere calibre-Formate → generiert/eingebettet wird nur EPUB.

## Tests

- calibre: pytest, Record/Replay-Gemini-Antworten, Golden-Files für Merge/Staging,
  D4-Spoiler-Invarianten als Assertions.
- KOReader: Specs für Importer mit Fixture-EPUB (eingebettete xray.json) unter
  `spec/mocks/`, im Runner registriert.

## Aufwand

calibre-Plugin ~4–6 Wochen bis zum ersten runden Stand (Merge-Port + calibre-Eigenheiten
sind die Treiber); KOReader-Seite ~1–1,5 Wochen. API-Wartezeit bleibt — Gewinn ist
Parallelisierung, freier Reader, Akku, Detailgrad.
