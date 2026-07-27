# Umsetzungsplan — „Was bisher geschah" (Prosa-Recap)

Fassung 2, nach adversarialer Prüfung durch drei Reviewer.
Basis: `docs/2026-07-26-recap-und-beziehungsnetz-design.md`, Fassung 2.
Nur Feature A. Das Ego-Netz bekommt seinen eigenen Plan.

**Stand 2026-07-27:** T1–T7 umgesetzt. Suite 220 pytest / 22 busted grün;
Version 26.7.29 (lokal, ohne Tag).

Elf Recaps für „Die Gefährten" erzeugt und auf einem Kobo Clara BW abgenommen:
Companion schlägt embedded, Stufenwahl korrekt, Text und Titel erscheinen
übersetzt, Länge passt (Messwerte im Design-Dokument). Der Lauf auf echten
Daten förderte zwei Fehler im Namens-Scan zutage, die keine Testsuite gefunden
hätte — Timeline-Ereignisse und Figurenbeschreibungen sind Material, das der
Prompt selbst liefert, und dürfen nicht als Leak gelten. Zusätzlich wurde die
Recap-Länge von „konstant 250–400 Wörter" auf Materialskalierung umgestellt.

**Abgenommen.** Die restlichen vier Prüfungen (Rücklauf über eine Lücke, beide
Leermeldungen, Gesten-Dialog, calibre-Einbettungsweg) wurden am 2026-07-27 auf
dem Gerät durchgeführt und bestanden. Der Plan ist vollständig umgesetzt; offen
ist nur noch, die Arbeit zu committen.

Kein Schema-Bump. `schema_version` bleibt 2, das Gerät gated auf Feldpräsenz.

Fassung 1 dieses Plans hatte eine Abnahmeliste, die 6/6 busted und 10/10 pytest
grün lief — gegen ein `recap()`, das seinen Index ignoriert, und ein `plan`,
das die letzten 12 statt 12 verteilter Stages wählt. Jeder Check unten trägt
deshalb den Fall, der die Sache falsifiziert.

## Dateikarte

| Datei | Status | Verantwortung |
|---|---|---|
| `xray.koplugin/xray_doc.lua` | ändern | `XRayDoc.recap(doc, idx)` — Rücklauf zur nächstniedrigeren Stage mit Recap |
| `xray.koplugin/xray_ui.lua` | ändern | `XRayUI.showRecap(doc, cp_idx)` — `TextViewer` |
| `xray.koplugin/main.lua` | ändern | `XRayPlugin:showRecap()` + Eintrag in `getSubMenuItems` **und** `onXRayShow` |
| `xray.koplugin/languages/de.po` | ändern | zwei `msgid` (Menütitel, Hinweistext) |
| `spec/xray_doc_spec.lua` | erweitern | Rücklauf, D4-Schranke, `""`, teilweise Abdeckung |
| `xray_core/prompts.py` | ändern | `RECAP_EN`/`RECAP_DE` + **eigener** Builder |
| `xray_core/schema.py` | ändern | Formprüfung: `recap` muss `str` sein, wenn vorhanden |
| `schema/xray.schema.json` | ändern | zweite Kopie desselben Vertrags |
| `tools/claude_xray_recap.py` | **neu** | Subkommandos `plan` und `fold` |
| `tests/test_recap.py` | **neu** | Stage-Auswahl, Prompt-Bau, Namens-Scan, Fold-Verhalten |
| `.claude/skills/xray/SKILL.md` | ändern | neuer Schritt zwischen §3 und §4 |

`tools/spec_runner.lua` bleibt unangetastet — `xray_doc_spec.lua` ist dort
gelistet (`:148`). Nachgemessen: die sechs geplanten Gerätefälle laufen mit den
vorhandenen Matchern (`assert.equals` / `assert.is_nil`), 21 passed gegen
Baseline 15.

## Verträge

