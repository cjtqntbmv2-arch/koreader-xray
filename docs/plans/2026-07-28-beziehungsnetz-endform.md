# Beziehungsnetz — Endform statt Phase 2

Beschluß 2026-07-28: das gezeichnete Zwei-Spalten-Netz wird **nicht** gebaut. Die
`Menu`-Liste aus Phase 1 ist die Endform. Dieser Plan zieht die Folgen daraus:
er räumt die Dokumente auf, macht aus N gestapelten Menus eines, führt vom Netz
einen Weg zur Figurenkarte und entfernt sieben Feldnamen, die `Menu` gar nicht
kennt.

Vorgänger: `docs/plans/2026-07-27-beziehungsnetz-plan.md` (Phase 1, umgesetzt und
am Gerät abgenommen). Design: `docs/2026-07-26-recap-und-beziehungsnetz-design.md`.

Baseline auf `main` (5ecfe24, gemessen 2026-07-28): **272 pytest passed**,
**busted 57 passed / 0 failed**.

Fassung 2 — nach drei unabhängigen Prüfungen. Was sich gegenüber Fassung 1
geändert hat, steht am Ende.

## Dateien

| Datei | Rolle in diesem Plan |
|---|---|
| `xray.koplugin/xray_ui.lua` | `showEgoNet` und `showEntry` umbauen, sieben tote Felder raus |
| `spec/spec_helper.lua` | Menu-Attrappe bekommt `item_table_stack`/`switchItemTable`/`onClose` |
| `spec/xray_ui_spec.lua` | fünf neue Fälle, ein Bestandsfall gelöscht, einer umgeschrieben |
| `docs/2026-07-26-recap-und-beziehungsnetz-design.md` | Phase 2 → „Verworfene Alternativen" |
| `docs/plans/2026-07-27-beziehungsnetz-plan.md` | ebenso, plus Hinweis auf diesen Plan |
| `VERSION`, `xray.koplugin/_meta.lua`, `README.md` | 26.7.31 |

`xray.koplugin/main.lua` wird **nicht** angefaßt: sein `separator = true` (Zeile
244) sitzt auf einem TouchMenu-Eintrag, und TouchMenu kennt das Feld wirklich
(`touchmenu.lua:702`). Nur `Menu`-Zeilen sind betroffen.

## Belegte Grundlagen

Aus KOReaders `frontend/ui/widget/menu.lua`, gegen die Fassungen v2015.11,
v2019.10, v2021.04, v2023.05, v2024.11, v2025.04, v2026.03 und master geprüft:

- `Menu:onMenuSelect` legt nur bei gesetztem `sub_item_table` selbst auf den
  Stapel; `sub_item_table_func` wird dort **nicht** ausgewertet (der einzige
  Treffer, `menu.lua:1585`, steht in `getMenuText` und betrifft nur die
  Chevron-Anzeige). Der Push muß also von uns kommen.
- `Menu:onClose()` poppt von sich aus: bei leerem Stapel `onCloseAllMenus()`,
  sonst `switchItemTable(parent.title, parent)` (`menu.lua:1460-1468`).
- Die Titelleisten-X ruft dasselbe `onClose` (`menu.lua:733`). Zurückgeste und X
  verhalten sich damit gleich, und der Ausstieg aus Tiefe N kostet N Tipps —
  schon heute so, der Umbau ändert daran nichts.
- `switchItemTable(title, table)` tauscht `item_table`, malt den Titel über
  `title_bar:setTitle` und zeichnet über `updateItems(1, …)` neu.
- **`switchItemTable` weist `self.title` nie zu.** `self.title` steht in
  `menu.lua` an genau drei Stellen, alle drei lesend: `:719` (Übergabe an die
  TitleBar), `:1191` (`setTitle`), `:1380` (der Push). In allen acht geprüften
  Fassungen. Daraus folgt T1s Zusatzzeile, siehe dort.
- `item_table_stack` existiert in **allen acht** Fassungen (je 5 Treffer),
  `Menu:switchItemTable` ab v2019.10 — v2015.11 schreibt es `swithItemTable`
  und merkt den Tippfehler selbst an. Eine Weiche für Fassungen ohne den
  Mechanismus ist damit unerreichbar und entfällt (Fassung 1 hatte sie als T2).
- `keep_menu_open` und `separator`: **0 Treffer in `menu.lua`** in allen acht
  Fassungen, 8 bzw. 5 in `touchmenu.lua`. Auf `Menu`-Zeilen wirkungslos.
  `mandatory` dagegen 41 Treffer — es wirkt und bleibt.
