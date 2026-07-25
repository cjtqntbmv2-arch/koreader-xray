"""Release-Werkzeug: stempelt EINE Version in alle Stellen, die sie tragen,
committet, taggt und pusht.

Seit dem Monorepo-Umzug leben Geräte-Plugin und Desktop-Erzeugung in einem Repo
und teilen sich eine Version. Vier Dateien tragen sie, jede aus einem eigenen
Grund -- deshalb stempelt dieses Skript sie, statt sie zur Laufzeit irgendwo
auszulesen:

  VERSION                      liest xray_core/generate.py zur Laufzeit
                               (landet als generator_version im Dokument)
  xray.koplugin/_meta.lua      liest der OTA-Updater auf dem Gerät; das
                               .koplugin-Zip enthält VERSION nicht
  calibre_plugin/__init__.py   calibre vergleicht Versions-Tupel beim Upgrade
  README.md                    Badge

Versionsschema: CalVer, exakt drei numerische Teile. Kein Suffix -- der
Versionsvergleich des Updaters (`_versionLessThan`, xray_updater.lua) zieht
per gmatch ALLE Zifferngruppen aus dem Tag, "26.7.25-hotfix2" würde damit
als neuer gelten als "26.7.25".
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_cmd(cmd):
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def stamp(rel_path, pattern, replacement):
    """Ersetzt genau ein Vorkommen. Kein Treffer = Abbruch, nicht Weitermachen:
    eine still nicht gestempelte Datei ist genau die Art Drift, die dieses
    Skript verhindern soll."""
    path = ROOT / rel_path
    if not path.exists():
        sys.exit(f"Error: {path} not found")
    content = path.read_text(encoding="utf-8")
    new_content, count = re.subn(pattern, replacement, content, count=1)
    if count == 0:
        sys.exit(f"Error: version pattern not found in {rel_path}")
    if new_content == content:
        print(f"  {rel_path}: already up to date")
        return False
    path.write_text(new_content, encoding="utf-8")
    print(f"  {rel_path}: stamped")
    return True


def main():
    if len(sys.argv) != 2:
        print("Usage: python tools/release.py <new_version>   # z.B. 26.7.25")
        sys.exit(1)

    new_version = sys.argv[1]
    parts = new_version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        sys.exit(f"Error: '{new_version}' ist kein reines dreiteiliges "
                 "Zahlen-Schema (z.B. 26.7.25) -- siehe Modul-Docstring")
    tuple_literal = f"({', '.join(parts)})"

    print(f"Stamping version {new_version}")
    version_file = ROOT / "VERSION"
    version_file_changed = version_file.read_text(encoding="utf-8").strip() != new_version
    if version_file_changed:
        version_file.write_text(new_version + "\n", encoding="utf-8")
        print("  VERSION: stamped")
    else:
        print("  VERSION: already up to date")

    changed = [
        version_file_changed,
        stamp("xray.koplugin/_meta.lua",
              r'version\s*=\s*"[^"]+"', f'version = "{new_version}"'),
        # Zeilenanfang-Anker: ohne ihn trifft das Muster auch
        # "minimum_calibre_version = (6, 0, 0)" und stempelt bei einer
        # Umsortierung der Klassenattribute still die falsche Zeile.
        stamp("calibre_plugin/__init__.py",
              r"(?m)^(\s*)version\s*=\s*\(\d+,\s*\d+,\s*\d+\)",
              rf"\g<1>version = {tuple_literal}"),
        stamp("README.md",
              r"badge/version-[^-]+-blue", f"badge/version-{new_version}-blue"),
    ]
    version_changed = any(changed)

    print("Executing git commands...")
    try:
        if version_changed:
            run_cmd(["git", "add", "VERSION", "xray.koplugin/_meta.lua",
                     "calibre_plugin/__init__.py", "README.md"])
            run_cmd(["git", "commit", "-m", f"Release {new_version}"])

        tag_check = subprocess.run(["git", "tag", "-l", new_version],
                                   capture_output=True, text=True)
        if new_version in tag_check.stdout.splitlines():
            print(f"Tag {new_version} already exists locally. Skipping.")
        else:
            run_cmd(["git", "tag", new_version])

        push_cmd = ["git", "push", "origin"]
        push_cmd.extend(["HEAD", new_version] if version_changed else [new_version])
        run_cmd(push_cmd)
        print(f"\nRelease {new_version} completed and pushed.")
    except subprocess.CalledProcessError as e:
        print(f"Error during git operations: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
