# X-Ray Neuausrichtung — radikaler Rückbau auf „liest Daten, zeigt Daten"

Stand: 2026-07-25, Fassung 2 (nach adversarialer Prüfung durch drei Reviewer).
Grundlage: Interview vom selben Tag (`/grill-me`).
Betrifft beide Repos: `calibre-xray` (lokal) und `koreader-xray` (öffentlich,
`cjtqntbmv2-arch/koreader-xray`).

## Ziel

Ein KOReader-Plugin, das beim Aufschlagen einfach da ist: X-Ray-Daten aus der
Buchdatei lesen, bis zum Lesepunkt anzeigen, fertig. Erzeugt wird ausschließlich
am Desktop mit dem Claude-Skill. calibre bettet die fertige `xray.json` ins EPUB
ein und schickt es per eigener WLAN-Verbindung ans Gerät.

Alles, was diesem Satz nicht dient, wird gelöscht — nicht abgeschaltet, nicht
konfigurierbar gemacht.

## Entscheidungen

| Thema | Entscheidung |
|---|---|
| Erzeugung | nur der `xray`-Claude-Skill; Gemini komplett raus |
| calibre | Mini-Plugin, ein Knopf: `xray.json` ans EPUB anhängen + Tag ergänzen |
| KOReader | Neubau, Anzeige-only; Timeline und Wörterbuch-Integration bleiben |
| gestrichen | Serien-Funktion, Erwähnungs-Scan, On-Device-AI, alle Provider |
| Lesepunkt | Bruchteil auf der **Textachse** (nicht Seiten-Prozent) + Marge |
| Anker | Schnipsel-/TOC-Anker entfallen, Schema-Bump |
| Gerätespeicher | kein Cache — `xray/xray.json` bei Bedarf aus der EPUB lesen |
| Import | kein Import-Schritt, kein Dialog; Identitätsprüfung wandert auf den Desktop |
| Einstellungen | genau eine: Wörterbuch-Integration an/aus |
| Sprache | KOReaders gettext, Strings englisch + `de.po` |
| Struktur | ein Repo (`koreader-xray`), eine Version |
| Versionierung | CalVer bleibt (nächstes Release `26.7.25`) |
| Updater | schlank portiert, wöchentlicher stiller Check bleibt |
| Altlasten | einmaliger Aufräumer mit Rückfrage beim ersten Start |

## Zielstruktur (Monorepo `koreader-xray`)

```
xray.koplugin/          _meta.lua  main.lua  xray_doc.lua  xray_ui.lua
                        xray_lookup.lua  xray_updater.lua  languages/de.po
calibre_plugin/         __init__.py  ui.py  plugin-import-name-xray_generator.txt
xray_core/              epub.py  checkpoints.py  generate.py  merge.py
                        prompts.py  schema.py  embed.py
tools/                  claude_xray_plan.py  claude_xray_assemble.py
                        build_calibre_plugin.py  build_koplugin.py  release.py
.claude/skills/xray/    SKILL.md
schema/xray.schema.json
spec/                   busted-Specs (Gerät) + mocks/
tests/                  pytest (Desktop)
VERSION                 26.7.25
```

## Phase 1 — Repos zusammenführen

1. Letzten Stand des alten Lua-Plugins taggen: `26.7.18-legacy` (bare, wie die
   bestehenden Tags). **Nur taggen, kein GitHub-Release** — ein Release nach
   `26.7.25` würde `/releases/latest` auf den Legacy-Stand ziehen und den
   Update-Kanal aller bestehenden Installationen still einfrieren.
2. `git subtree add --prefix=_import ../calibre-xray master`, danach per
   `git mv` in die Zielstruktur, dann `_import/` entfernen. `--prefix=.` ist
   nicht erlaubt, und `tools/` sowie `docs/` existieren auf beiden Seiten —
   direkter Subtree in ein bestehendes Verzeichnis scheitert.
3. `.gitignore` von Hand mergen. **Kritisch:** koreaders `.gitignore` enthält
   `.claude/` — der Claude-Skill, künftig der einzige Erzeugungspfad, wäre nach
   dem Umzug still untracked. Ausnahme ergänzen (`!.claude/skills/`) und mit
   `git check-ignore -v .claude/skills/xray/SKILL.md` verifizieren. `CLAUDE.md`
   und `README.md` existieren beidseitig und werden inhaltlich zusammengeführt.
