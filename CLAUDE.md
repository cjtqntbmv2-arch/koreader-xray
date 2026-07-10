# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

calibre-Plugin: generiert spoiler-gestaffelte X-Ray-Daten (Gemini-API) auf dem Desktop
und bettet sie als `xray/xray.json` in EPUBs ein. Gegenstück: das KOReader-Plugin in
`../koreader-xray-plugin-main` (dort der Importer). Design-Spec:
`docs/2026-07-09-calibre-xray-desktop-generation-design.md` — sie ist die maßgebliche
Referenz für Architektur, Ankerkette und Austauschformat.

## Kommandos

```bash
python3 -m pytest tests/                     # alle Tests (ohne Netz, ohne calibre; ~0.5s)
python3 -m pytest tests/test_merge.py -q     # eine Datei
python3 -m pytest tests/test_e2e.py::test_golden_equality   # ein einzelner Test

# CLI (= dieselbe Pipeline, die der Plugin-Job aufruft)
python3 -m xray_core BOOK.epub --api-key KEY [--embed] [--workdir DIR]
python3 -m xray_core BOOK.epub --api-key x --transport-fixture DIR   # ohne Netz

python3 tools/build_plugin.py                                # baut dist/xray-generator-<VERSION>.zip
calibre-customize -a dist/xray-generator-$(cat VERSION).zip  # installieren
calibre-debug -g                                             # calibre mit Debug-Konsole
```

`calibre-customize -b calibre_plugin` funktioniert **nicht**: das Verzeichnis enthält
kein `xray_core/` und keine `VERSION`, beides bündelt erst `tools/build_plugin.py`
(README nennt es noch — nicht übernehmen). Immer über das Zip installieren.

Golden-File regenerieren: der Einzeiler im Docstring von `tests/test_e2e.py`
(der Test selbst schreibt nie); Diff danach von Hand lesen.

## Architektur

Eine Pipeline, zwei Aufrufer (CLI `xray_core/__main__.py`, calibre-Job
`calibre_plugin/ui.py`). Beide durchlaufen exakt dieselben Schritte:

```
read_epub()        epub.py       → BookText(full_text, offsets, toc, text_hash)
plan_checkpoints() checkpoints.py→ [Checkpoint(percent, offset, snippet_anchor, chapter_anchor)]
generate_xray()    generate.py   → Phase A/B/C (unten)
validate()         schema.py     → hart: ungültiges Doc ⇒ ValueError, nichts wird geschrieben
embed_xray()       embed.py      → xray/xray.json ins Zip + OPF-<manifest>-Item
```

**Die drei Phasen in `generate_xray()`** — die Trennung *ist* die D4-Spoiler-Garantie,
nicht ein Implementierungsdetail:

- **A — parallel:** ein `ThreadPoolExecutor` (rate-limited über `RateLimiter`) holt alle
  Chunks aller Checkpoints nebenläufig. Ergebnisse werden nur **gesammelt**, gekeyed nach
  `(cp_idx, chunk_idx)` — nie gemerged.
- **B — Ordered-Merge-Barriere:** ein streng sequenzieller Pass merged in Index-Reihenfolge
  in eine `BookState` und friert nach jedem Checkpoint einen `snapshot()` ein. Weil dieser
  Pass die Fetch-Reihenfolge ignoriert, kann ein früh fertiger später Chunk nie in einen
  früheren Snapshot lecken.
- **C — sequenzielles Enrichment** (nur `detail_level=detailed`): patcht **ausschließlich**
  `description` in den bereits eingefrorenen Snapshots. Niemals aus der lebenden `BookState`
  neu snapshotten — die ist zu diesem Zeitpunkt der Endzustand des ganzen Buchs, das war der
  ursprüngliche Spoiler-Leak-Bug.

**Resume/Teilergebnis:** mit `workdir` landet jeder Chunk atomar als
`chunk_<cp>_<chunk>.json`; ein Rerun lädt ihn ohne API-Call. Nach einer `QuotaError` merged
Phase B nur das **zusammenhängende Präfix** ab Checkpoint 0 (`_completed_prefix_len`) —
das Doc trägt dann `complete: false` + `last_percent`, die CLI exitet mit 2, das Plugin
zeigt einen Warn-Dialog und behält das `workdir`.

**Plugin-Packaging (nicht offensichtlich, betrifft jeden Import):** `build_plugin.py` legt
`calibre_plugin/*` flach ins Zip-Root und `xray_core/` + `VERSION` als Geschwister daneben.
`calibre_plugin/__init__.py` aliast `calibre_plugins.xray_generator.xray_core` in
`sys.modules["xray_core"]`, **bevor** irgendetwas importiert. Konsequenz: `xray_core/`
benutzt durchgehend absolute Top-Level-Imports (`from xray_core.epub import ...`) und läuft
unverändert in CLI, pytest und Plugin. `calibre_plugin/` selbst importiert seine
Geschwister als `calibre_plugins.xray_generator.<modul>`. `VERSION` muss Zip-Root-Geschwister
bleiben — `_generator_version()` liest sie als `../VERSION` relativ zu `generate.py`.