**Dokumentform.** `recap` ist Geschwister von `snapshot`, nicht in ihm:
`{"percent": …, "snapshot": {…}, "recap": "…"}`. Ein Recap, den der Namens-Scan
verwirft, führt zum **Weglassen des Schlüssels** — nie zu `""`. Das Gerät fängt
`""` trotzdem ab, weil ein handgepatchtes Dokument existieren kann und `""` in
Lua wahr ist.

**Geräteseite.** `XRayDoc.recap(doc, idx) -> string | nil`. Läuft von `idx`
abwärts bis zur ersten Stage mit `type(r) == "string" and r ~= ""`. Erbt MARGIN
über den Aufrufer, der `selectCheckpoint()` benutzt — dieselbe Kopplung wie
`timeline()` (`xray_doc.lua:379-391`).

**UI-Stil.** `xray_ui.lua` ruft **nie** `XRayDoc.load`; alle vier vorhandenen
Views bekommen die Daten gereicht (`showList(ui, doc, cp_idx, category)` `:136`,
`showStatus(plugin, doc, cp_idx, pct)` `:303`). Dokument und Fehlerpfad gehören
`main.lua` (`XRayPlugin:current()` `:78` plus die Wrapper `:90`/`:99`/`:108`).
Deshalb `showRecap(doc, cp_idx)`, aufgerufen aus `XRayPlugin:showRecap()`.

**Menüeintrag ist immer sichtbar.** Kein Recap → eigener Hinweistext beim
Antippen. `showNotYetAvailable` (`xray_ui.lua:122-133`) taugt dafür **nicht**:
es meldet „X-Ray data available from N%" und beschreibt Checkpoints, nicht
Recaps — bei einem Dokument mit `checkpoints[1].percent == 1` und einem Leser
bei 60 % sind beide Hälften der Aussage falsch.

Der Eintrag bleibt sichtbar, weil Ausblenden `XRayDoc.load` in den Menüaufbau
zöge. `getSubMenuItems` (`main.lua:207-235`) ist heute vollständig datenfrei;
der ungecachte Ladepfad ist `readEmbedded` (`xray_doc.lua:193-227`) mit
`mkdir -p` + `unzip`, im BusyBox-Fallback über das **ganze** Archiv. Das erste
Öffnen des Untermenüs würde auf E-Ink sichtbar blockieren, wo es heute nichts
kostet.

**Arbeitsverzeichnis.** `recap plan` schreibt `recap_manifest.json` und
`recap_<percent>.prompt.txt`; die Subagents schreiben `recap_<percent>.txt`.
Prosa als reiner Text, nicht JSON — 400 Wörter Fließtext durch JSON-Escaping zu
schicken kauft nichts und bringt eine Parse-Fehlerklasse mit.

**Manifest-Feldsatz** (nach dem Vorbild `claude_xray_plan.py:57-66`):

```
{ "text_hash": "…",
  "companion_name": "<book>.epub.xray.json",
  "stages": [ {"stage_idx": 7, "percent": 40,
               "prompt_file": "recap_040.prompt.txt",
               "out_file": "recap_040.txt"} ] }
```

`text_hash` und `companion_name` sind Pflicht, nicht Zierrat:

- **Stage-Indizes sind nicht stabil.** Nachgemessen am Fixture-Buch: 3 Kapitel
  → 9 Stages `[12,23,34,45,56,67,78,89,100]`, 4 Kapitel → 11 Stages. `stage_idx
  5` bedeutet einmal 67 %, einmal 51 %. Ein gegen das erste Dokument geplantes
  `recap_5.txt` ins zweite gefaltet hängt Prosa über 0–67 % an eine Stage, die
  51 % behauptet — eine D4-Verletzung, die der Namens-Scan nicht fängt.
  `fold` bricht deshalb ab wie `assemble` bei `text_hash`-Drift
  (`claude_xray_assemble.py:52-58`).
- **Der Companion-Name steht nirgends im Dokument.** `assemble` leitet ihn aus
  dem EPUB-Pfad ab (`:49`, `:81`); `book_fingerprint` trägt nur `calibre_uuid`,
  `title`, `authors`, `text_hash`. Globben nach `*.xray.json` ist keine Option —
  `--out` darf das Buchverzeichnis selbst sein (SKILL.md `:96`), in dem mehrere
  Companion-Dateien liegen.