4. Zwei Build-Skripte: `build_koplugin.py` → `xray.koplugin.zip` (Asset-Name
   unverändert, an drei Stellen im Updater hartkodiert), `build_calibre_plugin.py`
   → `dist/xray-generator-<VERSION>.zip`. Pfadableitung nicht über
   `parent.parent`, sondern nach oben laufen bis `VERSION` gefunden ist — und
   nicht auf Modulebene lesen, sonst stirbt der Build beim Import statt beim
   Aufruf.
5. **Versionsquelle konsolidieren.** `VERSION` ist die Quelle; der Build stempelt
   sie in `_meta.lua` (das `.koplugin`-Zip enthält `VERSION` nicht, der Updater
   liest aber `_meta.lua`). `calibre_plugin/__init__.py` liest sie ebenfalls.
   `_generator_version()` mitsamt hartkodiertem `"0.1.0"`-Fallback fliegt raus;
   die Version wird dem Assembler übergeben. Sonst pinnt das Golden-File die
   Generator-Version und **jeder** künftige Bump macht `test_golden_equality` rot.
6. `.github/workflows/` prüfen — die CI dort kennt `tests/` noch nicht.
7. `calibre-xray` lokal archivieren, nicht löschen.

**Fertig, wenn:** `python3 -m pytest tests/` im Monorepo grün ist, beide
Build-Skripte ein installierbares Zip erzeugen und `git check-ignore` den Skill
nicht mehr verschluckt.

## Phase 2 — Desktop-Rückbau

Der Claude-Pfad fährt `generate_xray` heute schon mit Stub-Client,
`enrich=False`, `glean=False` (`tools/claude_xray_assemble.py:92`). Alles, was
nur für den Netzpfad existiert, ist damit toter Code.

1. **Löschen:** `xray_core/gemini.py`, `xray_core/__main__.py`,
   `calibre_plugin/config.py`.
2. **`generate.py` eindampfen** auf: Chunks aus dem Workdir laden →
   `clean_response` → geordneter Merge (Phase B) → Snapshots einfrieren →
   validieren. Weg fallen `ThreadPoolExecutor`, `RateLimiter`, `QuotaError`,
   `_completed_prefix_len`, Phase A und Phase C samt `_enrich_checkpoint`,
   `_glean_chunk`, `_fetch_with_retry`, `progress_cb`, `max_workers`.
   `_chunk_path`/`_chunk_segment` bleiben (der Skill baut darauf).
3. **Checkpoint-Prozent aufrunden statt abrunden.** `checkpoints.py:119` rechnet
   `p * 100 // total`; das Coalescing behält bei gleichem Prozentwert den
   Checkpoint mit dem größten Offset. Gemessen an einem echten Buch deckt der
   Checkpoint mit `percent = 4` real bis 4,92 % ab, der mit `percent = 15` bis
   15,78 % — fast ein voller Punkt der Sicherheitsmarge ist weg, bevor die
   Geräte-Abweichung überhaupt dazukommt. Für nicht-finale Checkpoints
   aufrunden (`-(-p * 100 // total)`); die strenge Aufsteigung und
   `first_pct <= checkpoint.percent` bleiben unberührt. Unter dem alten
   Ankermodell war das folgenlos, unter dem Prozentmodell ist es spoilerrelevant.
4. **Schema v2:** `snippet_anchor` und `chapter_anchor` aus `schema.py`,
   `schema/xray.schema.json`, dem Golden-File **und den Fixture-Kopien unter
   `spec/mocks/`** entfernen, `schema_version` erhöhen. In `checkpoints.py`
   entfällt die Schnipsel-Extraktion; ein Checkpoint ist danach `percent` +
   `offset` + `snapshot`.
5. **`prompts.py`:** `EXTRACT_RESPONSE_SCHEMA` (Gemini-Response-Format) raus,
   `build_prompt` bleibt.
6. **`complete` / `last_percent`** bleiben im Schema und werden auf dem Gerät im
   Status angezeigt (siehe Phase 4). Über den Claude-Pfad ist `complete: false`
   nicht mehr erzeugbar — der Assembler bricht bei fehlenden Chunks ab.
