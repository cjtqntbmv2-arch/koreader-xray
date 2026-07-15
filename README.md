![version](https://img.shields.io/badge/version-0.2.0-blue)

# calibre-xray

calibre-Plugin, das Kindle-artige X-Ray-Daten (Charaktere, Orte, Begriffe, Ereignisse;
spoiler-gestaffelt) per Gemini-API auf dem Desktop generiert und als `xray/xray.json`
in die EPUB einbettet. Das Gegenstück auf dem E-Reader ist das
[koreader-xray-Plugin](https://github.com/cjtqntbmv2-arch/koreader-xray), das die
eingebetteten Daten beim ersten Öffnen in native `.sdr`-Snapshots importiert.

Design: `docs/2026-07-09-calibre-xray-desktop-generation-design.md`

## Struktur

- `xray_core/` — reine Python-Lib: EPUB-Textextraktion, Checkpoint-Planung,
  Gemini-Client, Merge/Staging, Schema (testbar ohne calibre)
- `calibre_plugin/` — calibre-Glue: InterfaceAction, Config, Background-Job (bettet am Job-Ende ein)
- `schema/xray.schema.json` — Austauschformat-Vertrag (Single Source of Truth)
- `tests/` — pytest (Record/Replay-Gemini, Golden-Files)
- `tools/build_plugin.py` — baut das installierbare Plugin-Zip

## Dev-Loop

```bash
python3 -m pytest tests/                                     # Tests (ohne Netz, ohne calibre)
python3 tools/build_plugin.py                                # dist/xray-generator-<VERSION>.zip bauen
calibre-customize -a dist/xray-generator-$(cat VERSION).zip  # Plugin installieren
calibre-debug -g                                             # calibre mit Konsole starten
```

Immer über das Zip installieren: `calibre-customize -b calibre_plugin` schlägt fehl, weil
das Verzeichnis weder `xray_core/` noch `VERSION` enthält — die bündelt erst `build_plugin.py`.