**Stage-Auswahl.** Höchstens 12 möglichst gleichmäßig über die Stages verteilte
Indizes, die **vorletzte** als späteste. Die letzte Stage ist hart auf 100
gesetzt (`generate.py:174`) und wird wegen `threshold = min(percent + MARGIN,
100)` erst bei exakt 100 % ausgewählt (nachgemessen: `pos=99.99 → idx=10`,
`pos=100.00 → idx=11`) — ein Recap dort wäre im ganzen Buch unsichtbar.

## Tasks

### T1 — `XRayDoc.recap` (Gerät, Datenzugriff)

Test zuerst, in `spec/xray_doc_spec.lua`:

- Stage 5 hat einen Recap, `idx = 5` → dieser Text.
- Recaps an Stage 2 und 5, `idx = 7` → der von Stage 5 (teilweise Abdeckung ist
  der **Normalfall** eines abgebrochenen Erzeugungslaufs).
- **Recaps an Stage 2 und 5, `idx = 3` → der von Stage 2.** Ohne diesen Fall
  besteht ein `recap()`, das `idx` ignoriert und schlicht den letzten
  nicht-leeren Text im Dokument liefert, die gesamte Liste — und zeigt dem
  Leser bei 30 % den Rückblick fürs Buchende.
- Stage 5 trägt `""`, Stage 2 einen Text, `idx = 5` → der von Stage 2.
- Keine Stage hat einen Recap → `nil`.
- `idx = nil` (Leser vor dem ersten Checkpoint) → `nil`, kein Fehler.
- Kaputte Eingaben (`doc` kein Table, `checkpoints` kein Table) → `nil`.

Fertig, wenn `luajit tools/spec_runner.lua` grün ist.

### T2 — Menüeintrag und Viewer (Gerät, UI)

`XRayUI.showRecap(doc, cp_idx)` nach dem Muster der vorhandenen
`TextViewer`-Aufrufe (`:241, 258, 372, 474`); `XRayPlugin:showRecap()` gebaut
wie `showStatus` (`main.lua:99-106`).

Eintrag an **beiden** Stellen: `getSubMenuItems` und `onXRayShow`
(`main.lua:162-178`). Letzteres ist der Dispatcher-Pfad und laut dem Kommentar
des Plugins selbst (`:142-155`) „the supported way to be one gesture away" —
eine Lesehilfe gehört nicht nur ins vergrabene Tools-Menü.

Zwei Strings in `de.po`. Die Datei ist **nicht** alphabetisch sortiert (7
Fehlordnungen gemessen) und `xray_i18n.parsePo` baut ohnehin eine Hashtabelle —
Position egal.

Fertig, wenn `python3 -m pytest tests/test_koplugin_catalog.py` grün ist. Der
Test prüft beide Richtungen (jedes `_("…")` hat ein `msgid`, jedes `msgid` wird
benutzt) und ist die einzige Absicherung, die T2 überhaupt hat.

### T3 — Recap-Prompt

`RECAP_EN` / `RECAP_DE` in `xray_core/prompts.py`, mit **eigenem** Builder.
Nicht über `build_prompt`/`_apply_percent_args`: die verdrahten „erste zwei
Specifier sind `%s` Titel/Autor, jeder weitere `%d` percent" und hängen
zwingend `"BOOK TEXT CONTEXT:\n" + segment_text + _CONTEXT_FOOTER` an
(`:321`, `:349`). Ein Recap-Prompt trägt kein Buchtext-Segment. Nachgemessen:
der naive Weg wirft `TypeError: %d format: a real number is required, not str`.

Inhalt nach dem Design: Bänder 0–0,5·P / 0,5–0,85·P / 0,85–P mit ~55/30/15 %
der Wörter, 250–400 Wörter gesamt, unter P = 20 % ohne Staffelung.