7. Golden-File neu erzeugen (Einzeiler im Docstring von `tests/test_e2e.py`),
   Diff von Hand lesen. Die `test_d4_*`-Familie muss unverändert grün bleiben —
   sie ist die Spoiler-Garantie und wird **nicht** angepasst, um Rot zu
   verstecken.

**Fertig, wenn:** pytest grün, ein realer Skill-Lauf erzeugt ein Dokument nach
Schema v2, und der D4-Test bestätigt weiterhin `first_pct <= checkpoint.percent`.

## Phase 3 — calibre-Mini-Plugin

Ersetzt `calibre_plugin/ui.py` vollständig. Aktion **„X-Ray einbetten"**: genau
ein Buch markiert → Dateidialog für die `xray.json` (letztes Verzeichnis merken)
→ einbetten → Format ersetzen. Mehrfachauswahl wird freundlich abgelehnt.

Vier Prüfungen, jede aus einem konkreten Prüfbefund:

1. **Richtiges Buch?** `doc["book_fingerprint"]["text_hash"]` gegen
   `read_epub(epub).text_hash` vergleichen, bei Abweichung abbrechen. Ersetzt
   die gestrichene Titel-Prüfung auf dem Gerät, ist strenger als sie und kostet
   drei Zeilen — `read_epub()` läuft in der Validierung ohnehin. Ohne das wäre
   ein Griff in die falsche Datei im Dateidialog stumm: fremde Figuren, fremde
   Timeline, keine Warnung.
2. **Bleibt die Lesestatistik erhalten?** partialMD5 (KOReaders Formel: 12 × 1 KB
   an den Offsets `1024·4^i`) **vor und nach** dem Einbetten berechnen und bei
   Abweichung abbrechen. Der Anhang-Modus allein garantiert das *nicht*:
   überschreitet die Datei durch das Anhängen eine Sample-Grenze (1 KiB, 4 KiB,
   … 1 MiB, 4 MiB), kommt ein zusätzliches Sample dazu und der Hash ändert sich.
   Gemessen: 0,7-MB-Buch + 1-MB-X-Ray → Hash ändert sich, Statistik weg. Der
   bestehende Regressionstest in `tests/test_embed.py:182` trifft diesen Fall
   nicht (er bleibt zwischen zwei Grenzen). Die Prüfung ersetzt jedes Nachdenken
   über Zip-Layout: sie misst genau das, worauf KOReader tatsächlich schaut.
3. **Erneutes Einbetten.** `embed.py:151` wirft im Anhang-Modus, wenn schon ein
   `xray/xray.json` drinsteckt — und nach dem ersten `add_format(replace=True)`
   ist das unberührte Original weg. Ohne Gegenmaßnahme ist die erste Einbettung
   endgültig. Also: enthält die Bibliothekskopie bereits X-Ray-Daten, wird das
   Zip einmal ohne diesen Eintrag neu geschrieben und danach neu angehängt —
   abgesichert durch dieselbe partialMD5-Prüfung aus Punkt 2, die laut wird,
   falls das Neuschreiben doch die Kopfbytes verschiebt.
4. **Tag ergänzen, nicht ersetzen.** `db.set_field("tags", …)` ist ein Setter:
   gemessen wurden aus `('fantasy','epic','read-later')` nach dem Setzen genau
   `('X-Ray',)`. Also bestehende Tags lesen und anhängen:
   `db.set_field("tags", {id: tuple(db.field_for("tags", id)) + ("X-Ray",)})`.

Dazu die bisherige Validierung vor `add_format(..., replace=True)`
(Zip-Integrität, Byte-Roundtrip des Dokuments, `read_epub()` parst noch). Kein
`ThreadedJob` (Anhängen dauert Millisekunden), keine Konfigurationsseite, kein
API-Key, keine Resume-Dialoge.

**Fertig, wenn:** ein eingebettetes, per WLAN gesendetes Buch auf dem Gerät
`xray/xray.json` enthält **und** KOReader den Lesefortschritt behält.

## Phase 4 — KOReader-Neubau

Neues, kleines Plugin. Aus dem alten Code wird **kein** Modul übernommen; was
bleibt, sind belegte Muster (unten jeweils mit Fundstelle im Altcode).

