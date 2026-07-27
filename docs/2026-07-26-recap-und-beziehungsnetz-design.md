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

### Daten

Kanten am Charakter-Objekt:

```json
{ "name": "Ned Stark",
  "relations": [ { "to": "Robb Stark", "label": "father of", "first_pct": 12 } ] }
```

### Drei Stellen, an denen Kanten heute verschwinden

**1. `clean_response` verwirft sie.** `merge.py:362-378` baut jeden Charakter
aus einem festen Key-Set neu auf (`name`, `role`, `description`, `gender`,
`occupation`, `aliases`); `relations` fällt heraus. Nachgemessen: `relations
survived: False`. Die Funktion läuft zweimal — in `claude_xray_assemble.py:68`
und beim Cache-Laden in `generate.py:204`, ein handgepatchter Chunk-Cache wird
also ebenfalls gestrippt. → `relations` in die Comprehension aufnehmen, mit
einem Mini-Cleaner analog `_aliases`.

Damit ist die Aussage aus Fassung 1, das koste „bei der Erzeugung fast nichts",
falsch: ohne diese Änderung kommt das Feld nie an.

**2. `BookState._merge` kann keine Listen vereinigen.** Beide vorhandenen
Feldregeln sind Skalarzuweisung (`merge.py:617-622`). Nachgemessen: ohne
Regel-Eintrag gewinnt das erste Segment und alle späteren Kanten gehen verloren;
mit `relations` in `newest_wins` gewinnt das letzte und alle früheren gehen
verloren. → Eigene Kantenschleife in `merge_segment` nach den vier
`_merge`-Aufrufen; `_merge` bleibt unangetastet. Vereinigung nach `(to, label)`,
kleinstes `first_pct` gewinnt.

**3. Der Chunk-Cache ist nicht auf den Feldsatz geschlüsselt.** `_chunk_path`
(`generate.py:114-123`) schlüsselt auf `(cp_idx, chunk_idx, language,
detail_level)`. `SKILL.md` weist ausdrücklich an, unterbrochene Läufe zu
resumen — ein vor dieser Änderung begonnenes workdir liefert dann still ein
Dokument ganz ohne Kanten. → Feldsatz-Marker in den Dateinamen, mit derselben
Begründung, die der dortige Docstring für `language`/`detail_level` schon gibt.

### `first_pct` wird gestempelt, nicht erfragt

Der Chunk-Subagent sieht ein Textstück und weiß nicht, bei welchem Buchprozent
er steht — er lässt `first_pct` weg oder rät. Nachgemessen: eine Kante ohne
`first_pct` rutscht durch jeden Guard (`schema.py:214` springt bei absentem Feld
nicht an) und landet im **frühesten** Snapshot; `validate()` liefert `[]`.

**Also:** `first_pct` beim ersten Sehen im Merge mit `checkpoint_pct` stempeln,
genau wie bei Entitäten (`merge.py:587-590`). Das Modell liefert das Feld gar
nicht. Damit gilt D4 für Kanten konstruktiv, und `_validate_chronology_entry`
wird **nicht** wiederverwendet — es verlangt `name` und `first_seq`, die eine
Kante nicht hat (nachgemessen: zwei Fehler pro Kante). Das Schema bekommt nur
eine Formprüfung.

### Kantenziele: eine Regel gegen drei Fehler

`to` ist ein roher Modell-String und trifft nicht verlässlich einen Knoten.
Zwei belegte Fälle:

- **Namensdrift im Merge:** Segment 1 kennt „Ser Jaime Lennister", Segment 2
  „Jaime Lennister". `_pick_canonical` (`merge.py:599-603`) benennt den Knoten
  um und schiebt die alte Form in `aliases` — die Kante zeigt weiter auf den
  alten String. Ein Namensvergleich auf dem Gerät findet nichts, die eingehende
  Kante fehlt in Jaimes Netz.
- **Ziel existiert im Snapshot noch nicht:** Eine bei 15 % extrahierte Kante
  `Ned Stark --(Vater von)--> Jon Snow` zeigt bei 15 % den Namen einer Figur,
  die dort in keiner Liste steht. `first_pct` ist dabei völlig korrekt — **der
  Name selbst ist der Spoiler.**

