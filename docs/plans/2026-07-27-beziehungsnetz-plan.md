# Beziehungs-Ego-Netz — Umsetzungsplan

Fassung 2 (2026-07-27, nach adversarialer Prüfung durch drei Reviewer).
Grundlage: `docs/2026-07-26-recap-und-beziehungsnetz-design.md`, Teil B in
Fassung 3. Basis: Plugin-Stand 26.7.29 auf `feat/recap`, Schema v2 (**kein
Bump**).

Vorbild ist Feature A: derselbe Nachlauf-Aufbau (`tools/claude_xray_recap.py`,
`plan`/`fold`), dieselbe Feldpräsenz-Gate-Regel, dieselbe Abnahmedisziplin.

Fassung 1 dieses Plans enthielt sieben belegte Fehler — vier Aufrufer statt
zwei, ein gelöschtes Werkzeug, eine Filterkette in falscher Reihenfolge, einen
Require-Zyklus, fehlende Aliase bei historischen Figuren, eine nicht ladbare
Testnaht und einen um Faktor 2–3 zu niedrigen Aufwand. Alle sind unten
eingearbeitet; die Belege stehen jeweils an Ort und Stelle.

## Zwei Phasen

**Phase 1 (dieser Plan, T1–T8): das Feature vollständig, Darstellung als
Liste.** Daten, Filterkette, Spoilerfilter, Einstieg und Tap-Navigation sind
fertig und am Gerät abnehmbar; die Nachbarn erscheinen in einem `Menu`, wie es
die bestehenden Kategorielisten schon tun.

**Phase 2 (später, eigener Plan): dasselbe als gezeichnetes Netz.** Nur T5 wird
ausgetauscht — alles davor und danach bleibt unverändert. Begründung und
Umfang stehen unten unter „Phase 2".

Der Zuschnitt folgt der Aufwandsmessung: die gezeichnete Variante ist mit
250–350 Zeilen die einzige unerprobte Position (`xray.koplugin/` enthält bisher
kein einziges `paintTo`), während die übrigen sieben Tasks nur vorhandene
Muster fortschreiben. Sie wird gebaut, wenn es echte Kanten zum Draufschauen
gibt, statt auf dem Papier.

## Modulschichtung — vorab, weil sie Phase 2 bindet

Der Require-Graph des Plugins ist streng geschichtet und muss es bleiben:

```
main        → xray_doc, xray_lookup, xray_ui, xray_updater
xray_lookup → xray_doc, xray_ui
xray_ui     → xray_doc
xray_doc    → (nichts)
```

Daraus folgt:

- **Alles, was `xray_doc` braucht, muss ohne Require auskommen.** Deshalb T4(a).
- Phase 1 legt **kein** neues Modul an — die Liste lebt in `xray_ui.lua`, das
  alles Nötige schon requirt.
- Für Phase 2: `xray_graph` gehört **zwischen** `xray_doc` und `xray_ui`. Ein
  `require("xray_ui")` darin wäre ein Zyklus — nachgemessen mit zwei sich
  gegenseitig requirenden Modulen unter LuaJIT: `loop or previous error loading
  module`. Er schlüge beim Plugin-Laden zu, nicht lazy, weil `main.lua:24`
  `xray_lookup` (das `xray_ui` zieht) vor `xray_ui` lädt.

---

## T1 — Schema: Formprüfung für `relations`