### `xray_doc.lua` (~150 Zeilen) — Datenzugriff

- **Vorab-Gate ohne Shell:** `_zipHasEntry` per reinem Lua-Parsing der
  Zip-Central-Directory (`xray_import.lua:423-483`) prüfen, *bevor* überhaupt
  ein Prozess startet. Ohne das löst jedes Buchöffnen ohne X-Ray-Daten unnötig
  `mkdir`/`unzip`/`rm -rf` aus.
- **Entpacken:** `mkdir -p` vor `unzip -d` (BusyBox legt das Zielverzeichnis
  nicht an), Ziel ist das `.sdr`-Verzeichnis statt `/tmp` (auf manchen Firmwares
  nicht schreibbar), Shell-Quoting per manuellem Single-Quote-Escaping statt
  Luas `%q` — alles wie `xray_import.lua:379-389,488-529`, inklusive Aufräumen
  auf Erfolgs- und Fehlerpfad.
- **JSON:** `pcall(require, "json")` + `pcall(json.decode, …)`, exakt wie
  `xray_import.lua:499-500`. **Nicht `rapidjson`** — das taucht im Altcode nur
  als optionaler Zweig im gelöschten `xray_aihelper.lua` auf; ein ungeprüftes
  `require` würde auf einem Gerät ohne das Modul hart abstürzen.
- **Schema-Gate:** eine Zeile gegen `schema_version` (Vorbild:
  `xray_import.lua:47-51`), sonst trifft ein neuer Plugin-Stand alte
  eingebettete Daten ohne Vorwarnung.
- **Alles in `pcall`:** „Failing to import must never cost the reader the book"
  (`main.lua:406-411`).
- Liegt `<buch>.epub.xray.json` daneben, gewinnt sie.

### Lesepunkt → Checkpoint (die eine Stelle, an der der Spoilerschutz hängt)

Verglichen wird **nicht** der Seiten-Prozentwert, sondern die Position auf der
Textachse. **Auf dem Gerät gemessen (2026-07-25, Kobo, 762-Seiten-Roman):**
`getFullHeight()` existiert nicht — weder auf `ui.rolling` noch auf
`ui.document`. Vorhanden und tragfähig ist stattdessen:

```lua
pos   = ui.document:getPosFromXPointer(ui.document:getXPointer())
total = ui.document:getPosFromXPointer(ui.document:getPageXPointer(page_count))
```

`ui.document.info.doc_height` (1040489) gibt es ebenfalls und liegt 0,07 % über
`total` (1039761) — die Seitenvariante ist konsistenter und wird benutzt.
`ui.rolling.current_pos` war 0 und ist unbrauchbar; `ui.document:getCurrentPos()`
liefert denselben Wert wie der XPointer-Weg.

Begründung, gemessen: `full_text` entsteht ausschließlich aus Spine-Text
(`epub.py:51,252-261`), ein Bild-Spine-Item liefert null Zeichen, belegt auf dem
Gerät aber mindestens eine Seite. Bei einem echten Buch aus dem Repo (131
Spine-Items, davon 53 unter 200 Zeichen) läuft Seiten-Prozent gegenüber
Zeichen-Prozent je nach Schriftgröße um bis zu **2,7 Punkte** vor — die Marge
von 2 Punkten ist bei kleiner Schrift also bereits aufgebraucht, und zwar am
stärksten bei den *frühesten* Checkpoints. Die Abweichung kippt am Buchende
sogar ins Negative, ist also nicht monoton; eine feste Marge im Seitenraum kann
sie prinzipiell nicht abdecken. Genau davor warnt der Altcode:
`xray_import.lua:184-193` markiert die Prozent-Stufe ausdrücklich als „last
resort", weil „calibre's percent is a CHARACTER percent". Die Textachse hat
dieses Problem nicht — sie skaliert mit Textmenge statt mit Seitenquantisierung
und ist zusätzlich gegen Schriftgrößenwechsel stabil.