- Kein `close_callback` gesetzt, nirgends im Plugin, und `onMenuSelect` ruft es
  nur, wenn es gesetzt ist (`:1372-1374`). Deshalb bleibt eine Liste nach einem
  Zeilentipp ohnehin offen, und das Entfernen von `keep_menu_open` ändert nichts.

## T1 — `showEgoNet` schaltet um, statt zu stapeln

`XRayUI.showEgoNet(doc, cp_idx, entry, menu)`, vierter Parameter neu und
optional.

Zeilenbau bleibt wie heute (Name, `historical`-Zusatz, `mandatory = label`).
Zwei Änderungen:

- `keep_menu_open` und `separator` entfallen.
- Der Zeilen-Callback öffnet **die Karte**, nicht das nächste Netz:
  `XRayUI.showEntry(nachbar.entry, nachbar.category, doc, cp_idx, sprung)`, wobei
  `sprung(viewer)` die Karte schließt und `showEgoNet(doc, cp_idx, nachbar.entry, ziel)`
  ruft. `ziel` ist die vorgezogene lokale Variable aus dem nächsten Absatz —
  **nicht** der Parameter `menu`, der beim ersten Aufruf `nil` ist.

Die Zeilen brauchen das Menu, das erst nach ihnen entsteht. Auflösung ist eine
vorgezogene Lokale `ziel`, die der Closure als Upvalue dient; Lua bindet die
Variable, nicht den Wert. Ohne `menu` wird ein neues `Menu:new{…}` erzeugt, `ziel`
darauf gesetzt und über `UIManager:show` gezeigt. Mit `menu` ist `ziel = menu`,
und statt zu zeigen:

```lua
menu.item_table.title = menu.title   -- Titel reist im alten item_table mit
table.insert(menu.item_table_stack, menu.item_table)
menu:switchItemTable(neuer_titel, zeilen)
menu.title = neuer_titel             -- switchItemTable tut das nicht
```

