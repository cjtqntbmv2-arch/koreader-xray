# Recap und Beziehungs-Ego-Netz — Design

Stand: 2026-07-26, Fassung 2 (nach adversarialer Prüfung durch drei Reviewer).
Basis: Plugin-Stand 26.7.28 (zurückgebaut nach
`docs/plans/2026-07-25-xray-neuausrichtung.md`), Schema v2.

Zwei Anzeige-Features. Beide folgen der Doktrin des Rückbaus: **erzeugt wird am
Desktop, das Gerät liest und zeigt.** Kein neuer Provider, keine Erzeugung auf
dem Gerät, keine zusätzliche Einstellung.

Fassung 1 wollte das Schema auf v3 bumpen, `first_pct` an Kanten vom Modell
stempeln lassen und den Recap pro Checkpoint erzeugen. Alle drei Annahmen waren
falsch; die Belege stehen jeweils an Ort und Stelle.

## Gemeinsame Grundlagen

### Kein Schema-Bump

Beide Felder sind rein additiv, und `validate()` akzeptiert sie **heute schon**
gegen die unveränderte `xray_core/schema.py` — es gibt keine Unknown-Key-Prüfung
(nachgemessen: ein Dokument mit `recap` und `relations` liefert `[]`). Der Bump
wäre nicht nur überflüssig, sondern aktiv schädlich:

`xray.koplugin/xray_doc.lua:166` prüft `doc.schema_version == SUPPORTED_SCHEMA`
— **strikte Gleichheit**, nicht `<=`. Ein Bump auf 3 macht damit beide
Richtungen kaputt: neu erzeugte Dokumente werden auf jeder noch nicht
aktualisierten Installation abgelehnt, und das aktualisierte Plugin weist
jedes bereits eingebettete v2-Dokument ab — nicht als „Feature aus", sondern
mit hartem Fehler bis in die `InfoMessage` (`:188`, `:226`, `:254`,
`main.lua:79-82`).

**Konsequenz für dieses Design:** `schema_version` bleibt 2. Das Gerät gated
auf **Feldpräsenz**, nie auf die Version. Damit entfallen Auslieferungs­reihenfolge,
Fixture-Aktualisierung, Neubau des calibre-Plugins und die Frage nach
Bestandsdokumenten vollständig.

Das `==`-Gate bleibt trotzdem eine Bombe für den nächsten *echten* Bump (das
`<=` war früher da, siehe `docs/2026-07-11-companion-xray-import-plan.md:28`,
und ging beim Rückbau verloren). Siehe „Offene Entscheidung" am Ende.

### D4 gilt konstruktiv, nicht deklarativ

Spoilerschutz entsteht dadurch, dass ein Datum gar nicht erst in einen zu
frühen Snapshot gelangt — nicht dadurch, dass ein Validator ihn hinterher
beanstandet. `generate_xray` wirft bei jedem Validierungsproblem
(`generate.py:251-253`), und zwar **nachdem** das gesamte Extraktionsbudget
verbraucht ist. Ein Validator als einzige D4-Instanz macht aus einer einzelnen
schlechten Kante einen Totalverlust des Laufs.

Also überall: **im Merge filtern, Validator nur als Backstop** — genau das
Muster, das für `first_pct` an Charakteren bereits existiert.

---

## A — „Was bisher geschah" (Prosa-Recap)

### Daten

`recap` als Geschwister von `snapshot` in einem Checkpoint-Eintrag — die reale
Form ist `{"percent": …, "snapshot": {…}}` (`generate.py:227`), nicht flach:

```json
{ "percent": 40, "snapshot": { "characters": [ … ] }, "recap": "…Fließtext…" }
```

Damit ist `XRayDoc.recap(doc, idx)` tatsächlich symmetrisch zu
`XRayDoc.snapshot()` (`xray_doc.lua:368-372`).

### Nicht jede Stage bekommt einen Recap

`doc["checkpoints"]` enthält **nicht** die ~11 geplanten Checkpoints, sondern
einen eingefrorenen Snapshot pro *Chunk*-Prozent (`generate.py:216-227`). Am
Beispielbuch (`book example/Fire and Blood`): `plan_checkpoints` → 11, aber
`chunk_plan` → 68 Chunks → **57 Stages**. Ein Recap pro Stage wären 57
Modellaufrufe und ~20 000 Wörter zusätzlich in einer Datei, die das Gerät per
`unzip` liest.