**Regel:** Beim Einfrieren eines Snapshots werden Kanten verworfen, deren `to`
nicht auf einen Eintrag *derselben* Snapshot-Charakterliste auflöst (Name oder
Alias). Am Desktop, weil dort die kanonischen Namen und Aliase bekannt sind —
das Gerät braucht dann keine Alias-Auflösung und kann nichts falsch machen.
Eine Regel schließt Namensdrift, Namens-Spoiler und tote, nicht antippbare
Knoten.

### Darstellung

Neues `xray_graph.lua`: eigenes Widget mit eigenem `paintTo`, Vorbild
`frontend/ui/widget/bookmapwidget.lua`.

- **Layout:** gewählte Figur im Zentrum, Nachbarn radial gleichverteilt.
  Mehr als 8 Nachbarn werden nach `first_pct` gekappt (früheste zuerst), mit
  Hinweiszeile „+N weitere". `table.sort` braucht dabei einen Fallback für
  fehlende Werte — sonst `attempt to compare nil with number`.
- **Knoten:** `paintRoundedRect`, Beschriftung über
  `RenderText:renderUtf8Text(bb, x, y + baseline, …)`.
- **Kanten:** Speichen vom Zentrum. KOReaders BlitBuffer hat **kein**
  `paintLine` — Diagonalen als ~20-Zeilen-Bresenham über `bb:setPixel`, genau
  wie `hatchRect` es intern macht. (`LineWidget` hilft nicht, das ist trotz
  Namens nur `paintRect`.)
- **Tap:** Trefferrechteck pro Knoten; Tap auf einen Nachbarn öffnet dessen
  Ego-Netz, ein Historien-Stack macht „zurück" zum vorigen Netz statt zum Menü.

Aufwand: ~200–250 Zeilen in einer neuen Datei.

### Verworfene Alternativen

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

- **Nicht-Vakuität zuerst:** das Fixture-Dokument enthält > 0 Kanten, und der
  Weg dahin führt durch `clean_response` — nicht durch einen Unit-Test, der sie
  umgeht. Ohne diese Assertion sind alle folgenden Kanten-Checks vakuum-grün.
- Schema als **Negativfall**: `recap: 12345` und `relations: "x"` erzeugen je
  ein `problems`-Element. (Der Positivfall ist heute schon grün und beweist
  nichts.)
- D4-Kanten am **ungestempelten** Fall: eine Kante ohne `first_pct` aus einem
  späten Chunk erscheint in keinem frühen Snapshot. Der Stempel-im-Merge macht
  das zur Konstruktion, der Test hält sie fest.
- Kantenziel-Filter: eine Kante auf eine im Snapshot noch nicht vorhandene
  Figur wird verworfen; eine auf einen umbenannten Knoten (`aliases`) bleibt.
- Merge vereinigt nach `(to, label)` und behält das kleinste `first_pct` —
  geprüft über zwei Segmente, nicht über einen direkten `_merge`-Aufruf.
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
- Layout: `#rects == math.min(n, 8)` für n = 0, 1, 8, 20 — die Anzahl mit
  assertieren, sonst besteht ein `layout()`, das immer `{}` liefert, alle vier
  Fälle. Dazu ein Nachbar ohne `first_pct` in der Liste.
- Ego-Netz mit **Abwesenheits-Gegenprobe**: eine dritte, unbeteiligte Figur mit
  eigener Kante taucht nicht auf. Ohne sie besteht eine `egoNet`, die schlicht
  alle Kanten aller Figuren zurückgibt.
- Ein Fall in `spec/xray_doc_spec.lua`, der das Versions-Gate berührt — heute
  gibt es dazu keinen einzigen (`grep schema_version spec/` → nichts).

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

Ursprüngliche Messliste:

- Linienbreite in Pixeln, bei der Bresenham-Kanten auf E-Ink sicher sichtbar
  sind.
- Trefferquote bei n Taps auf Knoten am Rand des Layouts.
- Schriftgröße, bei der 8 Knotenlabels noch lesbar sind — **dieser Wert
  entscheidet die Kappungsgrenze**, die bis dahin bei 8 nur gesetzt ist.

---

## Offene Entscheidung

Das `==` in `xray_doc.lua:166` ist unabhängig von diesen Features kaputt: es
macht jeden künftigen Schema-Bump zu einem Bruch in beide Richtungen. Der Fix
ist eine Zeile (`<=`), betrifft aber den Ladepfad aller Bücher und gehört
deshalb nicht in einen Feature-Branch, sondern auf einen eigenen Zweig mit
eigenem Release — und zwar **vor** dem nächsten Bump, nicht mit ihm.