**Datei:** `xray_core/schema.py`, dazu `schema/xray.schema.json` (laut CLAUDE.md
„zwei Kopien desselben Vertrags", von Hand synchron).

`validate()` (`:58`) bekommt eine Prüfung für `doc["relations"]`: fehlt das Feld,
passiert nichts; ist es vorhanden, muss es eine Liste von Objekten mit den
nicht-leeren Strings `from`, `to`, `label` sein. Jeder Verstoß ist **ein**
`problems`-Eintrag mit Index im Label.

`_validate_chronology_entry` wird **nicht** wiederverwendet — es verlangt `name`
und `first_seq`, die eine Kante nicht hat.

Kein Cross-Field-Check: dass beide Endpunkte auflösen, sichert der `fold` (T3).
Begründung siehe Design, „D4 gilt konstruktiv".

**Prüfung:** drei Negativfälle (`relations: "x"`, Eintrag ohne `to`, `label` als
Zahl) erzeugen je genau ein `problems`-Element — **und ein Dokument mit
wohlgeformten `relations` erzeugt keins.** Ohne den Positivfall besteht eine
Regel, die alles ablehnt: nachgemessen, ein `reject_everything`-Mutant lief
10/10 grün gegen die Abnahmeliste ohne diesen Fall.

## T2 — Prompt

**Datei:** `xray_core/prompts.py`

`RELATIONS_EN` / `RELATIONS_DE` mit den Tags `{CHARACTERS}` und `{HISTORICAL}`.
Inhaltlich festzulegen:

- zulässige Beziehungsarten: Verwandtschaft, Herrschaft/Dienst, Bündnis,
  Feindschaft, Liebe/Ehe, Mentorschaft — und nichts sonst;
- höchstens `MAX_RELATIONS_PER_FIGURE` je Figur;
- **jede Beziehung zweimal**, einmal je Richtung, mit dem jeweils passenden
  Label; `label` benennt die Rolle, die `to` für `from` hat;
- ausschließlich Namen aus der gelieferten Liste, keine erfundenen;
- Ausgabe als JSON-Objekt `{"relations": [...]}`.

`build_relations_prompt(language, title, author, characters, historical)`
benutzt **ausschließlich `.replace()`**, keine `%`-Formatierung und
insbesondere **nicht `_apply_percent_args`**. Das ist kein Stilpunkt: der
Helfer verlangt ein `percent`, das ein buchweiter Prompt nicht hat, und
verteilt es auf *jeden* Specifier ab dem dritten (`prompts.py:279-282`) —
nachgemessen wird ein als `%d` geschriebenes `MAX_RELATIONS_PER_FIGURE`
dadurch still zu `0`. Auch `build_prompt` scheidet aus: es stellt unbedingt
`_SEGMENT_PREFIX + segment_text` voran.

`{HISTORICAL}` liefert **nur Namen und Beschreibungen, keine Aliase** — siehe
T3.

## T3 — Nachlauf-Tool

**Datei:** `tools/claude_xray_relations.py` (neu)

```
plan  --doc xray.json --workdir DIR  BOOK.epub
fold  --doc xray.json --workdir DIR --out DIR
```

`plan` schreibt `relations.prompt.txt` und ein `relations_manifest.json` mit
`text_hash` und `companion_name`. `fold` liest die Antwort, wendet die
Filterkette an, validiert und schreibt **beide** Dateinamen (`xray.json` und
`<book>.epub.xray.json`). `_refuse_on_drift` aus dem Recap-Tool sinngemäß
übernehmen.

**Filterkette, in genau dieser Reihenfolge** — die Normalisierung steht
vorn, nicht hinten:

1. Formfehler (fehlendes Feld, leerer String) → verwerfen.
2. **Namen auf die kanonische Schreibweise der letzten Stage normalisieren.**
3. Selbstkante (`from == to`) → verwerfen.
4. Duplikat `(from, to)` → erste gewinnt.
5. Endpunkt löst in der letzten Stage nicht auf → verwerfen (Halluzinationsfilter).
6. Mehr als `MAX_RELATIONS_PER_FIGURE` je `from` → überzählige verwerfen,
   stabil nach Eingabereihenfolge.

Fassung 1 normalisierte zuletzt. Nachgemessen mit einem Prompt, der dem Modell
legitim Namen **und** Aliase anbietet: `{"from":"Ned",…}` und
`{"from":"Eddard Stark",…}` überleben Schritt 3–5 als verschiedene Kanten und
werden erst danach auf denselben Namen umgeschrieben — das ausgelieferte
Dokument trug zwei identische Eddard→Robb-Kanten mit widersprüchlichen Labels
plus eine Selbstkante `Ned → Eddard Stark`, `validate()` meldete `[]`, und alle
zehn Abnahmechecks blieben grün. Dieselbe Lücke ließ eine Figur `2 ×
MAX_RELATIONS_PER_FIGURE` Kanten unter zwei Schreibweisen tragen.

**Auflösung über Name und Aliase, für beide Figurenkategorien.**

*Korrigiert am 2026-07-28.* Umgesetzt war es zunächst name-only für
`historical_figures`, begründet damit, dass `clean_response` diese Kategorie
ohne `aliases`-Schlüssel aufbaut (`merge.py:391-401` gegen `:374`) — das
stimmt für `clean_response`, aber der Snapshot ist **nach** dem Merge, und
`_add_alias` (`merge.py:603`) legt den Schlüssel dort an. Nachgemessen:
„Yssa the Elder" gemerged mit „Queen Yssa the Elder" speichert
`aliases: ['Queen Yssa the Elder']`. (Mit „König Aegon" greift es nicht, weil
der deutsche Titel nicht in der Honorific-Liste steht — daran ging der erste
Gegentest vorbei.) Die Einschränkung verwarf still jede Kante, die eine solche
Form benutzte.

**Unerwiderte Kanten werden gemeldet, nicht verworfen:** existiert `A→B` ohne
`B→A`, gibt der `fold` eine Warnung mit beiden Namen aus.

**Prüfung:** die pytest-Liste im Design, jeder Punkt mit Gegenprobe,
Nicht-Vakuität zuerst. Der Dedup-Check muss die **Alias-Schreibweise** treffen,
nicht zwei identische Strings — sonst prüft er die Lücke oben nicht.

## T4 — Gerät: Auflösung und Kantenzugriff

**Datei:** `xray.koplugin/xray_doc.lua`

Zwei Schritte, und der erste entfernt eine Duplikation, statt eine anzulegen:

**(a) Resolver nach unten ziehen.** `xray_lookup.lua` hat mit `normalize` /
`matchesEntry` / `XRayLookup.find(snapshot, word)` (`:69`) bereits genau den
alias-fähigen, kategorie-getaggten Resolver, den das Netz braucht — aber
`xray_doc` darf `xray_lookup` nicht requiren (Schichtung oben). Also wandert
die Auflösung nach `xray_doc.lua` als

```lua
XRayDoc.resolve(snapshot, name) -> { { entry = …, category = … }, … }
```

und `XRayLookup.find` ruft sie auf (`xray_lookup` requirt `xray_doc` bereits).
Ein Resolver statt zwei; das Verhalten von `find` bleibt unverändert, was seine
bestehenden Specs absichern.

**(b) Kantenzugriff:**

```lua
XRayDoc.egoNet(doc, idx, entry) -> { { entry = …, category = …, label = … }, … } | nil
```

`entry` hinein statt eines Namens, **Einträge** heraus statt Namen. Beides ist
belegt nötig: der Aufrufer hält den Eintrag ohnehin schon, ein Name kollidiert
mehrdeutig über Kategorien hinweg (der Fall, für den `showPicker` existiert),
und jeder Konsument braucht die Tabelle — das Widget für die Beschriftung, ein
Tap auf die Detailkarte für `role`/`description`/`aliases`.

Der Filter:

- Kanten, deren `from` auf `entry` auflöst (Name oder Alias);
- `to` muss über `XRayDoc.resolve` im Snapshot `idx` auf `characters` oder
  `historical_figures` treffen — sonst wird die Kante verworfen;
- **beschriftet wird mit dem `name` des aufgelösten Snapshot-Eintrags**, nicht
  mit dem `to` der Kante. Sonst trägt ein Knoten den kanonischen Namen der
  letzten Stage, während die Figurenliste an dieser Stage die frühere
  Schreibweise führt — genau der Alias-Fall, den die Abnahme verlangt;
- Rückgabe alphabetisch nach angezeigtem Namen, damit die Anzeige
  deterministisch und der Test stabil ist.

Kein Rücklauf zu früheren Stages: die Kantenliste ist dokumentweit, gefiltert
wird gegen den Snapshot `idx`.

**Testnaht:** `egoNet` und `resolve` gehören in `spec/xray_doc_spec.lua`, das
bereits existiert und in `tools/spec_runner.lua` (`:139-150`) registriert ist —
Phase 1 braucht also weder ein neues Spec noch einen Eintrag in der
hartkodierten Liste. (Für Phase 2 gilt beides wieder, plus ein
`package.loaded["ui/rendertext"]`-Stub in `spec/spec_helper.lua`: nachgemessen
fehlt genau dieses Modul, und ein `require` darauf auf Modulebene **tötet die
Suite** — im `describe`-Block schluckt `spec_runner.lua:32-35` den Fehler per
`pcall` und meldet `All tests passed`, Exit 0.)

## T5 — Gerät: Anzeige (Phase 1: Liste)

**Datei:** `xray.koplugin/xray_ui.lua` — kein neues Modul.

`XRayUI.showEgoNet(doc, cp_idx, entry)` zeigt ein `Menu` nach dem Muster von
`showList` (`:136`): Titel ist der Name der Zentrumsfigur, eine Zeile je
Nachbar mit dessen Namen, das Beziehungslabel als `subtext` — dieselbe Form,
die `buildRow` (`:99-119`) für die Kategorielisten schon benutzt.

- Tap auf einen Nachbarn öffnet **dessen** Ego-Netz. `UIManager` stapelt die
  Menüs, „zurück" ergibt sich daraus von selbst — der Historien-Stack aus dem
  Design ist erst in Phase 2 nötig.
- Historische Figuren werden in der Zeile gekennzeichnet, damit ablesbar
  bleibt, in welche Kategorie ein Tap führt.
- Keine Kappung. `MAX_RELATIONS_PER_FIGURE` = 5 aus T3 ist die einzige Grenze;
  ein `Menu` scrollt ohnehin.

Aufwand: ~25 Zeilen. Nur erprobte Widgets, keine neue Datei, kein `paintTo`.

## T6 — Gerät: Einstieg

**Datei:** `xray.koplugin/xray_ui.lua` **und** `xray.koplugin/xray_lookup.lua`

`XRayUI.showEntry` hat **vier** Aufrufer in **zwei** Dateien, nicht zwei in
einer:

| Aufrufer | Hat `doc`/`cp_idx`? |
|---|---|
| `xray_ui.lua:105` (`buildRow`, Timeline) | nein — von `showList` durchzureichen |
| `xray_ui.lua:112` (`buildRow`, Entität) | nein — von `showList` durchzureichen |
| `xray_lookup.lua:117` (`showPicker`) | **nein** — `showPicker` braucht dieselbe Signaturerweiterung |
| `xray_lookup.lua:162` (`performLookup`) | ja (`:143`, `:150`) |

Der Lookup-Weg ist der wichtigste von allen — ein Name im Text antippen ist der
natürliche Weg zu einer Figurenkarte. Wird er vergessen, fehlt der Knopf dort
**stillschweigend**, weil `showEntry`'s Rumpf in einem `pcall` liegt (`:237`,
`:263`).