Test: `_real_specifier_count(RECAP_EN) == _real_specifier_count(RECAP_DE)`, und
beide rendern ohne Exception. `_SPEC_RE` (`:229`) leistet das **nicht** — es
zählt Specifier in *einem* Template, um das Args-Tupel zu bauen, und vergleicht
EN nie mit DE. Nachgemessen: ein DE-Template mit fehlendem `%s` lässt alle 190
Tests grün und stirbt erst beim ersten deutschen Buchlauf.

### T4 — `recap plan`

Test zuerst:

- 57 Stages → höchstens 12 gewählt, streng aufsteigend, **und der Prozent der
  frühesten gewählten Stage liegt ≤ 15**. Ohne die letzte Bedingung besteht
  `range(n-12, n)` alle übrigen Checks — das Buch hätte bis 81 % keinen Recap.
- Die späteste gewählte Stage ist nicht die letzte des Dokuments.
- 3 Stages → alle drei.
- Zweimal aufrufen → identische Auswahl, identische Dateinamen.
- Für jede gewählte Stage entsteht genau eine `.prompt.txt`; sie enthält die
  Timeline-Events mit `pct <= percent`, **mindestens eines davon**, und keines
  darüber. (Das Fixture braucht dafür eine nicht-leere Timeline — sonst ist die
  Assertion beidseitig vakuum.)
- Das Manifest trägt `text_hash`, `companion_name` und für jede Stage
  `stage_idx`/`percent`/`prompt_file`/`out_file`.

`plan` arbeitet auf `xray.json` plus dem EPUB-Pfad — letzterer nur für
`os.path.basename` und den `text_hash`-Abgleich, kein `read_epub` nötig.

### T5 — Schema-Formprüfung

`recap`, wenn vorhanden, muss `str` sein. Andockstelle ist `_validate_checkpoints`
(`schema.py:113-137`), direkt nach der `snapshot`-Prüfung; `schema.json` hat
kein `additionalProperties` (geprüft), das Feld lässt sich dort ergänzen.

Zwei Tests, nicht einer:

- Negativfall: `recap: 12345` erzeugt ein `problems`-Element.
- Positivfall: ein Dokument mit gültigem Recap liefert `[]`. Heute beweist der
  nichts — ab dieser Änderung fängt er eine übereifrige Prüfung, die jedes
  echte Dokument nach dem Falten ablehnen würde.

Dazu eine Zeile, die `"recap"` in `schema/xray.schema.json` sucht: der Sync der
zweiten Vertragskopie hat sonst keinen Wächter.

### T6 — `recap fold`

Test zuerst:

- **Ortsassertion:** nach dem Falten gilt
  `doc["checkpoints"][i]["recap"] == Inhalt von recap_<percent>.txt` für ein
  `i > 0`. Das pinnt Ort und Zuordnung. `validate() == []` allein beweist
  nichts — nachgemessen liefert es `[]` auch dann, wenn `fold` gar nichts tut,
  wenn `recap` **in** `snapshot` statt daneben landet, oder wenn der Recap vom
  Buchende in die 20-%-Stage geschrieben wird.
- Namens-Scan positiv: Recap nennt eine Figur aus einem späteren Snapshot →
  Schlüssel wird weggelassen, Warnung, Lauf läuft weiter.
- Namens-Scan negativ: sauberer Recap → Feld gesetzt, keine Warnung.
- Keine Fehlalarme, zwei Fälle: ein Name aus Snapshot i selbst löst nichts aus;
  **ein späterer Name als Wortpräfix in einem anderen Wort löst nichts aus**
  („Robb" in „Robbers plundered three villages"). Ohne den zweiten Fall besteht
  eine reine Substring-Suche alle Checks und verwirft einwandfreie Recaps.
- Fehlende `recap_<percent>.txt` → Stage wird übersprungen, kein Abbruch.
- `text_hash`-Drift zwischen Manifest und Dokument → harter Abbruch.
- `fold` schreibt beide Dateinamen mit identischen Bytes.

Namens-Scan-Regel: für Stage i alle `name`/`aliases` aus Snapshots > i minus
die aus Snapshot i, **Wortgrenzen-Match**, case-sensitive, Namen unter 4
Zeichen übersprungen.