Auswahlregel: größter Checkpoint mit
`math.min(cp.percent + MARGIN, 100) <= aktuelle_position`. Das Klemmen auf 100
ist nicht kosmetisch: `checkpoints.py:119` setzt den letzten Checkpoint hart auf
100, ohne Klemmung wäre er nie erreichbar und die vollständigen Daten blieben
für immer unsichtbar. `MARGIN = 2` als benannte Konstante an genau einer Stelle
— sie ist der Kalibrierknopf, wenn die Messung auf dem Gerät etwas anderes sagt.

Vor dem ersten Checkpoint wird **nichts** angezeigt, sondern eine Zeile
„X-Ray-Daten ab N % verfügbar". Das weicht bewusst von der alten, toleranten
Regel ab (`xray_prefetch.lua:496-518` zeigte den kleinsten Snapshot) — dieser
enthält Daten bis 10–15 %, die der Leser bei 3 % noch nicht gelesen hat.

**Fallback, falls die Messung auf dem Gerät die Marge sprengt:** `chapter_anchor`
(TOC-Stufe, `xray_import.lua:93-116`) wieder ins Schema aufnehmen — sie ist
exakt und braucht gar keine Marge. Erst dann, nicht vorsorglich.

### `xray_ui.lua` (~250 Zeilen) — Listen und Detailkarten

Sortierung wie festgelegt: Charaktere/Orte chronologisch (`first_seq`), Begriffe
alphabetisch, historische Figuren nach Rollen-Gewicht. Leere Felder werden nicht
gerendert.

**Die Timeline braucht ein eigenes Gate.** Sie ist kein Teil eines Snapshots,
sondern eine flache Ganzbuch-Liste auf Dokumentebene (`generate.py:447`,
`merge.py:504-507`). Wer sie naiv gegen `doc.timeline` rendert, liefert bei 5 %
Lesefortschritt das Ende des Buchs als Ereignisliste. Also filtern gegen den
**gewählten** Checkpoint (`ev.pct <= gewählt.percent`), nicht gegen die aktuelle
Position — dann erbt sie die Marge automatisch und bleibt mit den Listen
konsistent.

Auch die Statuszeile zählt aus dem aktiven Snapshot, nicht aus dem Dokument;
sonst verrät sie bei 10 %, dass das Buch 87 Charaktere hat. Ist
`complete ~= true`, nennt sie zusätzlich `last_percent`.

### `main.lua` (~200 Zeilen) — Lebenszyklus und Menü

Reader-Menü → X-Ray klappt direkt auf: Charaktere, Orte, Begriffe, Historische
Figuren, Timeline. Darunter ein Untermenü „Mehr": Status, „Nach Update suchen",
„Wörterbuch-Integration" (die eine Einstellung), einmalig „Alte X-Ray-Daten
entfernen". Verschachtelung und direkte Listenöffnung sind durch den Altcode
gedeckt (`main.lua:983-1173`, `xray_ui.lua:794ff`). Die Einstellung liegt wie
bisher in `DataStorage:getSettingsDir()/xray/settings.json` — außerhalb des
Plugin-Verzeichnisses und damit vom Updater unberührt.

### `xray_lookup.lua` (~180 Zeilen) — Wörterbuch-/Auswahl-Popup

Einhängung an zwei Stellen: neue Dict-Button-API (`main.lua:208-219`) und
Highlight-Dialog (`main.lua:170-220`). Der Legacy-Hook `onDictButtonsReady`
entfällt. Treffer per Exakt- und Alias-Vergleich **im aktuellen Snapshot** (nie
im vollen Dokument — das wäre ein Spoilerleck durch die Hintertür), bei
Mehrfachtreffern eine einfache Auswahlliste. Das mehrstufige Contains-Scoring
des alten `xray_lookupmanager.lua` (261 Zeilen) wird bewusst nicht portiert —
mit dieser Kürzung ist die Schätzung realistisch, mit der ursprünglichen von
80 Zeilen war sie es nicht.

### `xray_updater.lua` (~250 Zeilen)

Aus dem Alten portiert: GitHub-Releases-API, Zip-Plausibilitätsprüfung,
BusyBox-taugliches `unzip`, stiller wöchentlicher Check. Weg fallen Beta-Kanal
und die komplette Key-Backup/Re-Injection-Logik — sie hängt ausschließlich an
den sechs API-Key-Feldern aus `xray_config.lua`, es stirbt nichts anderes mit
(geprüft). Regel für Tags: rein dreiteilig-numerisch, keine Suffixe —
`_versionLessThan` zieht alle Zifferngruppen und würde `26.7.25-hotfix2` für
neuer halten als `26.7.25`.