**Regel:** Recaps nur an den Stages, deren Prozent einem geplanten Checkpoint
entspricht (~10–12 pro Buch). `XRayDoc.recap(doc, idx)` läuft von `idx`
abwärts zur nächstniedrigeren Stage mit nicht-leerem `recap`. Der Recap ändert
sich damit etwa alle 9 % statt alle 2 % — für einen Rückblick beim
Wiederaufnehmen völlig ausreichend.

### Gewichtung: Fernes ausführlicher

Der Recap zu Prozent P deckt 0…P ab, gewichtet **gegen** die Leserichtung:

| Band | Bereich | Wortanteil |
|---|---|---|
| fern | 0 … 0,5·P | ~55 % |
| mittel | 0,5·P … 0,85·P | ~30 % |
| nah | 0,85·P … P | ~15 % |

Unterhalb P = 20 % entfällt die Staffelung; dort folgt der Text schlicht der
Chronologie.

**Gesamtlänge folgt dem Material, nicht dem Lesefortschritt**: 8 Wörter je
Timeline-Ereignis, mindestens 150, höchstens 400 (`recap_target_words`). Eine
Stage ohne Ereignisse bekommt gar keinen Recap.

Die erste Fassung sagte „250–400 Wörter, konstant" — am echten Buch gemessen
war das falsch. Bei 16 % lagen 11 Ereignisse vor, und das Modell schrieb
trotzdem 399 Wörter: die fehlende Handlung füllte es mit der Gründung des
Auenlands 1601 und einer Geschichte des Pfeifenkrauts. Ein dicht erzähltes Buch
bei 20 % hat mehr zu berichten als ein langsames bei 40 %, und die
Ereigniszahl ist das, was davon weiß.

Das ist die als §10/K5 zurückgestellte Idee aus dem alten Design, hier erstmals
umgesetzt.

### Erzeugung: eigenes Tool

Ein Pass „nach dem Assemble" hat heute keinen Ort: `assemble()` schreibt das
Dokument **zweimal** mit identischen Bytes (`claude_xray_assemble.py:80-83`:
`xray.json` und `<book>.epub.xray.json`), und validiert wird *innerhalb* von
`generate_xray` vor dem Schreiben. Auf dem USB-Companion-Weg gibt es danach
keine zweite Prüfung mehr.

**Also: `tools/claude_xray_recap.py`** — liest `xray.json`, erzeugt die
Recap-Prompts, faltet die Antworten ein, prüft (siehe unten), validiert und
schreibt **beide** Dateinamen neu. Damit hat auch pytest einen Angriffspunkt;
die Extraktion selbst lebt in Prompt-Dateien plus Subagents (`SKILL.md` §2) und
ist von außen nicht testbar.

Input je Aufruf: der eingefrorene Snapshot dieser Stage plus die
Timeline-Events mit `pct <= percent`. Nicht der Buch-Rohtext, und unter keinen
Umständen der lebende Endzustand — der ursprüngliche Spoiler-Leak-Bug bestand
genau darin, aus dem lebenden `BookState` neu zu snapshotten.

### Der Spoiler-Check, den es geben kann

Ob ein Prosatext spoilert, kann kein Test semantisch entscheiden. Die
dominante, mechanisch fassbare Leak-Klasse ist aber **ein Eigenname, der erst
in einem späteren Snapshot existiert** — und die ist ohne Modell und ohne Netz
prüfbar:

> Für Stage i: alle `name`/`aliases` aus Snapshots > i, minus die aus Snapshot i.
> **Wortgrenzen-Match** im Recap-Text von i, case-sensitive, Namen unter 4
> Zeichen übersprungen. Treffer → Recap für diese Stage **verwerfen**
> (Schlüssel weglassen, nie `""` schreiben) und warnen.

Wortgrenzen, nicht Substring: eine reine Substring-Suche verwirft einen
einwandfreien Recap, sobald ein späterer Name als Präfix in einem anderen Wort
steckt („Robb" in „Robbers plundered three villages") — nachgemessen. Ein
Falschpositiv kostet den Recap dieser Stage und sieht dabei aus wie ein echter
Leak.

Verwerfen statt Abbrechen, weil der Recap-Lauf sonst nach verbrauchtem Budget
stirbt. Der Check läuft in `claude_xray_recap.py`, **nicht** nur in pytest —
sonst prüft Fixture-Prosa nur den Checker und nie ein echtes Buch.

Ein prototypischer Scan über die vorhandenen Daten fand einen eingebauten Leak
sofort (`checkpoint 15%: recap names 'Emeric Thale'`).

### Gerät