`ponytail:` Heuristik mit bekanntem Ceiling — sie fängt den Eigennamen-Leak,
nicht die umschriebene Enthüllung („der wahre Erbe stellt sich später als
jemand anders heraus"). Falschpositive kosten einen guten Recap, darum
konservativ. Nächster Schritt, falls zu grob: Namensliste pro Stage statt
Textsuche.

Verwerfen statt Abbrechen, weil sonst der ganze Lauf nach verbrauchtem Budget
stirbt — dieselbe Falle wie `generate.py:251-253` für den Hauptlauf.

`fold` bekommt dieselben Argumente wie `assemble` (EPUB-Pfad für den
Basenamen, `--out`).

### T7 — Skill-Schritt und Gerätemessung

SKILL.md: neuer Schritt zwischen §3 Assemble und §4 Report — `recap plan`,
Subagents nach dem Muster von §2 (mehrere Stages pro Subagent, absolute Pfade),
`recap fold`.

Zwei Sätze, die dort nicht fehlen dürfen:

- **Nach jedem erneuten `assemble` muss `recap fold` erneut laufen.**
  `generate_xray` baut die Checkpoints allein aus dem Chunk-Cache
  (`generate.py:227`) und `assemble` überschreibt beide Dateien — nachgemessen:
  `recap present after fold: True`, nach erneutem `assemble` `False`, ohne jede
  Meldung. SKILL.md fordert diesen Neulauf ausdrücklich (`:91-93`, `:134-135`).
  Billig, die Texte liegen im workdir.
- **Die Recap-Entscheidung fällt vor der Übergabe in §4.** Nachträglich
  ergänzen heißt, in ein Buch neu einzubetten, das bereits `xray/xray.json`
  trägt: der Append-Modus verweigert das (`embed.py:148-154`), das
  calibre-Plugin fällt auf Vollumschreibung zurück und warnt selbst, dass
  KOReader das Buch „as a different book" sehen und die Lesestatistik
  zurücksetzen kann (`calibre_plugin/ui.py:120`, `:126`).

Auf dem Gerät zu messen und in das Design-Dokument zurückzuschreiben (Gerät,
Datum, Zahl — Vorbild `docs/plans/2026-07-25-xray-neuausrichtung.md:200-202`):

- Länge des `TextViewer`-Inhalts in Bildschirmseiten bei 400 Wörtern.
- Dass der Eintrag bei teilweiser Abdeckung den älteren Text zeigt statt zu
  verschwinden.
- Dass der Hinweistext bei einem Dokument ganz ohne Recaps erscheint und die
  richtige Aussage macht.

## Reihenfolge

**T1 → T2 → T3 → T4 → T5 → T6 → T7.**

Gegenüber Fassung 1 sind zwei Inversionen behoben: der Prompt (T3) kommt vor
dem Tool, das ihn schreibt (T4) — sonst prüft T4s Prompt-Inhaltstest etwas, das
noch nicht existiert. Und die Schema-Prüfung (T5) kommt vor `fold` (T6), sonst
ist T6s Validierungs-Assertion vakuum-grün.

T1/T2 (Gerät) und T3–T6 (Desktop) bleiben voneinander unabhängig.

## Bekannte Fallen

- `""` ist in Lua wahr. `cp.recap or nil` ist der falsche Test.
- Der Recap-Pass ist strukturell die gestrichene Phase C: ausschließlich auf
  eingefrorenen Snapshots arbeiten, nie auf einem lebenden `BookState`.
- `doc["checkpoints"]` sind Stages pro Chunk-Prozent (~57), nicht die ~11
  geplanten Checkpoints (`generate.py:216-227`).
- Teilweise Abdeckung ist überall der erwartete Zustand, nicht der Fehlerfall.
- Ein `load()`-basierter busted-Test ist ohne `SQUASHFS_ROOT` nicht lauffähig —
  in der nackten Umgebung ist kein JSON-Modul vorhanden (`rapidjson`/`json`/
  `dkjson`/`cjson` alle nicht auffindbar). Die Ortsassertion in T6 tritt an
  diese Stelle.