### Aufräumer (Wegwerf-Code, mit Verfallsdatum im Kommentar)

Listet gefundene Altlasten (`<buch>.sdr/xray_cache.lua`, `xray_snapshot_*.lua`,
Serien-Cache im Settings-Verzeichnis, `xray_config.lua`), fragt **einmal** nach,
löscht dann.

### Lokalisierung — `xray_i18n.lua` (~40 Zeilen)

Auf dem Gerät gemessen und **widerlegt**: KOReaders natives `_()` findet keinen
`.po`-Katalog im Plugin-Verzeichnis, und `gettext:changeLang(<code>)` scheitert
dort für jeden Sprachcode (siehe R6). Es gibt keinen Nachladeweg, also auch
keinen nativen Weg zu plugin-eigenen Strings.

Entscheidung (2026-07-25): ein eigener, winziger `.po`-Leser. Beim Start die
Datei zur eingestellten Sprache (`G_reader_settings:readSetting("language")`,
Rückfall `gettext.current_lang`) aus `languages/<code>.po` lesen, `msgid` →
`msgstr` in eine Tabelle, Rückfall auf den englischen Originalstring. Strings
stehen englisch im Code, `de.po` wird gepflegt. Das ersetzt
`localization_xray.lua` (580 Zeilen) durch rund 40 — kein Plural-Handling,
keine Kontexte, kein RTL-Patch (der entfällt mit der Sprachwahl ohnehin).

**Gelöscht:** `xray_aihelper`, `xray_fetch`, `xray_prefetch`,
`xray_chapteranalyzer`, `xray_seriesmanager`, `xray_mentions`,
`xray_lookupmanager`, `xray_cachemanager`, `xray_data`, `xray_import`,
`xray_config`, `localization_xray`, `prompts/`.

**Fertig, wenn:** eine busted-Spec die Snapshot-Auswahl abdeckt (kein Checkpoint
erreicht → Platzhalter; genau auf der Grenze → noch nicht; Grenze + Marge → ja;
letzter Checkpoint bei 100 % → erreichbar; Timeline gegen den gewählten
Checkpoint gefiltert) und ein reales Buch auf dem Gerät die erwarteten Listen
zeigt.

## Phase 5 — Release und E2E

1. Version auf `26.7.25`, README beider Hälften neu (das alte README verspricht
   fünf AI-Provider, Serien und Mentions — alles weg).
2. Release mit Asset `xray.koplugin.zip` — Name unverändert, sonst findet der
   Updater nichts.
3. E2E auf dem Kobo: Buch in calibre → einbetten → WLAN-Versand → öffnen →
   Listen erscheinen, wachsen beim Weiterlesen, nichts jenseits des Lesepunkts.
4. Am selben Lauf prüfen, dass der Lesefortschritt erhalten bleibt.

## Risiken

**R1 — calibres Metadaten-Umschreiben beim Senden: geprüft, hält.** Der
angehängte, nicht im Manifest stehende Eintrag überlebt calibres
`set_metadata`-Rewrite beim Versand (vier Läufe in calibres eigenem Python; das
Zip wird neu gepackt, aber **jeder** Member wird mitkopiert). Zwei Korrekturen
an bisherigen Annahmen: der oft empfohlene Schalter „Metadaten beim Senden
aktualisieren" gilt für die *Geräte-Datenbank*, nicht für das Schreiben in die
Buchdatei — `DeviceManager._upload_books` ruft `set_metadata` ohne jede
Konfigurationsabfrage. Und calibres Rewrite verändert die Kopfbytes ab Offset 6
(Zip-Flag) gegenüber einer nie von calibre versendeten Datei; wer ein Buch
einmal per USB direkt aus dem Bibliotheksordner kopiert und später per WLAN
nachschickt, verliert die Statistik unabhängig von X-Ray.

**R2 — Positionsmessung: teilweise entwarnt.** Gemessen über 21 Stichproben
eines 762-Seiten-Romans weicht der Seiten-Prozentwert von der Textachse um
höchstens **+0,39 / −0,67 Prozentpunkte** ab — die 2-Punkte-Marge deckt das
mühelos. Die Messung lief ohne jede Positionsänderung (rein rechnerisch über
`getPosFromXPointer`), ist also beliebig wiederholbar.