- `XRayDoc.recap(doc, idx)` mit Rücklauf zur nächstniedrigeren Stage mit Recap.
- Menüeintrag „Story so far" neben den Kategorien, öffnet einen `TextViewer`.
  Zusätzlich im Gesten-Dialog (`onXRayShow`) — das ist der Schnellzugriff, und
  eine Lesehilfe gehört nicht nur ins vergrabene Tools-Menü.
- **Der Eintrag ist immer sichtbar**, auch ohne Recaps im Dokument; dann zeigt
  das Antippen einen eigenen Hinweistext. Ausblenden würde `XRayDoc.load` in
  den Menüaufbau ziehen, und der ungecachte Ladepfad ist `mkdir -p` + `unzip`
  über die EPUB (`xray_doc.lua:193-227`), im BusyBox-Fallback über das ganze
  Archiv — `getSubMenuItems` ist heute vollständig datenfrei und soll es
  bleiben.
- Kein Auto-Popup, kein Lesedatum, keine Einstellung.
- **Leerer String ist in Lua wahr.** `cp.recap or nil` liefert bei `""` einen
  sichtbaren Menüeintrag und einen leeren `TextViewer`. Die Prüfung muss
  `type(s) == "string" and s ~= ""` sein — teilweise Abdeckung (Recap für die
  ersten Stages, keiner für die späteren) ist der *Normalfall* eines
  abgebrochenen Laufs, nicht der Ausnahmefall.

---

## B — Beziehungs-Ego-Netz

Fassung 3 (2026-07-27, nach dem Interview zu Feature B). Fassung 2 setzte
stillschweigend voraus, dass Kanten aus den Buchtext-Chunks fallen, und leitete
daraus drei Merge-Umbauten, eine `first_pct`-Stempelung und einen selbst
geschriebenen Bresenham ab. Der Nachlauf-Weg, der sich bei Feature A bewährt
hat, macht alle fünf gegenstandslos. Was davon warum entfällt, steht unten unter
„Was ersatzlos entfällt" — die Belege der alten Fassung waren richtig, nur die
Voraussetzung war es nicht.

### Erzeugung: ein Aufruf pro Buch, im Nachlauf

`tools/claude_xray_relations.py`, gebaut wie `claude_xray_recap.py`: liest das
fertige `xray.json`, schreibt eine Prompt-Datei, faltet die Antwort ein,
validiert, schreibt **beide** Dateinamen neu.

Material für den einen Aufruf: die `characters`- und `historical_figures`-Liste
der **letzten** Stage, mit Namen, Aliasen und Beschreibungen. Nicht der
Buch-Rohtext. Ein Aufruf pro Buch statt einer pro Stage, weil D4 hier nicht aus
dem Material entsteht, sondern am Gerät (siehe unten) — und ein Nachlauf über
alle 43 Stages wäre der vierfache Aufwand des Recaps für dieselbe Anzeige.

Weil der Nachlauf nur das fertige Dokument braucht, lassen sich **bestehende
Bücher nachrüsten**, ohne einen einzigen Buchtext-Chunk erneut zu holen.

### Daten

Flache Liste auf Dokumentebene, Geschwister von `checkpoints` — nicht am
Charakter, weil die Kanten den Merge nie berühren:

```json
{ "schema_version": 2,
  "checkpoints": [ … ],
  "relations": [
    { "from": "Robb Stark",    "to": "Eddard Stark", "label": "Vater" },
    { "from": "Eddard Stark",  "to": "Robb Stark",   "label": "Sohn" }
  ] }
```

`label` benennt die Rolle, die `to` für `from` hat — in Robbs Netz steht neben
Eddard „Vater". Jede Beziehung wird deshalb **zweimal** geliefert, einmal je
Richtung. Das Gerät filtert dann nur auf `from` und braucht keinerlei
Umkehrlogik; ein `label_rev` und die Frage, was ein fehlendes `label_rev`
anrichtet, entfallen. Kein `first_pct` (siehe D4 unten).

Vergisst das Modell eine Gegenrichtung, **fehlt** die Kante in einem der beiden
Netze, statt falsch beschriftet zu erscheinen. Der `fold` meldet jede
unerwiderte Kante — stillschweigend durchlassen darf er sie nicht, sonst ist
das Netz asymmetrisch und niemand erfährt es.