Die Detailkarte erhält einen Knopf „Relations". Zwei Formdetails, beide
belegt an `frontend/ui/widget/textviewer.lua`:

- **`add_default_buttons = true` muss mitgesetzt werden.** Ohne das ersetzt
  `buttons_table` die Standardzeile, statt sie zu ergänzen (`:391-392`, Kommentar
  bei `:76-77`) — die **Schließen**-Taste verschwände von genau den Karten, die
  das Feature betrifft, und wäre auf den anderen weiter da.
- `buttons_table` ist eine Liste von **Zeilen** (`{{ {text=…, callback=…} }}`),
  wird an `ButtonTable{buttons=…}` durchgereicht (`:398-401`) und dabei per
  `table.insert` **in place** verändert — also frisch je `showEntry`-Aufruf
  bauen, nie als Modulkonstante.

**Ohne übrigbleibende Kanten erscheint der Knopf nicht.** Auf der Karte ist das
Dokument längst geladen, die Prüfung kostet nichts — anders als im datenfreien
`getSubMenuItems` von Feature A.

Für `historical_figures` gilt dasselbe: auch sie können Kanten haben.

## T7 — Übersetzung

**Nicht** `tools/sync_translations.py` aufrufen — die Datei existiert nicht
mehr, gelöscht in `839856f` („feat(device): rewrite the KOReader plugin as
display-only"), zusammen mit `translate_all.py`. Es gibt auch kein `en.po` und
keine „alle `.po`-Dateien": `xray.koplugin/languages/` enthält genau `de.po`,
und `xray_i18n.lua` schlägt über den **englischen Quelltext** als msgid nach.

Also: englische `_()`-Literale im Lua-Code, passende `msgid`/`msgstr`-Paare von
Hand in `de.po`, verifiziert mit
`python3 -m pytest tests/test_koplugin_catalog.py`. Der Test globbt
`xray.koplugin/*.lua`, greift also automatisch auch `xray_graph.lua`, und prüft
drei Dinge: fehlende Übersetzungen, **tote Einträge** und `%s`/`%d`-Parität.
Der Dead-Entry-Check heißt: keine Schlüssel auf Vorrat.

## T8 — Skill-Workflow

**Datei:** `.claude/skills/xray/SKILL.md`

Neuer Abschnitt „## 5. Relations (optional)" zwischen Recap (§4) und Report,
Report wird §6. Zwei verpflichtende Hinweise, beide analog zum Recap:

- Nach jedem erneuten Lauf von §3 (Assemble) muss der Relations-`fold`
  wiederholt werden — Assemble schreibt das Dokument neu und verwirft die
  Kanten stillschweigend.
- Die Entscheidung fällt **vor** der Übergabe in §6: nachträgliches Einbetten
  löst einen vollständigen EPUB-Neuschrieb aus und setzt die Lesestatistik
  zurück.

---

## Phase 2 — das gezeichnete Netz (eigener Plan, später)

Nur T5 wird ersetzt: `xray.koplugin/xray_graph.lua`, Zentrum mittig, Nachbarn
in zwei Spalten, Kanten als waagerechte `paintRect`-Striche, Knoten als
`paintRoundedRect`, Beschriftung über `RenderText:renderUtf8Text`, lange Namen
über `truncateTextByWidth`. Getrennt in eine reine `layout()`-Funktion (trägt
die Abnahme, liefert **alle** Geometrie inklusive Zentrum und Kantenbalken) und
das Widget mit eigenem `paintTo` und Trefferrechtecken. Historienstack für
„zurück". Kein `require("xray_ui")` — siehe Schichtung oben; die Detailkarte
erreicht es über ein Callback, das `xray_ui` hineinreicht.

Aufwand **250–350 Zeilen**, gezählt gegen: `HistogramWidget` 48 für reine
Balken ohne Text und Tap (`calendarview.lua:33-80`), `ProgressWidget:paintTo`
117 für *einen* Balken (`progresswidget.lua:112-228`), `CalendarDay` 94 für
**eine** antippbare beschriftete Box (`calendarview.lua:81-174`), `BookMapRow`
634 (`bookmapwidget.lua:38-671`). `grep -rn "paintTo\|InputContainer\|
GestureRange" xray.koplugin/` findet **nichts** — es gibt kein hauseigenes
Gerüst zum Abschreiben.

Dazu gehören die Layout-Abnahmefälle des Designs (`#nodes == n` **plus**
`w > 0 and h > 0` und keine doppelten Koordinaten), die Spec-Registrierung in
`tools/spec_runner.lua` und der `ui/rendertext`-Stub in `spec/spec_helper.lua`.
Beide im Design offen gelassenen Gerätemessungen — lange Namen, Trefferquote am
Rand — gehören ebenfalls hierher.

## Abnahme

Die Liste im Design („Woran sich zeigt, dass es funktioniert") ist bindend,
einschließlich jeder Gegenprobe — mit den vier Korrekturen, die die Prüfung
dieses Plans erzwungen hat und die dort eingearbeitet sind: der D4-Check muss
**gestaffelt** sein, der Layout-Check braucht eine **Geometrie**-Assertion, das
Schema braucht seinen **Positivfall**, und das Versions-Gate muss exportiert
werden oder entfallen. Die Layout-Fälle gehören zu Phase 2; alles andere ist in
Phase 1 zu erfüllen.

Der Grund für diese Strenge ist gemessen, nicht befürchtet: gegen die
Abnahmeliste in ihrer bisherigen Fassung überlebten drei Mutanten grün — ein
`egoNet`, das gegen den **letzten** statt den sichtbaren Snapshot auflöst (das
hieße: ein Leser bei 20 % sieht jeden Namen des Buchs, also der vollständige
Ausfall der einzigen D4-Instanz), ein `layout()`, das die richtige Anzahl
Rechtecke mit Größe null liefert (kein Tap träfe je), und eine Schema-Regel,
die alles ablehnt.

Vor dem Abschluß außerdem:

- volle Suiten grün: `python3 -m pytest tests/` (Baseline **220 passed**) und
  `luajit tools/spec_runner.lua` (Baseline **22/0 ohne `SQUASHFS_ROOT`**). Die
  in CLAUDE.md genannten „11 Fails ohne SQUASHFS_ROOT" sind Teil-A-Altbestand
  und für das zurückgebaute Plugin **stale** — gegen sie zu messen würde elf
  echte Fehlschläge verdecken.
- ein echter Lauf über „Die Gefährten": Kanten erzeugen, `fold`, Companion aufs
  Gerät, die Messposten aus dem Design abarbeiten.

## Ausdrücklich nicht in diesem Plan

Kanten aus dem Extraktions-Prompt, Nachlauf pro Stage, `first_pct` an Kanten,
radiales Layout, Kappung/Überlaufzeile, Gesamtübersicht, Zoom/Pan, Beziehungen
für Orte und Begriffe. Begründungen im Design unter „Verworfene Alternativen"
und „Was ersatzlos entfällt".

Das gezeichnete Netz ist **nicht verworfen, sondern Phase 2** — mit demselben
Zwei-Spalten-Entwurf, den das Design beschreibt.

Das `==`-Gate in `xray_doc.lua:166` bleibt unangetastet — es gehört auf einen
eigenen Zweig mit eigenem Release, **vor** den nächsten echten Schema-Bump.
