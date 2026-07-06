# Design: Ablösung vom Original-Repo → eigenes Repo `cjtqntbmv2-arch/koreader-xray`

Datum: 2026-07-06 · Status: vom User abgenommen

## Ziel

Das Projekt vom Original-Repo (`ultimatejimmy/koreader-xray-plugin`) lösen: eigenes,
öffentliches GitHub-Repo `cjtqntbmv2-arch/koreader-xray`, alle Hinweise auf das
Ursprungs-Repo entfernen bis auf **eine** Autoren-Attribution, und der In-App-OTA-Updater
(`xray_updater.lua`) bezieht Updates künftig aus dem neuen Repo.

## Entscheidungen (User)

- Repo-Name: **koreader-xray**, Account **cjtqntbmv2-arch**, **öffentlich**.
- Erstes Release **26.7.4 sofort** publizieren (Draft → published), damit OTA ab Tag 1 funktioniert.
- README: kürzen, Wiki-/Spenden-Links raus, eine Attributionszeile.
- Vorgehen A: bestehende Historie weiterführen (29 Commits, alle vom User; Baseline war
  Zip-Import ohne Fremdautoren; `xray_config.lua` wurde nie mit Key committet — geprüft).
- LICENSE bleibt unverändert (MIT-Pflichtvermerk „Copyright (c) 2026 Jimmy Pautz");
  bewusst **kein** Klarname des Users (Pseudonymität von Account/Commits erhalten).

## Änderungen

1. **`xray.koplugin/xray_updater.lua`**: `GITHUB_OWNER = "cjtqntbmv2-arch"`,
   `GITHUB_REPO = "koreader-xray"`. `ASSET_NAME = "xray.koplugin.zip"` bleibt
   (passt zum bestehenden Release-Workflow).
2. **`README.md`**: Neu — Titel, Badges (Version 26.7.4, Platform, License),
   Kurzbeschreibung, Feature-Liste (bestehende übernehmen), Kurz-Setup
   (Installation + API-Key), Attributionszeile
   „Based on [koreader-xray-plugin](https://github.com/ultimatejimmy/koreader-xray-plugin) by Jimmy Pautz (MIT)".
   Raus: Wiki-Links, Wiki-Screenshots, liberapay/buymeacoffee.
3. **`.agents/rules/release_notes.md`**: Links auf `cjtqntbmv2-arch/koreader-xray`
   umstellen, „Support me"-Absatz entfernen.
4. **`run_koreader.bat`, `tools/wsl_test.ps1`, `tools/spec_runner.lua`**:
   `/home/jimmy/…` → `/home/user/…` (neutraler Platzhalter; `SQUASHFS_ROOT`-Env-Var
   bleibt der Steuerweg, Funktion unverändert).
5. **`.leann/`** (3,6 MB lokaler Suchindex, absolute Pfade): `git rm -r --cached`,
   Eintrag in `.gitignore`.

## Veröffentlichung

1. `gh repo create cjtqntbmv2-arch/koreader-xray --public`, `origin` setzen, `main` pushen.
2. Tag `26.7.4` (bare CalVer, Repo-Konvention) auf den finalen Stand nach Rebranding,
   **nur diesen Tag** pushen — Alt-Tags 26.7.2/26.7.3 bleiben lokal (jeder gepushte Tag
   triggert den Release-Workflow).
3. Workflow baut `xray.koplugin.zip` als **Draft**-Release → per
   `gh release edit 26.7.4 --draft=false` publizieren (Drafts sind für die
   unauthentifizierte Updater-API unsichtbar).

## Verifikation

- `curl https://api.github.com/repos/cjtqntbmv2-arch/koreader-xray/releases/latest`
  (exakt die Updater-URL) liefert Tag 26.7.4 mit Asset `xray.koplugin.zip`.
- Testsuite: Baseline 197 passed / 11 failed (die 11 = dokumentierte AI-Helper-Fails
  ohne `SQUASHFS_ROOT`), keine neuen Fails; `luajit -bl` je geänderter Lua-Datei.
- `xray.koplugin/xray_config.lua` (lokaler User-Key) bleibt unangetastet und uncommitted;
  Staging ausschließlich per explizitem Pfad.

## Nicht-Ziele

- Kein History-Rewrite, keine Umbenennung des Plugin-Verzeichnisses (`xray.koplugin`),
  keine Änderung an Menüstruktur oder Kernverhalten, kein eigenes Wiki.