**Weiterhin ungemessen ist die Hälfte, auf die es ankommt:** verglichen wurden
zwei *Geräte*-Maße (Seiten vs. gerenderte Pixel). Der Checkpoint-Prozentwert
kommt aber vom Desktop und ist ein *Zeichen*-Anteil an `full_text`. Bilder
belegen Pixel ohne Zeichen, verschieben die Pixelachse also gegenüber der
Zeichenachse — beim Testbuch offenbar kaum, bei einem bebilderten Sachbuch
womöglich deutlich. Das entscheidet sich im E2E-Lauf (Phase 5); wenn es dort
driftet, ist der billige Kalibriertest: auf dem Gerät `getPageText(seite)` für
ein paar Seiten holen, denselben Text am Desktop in `full_text` suchen und die
beiden Prozentwerte gegenüberstellen.

**R3 — Speicher: entwarnt.** Auf dem Gerät gemessen: ein 0,89-MB-JSON parst in
**0,042 s** und kostet **+689 KB** Lua-Heap. `rapidjson` ist vorhanden (schneller
als das reine `json`-Modul) und wird bevorzugt, `json` bleibt Rückfall. Die
Aufspaltung in eine Datei pro Checkpoint ist damit nicht nötig.

**R6 — Plugin-eigene Übersetzungskataloge funktionieren nicht.** Gemessen:
KOReaders `_()` findet keinen `.po`-Katalog im Plugin-Verzeichnis, und
`gettext:changeLang(<code>)` scheitert auf diesem Build für *jeden* Code
(`frontend/gettext.lua:169: attempt to call method 'match' (a nil value)`) —
auch für die bereits eingestellte Sprache. Das Modul bietet `dirname`,
`textdomain`, `translation`, `context` als Felder, aber keinen benutzbaren
Nachladeweg. Konsequenz: die gettext-Entscheidung aus dem Interview ist nicht
umsetzbar, siehe „Offene Entscheidung" unten. Nebenwirkung des Tests: der
geladene Katalog blieb entladen, bis KOReader neu startete.

**R4 — Updater-Versionsvergleich.** CalVer bleibt genau deshalb. Die Regel
lautet präzise: die erste Zahl darf nie kleiner werden. Ein Wechsel auf SemVer
`1.0.0` ergäbe `{1,0,0} < {26,7,18}` und kappt die Update-Kette bestehender
Installationen dauerhaft.

**R5 — Klartext-Key.** In der Arbeitskopie steht in
`xray.koplugin/xray_config.lua` ein echter Gemini-Key. Entwarnung im Detail: die
Datei trägt bereits das `skip-worktree`-Bit, `git add -A` erfasst sie also nicht.
Ein expliziter `git add <datei>` würde ihn trotzdem stagen — lokal leeren, bevor
irgendetwas anderes passiert.

## Bewusst offen gelassen

**Beschreibungen können beim Weiterlesen ärmer werden.** Ohne Phase C gewinnt
bei `description`/`biography` der neueste nicht-leere Wert (`merge.py:463-465`).
Erwähnt ein spätes Segment eine Figur nur beiläufig, ersetzt der dünne Satz die
dichte frühere Beschreibung. Spoilerseitig sauber (der Wert stammt immer aus
Text bis zu diesem Checkpoint), aber eine Qualitätsdelle. Einzeiler-Fix, falls
es im Betrieb stört: den *längeren* der beiden Werte behalten statt „neuester
gewinnt". Nebenwirkung ohne Phase C: `detail_level: "detailed"` bedeutet nur
noch längere Zeichen-Caps pro Segment.

## Reihenfolge

Phase 1 → 2 → 3 → 4 → 5. Phase 3 und 4 sind unabhängig voneinander und könnten
parallel laufen. Die beiden Messungen, die früh Klarheit schaffen: die
partialMD5-Grenze am echten Buchpaar (Einzeiler, kein Gerät nötig) und die
Positionsmessung auf dem Kobo (entscheidet über die Marge und damit über den
TOC-Anker-Fallback).
