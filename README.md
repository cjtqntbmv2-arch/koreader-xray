![version](https://img.shields.io/badge/version-0.1.0-blue)

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
- `calibre_plugin/` — calibre-Glue: InterfaceAction, Config, Background-Job, Embed-Hook
- `schema/xray.schema.json` — Austauschformat-Vertrag (Single Source of Truth)
- `tests/` — pytest (Record/Replay-Gemini, Golden-Files)
- `tools/build_plugin.py` — baut das installierbare Plugin-Zip

## Dev-Loop

```bash
calibre-customize -b calibre_plugin   # Plugin aus dem Verzeichnis laden
calibre-debug -g                      # calibre mit Konsole starten
python3 -m pytest tests/
```