**Enge statt Vollständigkeit.** Der Prompt gibt die zulässigen Arten vor —
Verwandtschaft, Herrschaft/Dienst, Bündnis, Feindschaft, Liebe/Ehe,
Mentorschaft — und höchstens fünf je Figur. Flüchtige Begegnungen („trifft",
„kennt") fallen raus. Auf 6 Zoll ist Sparsamkeit die Qualität: damit passen die
Nachbarn praktisch immer auf eine Bildschirmseite, und die Kappung wird zur
Absicherung statt zum Regelfall. Labels folgen `language` wie aller andere
erzeugte Text.

### D4 entsteht am Gerät, konstruktiv

Der eine Nachlauf sieht das gesamte Figurenpersonal. Die Spoilerfreiheit trägt
deshalb **allein der Anzeigefilter**, und der ist eine einzige Regel:

> Eine Kante wird gezeigt, wenn **beide** Endpunkte in `characters` **oder**
> `historical_figures` des gerade sichtbaren Snapshots auflösen (Name oder
> Alias, case-insensitiv).

Damit kann keine Kante einen Namen zeigen, den der Leser an dieser Stelle nicht
ohnehin in der Liste findet — der Fall „`first_pct` ist korrekt, aber der Name
selbst ist der Spoiler" aus Fassung 2 kann gar nicht erst entstehen. Es gibt
kein Feld, das ein Modell raten müsste, und keinen Snapshot-Filter am Desktop.
Historische Figuren sind gültige Ziele, weil sie aus **demselben** Snapshot
stammen, also derselben Spoilergrenze unterliegen; im Netz werden sie optisch
abgesetzt, damit ablesbar bleibt, dass ein Tap dort in eine andere Kategorie
führt.

Der `fold` verwirft zusätzlich schon am Desktop, was in der letzten Stage nicht
auflöst — das fängt Halluzinationen ab, bevor sie ausgeliefert werden.

**Bekannte Grenze: früh eingefrorene Umbenennungen.** `_pick_canonical`
(`merge.py:599-603`) benennt einen Knoten um und schiebt die *alte* Form in
`aliases`; `snapshot()` friert per `deepcopy` ein (`merge.py:670`). Heißt eine
Figur in Stage 5 noch „Ser Jaime Lennister" und erst ab Stage 20 „Jaime
Lennister", steht die spätere Form im Stage-5-Snapshot weder als Name noch als
Alias. Eine Kante auf den kanonischen Namen löst dort also nicht auf und
**fehlt**, bis der Leser die umbenennende Stage erreicht. Das ist die
D4-sichere Richtung des Fehlers (zu wenig, nie zu viel), betrifft nur
umbenannte Figuren und wird bewusst nicht behandelt — jede Gegenmaßnahme
verlangte Alias-Historie im Dokument oder Auflösungslogik am Gerät, beides
teurer als der Schaden.

### Darstellung

**In zwei Phasen ausgeliefert** (Beschluss 2026-07-27, nach der Aufwandsmessung
unten): Phase 1 zeigt die Nachbarn als `Menu` nach dem Muster der bestehenden
Kategorielisten — ~25 Zeilen, nur erprobte Widgets, kein neues Modul. Daten,
Spoilerfilter, Einstieg und Tap-Navigation sind damit vollständig und am Gerät
abnehmbar. Phase 2 tauscht allein die Darstellung gegen das unten beschriebene
gezeichnete Netz aus, wenn es echte Kanten zum Draufschauen gibt. Details in
`docs/plans/2026-07-27-beziehungsnetz-plan.md`.

Das gezeichnete Netz (Phase 2): neues `xray_graph.lua`, eigenes Widget mit
eigenem `paintTo`, Vorbild `frontend/ui/widget/bookmapwidget.lua` (dort
`bb:paintRect` bei :610, :617, :2116).

- **Layout:** gewählte Figur mittig, Nachbarn in je einer Spalte links und
  rechts, abwechselnd befüllt.
- **Kanten:** waagerechte Striche von der Mittelfigur zur jeweiligen Spalte —
  reines `paintRect`. Kein Bresenham, keine selbst geschriebene Rasterlinie.
  Belegt gegen `koreader-base/ffi/blitbuffer.lua`: `paintRect`,
  `paintRoundedRect`, `setPixel`, `paintCircle`, `paintBorder` existieren,
  `paintLine` tatsächlich nicht.
- **Knoten:** `paintRoundedRect`, Beschriftung über
  `RenderText:renderUtf8Text(bb, x, y + baseline, …)`; historische Figuren mit
  abgesetztem Rahmen.
- **Keine Kappung.** Fassung 2 kappte bei 8 und wollte die Grenze aus der
  Schriftgröße messen; eine Zwischenfassung rechnete sie aus der
  Bildschirmhöhe. Beides ist gegenstandslos: der `fold` begrenzt bereits auf
  fünf Kanten je Figur, während eine Clara BW (1072 px, Knoten ~60 px) auf ~17
  Plätze je Spalte käme. Die Grenze kann nicht greifen — mit ihr entfallen der
  Parameter, die Frage wer ihn ausrechnet, die Überlaufzeile „+N weitere" und
  zwei Abnahmefälle. Nähert sich die Kantengrenze je einer Spaltenhöhe, kommt
  die Kappung zurück, dann im Widget gerechnet.
- **Tap:** Trefferrechteck pro Knoten; Tap auf einen Nachbarn öffnet dessen
  Ego-Netz, ein Historien-Stack macht „zurück" zum vorigen Netz statt zum Menü.
  Soll zusätzlich die Detailkarte erreichbar sein, reicht `xray_ui` dafür ein
  Callback hinein — `xray_graph` darf `xray_ui` **nicht** requiren, das wäre
  ein Zyklus (`xray_ui` → `xray_doc`, `xray_lookup` → `xray_ui`, und `main.lua:24`
  lädt `xray_lookup` zuerst).

Aufwand: **250–350 Zeilen**. Die ~120 einer früheren Fassung waren nicht
belegt. Gezählte Vergleichswerte: `HistogramWidget` 48 Zeilen für reine Balken
ohne Text und Tap, `ProgressWidget:paintTo` 117 für *einen* Balken,
`CalendarDay` 94 für **eine** antippbare beschriftete Box, `BookMapRow` — das
Vorbild oben — 634. Dazu kommt, dass `xray.koplugin/` bisher **kein** einziges
`paintTo`, `InputContainer` oder `GestureRange` enthält: es gibt kein
hauseigenes Gerüst zum Abschreiben.

### Einstieg: Knopf auf der Figurenkarte

Die Detailkarte (`XRayUI.showEntry`, `xray_ui.lua:236`) bekommt unten einen
Knopf „Beziehungen". `TextViewer.buttons_table` ist dafür vorhanden
(`textviewer.lua:48`). Kein eigener Menüeintrag: eine Figurenauswahl existiert
mit `showList` bereits, ein zweiter Einstieg müsste sie duplizieren.

`showEntry` braucht dafür zusätzlich den Snapshot — heute bekommt es nur
`entry` und `category`. Das betrifft **vier Aufrufer in zwei Dateien**:
`xray_ui.lua:105` und `:112` (beide in `buildRow`, das die Werte selbst von
`showList` bekommen muss) sowie `xray_lookup.lua:117` und `:162`. Der
Lookup-Weg — einen Namen im Text antippen — ist dabei der wichtigste von
allen, und `showPicker` (`:108`) hält weder `doc` noch `cp_idx`, braucht also
dieselbe Erweiterung. Übersieht man ihn, fehlt der Knopf dort
**stillschweigend**, weil `showEntry`s Rumpf in einem `pcall` liegt.

Zwei Formdetails am `TextViewer`: `add_default_buttons = true` muss mitgesetzt
werden, sonst *ersetzt* `buttons_table` die Standardzeile und die
**Schließen**-Taste verschwindet von genau den Karten, die das Feature
betrifft (`textviewer.lua:391-392`, Kommentar `:76-77`). Und `buttons_table`
ist eine Liste von **Zeilen**, die per `table.insert` in place verändert wird —
also je Aufruf frisch bauen.

**Ohne übrigbleibende Kanten erscheint der Knopf nicht.** Das widerspricht
Feature A nur scheinbar: dort bleibt der Menüeintrag sichtbar, weil Ausblenden
`XRayDoc.load` in den datenfreien `getSubMenuItems` zöge. Auf der Figurenkarte
ist das Dokument längst geladen — der Weg dorthin führt durch `showList`, das
`doc` hält. Prüfen kostet hier also nichts.

Der Kopfkommentar von `xray_ui.lua:6-10` nennt „linked entries" ausdrücklich
als Feature, das dieser Rebuild weggeworfen hat. Wir holen einen Teil davon
zurück — aber als Knopf auf dem bestehenden `TextViewer`, nicht als Rückkehr
zur alten `ButtonDialog`/`VerticalGroup`-Detailansicht, die der Kommentar meint.

### Was ersatzlos entfällt

Alles Folgende stand in Fassung 2 und ist mit dem Nachlauf-Weg gegenstandslos —
nicht widerlegt, sondern ohne Gegenstand, weil Kanten den Merge nie berühren:

| Aus Fassung 2 | Warum weg |
|---|---|
| `clean_response` um `relations` erweitern | Kanten laufen nie durch `clean_response` |
| Kantenschleife in `merge_segment`, Vereinigung nach `(to, label)` | kein Merge, keine Segmente |
| Feldsatz-Marker im Chunk-Cache-Schlüssel | Chunk-Cache unberührt |
| `first_pct` im Merge stempeln | Feld existiert nicht mehr; D4 am Gerät |
| Kantenfilter beim Einfrieren des Snapshots | Filter am Gerät, gegen den sichtbaren Snapshot |
| Bresenham über `bb:setPixel` | waagerechte Kanten, `paintRect` genügt |
| Schriftgröße für 8 Knoten messen | Spalten überlappen nicht, Kappung wird gerechnet |

Damit fällt der Aufwand von „drei Desktop-Umbauten plus 200–250 Zeilen Lua" auf
„ein Tool plus ~120 Zeilen Lua", und der Altbestand ist ohne neuen
Buchtext-Lauf nachrüstbar.

### Verworfene Alternativen

- **Kanten aus dem Extraktions-Prompt** — tiefere Beziehungen, weil das Modell
  den Rohtext sieht. Preis: die drei Merge-Umbauten oben, und Nachrüsten
  bestehender Bücher hieße ein kompletter Neulauf über alle Chunks.
- **Nachlauf pro Stage** (~11 wie beim Recap, oder alle 43) — Spoilerfreiheit
  doppelt abgesichert, aber bei Rücklauf bekommt eine gerade neu aufgetauchte
  Figur ein leeres Netz, und 43 Aufrufe sind der vierfache Recap-Aufwand.
- **Radiales Layout** — sieht am ehesten nach „Graph" aus, verlangt aber
  Bresenham und lässt bei 8 Nachbarn die Labels auf den Diagonalen überlappen.
- **Liste statt Bild** (`Menu` wie die Kategorien, ~25 Zeilen) — auf jedem
  Display garantiert lesbar, aber ausdrücklich kein Bild mehr; das war der Kern
  der ursprünglichen Idee.
- **Symmetrische Labels** („Vater und Sohn") — halb so viele Einträge, aber bei
  Ned im Zentrum und Robb daneben nicht ablesbar, wer der Vater ist.
- **Fertiges Bild vom Desktop einbetten** — scheitert an der Navigierbarkeit
  (Tap-Ziele auf einem PNG) und an fester Auflösung über sehr verschiedene
  Displays; außerdem ist `xray_core` stdlib-only.
- **SVG** (`ImageWidget{file=…svg}` → NanoSVG) wäre fauler, rendert aber nach
  Upstream-Verhalten keine `<text>`-Elemente — die Knotenlabels gingen verloren.
- **Gesamtübersicht aller Figuren** — ab ~15 Knoten auf 6 Zoll unlesbar.

## Ausdrücklich nicht enthalten

Gesamtübersicht, Zoom/Pan, Force-Directed-Layout, Vermeidung von
Kantenkreuzungen, automatischer Recap nach Lesepause, Recap-Erzeugung aus dem
Buch-Rohtext, Beziehungen für Orte/Begriffe/historische Figuren.

---

## Woran sich zeigt, dass es funktioniert

Die Abnahmeliste aus Fassung 1 war wertlos: gegen eine absichtlich kaputte
Implementierung — null Kanten, ungestempelte Kanten im frühesten Snapshot, ein
Recap, der in jedem Checkpoint eine Figur aus dem letzten Satz des Buchs nennt,
ein Layout, das für 20 Nachbarn nichts zeichnet — liefen **6/6 pytest und 4/4
busted grün**. Jeder Check unten trägt deshalb seine Gegenprobe.

**pytest (Desktop)**

- **Nicht-Vakuität zuerst:** das Fixture-Dokument enthält nach dem `fold` > 0
  Kanten, und der Weg dahin führt durch den `fold` — nicht durch einen
  Unit-Test, der ihn umgeht. Ohne diese Assertion sind alle folgenden
  Kanten-Checks vakuum-grün.
- Schema als **Negativfall**: `recap: 12345`, `relations: "x"` und ein
  Kanteneintrag ohne `to` erzeugen je ein `problems`-Element — **plus
  Positivfall**: ein Dokument mit wohlgeformten `relations` erzeugt keins. Ohne
  ihn besteht eine Regel, die alles ablehnt, die Liste mit 10/10 (nachgemessen).
- `fold` verwirft, was in der letzten Stage nicht auflöst — mit Gegenprobe:
  eine Kante auf einen Namen, der dort nur als **Alias** steht, bleibt
  erhalten. Ohne diese zweite Hälfte besteht ein `fold`, der alles verwirft.
  Das gilt für **beide** Figurenkategorien. (Bis 2026-07-28 war die
  Alias-Hälfte auf `characters` beschränkt, begründet damit, dass
  `clean_response` historische Figuren ohne `aliases`-Schlüssel aufbaut
  (`merge.py:391-401` gegen `:374`). Die Begründung war zu eng: der Snapshot
  ist **nach** dem Merge, und `_add_alias` legt den Schlüssel dort an —
  nachgemessen ergibt „Yssa the Elder" + „Queen Yssa the Elder"
  `aliases: ['Queen Yssa the Elder']`. Die Einschränkung verwarf still Kanten.)
- `fold` meldet eine **unerwiderte** Kante: `A→B` ohne `B→A` erzeugt eine
  Warnung. Gegenprobe: ein vollständig erwidertes Paar erzeugt keine.
- `fold` setzt die Obergrenze je `from` durch und verwirft Selbstkanten und
  Duplikate — mit Anzahl-Assertion, nicht nur „enthält nicht", und **in
  Alias-Schreibweise**: `„Ned"→X` und `„Eddard Stark"→X` müssen als *eine*
  Kante enden. Mit zwei identischen Strings geprüft, übersieht der Check eine
  Filterkette, die zu spät normalisiert — nachgemessen liefert die dann zwei
  widersprüchliche Kanten und eine Selbstkante ins ausgelieferte Dokument,
  bei grünem `validate()` und grüner Abnahme.
- Recap-Namens-Scan: gegen ein Dokument mit eingebautem Leak meldet er ihn und
  leert das Feld; gegen ein sauberes Dokument meldet er nichts.

**busted (Gerät)**

- **Die D4-Schranke:** Recap an Stage 2 und 5, Leseposition bei Stage 3 → es
  erscheint der von Stage 2. Ohne diesen Fall besteht ein `recap()`, das seinen
  Index ignoriert und den letzten nicht-leeren Text im Dokument liefert, jede
  andere Assertion — und zeigt bei 30 % den Rückblick fürs Buchende.
- Teilweise Abdeckung: Recap an Stage 2 und 5, Leseposition bei Stage 7 → es
  erscheint der von Stage 5.
- `recap` als `""` wird wie „kein Recap" behandelt: Rücklauf zur früheren
  Stage, sonst der Hinweistext — nie ein leerer Viewer.
- Ein Test über `XRayDoc.load()` mit echter Datei wäre die sauberere Naht, ist
  aber ohne `SQUASHFS_ROOT` nicht lauffähig: in der nackten Umgebung findet
  sich kein JSON-Modul (`rapidjson`/`json`/`dkjson`/`cjson` alle nicht
  vorhanden). Ersatz ist die Ortsassertion auf der Desktop-Seite, die festhält,
  dass `recap` neben `snapshot` landet und nicht darin.
- Layout (**erst Phase 2**, mit dem gezeichneten Netz): `#nodes == n` für
  n = 0, 1, 5 — **und** alle Rechtecke haben
  `w > 0 and h > 0`, und keine zwei teilen sich dieselbe `(x, y)`. Die
  Anzahl allein genügt nicht: ein `layout()`, das die richtige Zahl Rechtecke
  der Größe null am Ursprung liefert, besteht sie (nachgemessen, 6/0) — und
  dann trifft kein einziger Tap je einen Knoten.
- **Der Anzeigefilter, gestaffelt und in beide Richtungen:** ein Dokument mit
  zwei Stages, eine Figur existiert erst in der späteren, eine Kante zeigt auf
  sie. An der frühen Stage erscheint sie **nicht**, an der späten **schon**;
  dazu eine Figur, die nur als **Alias** geführt wird und erscheinen muss.
  Ungestaffelt — nur „Figur fehlt im Snapshot" — besteht ein `egoNet`, das
  gegen den **letzten** statt den sichtbaren Snapshot auflöst, die Liste mit
  6/0 (nachgemessen). Das ist kein Randfall: dieser Mutant zeigt einem Leser
  bei 20 % jeden Namen des Buchs, also der vollständige Ausfall der einzigen
  D4-Instanz dieses Features. Der Recap-Check nebenan ist genau deshalb
  gestaffelt formuliert.
- Historische Figuren als Ziel: eine Kante auf einen Eintrag aus
  `historical_figures` erscheint **und** ist als solche markiert. Ohne die
  zweite Assertion besteht eine Implementierung, die sie zwar zeigt, aber vom
  Tap in die falsche Kategorie schickt.
- **Richtung:** in Robbs Netz trägt Ned das Label „Vater", in Neds Netz trägt
  Robb „Sohn". Ohne diesen Fall besteht eine `egoNet`, die auf `to` statt auf
  `from` filtert — sie liefert dieselben Nachbarn und vertauscht nur alle
  Labels.
- Ego-Netz mit **Abwesenheits-Gegenprobe**: eine dritte, unbeteiligte Figur mit
  eigener Kante taucht nicht auf. Ohne sie besteht eine `egoNet`, die schlicht
  alle Kanten aller Figuren zurückgibt.
- Kein Knopf ohne Kanten: eine Figur ohne auflösbare Nachbarn bekommt keinen
  `buttons_table`-Eintrag — mit Gegenprobe, dass eine Figur mit Nachbarn ihn
  bekommt.
- Ein Fall in `spec/xray_doc_spec.lua`, der das Versions-Gate berührt — heute
  gibt es dazu keinen einzigen (`grep schema_version spec/` → nichts). **Das
  verlangt eine Zeile Vorarbeit:** `schemaOk` und `SUPPORTED_SCHEMA`
  (`xray_doc.lua:34`, `:165-166`) sind beide lokal, und der einzige Weg von
  außen führt über `XRayDoc.load` → `decodeJson`, das ohne JSON-Modul `nil`
  liefert und vor dem Gate kurzschließt. Nachgemessen antworten
  `schema_version` 2, 3 und 99 identisch mit „could not be read" — ein Spec
  „v3 wird abgelehnt" wäre also **vakuum-grün**, und „v2 wird akzeptiert"
  färbt die nackte Suite rot, die die Abnahme grün verlangt. Entweder
  `XRayDoc.schemaOk` exportieren (eine Zeile, dann ist der Fall trivial und
  JSON-frei) oder den Punkt streichen und das Gate als nur am Gerät geprüft
  führen.

**Auf dem Gerät gemessen (2026-07-27, Kobo Clara BW, „Die Gefährten", 43
Stages / 11 Recaps, Companion-Datei 4,27 MB):**

- **Quelle: Begleitdatei** — die Companion-Datei gewinnt gegen die im selben
  Buch eingebettete `xray/xray.json` (3,38 MB). Precedence bestätigt.
- **Stufenwahl** an drei Positionen: 49,5 % → Daten bis 46 %; 69,9 % → 67 %;
  4,3 % → 2 %. MARGIN verhält sich wie vorgesehen.
- **Länge:** 211 Wörter (Stage 46 %) füllen gut eine Bildschirmseite plus einen
  kleinen Rest. Hochgerechnet liegt der längste Recap (421 Wörter, Stage 89 %)
  bei rund zwei Seiten — im Rahmen.
- Titel und Menüeintrag erscheinen übersetzt („Was bisher geschah").

Am selben Tag vom Autor auf dem Gerät nachgeprüft und bestanden: der Rücklauf
bei 69,9 % (zeigt den Text ab 61 %, nicht den ab 72 % — die Lücke wird rückwärts
übersprungen), die beiden unterschiedlichen Leermeldungen, der Gesten-Dialog und
der calibre-Einbettungsweg.

Damit ist Feature A abgenommen.

Am Gerät zu messen bleibt für Feature B:

- Trefferquote bei n Taps auf Knoten am Rand des Layouts.
- Verhalten langer Namen in einer Spalte — Umbruch, Kürzung oder Überlauf.
- Ob fünf Kanten je Figur in der Praxis eine sinnvolle Grenze sind; das ist der
  einzige Prompt-Parameter, den erst ein echtes Buch beantwortet.

Die beiden übrigen Posten der ursprünglichen Messliste sind entfallen:
Linienbreite für Bresenham-Kanten (es gibt keine Diagonalen mehr) und
Schriftgröße für 8 Labels (Spalten überlappen nicht, die Kappung wird
gerechnet).

---

## Offene Entscheidung

Das `==` in `xray_doc.lua:166` ist unabhängig von diesen Features kaputt: es
macht jeden künftigen Schema-Bump zu einem Bruch in beide Richtungen. Der Fix
ist eine Zeile (`<=`), betrifft aber den Ladepfad aller Bücher und gehört
deshalb nicht in einen Feature-Branch, sondern auf einen eigenen Zweig mit
eigenem Release — und zwar **vor** dem nächsten Bump, nicht mit ihm.