Die letzte Zeile ist der Kern des Ganzen und der Grund, warum Fassung 1 falsch
war: ohne sie schreibt jeder Push ab Tiefe 2 den **Konstruktionstitel** ins
`item_table`, und der Rückweg zeigt die richtigen Zeilen unter dem falschen
Namen (Frodo → Sam → Merry, einmal zurück: Sams Nachbarn unter der Überschrift
„Frodo — Beziehungen"). Nachgemessen an den wörtlichen Upstream-Rümpfen. Die
Zeile ist gefahrlos: `self.title` wird upstream nur an den drei oben genannten
Stellen gelesen, und alle drei wollen genau diesen Wert.

## T2 — `showEntry` nimmt einen Sprung-Callback

`XRayUI.showEntry(entry, category, doc, cp_idx, on_relations)`, fünfter
Parameter neu und optional.

Der Knopf „Beziehungen" ruft `on_relations(viewer)`, wenn gesetzt, sonst
unverändert `XRayUI.showEgoNet(doc, cp_idx, entry)`. Der Viewer wird über
dieselbe vorgezogene Lokale erreicht wie in T1.

Das Schließen der Karte gehört bewußt in den Callback des Aufrufers, nicht in
`showEntry`: der Weg Kategorienliste → Karte → Netz ist am Gerät abgenommen und
soll sich nicht ändern. Nur der neue Weg aus dem Netz heraus schließt die Karte,
weil das Umschalten darunter sonst unsichtbar bliebe.

Die Bedingung für den Knopf (`is_figure` und nichtleeres Netz) bleibt wörtlich.
Bestehende Aufrufer bleiben unberührt — geprüft: `showEntry` wird mit 2 Argumenten
gerufen (`xray_ui.lua:108`) und mit 4 (`:115`, `xray_lookup.lua:88`, `:133`),
`showEgoNet` mit 3 (`:274`, `:337`); ein neuer hinterer Parameter trifft keinen
belegten Platz.

## T3 — Tote Felder entfernen

**Sieben** Vorkommen in `xray_ui.lua`: Zeilen 106/107 und 113/114 (`buildRow`),
169 (Leerzeilen-Platzhalter in `showList`), 271/272 (`showEgoNet`, fällt schon
mit T1). `main.lua:244` bleibt.

Dazu **eine** Kommentarzeile an `buildRow`, die den Befund festhält — nach dem
Vorbild der `subtext`-Notiz, die dort schon steht. Kein Kommentar an jeder
einzelnen Fundstelle.

## T4 — Menu-Attrappe stapelfähig machen

`spec/spec_helper.lua` liefert heute `{ type = "Menu", args = … }`, ein nacktes
Tischchen ohne Verhalten. Die Attrappe bekommt `item_table_stack`, `item_table`,
`title`, `switchItemTable` und `onClose`.

**Die Attrappe muß den Titel genauso spalten wie das Original, sonst ist die
ganze Prüfung wertlos.** Das Original setzt in `switchItemTable` **nur**
`item_table` und malt den Titel in die TitleBar; `self.title` bleibt stehen. Die
Attrappe bildet das nach:

- `switchItemTable(title, tbl)` setzt `item_table = tbl` und
  `painted_title = title` — und **nicht** `self.title`.
- `onClose()` ist eine Abschrift von `menu.lua:1460-1468`: bei leerem Stapel
  schließen (in `_G.ui_tracker.closed` vermerken), sonst poppen und
  zurückschalten.

Eine Attrappe, deren `switchItemTable` auch `title` setzt, macht T5s Fälle 2 und
3 auf der kaputten Umsetzung grün — nachgemessen: Attrappe zeigt „Sam", das echte
Widget zeigt „Frodo". Genau daran ist Fassung 1 gescheitert.

## T5 — Prüffälle

In `spec/xray_ui_spec.lua`, gegen die bestehenden gestaffelten Vorrichtungen.
Fünf neue Fälle:

1. Nach zwei Sprüngen ist genau **ein Widget vom Typ `Menu`** in
   `ui_tracker.shown`. Nach Typ zählen, nicht nach Anzahl: `ui_tracker.shown`
   sammelt jedes gezeigte Widget (`spec_helper.lua:64-73`), und da jeder Sprung
   jetzt eine Karte zeigt, steht dort auch bei richtiger Umsetzung eine 3.
2. Nach dem Sprung trägt `painted_title` die neue Figur, `item_table` deren
   Nachbarn, und `#item_table_stack == 1`.
3. `onClose()` auf Ebene 1 zeigt wieder die Zeilen **und den `painted_title`** der
   vorigen Figur und schließt nicht; auf Ebene 0 schließt es.
4. Ein Zeilentipp im Netz zeigt einen `TextViewer` — **und dessen `title` ist der
   Nachbar**, nicht die Mittelfigur. Ohne diese Hälfte besteht der Fall auch dann,
   wenn der Callback `entry` statt `nachbar.entry` weiterreicht und immer die
   Karte der Figur öffnet, auf der man schon steht.
5. Der Knopf „Beziehungen" einer aus dem Netz geöffneten Karte schaltet das
   bestehende Menu um und zeigt kein zweites. **Auf Robb**, nicht auf Jon Schnee:
   Jon kommt in der Fixtur nur als `to` vor, hat also kein eigenes Netz und
   folglich keinen Knopf (`spec/xray_ui_spec.lua:25-30`).

Zwei Bestandsfälle brechen unter T1 — beide erwarten nach einem Zeilentipp ein
`Menu` und bekommen einen `TextViewer`. Nachgemessen: 55 passed / 2 failed.

- `:117-134` „opens the neighbour's own net when its row is tapped" — **gelöscht**,
  Fall 4 tritt an seine Stelle.
- `:180-191` „keeps the reader's stage across a tap into a neighbour's net" —
  **umgeschrieben**, nicht gelöscht: es ist der einzige Fall, der festhält, daß die
  Stufe des Lesers einen Sprung überlebt. Neuer Weg über die Karte, Stufenassertion
  wörtlich erhalten.

Erwartete Summe: 57 − 1 + 5 = **61**. Der gestaffelte D4-Fall bleibt wörtlich
stehen.

**Gegenprobe, verpflichtend.** Drei Mutanten, und die rote Menge muß **genau**
stimmen — „irgendetwas wurde rot" genügt nicht:

| Mutant | muß rot machen |
|---|---|
| Push weggelassen | 2 und 3 |
| `menu.title = neuer_titel` weggelassen | 3 |
| Zeilen-Callback reicht `entry` statt `nachbar.entry` | 4 |

Wird eine Zeile nicht rot, prüft der Fall nichts — dann ist der Fall falsch
geschrieben, nicht der Code richtig. Alle drei werden gefahren und das Ergebnis
berichtet.

## T6 — Dokumente

Im Design: der Absatz „Das gezeichnete Netz (Phase 2)" samt Aufwandsrechnung
wird zu **einem** Eintrag unter „Verworfene Alternativen" zusammengezogen, mit
dem Grund (die Liste trägt am Gerät, das Bild wäre 250–350 Zeilen neuer
Widget-Code ohne erprobtes Vorbild im Plugin). Die Zwei-Phasen-Ankündigung im
Abschnitt „Darstellung" fällt weg. Aus der Meßliste entfallen Trefferquote am
Layoutrand und lange Namen in einer Spalte; **bestehen bleiben** die Frage nach
fünf Kanten je Figur und die nach der Gegenrichtung — beide betreffen den
Prompt, nicht die Darstellung.

Im Vorgängerplan: der Abschnitt „Phase 2" wird durch einen Zweizeiler ersetzt,
der auf diesen Plan verweist. Die Layout-Abnahmefälle im Abnahmeabschnitt
entfallen mit ihm.

## T7 — Version und Abschluß

`python3 ~/.claude/skills/versioning/check_version.py . --set 26.7.31`. Commit
`chore: bump version to 26.7.31`.

Zweig `feat/beziehungsnetz-endform`, Abschluß mit `finish`. **Kein Tag, kein
Release** — die Projektregel läßt Bumps liegen, bis eine Veröffentlichung
ausdrücklich verlangt wird. `finish` erkennt die Testeinstiege dieses Repos
nicht („Tests: KEINE GEFUNDEN"), beide Suiten laufen deshalb von Hand davor.

## Abnahme

- `python3 -m pytest tests/` — 272 passed, unverändert (nichts an `xray_core`).
- `luajit tools/spec_runner.lua` — **61 passed / 0 failed**.
- Die drei Mutanten aus T5 gefahren, die rote Menge stimmt mit der Tabelle
  überein, Ergebnis berichtet.
- `check_version.py .` zeigt 26.7.31 an allen drei Stellen. Das Werkzeug meldet
  dabei weiterhin **ABWEICHUNG**, weil es den letzten Git-Tag (26.7.27) in sein
  Urteil einrechnet und dieser Plan ausdrücklich nicht taggt. KONSISTENT ist hier
  unerreichbar und kein Ziel — die drei Stellen sind es.

Eine Geräteabnahme ist **nicht** Teil dieses Plans: der Umbau ändert Navigation
und Widgetzahl, nicht die Daten. Wenn du sie willst, kommt sie obendrauf — dann
mit dem Kobo am Kabel und dem Companion aus dem Gefährten-Lauf.

## Ausdrücklich nicht enthalten

Das gezeichnete Netz. Eine Weiche für KOReader ohne `item_table_stack` (in acht
geprüften Fassungen zurück bis v2015.11 vorhanden — unerreichbar; falls je ein
Gerät davor auftaucht, sind es zwei Zeilen). Platzhalternamen aus Prompt und
Auflöser filtern (in „Die Gefährten" 151 Figuren, 0 Treffer). Das `==`-Gate in
`xray_doc.lua:166`. Ein schneller Ausstieg aus tiefen Netzen. Beziehungen für
Orte und Begriffe.

## Was Fassung 1 falsch hatte

Drei unabhängige Prüfer, Linsen: KOReader-API-Annahmen, Falsifizierbarkeit der
Prüffälle, Blast-Radius und Schnittstellen.

- **Der Titel war ab Tiefe 2 falsch.** Zwei Prüfer unabhängig, einer gegen die
  wörtlichen Upstream-Rümpfe gefahren. → T1s Zusatzzeile.
- **Die Attrappe hätte genau diesen Fehler verdeckt.** Fassung 1 schrieb, ihr
  `switchItemTable` setze „beide"; das Original setzt nur eines. → T4s Spaltung
  in `painted_title` und `title`.
- **Fall 4 prüfte nur, daß *eine* Karte erscheint, nie wessen.** Ein Callback mit
  `entry` statt `nachbar.entry` bricht das Feature vollständig und blieb grün.
- **Die Gegenprobe war eine Sonde mit zwei Hüten:** beide vorgeschriebenen
  Mutanten machten denselben einen Fall rot. → drei Mutanten mit benannter roter
  Menge.
- **Zwei Bestandsfälle brechen** und waren nicht erwähnt; die Abnahme nannte keine
  Summe, an der das auffallen würde.
- **Fall 1 zählte Widgets statt Menus** und hätte auf 3 gestanden — genau der
  Zahl, die Fassung 1 als „heute, kaputt" zitierte.
- **T2 war eine Weiche gegen einen gemessen unmöglichen Fall** und zugleich ein
  Kanal, der das Feature stumm hätte schlucken können. Gestrichen.
- **`check_version.py` kann hier nie KONSISTENT melden**, weil es den Tag
  einrechnet. Die Abnahme forderte Unmögliches.
- Sieben tote Felder, nicht sechs.

Nicht übernommen: der Vorschlag, gegen die stumme Weiche eine Diagnosezeile zu
setzen — die kleinere Antwort war, die Weiche zu streichen.
