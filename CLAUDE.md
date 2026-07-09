# CLAUDE.md

calibre-Plugin: generiert spoiler-gestaffelte X-Ray-Daten (Gemini-API) auf dem Desktop
und bettet sie als `xray/xray.json` in EPUBs ein. Gegenstück: das KOReader-Plugin in
`../koreader-xray-plugin-main` (dort der Importer). Design-Spec:
`docs/2026-07-09-calibre-xray-desktop-generation-design.md` — sie ist die maßgebliche
Referenz für Architektur, Ankerkette und Austauschformat.

## Regeln

- **Trennung strikt einhalten:** `xray_core/` importiert nie aus `calibre`;
  alles calibre-Spezifische lebt in `calibre_plugin/`. pytest läuft ohne calibre.
- **Fachliche Referenz ist das Lua-Original:** Checkpoint-Algorithmus
  (`xray_prefetch.lua:computeCheckpoints`), Gemini-Request/Parse (`xray_aihelper.lua`),
  Merge/Staging (`xray_data.lua`), Prompts (`prompts/en.lua`, `de.lua`) im
  KOReader-Repo. Bei Portierungsfragen dort nachsehen, nicht raten.
- **Schema-Änderungen** (`schema/xray.schema.json`) sind ein Zwei-Repo-Ereignis:
  `schema_version` bumpen, Fixture-Kopie in `spec/mocks/` des KOReader-Repos
  aktualisieren, Versions-Gate im Importer beachten.
- **Spoiler-Invarianten (D4):** ein Snapshot enthält nie Daten jenseits seines
  Checkpoints; Grenzen im Zweifel abrunden. Tests müssen das als Assertions tragen.
- **API-Keys** nie committen; Nutzer-Config bleibt außerhalb des Repos bzw. in
  gitignorten Dateien.
- Repo ist lokal (kein Remote). Version in `VERSION` + README-Badge, SemVer ab 0.1.0;
  Tags/Push entfallen bis ein Remote existiert.

## Festgelegte Entscheidungen (Brainstorming 2026-07-09)

- **Implementierungsplan:** `docs/plans/2026-07-09-calibre-xray-plugin.md` — maßgeblich
  für die Umsetzung (subagent-driven, ein Task pro Subagent; mechanische Tasks dürfen
  auf ein günstigeres Modell wie Sonnet).
- **Nur Gemini** im ersten Wurf (Modell-Default `gemini-3.5-flash`); weitere Provider später.
- **Kein Send-Hook:** Einbettung von `xray/xray.json` geschieht am Ende des
  Generierungs-Jobs direkt in die Bibliotheks-EPUB (`db.add_format`, ersetzt Format) —
  damit trägt jeder Transferweg die Daten.
- **Stdlib-only in `xray_core/`** — auch die EPUB-Extraktion (zipfile/ElementTree/
  html.parser), nicht calibres Container-API; dadurch ein Extraktor für CLI und Plugin.
- **Chronologie-Konvention:** Desktop stempelt `first_pct` (Checkpoint-Prozent) +
  `first_seq` (monotoner Zähler) statt Geräte-`first_page`; der KOReader-Importer mappt
  `first_pct` → Seite. Sortierung: Charaktere/Orte chronologisch, Begriffe alphabetisch,
  historische Figuren nach Rollen-Gewicht.
- **Detailgrade:** `normal` = Lua-Defaults (200/100/80/100/100 Zeichen),
  `detailed` = 400/200/150/200/200; Zähl-Caps nach den Lua-Formeln.
- **Anker:** dreistufig pro Checkpoint — Text-Snippet (80–120 Zeichen, satzgrenzen-
  geschnitten, whitespace-normalisiert; primär) → TOC-Anker → Prozent (abgerundet).
  Checkpoint-Auswahl = 1:1-Port von `computeCheckpoints` (10/12/15%-Konstanten).
- **`text_hash`-Kontrakt:** sha256 über `normalize_text(full_text)` (Whitespace-Runs →
  ein Space, Soft-Hyphens raus) — der Importer muss denselben Hash reproduzieren können.
- **Tests ohne Netz:** Gemini-Transport ist injizierbar; Tests nutzen Fakes/Kassetten.
  Version bleibt 0.1.0, bis das Paar calibre-Generator + KOReader-Importer an einem
  echten Buch end-to-end verifiziert ist.

## Kommandos

```bash
python3 -m pytest tests/               # Tests
calibre-customize -b calibre_plugin    # Plugin aus Verzeichnis installieren
calibre-debug -g                       # calibre mit Debug-Konsole
python3 tools/build_plugin.py          # Plugin-Zip bauen
```
