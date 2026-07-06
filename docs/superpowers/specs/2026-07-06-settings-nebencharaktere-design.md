# Design: Settings-Bereinigung nach Checkpoint-Prefetch + Nebencharakter-Vollständigkeit

Datum: 2026-07-06 · Basis: Version 26.7.2 (Checkpoint-Prefetch gemerged) · Status: vom User freigegeben

## 1. Kontext & Problem

Nach dem Checkpoint-Prefetch (26.7.2) sind zwei Baustellen offen:

**Teil A — irreführende Menütexte.** Die Spoiler-Einstellung (`spoiler_setting`: `spoiler_free` | `full_book`) hat weiterhin echte Funktion (Vollbuch-Modus für Sachbücher/Re-Reads: ein Abruf, keine Positionsfilterung), aber ihre Texte beschreiben die alte Abruf-Semantik („Up to X% of the book"). Nach einem Prefetch liegen 100 % der Daten im Cache; „spoilerfrei" bedeutet jetzt „Anzeige folgt der Leseposition". Zusätzlich koppeln die Description-Length-Settings unsichtbar die Anzahl erfasster Einträge (200 Zeichen → 25 Charaktere, 500 → 10; `xray_aihelper.lua:1420`), ohne dass das Menü es erwähnt.

**Teil B — Nebencharaktere fehlen.** Bei figurenreichen Büchern (Das Lied von Eis und Feuer: 46–82 Kapitel/Band; Feuer und Blut) fehlen kleinere Figuren systematisch. Ursachen (alle verifiziert):

1. Anti-Truncation-Protokoll im Prompt: >40 Kapitel ⇒ „top 10 characters only" (`prompts/en.lua:61-62`).
2. `{NUM_CHARS}` = 25 Standard, invers gekoppelt an Beschreibungslänge, Floor 10, Cap 40 (`xray_aihelper.lua:1420`).
3. Wording „Extract important characters" biased gegen Nebenfiguren.
4. Kapitel-Samples decken beim Ein-Call-Vollabruf nur einen kleinen Teil jedes Kapitels ab.

Die harte Grenze dahinter ist real: Die JSON-Antwort der KI hat ein Ausgabelimit; ein einzelner Call kann ein 80-Kapitel-Buch nicht vollständig abdecken.

**Teil B+ — Namenskollisionen.** Verschiedene Figuren mit gleichem Namen (Dynastien: Aegon, Walder, Brandon …) werden heute auf drei Ebenen stillschweigend zu einem Eintrag verschmolzen:

1. `deduplicateByName` (`xray_data.lua:152`): merged bei exakt gleichem Namen, bei Alias-Treffer und sogar bei bloßem Vornamen-Treffer ≥5 Zeichen gegen die Alias-Map (Check 3).
2. Merge-Mode-Instruktionen zeigen der KI die existierende gleichnamige Figur mit Auftrag „Beschreibungen verschmelzen" (`xray_aihelper.lua:1228`).
3. Die finale Duplikat-Prüfung (`find_duplicates`-Prompt) sagt bei identischen Namen tendenziell „gleiche Entität".

Mehr Nebenfiguren (Teil B) verschärfen das ohne Gegenmaßnahmen.

## 2. Entscheidungen (User-Dialog)

- **E1 Spoiler-Menü:** behalten, nur neu rahmen. Config-Key `spoiler_setting` und Werte bleiben unverändert (Bestandsconfigs gültig).
- **E2 Vollständigkeits-Linie:** „handelnde Figuren" — jede Figur, die im gelesenen Text spricht oder handelt, auch Einszenen-Nebenfiguren. Nicht: bloße Erwähnungen, Aufzählungen, Stammbäume (Kindle-X-Ray-Linie).
- **E3 Pfad & Kosten:** Der Checkpoint-Prefetch ist der Vollständigkeitspfad (passt zum Workflow „am Anfang online vorbereiten, dann komplett offline lesen"). Null zusätzliche API-Calls; der Einzel-Fetch bleibt bewusst top-N. Kein Mehrpass-Mechanismus.
- **E4 Namenskollisionen:** Maßnahmen B4–B6 aufnehmen. Leitlinie: Lieber vorübergehend zwei Einträge für dieselbe Person (reparierbar durch Dupe-Check/manuellen Merge) als zwei Personen in einem Eintrag (irreversibler Datenverlust).

## 3. Design Teil A — Menütexte (kein Verhaltenswechsel)

**A1 Spoiler-Menü neu rahmen.** Neue Texte für die beiden Optionen und den About-Text in `showSpoilerSettings` (`xray_ui.lua:2549`):

- Option 1: „Spoilerfrei (empfohlen) — Anzeige folgt deiner Leseposition."
- Option 2: „Alles anzeigen (Vollbuch) — für Sachbücher und Re-Reads: ein Abruf, keine Positionsfilterung."
- About-Text erklärt das Zusammenspiel mit der Offline-Vorbereitung: Nach dem Prefetch sind alle Daten lokal; spoilerfrei steuert die Anzeige, nicht den Datenstand.

Umsetzung über die vorhandenen Loc-Keys (`spoiler_free_menu_option`, `spoiler_free_about`, `spoiler_preference_desc`, ggf. fehlende Keys ergänzen — beim Implementieren gegen `xray_ui.lua:2549 ff.` prüfen). Fallback-Strings in `localization_xray.lua` (CRLF!) anpassen, EN + DE in den `.po`-Dateien gepflegt, übrige Sprachen englische Platzhalter via `python3.12 tools/sync_translations.py`.

**A2 Description-Length-Menü ehrlich machen.** Neuer Hinweistext im Menü (`menu_desc_length_settings`-Untermenü in `xray_ui.lua`): „Längere Beschreibungen reduzieren die Anzahl der pro Abruf erfassten Einträge." Die Kopplung selbst bleibt bestehen (Truncation-Schutz beim Einzel-Fetch; beim Prefetch neutralisiert B2 sie).

**A3 Nichts streichen.** Auto-X-Ray-Frequenz, Book Type, Mentions, Series Context, Linked Entries bleiben unverändert. Kein Zusatzhinweis im Frequenz-Dialog (Guard arbeitet lautlos korrekt).

## 4. Design Teil B — Nebencharakter-Vollständigkeit

Alle Prompt-Ergänzungen erfolgen **Lua-seitig** in `xray_aihelper.lua` als angehängter englischer Zusatzkontext — derselbe Mechanismus wie die bestehende Alias-Regel (`xray_aihelper.lua:1402`), wirkt für alle 16 Sprachen. **Keine Änderungen an `prompts/<lang>.lua`.**

**B1 Charakter-Definition (alle comprehensive-Abrufe).** An den `comprehensive_xray`-Kontext wird angehängt (sinngemäß): „Ein Charakter ist jede Figur, die im vorliegenden Text spricht oder handelt — ausdrücklich auch Nebenfiguren mit nur einer Szene. Figuren, die nur in Aufzählungen, Genealogien oder beiläufigen Erwähnungen vorkommen, sind keine Einträge. Reicht der Platz nicht, priorisiere nach Wichtigkeit und kürze zuerst bei Nebenfiguren."

**B2 Segment-Anweisung im Checkpoint-Fetch (der Hebel).** `xray_fetch.continueWithFetch` setzt `context.prefetch_segment = true` genau dann, wenn `self.prefetch_active`. Bei gesetztem Flag hängt `createPrompt` an den comprehensive-Zusatzkontext (Block bei `xray_aihelper.lua:1401 ff.`, außerhalb des Merge-Blocks — greift damit auch beim ersten Checkpoint, der ohne Bestandsdaten läuft) an: „Dieser Abruf deckt ein abgegrenztes Textsegment ab. Erfasse JEDE sprechende/handelnde Figur dieses Segments, auch kleine. Die Zielanzahl {NUM_CHARS} gilt für NEUE Figuren dieses Segments, nicht für die Gesamtliste. Nebenfiguren erhalten kurze Beschreibungen. Wenn der Platz nicht reicht: unwichtigste zuerst weglassen." Vollständigkeit akkumuliert damit über die ~10 Checkpoint-Calls; pro Segment (~7 Kapitel) sieht die KI durch das Sample-Budget etwa zehnmal mehr Text pro Kapitel als beim Vollabruf.

**B3 Duplikate über Segmentgrenzen.** Kein neuer Mechanismus — der finale `runPostFetchDuplicateCheck(…, 100, true)` am Prefetch-Ende fängt Mehrfach-Erfassungen ab.

**B4 Eindeutige Namen als Regel (KI-seitig).** Zusatz in B1- und B2-Anhang: „Tragen verschiedene Figuren denselben Namen (Dynastien, Vater/Sohn), verwende IMMER die unterscheidende Namensform als Eintragsnamen (Ordnungszahl, Beiname, Sitz — z. B. ‚Aegon II Targaryen', ‚Walder Frey, Lord of the Crossing'). Der nackte geteilte Name darf bei keiner dieser Figuren als Alias stehen. Behandle eine neue Figur nur dann als bereits bekannt, wenn der Text eindeutig dieselbe Person meint — lege sonst einen separaten, disambiguierten Eintrag an."

**B5 Warnung in der Duplikat-Prüfung.** An den `find_duplicates`-Prompt (Aufbaustellen `xray_aihelper.lua:2009` und `:2045`) wird angehängt: „Gleicher oder ähnlicher Name beweist NICHT dieselbe Entität — Dynastien vergeben Namen mehrfach. Vergleiche Rolle und Beschreibung; markiere nur bei eindeutiger Identität, im Zweifel nicht."

**B6 Vornamen-Automatismus entschärfen (Code).** In `deduplicateByName` (`xray_data.lua:172 ff.`) entfällt Check 3 (First-Name-Komponente ≥5 Zeichen gegen die Alias-Map). Verhalten danach: Mehrteilige eingehende Namen mergen nur noch über exakten Namens-Treffer (Check 1) oder vollen Alias-Treffer (Check 2); einteilige Namen („Daenerys") werden von Check 2 weiterhin gefangen. Konsequenz: „Daenerys Stormborn" neben „Daenerys Targaryen" bleibt zunächst zweifach und wird von der KI-Duplikat-Prüfung (die Beschreibungen vergleicht) oder manuell gemerged — gewollt gemäß E4. Bestehende Specs, die Check-3-Verhalten testen, werden entsprechend angepasst.

**Erwartung:** ASOIAF-Band nach Prefetch realistisch 60–150 Figuren (statt heute 10–25), spoilergerecht auf Snapshots verteilt. Prompt-Wachstum durch mehr Bestandsfiguren bleibt begrenzt: Der Merge-Kontext enthält nur Figuren, deren Name/Alias im neuen Sample vorkommt (vorhandenes `found_in_sample`-Trimming, `xray_aihelper.lua:1230 ff.`).

## 5. Bekannte Grenzen (dokumentiert, nicht gelöst)

- **Samples ≠ 100 % Text:** Auch Segment-Abrufe sehen Textproben. Eine Figur, die nur mitten in einem Kapitel kurz auftritt, kann durchrutschen — „vollständig" heißt „nahezu vollständig". Auffangnetz: Wort-Lookup legt Figuren gezielt an.
- **Extrem figurenreiche Segmente** können trotz Segment-Scope das Ausgabelimit reißen → Sicherheitsventil in B2 (priorisieren statt platzen); Truncation-Repair existiert.
- **Mentions bei Namensvettern** bleiben unscharf: Ein nacktes „Aegon" im Text ist keinem der Aegons sicher zuzuordnen. Wird nicht gelöst.
- **Einzel-Fetch ohne Prefetch** bleibt top-N (nur mit besserer Charakter-Definition durch B1) — by design, E3.

## 6. Nicht-Ziele

- Kein Mehrpass-Mechanismus für den Einzel-Fetch.
- Keine Änderungen an `prompts/<lang>.lua` (alle 16 Sprachen unangetastet).
- Keine neuen UI-Features für lange Charakterlisten.
- Keine Änderung an Config-Keys oder deren Werten.
- Kein Rückbau der Anzahl-Kopplung in den Description-Length-Settings (nur Transparenz, A2).

## 7. Betroffene Dateien

| Datei | Änderung | Zeilenenden |
|---|---|---|
| `xray.koplugin/xray_aihelper.lua` | B1, B2 (Anhang), B4, B5 | **CRLF — erhalten!** |
| `xray.koplugin/xray_fetch.lua` | B2 (Flag setzen) | LF |
| `xray.koplugin/xray_data.lua` | B6 (Check 3 entfernen) | LF |
| `xray.koplugin/xray_ui.lua` | A1, A2 (Menütexte/Loc-Aufrufe) | LF |
| `xray.koplugin/localization_xray.lua` | A1, A2 (Fallback-Strings) | **CRLF — erhalten!** |
| `xray.koplugin/languages/*.po` | A1, A2 Keys (EN+DE gepflegt, Rest Platzhalter) | — |
| `spec/xray_aihelper_spec.lua`, `spec/xray_fetch_spec.lua`, `spec/xray_data_spec.lua` | neue/angepasste Tests | LF |
| `xray.koplugin/_meta.lua` | Version 26.7.3 | **CRLF — erhalten!** |

## 8. Teststrategie

- **aihelper:** comprehensive-Prompt enthält B1+B4-Passagen; Merge-Instruktionen enthalten die Segment-Passage genau dann, wenn `context.prefetch_segment` gesetzt ist; `find_duplicates`-Prompt enthält B5-Passage.
- **fetch:** `continueWithFetch` setzt `prefetch_segment` genau bei `self.prefetch_active` (positiv + negativ).
- **data:** `deduplicateByName` merged mehrteilige Namen nicht mehr über bloßen Vornamen-Alias („Aegon Blackfyre" bleibt neben Eintrag mit Alias „Aegon" bestehen); einteilige Namen mergen weiterhin („Daenerys" → „Daenerys Targaryen"); bestehende Tests auf Check-3-Abhängigkeit prüfen/anpassen.
- **Lokalisierung:** `python3.12 tools/sync_translations.py` + `python3.12 tools/check_translations.py` grün.
- **Gesamt:** `luajit tools/spec_runner.lua` ohne neue Fehler (11 dokumentierte Env-Fails ohne `SQUASHFS_ROOT` erlaubt); Syntax via `luajit -bl` je geänderter Datei; CRLF-Erhalt via `file` verifizieren; danach `git checkout -- spec/mocks/xray/series/the_wheel_of_time.lua` (bekannter Suite-Seiteneffekt, sofern Task „Series-Spec-Fix" noch nicht gelandet ist).
- **Abschluss:** Version 26.7.3 in `_meta.lua`, Commit(s) pro Phase, Tag `26.7.3` (lokal, kein Remote konfiguriert → kein Push).