**calibre-Job-Falle:** `ThreadedJob` injiziert `log`/`abort`/`notifications` als kwargs —
die Worker-Funktion muss sie keyword-only führen. Das Ergebnis-EPUB ersetzt die Bibliotheks-
kopie erst nach `_validate_embedded_epub()` (Zip-Integrität + Byte-Roundtrip des Docs +
`read_epub()` parst noch).

## Regeln

- **Trennung strikt einhalten:** `xray_core/` importiert nie aus `calibre`;
  alles calibre-Spezifische lebt in `calibre_plugin/`. pytest läuft ohne calibre.
- **`xray_core/` ist stdlib-only** — auch die EPUB-Extraktion (zipfile/ElementTree/
  html.parser), nicht calibres Container-API; auch der Schema-Validator ist handgeschrieben
  statt `jsonschema`. Ein Extraktor für CLI und Plugin.
- **`schema.py` und `schema/xray.schema.json` sind zwei Kopien desselben Vertrags** und
  werden von Hand synchron gehalten. Schema-Änderungen sind zudem ein **Zwei-Repo-Ereignis**:
  `schema_version` bumpen, Fixture-Kopie in `spec/mocks/` des KOReader-Repos aktualisieren,
  Versions-Gate im Importer beachten.
- **Fachliche Referenz ist das Lua-Original:** Checkpoint-Algorithmus
  (`xray_prefetch.lua:computeCheckpoints`), Gemini-Request/Parse (`xray_aihelper.lua`),
  Merge/Staging (`xray_data.lua`), Prompts (`prompts/en.lua`, `de.lua`) im
  KOReader-Repo. Bei Portierungsfragen dort nachsehen, nicht raten.
- **Spoiler-Invarianten (D4):** ein Snapshot enthält nie Daten jenseits seines
  Checkpoints; Grenzen im Zweifel abrunden. Tests müssen das als Assertions tragen
  (siehe die `test_d4_*`-Familie in `tests/test_e2e.py`).
- **Tests ohne Netz:** Gemini-Transport ist injizierbar (`GeminiClient(..., transport=)`);
  Tests nutzen `FakeClient`/Fixture-Transport, nie echte Calls.
- **API-Keys** nie committen; Nutzer-Config bleibt außerhalb des Repos bzw. in
  gitignorten Dateien.
- Repo ist lokal (kein Remote). Version in `VERSION` + README-Badge + `XRayGeneratorPlugin.version`,
  SemVer ab 0.1.0; Tags/Push entfallen bis ein Remote existiert. Version bleibt 0.1.0, bis das
  Paar calibre-Generator + KOReader-Importer an einem echten Buch end-to-end verifiziert ist.

## Festgelegte Entscheidungen (Brainstorming 2026-07-09)

- **Implementierungsplan:** `docs/plans/2026-07-09-calibre-xray-plugin.md` — maßgeblich
  für die Umsetzung (subagent-driven, ein Task pro Subagent; mechanische Tasks dürfen
  auf ein günstigeres Modell wie Sonnet).
- **Nur Gemini** im ersten Wurf (Modell-Default `gemini-3.5-flash`); weitere Provider später.
- **Kein Send-Hook:** Einbettung von `xray/xray.json` geschieht am Ende des
  Generierungs-Jobs direkt in die Bibliotheks-EPUB (`db.add_format`, ersetzt Format) —
  damit trägt jeder Transferweg die Daten.
- **Chronologie-Konvention:** Desktop stempelt `first_pct` (Checkpoint-Prozent) +
  `first_seq` (monotoner Zähler) statt Geräte-`first_page`; der KOReader-Importer mappt
  `first_pct` → Seite. Sortierung: Charaktere/Orte chronologisch, Begriffe alphabetisch,
  historische Figuren nach Rollen-Gewicht.
- **Detailgrade:** `normal` = Lua-Defaults (200/100/80/100/100 Zeichen),
  `detailed` = 500/300/200/400/300 (= Luas Clamp-Maxima, `prompts.py:31-32`);
  Zähl-Caps nach den Lua-Formeln.
- **Anker:** dreistufig pro Checkpoint — Text-Snippet (80–120 Zeichen, satzgrenzen-
  geschnitten, whitespace-normalisiert; primär) → TOC-Anker → Prozent (abgerundet).
  Checkpoint-Auswahl = 1:1-Port von `computeCheckpoints` (10/12/15%-Konstanten).
- **`text_hash`-Kontrakt:** sha256 über `normalize_text(full_text)` (Whitespace-Runs →
  ein Space, Soft-Hyphens raus) — der Importer muss denselben Hash reproduzieren können.
