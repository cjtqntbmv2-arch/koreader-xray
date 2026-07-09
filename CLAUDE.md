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

## Kommandos

```bash
python3 -m pytest tests/               # Tests
calibre-customize -b calibre_plugin    # Plugin aus Verzeichnis installieren
calibre-debug -g                       # calibre mit Debug-Konsole
python3 tools/build_plugin.py          # Plugin-Zip bauen
```
