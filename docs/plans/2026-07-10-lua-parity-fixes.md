# Lua-Paritäts-Fixes + Schema-Härtung — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vier belegte Abweichungen vom Lua-Original in `xray_core/` beseitigen, den handgeschriebenen Schema-Validator gegen die Lücken schließen, die ein Audit gefunden hat, und die Doku auf den Stand des Codes bringen.

**Architecture:** Alle Änderungen liegen in `xray_core/` (stdlib-only, kein calibre). Erst der gemeinsame Guard in `is_non_narrative` (ein Fix, zwei Aufrufer), dann die Feld-Extraktion in `clean_response`, dann die lokalisierten Namens-Platzhalter, dann die `role`-Merge-Semantik, dann der Validator. Fixture, Golden und Doku ziehen am Ende nach.

**Tech Stack:** Python 3.11, stdlib only, pytest. Referenz-Implementierung: Lua unter `../koreader-xray-plugin-main/xray.koplugin/`.

## Global Constraints

- `xray_core/` importiert **nie** aus `calibre` und **nie** ein Third-Party-Paket. Auch nicht in Tests für `xray_core`.
- `python3 -m pytest tests/` muss ohne calibre grün bleiben. Stand vor Beginn: **101 passed**.
- **D4:** Ein Checkpoint-Snapshot enthält nie Daten jenseits seines Checkpoints. Jede Task, die `merge.py` oder `generate.py` anfasst, lässt `tests/test_e2e.py` laufen.
- **Fachliche Referenz ist das Lua-Original.** Jede bewusste Abweichung bekommt einen Kommentar im Code, der sagt *was* abweicht und *warum*.
- **Kein `schema_version`-Bump.** Der Validator wird nur strenger, das Format ändert sich nicht. Kein Zwei-Repo-Ereignis, keine Fixture-Änderung im KOReader-Repo.
- **Version bleibt `0.1.0`.** CLAUDE.md sperrt den Bump bis zur End-to-End-Verifikation mit dem KOReader-Importer. Kein Bump, kein Tag.
- Repo ist lokal, kein Remote. Kein `push`.
- Ein Commit pro Task, am Ende der Task. **Kein Task committet mit rotem Test.**

## Getroffene Entscheidungen (Nutzer, 2026-07-10, nach Red-Team + Grilling)

1. **Inhalts-Platzhalter: keine.** `role`, `description`, `biography`, `importance_in_book`, `context_in_book` bleiben leer, wenn die KI nichts liefert. Grund liegt im Code des Gegenparts: der Viewer blendet leere Felder aus (`xray_ui.lua:190`, `:214`, `:1080`), ein eingebrannter Platzhalter erscheint dagegen als sichtbarer Text auf jeder Karte. Das Gerät hat keine eigene Lokalisierung dieser Felder — es füllt nichts nach, es zeigt sie nur nicht an. **Bewusste Divergenz zu Lua**, das `"Not Specified"` / `"No Description"` einbrennt.
2. **Namens-Platzhalter: ja, lokalisiert.** Sie sind tragend (`_merge` dedupliziert über `name`; namenlose Einträge kollidieren nie, `merge.py:193`) und erscheinen als Kartentitel. `de` = `Unbenannter Charakter` / `Unbenannte Person` / `Unbekannter Ort`, `en` = `Unnamed Character` / `Unnamed Person` / `Unknown Place`. Die ersten beiden verbatim aus `prompts/de.lua:361-364` bzw. `prompts/en.lua:322-325`; `Unknown Place` ist in Lua hart einkodiert (`xray_aihelper.lua:2046`), die deutsche Fassung ist neu.
3. **`role`-Merge: neuester nicht-leerer Wert gewinnt** — für Charaktere **und** historische Figuren. Lua-Parität im Kern (`xray_fetch.lua:587`, `:660`), aber mit Auslass-Schutz: ein Abschnitt ohne `role` löscht keine bekannte Rolle. Das ist eine **echte Divergenz**: Lua defaultet Hist-`role` auf `""` (`xray_aihelper.lua:2033`) und überschreibt bedingungslos, kann eine Rolle also blanken. Nutzer-Entscheidung: Informationserhalt schlägt Bit-Parität. Wirkung an echten Daten: 19 von 101 Charakterrollen ändern sich.
4. **E2E-Fixture wird erweitert** (Task 6). Ohne das bleibt das Golden byte-identisch und beweist über die Tasks 1–4 nichts.
5. **Umfang:** Paritätsfixes + Schema-Härtung + Doku. Der Plugin-Temp-Leak in `calibre_plugin/ui.py` ist **nicht** Teil dieses Plans.

**Vorab verifiziert** (Probelauf einer *früheren* Fassung der Tasks 1–5 in einer Repo-Kopie unter `/tmp/planprobe`): Suite durchgehend grün, nichts abgewählt, `test_golden_equality` blieb grün, Golden byte-identisch, `validate()` sauber gegen Golden **und** gegen das echte `/tmp/xray.json`. Deshalb gibt es in keinem Task ein `--deselect`. Erst Task 6 ändert das Golden — durch die Fixture-Erweiterung, absichtlich, im selben Commit. Die genaue Testzahl ist bewusst nirgends festgeschrieben; maßgeblich ist „alle grün".

## Lua-Quellen (Ground Truth, zweifach verifiziert)

Pfad-Präfix: `../koreader-xray-plugin-main/xray.koplugin/`

- `xray_data.lua:327-335` — `isNonNarrativeChapter`: `if not title then return true end`, danach `if lower == "" then return true end`.
- `xray_prefetch.lua:46` und `xray_fetch.lua:534` — **beide** Aufrufer teilen den Helper; `:534` verwirft Timeline-Ereignisse mit leerem `ev.chapter`.
- `xray_aihelper.lua:2015-2048` — `validateAndCleanData`, die Feld-Ketten (Task 2) und die Namens-Defaults (Task 3).
- `xray_aihelper.lua:1997` — `validateAndCleanData(normalizeKeys(data))`: die Groß-Varianten `c.Name` / `l.Lugar` im Lua sind **toter Code**, weil vorher normalisiert wird. Deshalb führt Task 2 nur Kleinschreibung.
- `xray_fetch.lua:587` / `:660` — `existing_char.role = new_char.role` bzw. `existing_fig.role = new_fig.role`, bedingungslos.
- `prompts/de.lua:359-366`, `prompts/en.lua:320-328` — die Fallback-Tabellen.
- `xray_ui.lua:190`, `:214`, `:1080` — der Viewer überspringt leere `role`/`occupation`/`gender`/`description`.

## File Structure

| Datei | Verantwortung | Änderung |
|---|---|---|
| `xray_core/checkpoints.py` | Checkpoint-Planung, `is_non_narrative` | Task 1: leerer Titel → `True` |
| `xray_core/merge.py` | Cleaning, Merge, Sortierung | Task 2: Fallback-Ketten · Task 3: lokalisierte Namens-Platzhalter · Task 4: `role` → `newest_wins` |
| `xray_core/generate.py` | Orchestrierung | Task 3: `language` an `clean_response` durchreichen |
| `xray_core/schema.py` | Vertrag zur Schreibzeit | Task 5: timeline, authors, chapter_anchor, Untergrenzen, Duplikate |
| `tests/test_checkpoints.py` | | Task 1 |
| `tests/test_merge.py` | | Tasks 1–4 |
| `tests/test_schema.py` | | Task 5 |
| `tests/test_e2e.py` + `tests/golden/xray_golden.json` | Integrationsabdeckung | Task 6: Fixture deckt die vier Fixes ab, Golden neu erzeugt |
| `CLAUDE.md`, `docs/…design.md`, `tools/build_plugin.py` | | Task 7 |

---

### Task 1: Leerer Kapiteltitel gilt als nicht-narrativ

Ein Guard in der gemeinsam genutzten Funktion, nicht in beiden Aufrufern. `epub.py:215` und `:233` erzeugen nachweislich leere TOC-Titel, und `merge.py:267` filtert damit auch die Timeline.

**Files:**
- Modify: `xray_core/checkpoints.py:31-33`
- Test: `tests/test_checkpoints.py`, `tests/test_merge.py`

**Interfaces:**
- Consumes: nichts.
- Produces: `is_non_narrative(title) -> bool` — Signatur unverändert; Verhalten für `None` / `""` / `"   "` kippt von `False` auf `True`.

- [ ] **Step 1: Write the failing tests**

`tests/test_checkpoints.py` importiert `is_non_narrative` bereits. Anhängen:

```python
def test_is_non_narrative_treats_blank_title_as_non_narrative():
    # xray_data.lua:328 `if not title then return true end`
    # xray_data.lua:330 `if lower == "" then return true end`
    assert is_non_narrative(None) is True
    assert is_non_narrative("") is True
    assert is_non_narrative("   ") is True


def test_is_non_narrative_still_accepts_real_chapters():
    assert is_non_narrative("Kapitel 1") is False
    assert is_non_narrative("cover") is True
```

In `tests/test_merge.py` anhängen:

```python
def test_timeline_drops_events_with_blank_chapter():
    # xray_fetch.lua:534 filtert Timeline-Ereignisse durch denselben Helper.
    state = BookState()

    state.merge_segment(
        clean_response(
            {
                "timeline": [
                    {"chapter": "", "event": "kapitellos"},
                    {"chapter": "Kapitel 1", "event": "echt"},
                    {"chapter": "Copyright", "event": "frontmatter"},
                ]
            }
        ),
        checkpoint_pct=10,
    )

    assert [ev["event"] for ev in state.timeline] == ["echt"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_checkpoints.py::test_is_non_narrative_treats_blank_title_as_non_narrative tests/test_merge.py::test_timeline_drops_events_with_blank_chapter -v
```

Erwartet: beide FAIL. Der erste mit `assert False is True`, der zweite mit `assert ['kapitellos', 'echt'] == ['echt']`.

- [ ] **Step 3: Write minimal implementation**

`xray_core/checkpoints.py`, die Funktion komplett ersetzen:

```python
def is_non_narrative(title) -> bool:
    """Port of `isNonNarrativeChapter` (`xray_data.lua:327-335`).

    A missing or blank title counts as non-narrative -- Lua's
    `if not title then return true end` plus `if lower == "" then return
    true end`. Both callers share that rule on the device too: chapter-
    boundary selection (`xray_prefetch.lua:46`) and the timeline filter
    (`xray_fetch.lua:534`), which is why the guard lives here and not in
    either caller.
    """
    t = (title or "").lower().strip()
    if not t:
        return True
    return any(p.match(t) for p in _NON_NARRATIVE_RE)
```

- [ ] **Step 4: Run the full suite**

```bash
python3 -m pytest tests/ -q
```

Erwartet: alle grün; die Suite wächst um die zwei neuen Tests. Bricht `tests/test_e2e.py`, ist das ein echtes Signal — dann hätte das Fixture-EPUB einen leeren TOC-Titel. Prüfen, nicht wegdrücken.

- [ ] **Step 5: Commit**

```bash
git add xray_core/checkpoints.py tests/test_checkpoints.py tests/test_merge.py
git commit -m "fix(checkpoints): blank chapter title is non-narrative (Lua parity)"
```

---

### Task 2: Fehlende Fallback-Schlüsselketten in `clean_response`

Liefert das Modell `place` statt `name`, verliert Python den Ortsnamen: der Ort-Name benutzt heute die **Charakter**-Kette. Alle Ketten unten sind gegen `xray_aihelper.lua:2015-2048` geprüft, jede einzeln bestätigt.

**Files:**
- Modify: `xray_core/merge.py:19` (Konstanten), `:40-112` (`clean_response`)
- Test: `tests/test_merge.py`

**Interfaces:**
- Consumes: nichts.
- Produces: `clean_response(raw: dict) -> dict` — Signatur unverändert (Task 3 ergänzt `language`). Ausgabefelder unverändert.

**Vorbedingung, die dokumentiert werden muss:** `clean_response` erwartet **bereits normalisierte** Schlüssel. `gemini.py:192` (`_parse_response`) ruft `normalize_keys`, das jeden Schlüssel auf Kleinschreibung senkt. Lua koppelt beides genauso (`xray_aihelper.lua:1997`). Deshalb entfallen die Großschreib-Varianten `"Name"` und `"Lugar"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_merge.py` anhängen:

```python
def test_clean_location_name_falls_back_to_place_and_lugar():
    # xray_aihelper.lua:2046 -- l.name or l.place or l.Lugar
    assert clean_response({"locations": [{"place": "Palermo"}]})["locations"][0]["name"] == "Palermo"
    assert clean_response({"locations": [{"lugar": "Vesuv"}]})["locations"][0]["name"] == "Vesuv"


def test_clean_location_never_uses_character_name_chain():
    # Regression: der Ort nutzte die Charakter-Kette (full_formal_name ...).
    # Ein Ort ohne name/place/lugar ist namenlos, egal was sonst dransteht.
    cleaned = clean_response({"locations": [{"full_formal_name": "Lord Farquaad"}]})
    assert cleaned["locations"][0]["name"] == "Unnamed location"


def test_clean_location_description_and_importance_fallbacks():
    # xray_aihelper.lua:2047-2048
    loc = clean_response({"locations": [{"name": "X", "desc": "d", "significance": "s"}]})["locations"][0]
    assert loc["description"] == "d"
    assert loc["importance"] == "s"
    loc2 = clean_response({"locations": [{"name": "X", "short_desc": "sd"}]})["locations"][0]
    assert loc2["description"] == "sd"


def test_clean_character_description_and_occupation_fallbacks():
    # xray_aihelper.lua:2017,2019
    c = clean_response({"characters": [{"name": "A", "bio": "b", "job": "j"}]})["characters"][0]
    assert c["description"] == "b"
    assert c["occupation"] == "j"
    assert clean_response({"characters": [{"name": "A", "history": "h"}]})["characters"][0]["description"] == "h"
    assert clean_response({"characters": [{"name": "A", "desc": "d"}]})["characters"][0]["description"] == "d"


def test_clean_historical_figure_fallbacks():
    # xray_aihelper.lua:2031-2035
    h = clean_response(
        {
            "historical_figures": [
                {
                    "name": "N",
                    "description": "d",
                    "historical_role": "r",
                    "significance": "s",
                    "context": "c",
                }
            ]
        }
    )["historical_figures"][0]
    assert h["biography"] == "d"
    assert h["role"] == "r"
    assert h["importance_in_book"] == "s"
    assert h["context_in_book"] == "c"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_merge.py -k "fallback or never_uses_character" -v
```

Erwartet: FAIL. `test_clean_location_never_uses_character_name_chain` schlägt fehl, weil `"Lord Farquaad"` heute durchkommt; die anderen liefern `""`.

- [ ] **Step 3: Write minimal implementation**

`xray_core/merge.py`, Zeile 19 (`_NAME_FALLBACKS = ...`) ersetzen durch:

```python
# Alternative keys the model sometimes emits, verbatim from
# `xray_aihelper.lua:2015-2048`. Lua's upper-case branches (`c.Name`,
# `l.Lugar`) are dead code there: `:1997` calls
# `validateAndCleanData(normalizeKeys(data))`. We rely on the same
# precondition -- see clean_response's docstring.
_CHAR_NAME_KEYS = ("name", "full_formal_name", "full_name", "formal_name")
_CHAR_DESC_KEYS = ("description", "bio", "history", "desc")
_CHAR_OCCUPATION_KEYS = ("occupation", "job")
_LOC_NAME_KEYS = ("name", "place", "lugar")
_LOC_DESC_KEYS = ("description", "desc", "short_desc")
_LOC_IMPORTANCE_KEYS = ("importance", "significance")
_HIST_NAME_KEYS = ("name",)
_HIST_BIO_KEYS = ("biography", "bio", "description")
_HIST_ROLE_KEYS = ("role", "historical_role")
_HIST_IMPORTANCE_KEYS = ("importance_in_book", "significance")
_HIST_CONTEXT_KEYS = ("context_in_book", "context")
```

Die drei Listen-Comprehensions in `clean_response` (Zeilen 47-81) ersetzen durch:

```python
    characters = [
        {
            "name": _first_nonempty(c, _CHAR_NAME_KEYS, "Unnamed character"),
            "role": _str(c, "role")[:40],
            "description": _first_nonempty(c, _CHAR_DESC_KEYS, ""),
            "gender": _str(c, "gender"),
            "occupation": _first_nonempty(c, _CHAR_OCCUPATION_KEYS, ""),
            "aliases": _aliases(c),
        }
        for c in raw.get("characters") or []
        if isinstance(c, dict)
    ]

    locations = [
        {
            "name": _first_nonempty(loc, _LOC_NAME_KEYS, "Unnamed location"),
            "description": _first_nonempty(loc, _LOC_DESC_KEYS, ""),
            "importance": _first_nonempty(loc, _LOC_IMPORTANCE_KEYS, ""),
            "aliases": _aliases(loc),
        }
        for loc in raw.get("locations") or []
        if isinstance(loc, dict)
    ]

    historical_figures = [
        {
            "name": _first_nonempty(h, _HIST_NAME_KEYS, "Unnamed historical figure"),
            "biography": _first_nonempty(h, _HIST_BIO_KEYS, ""),
            "role": _first_nonempty(h, _HIST_ROLE_KEYS, "")[:40],
            "importance_in_book": _first_nonempty(h, _HIST_IMPORTANCE_KEYS, ""),
            "context_in_book": _first_nonempty(h, _HIST_CONTEXT_KEYS, ""),
        }
        for h in raw.get("historical_figures") or []
        if isinstance(h, dict)
    ]
```

Die Namens-Platzhalter bleiben in dieser Task noch englisch und hartkodiert — Task 3 lokalisiert sie. `_NAME_FALLBACKS` ist danach unbenutzt: löschen. Ein `grep -rn "_NAME_FALLBACKS" .` muss leer sein.

Und den Docstring von `clean_response` um die Vorbedingung erweitern:

```python
    """Port of `validateAndCleanData`'s per-field defaulting (essentials).

    PRECONDITION: `raw`'s keys are already lower-cased by
    `gemini.normalize_keys` (`gemini.py:192`), exactly as Lua couples the two
    in `xray_aihelper.lua:1997`. Calling this with raw model output that
    skipped that step will silently miss upper-case keys.

    Nameless characters/locations are KEPT with a placeholder name
    (`xray_aihelper.lua:2015`) -- never dropped, so a character or place the
    AI described but couldn't name never silently disappears.
    """
```

- [ ] **Step 4: Run the full suite**

```bash
python3 -m pytest tests/ -q
grep -rn "_NAME_FALLBACKS" xray_core/ tests/
```

Erwartet: alle grün; `grep` findet nichts.

- [ ] **Step 5: Commit**

```bash
git add xray_core/merge.py tests/test_merge.py
git commit -m "fix(merge): restore Lua's alternative key chains in clean_response"
```

---

### Task 3: Lokalisierte Namens-Platzhalter

Nur die **Namen**. Inhaltsfelder bleiben leer — der Viewer blendet sie dann aus (`xray_ui.lua:190`, `:214`), während ein Platzhalter dort als sichtbarer Text erschiene.

Die Namens-Platzhalter dagegen **müssen** in `clean_response` gesetzt werden: `_merge` dedupliziert über `name`, und ein leerer Name kollidiert nie (`merge.py:193`). Zöge man sie ans Ende, würden mehrere namenlose Figuren nicht mehr zu einer verschmelzen — das täte weder Lua noch der heutige Code.

**Files:**
- Modify: `xray_core/merge.py` (Tabelle + `clean_response`-Signatur)
- Modify: `xray_core/generate.py` (`_fetch_with_retry`, `_enrich_checkpoint`)
- Test: `tests/test_merge.py`, `tests/test_generate.py`

**Interfaces:**
- Consumes: `clean_response` aus Task 2.
- Produces:
  - `fallback_strings(language: str) -> dict[str, str]` — unbekannte Sprache → englische Tabelle, nie `KeyError`.
  - `clean_response(raw: dict, language: str = "en") -> dict` — rückwärtskompatibel, bestehende Aufrufer bleiben gültig.

- [ ] **Step 1: Write the failing tests**

In `tests/test_merge.py` die Importzeile 1 ersetzen (kein Import mitten in der Datei):

```python
from xray_core.merge import (
    BookState,
    clean_response,
    fallback_strings,
    is_more_complete_name,
    sort_entity_list,
)
```

Dann anhängen:

```python
def test_fallback_strings_are_localized():
    # prompts/de.lua:361-364, prompts/en.lua:322-325
    assert fallback_strings("de")["unnamed_character"] == "Unbenannter Charakter"
    assert fallback_strings("en")["unnamed_character"] == "Unnamed Character"
    # Unbekannte Sprache faellt auf Englisch zurueck, nie auf KeyError.
    assert fallback_strings("fr")["unnamed_character"] == "Unnamed Character"


def test_clean_response_localizes_name_placeholders():
    de = clean_response({"characters": [{"role": "x"}]}, language="de")
    assert de["characters"][0]["name"] == "Unbenannter Charakter"

    de_loc = clean_response({"locations": [{"description": "d"}]}, language="de")
    assert de_loc["locations"][0]["name"] == "Unbekannter Ort"

    de_hist = clean_response({"historical_figures": [{"biography": "b"}]}, language="de")
    assert de_hist["historical_figures"][0]["name"] == "Unbenannte Person"


def test_clean_response_leaves_content_fields_empty():
    # Bewusste Divergenz zu Lua: der Viewer blendet leere Felder aus
    # (xray_ui.lua:190,214), ein Platzhalter waere sichtbares Rauschen.
    c = clean_response({"characters": [{"name": "A"}]}, language="de")["characters"][0]
    assert c["role"] == ""
    assert c["description"] == ""

    h = clean_response({"historical_figures": [{"name": "H"}]}, language="de")["historical_figures"][0]
    assert h["biography"] == ""
    assert h["importance_in_book"] == ""
    assert h["context_in_book"] == ""


def test_empty_field_is_still_fillable_by_a_later_segment():
    # Der Kern der Entscheidung: leere Felder lassen Luecken zuwachsen.
    state = BookState()
    state.merge_segment(clean_response({"characters": [{"name": "Alice"}]}, "de"), 10)
    state.merge_segment(
        clean_response({"characters": [{"name": "Alice", "occupation": "Forscherin"}]}, "de"), 50
    )

    assert state.characters[0]["occupation"] == "Forscherin"
```

In `tests/test_generate.py` anhängen. Die Datei hat bereits `_two_chapter_book`, `FakeClient` und `_ok` (Zeilen 45-79) — die werden benutzt, nichts Neues erfinden:

```python
def test_generated_snapshots_carry_localized_name_placeholders():
    ch1 = "Alice walks through the CH1MARKER village at dawn, greeting everyone she meets today. " * 5
    ch2 = "Bob arrives at the CH2MARKER harbor just as the tide turns for the evening light. " * 5
    book = _two_chapter_book(ch1, ch2)

    client = FakeClient([
        ("CH1MARKER", _ok({"characters": [{"description": "eine namenlose Gestalt"}]})),
        ("CH2MARKER", _ok({"characters": [{"name": "Bob"}]})),
    ])

    doc = generate_xray(book, client, "de", "normal")

    assert validate(doc) == []
    names = {c["name"] for c in doc["checkpoints"][-1]["snapshot"]["characters"]}
    assert "Unbenannter Charakter" in names
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_merge.py -k "fallback_strings or localize or fillable or content_fields" -v
```

Erwartet: `ImportError: cannot import name 'fallback_strings' from 'xray_core.merge'`.

- [ ] **Step 3: Write minimal implementation**

`xray_core/merge.py`, nach den Key-Tupeln aus Task 2 einfügen:

```python
# `unnamed_character` / `unnamed_person` verbatim from `prompts/en.lua:322-325`
# and `prompts/de.lua:361-364`. `unknown_place` is NOT in Lua's fallback table
# -- Lua hardcodes the English literal (`xray_aihelper.lua:2046`) even for
# German books; the German wording here is ours.
#
# Deliberate divergence: Lua also defaults role/description/biography to
# localized placeholders (`:2016`, `:2017`, `:2032`). We leave those empty.
# The device's viewer skips empty fields entirely (`xray_ui.lua:190`, `:214`,
# `:1080`), so a placeholder would only add visible noise to every card the
# model knew nothing about -- and a non-empty value would block a later
# segment from filling the gap.
_FALLBACKS = {
    "en": {
        "unnamed_character": "Unnamed Character",
        "unnamed_person": "Unnamed Person",
        "unknown_place": "Unknown Place",
    },
    "de": {
        "unnamed_character": "Unbenannter Charakter",
        "unnamed_person": "Unbenannte Person",
        "unknown_place": "Unbekannter Ort",
    },
}


def fallback_strings(language: str) -> dict:
    """Localized placeholder names; unknown languages fall back to English."""
    return _FALLBACKS.get(language, _FALLBACKS["en"])
```

`clean_response` bekommt den Sprachparameter. Signatur und erste Zeile:

```python
def clean_response(raw: dict, language: str = "en") -> dict:
```

Direkt nach dem Docstring:

```python
    strings = fallback_strings(language)
```

Und die drei Namens-Zeilen aus Task 2 ersetzen:

```python
            "name": _first_nonempty(c, _CHAR_NAME_KEYS, strings["unnamed_character"]),
...
            "name": _first_nonempty(loc, _LOC_NAME_KEYS, strings["unknown_place"]),
...
            "name": _first_nonempty(h, _HIST_NAME_KEYS, strings["unnamed_person"]),
```

`xray_core/generate.py` — in `_fetch_with_retry`, im Nicht-Split-Zweig:

```python
    if not result.truncated or depth >= _MAX_SPLIT_DEPTH:
        return clean_response(result.data, language)
```

und in `_enrich_checkpoint`:

```python
    cleaned = clean_response(result.data, language)
```

- [ ] **Step 4: Zwei bestehende Tests anpassen und Suite laufen**

**Beide** Tests prüfen heute die alten englischen Strings und müssen auf die Lua-Werte gehen. Das ist die beabsichtigte Änderung, kein Kollateralschaden:

```python
def test_clean_keeps_nameless_with_placeholder_and_truncates_role():
    raw = {
        "characters": [{"role": "x" * 50, "description": "a mystery"}],
        "locations": [{"description": "somewhere dark"}],
    }

    cleaned = clean_response(raw)

    assert cleaned["characters"][0]["name"] == "Unnamed Character"
    assert cleaned["characters"][0]["role"] == "x" * 40
    assert cleaned["locations"][0]["name"] == "Unknown Place"


def test_clean_location_never_uses_character_name_chain():
    # aus Task 2, Platzhalter ist jetzt lokalisiert
    cleaned = clean_response({"locations": [{"full_formal_name": "Lord Farquaad"}]})
    assert cleaned["locations"][0]["name"] == "Unknown Place"
```

```bash
python3 -m pytest tests/ -q
```

Erwartet: alle grün, **einschließlich** `test_golden_equality` — das Fixture hat keine namenlose Entität, das Golden bleibt byte-identisch.

- [ ] **Step 5: Commit**

```bash
git add xray_core/merge.py xray_core/generate.py tests/test_merge.py tests/test_generate.py
git commit -m "feat(merge): localized name placeholders; content fields stay empty"
```

---

### Task 4: `role` wird vom neuesten nicht-leeren Wert überschrieben

**Files:**
- Modify: `xray_core/merge.py:240-264` (`merge_segment`)
- Test: `tests/test_merge.py`

**Interfaces:**
- Consumes: `BookState.merge_segment` aus Task 3.
- Produces: keine Signaturänderung. `role` verhält sich für `characters` und `historical_figures` wie `description`.

- [ ] **Step 1: Write the failing tests**

```python
def test_role_is_overwritten_by_newest_nonempty_value():
    # xray_fetch.lua:587 -- existing_char.role = new_char.role
    state = BookState()
    state.merge_segment(clean_response({"characters": [{"name": "Franz", "role": "Protagonist"}]}), 10)
    state.merge_segment(clean_response({"characters": [{"name": "Franz", "role": "Trauerredner"}]}), 50)

    assert state.characters[0]["role"] == "Trauerredner"


def test_character_role_survives_a_segment_that_omits_it():
    # Bewusste Divergenz: Lua wuerde hier mit dem Platzhalter ueberschreiben.
    state = BookState()
    state.merge_segment(clean_response({"characters": [{"name": "Franz", "role": "Protagonist"}]}), 10)
    state.merge_segment(clean_response({"characters": [{"name": "Franz", "description": "d"}]}), 50)

    assert state.characters[0]["role"] == "Protagonist"


def test_historical_figure_role_also_newest_wins():
    # xray_fetch.lua:660 -- existing_fig.role = new_fig.role
    state = BookState()
    state.merge_segment(clean_response({"historical_figures": [{"name": "Cäsar", "role": "Feldherr"}]}), 10)
    state.merge_segment(clean_response({"historical_figures": [{"name": "Cäsar", "role": "Diktator"}]}), 50)

    assert state.historical_figures[0]["role"] == "Diktator"


def test_historical_figure_role_survives_a_segment_that_omits_it():
    # Hier ist die Divergenz am schaerfsten: Lua defaultet Hist-role auf ""
    # (xray_aihelper.lua:2033) und ueberschreibt bedingungslos
    # (xray_fetch.lua:660) -- es kann eine bekannte Rolle also loeschen.
    state = BookState()
    state.merge_segment(clean_response({"historical_figures": [{"name": "Cäsar", "role": "Feldherr"}]}), 10)
    state.merge_segment(clean_response({"historical_figures": [{"name": "Cäsar", "biography": "b"}]}), 50)

    assert state.historical_figures[0]["role"] == "Feldherr"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_merge.py -k role -v
```

Erwartet: `test_role_is_overwritten_by_newest_nonempty_value` und `test_historical_figure_role_also_newest_wins` FAIL (behalten `"Protagonist"` bzw. `"Feldherr"`). Die beiden `survives`-Tests sind schon grün — sie sichern das Verhalten gegen die Umstellung ab und dürfen danach nicht kippen.

- [ ] **Step 3: Write minimal implementation**

`xray_core/merge.py`, in `merge_segment`:

```python
        self._merge(
            self.characters, cleaned.get("characters") or [],
            # `role` newest-wins per xray_fetch.lua:587. Divergence: Lua
            # overwrites unconditionally; since we no longer default `role`
            # to a placeholder, an unconditional overwrite would let a
            # segment that never mentions the role erase a known one.
            newest_wins=("description", "role"), fill_if_empty=("gender", "occupation"),
            stamp=True, checkpoint_pct=checkpoint_pct,
        )
```

und:

```python
        self._merge(
            self.historical_figures, cleaned.get("historical_figures") or [],
            # xray_fetch.lua:660. Same non-empty guard -- and here Lua really
            # can blank a role: it defaults hist `role` to "" (:2033) and
            # overwrites regardless. We keep the known value instead.
            newest_wins=("biography", "role"),
            fill_if_empty=("importance_in_book", "context_in_book"),
            stamp=False, checkpoint_pct=checkpoint_pct,
        )
```

- [ ] **Step 4: Run the full suite**

```bash
python3 -m pytest tests/ -q
```

Erwartet: alle grün, `test_golden_equality` inklusive — im Fixture kommt keine Entität in zwei Segmenten mit unterschiedlicher `role` vor. Genau das ändert Task 6.

- [ ] **Step 5: Commit**

```bash
git add xray_core/merge.py tests/test_merge.py
git commit -m "fix(merge): role is newest-non-empty-wins for characters and historical figures"
```

---

### Task 5: Schema-Härtung in `schema.py`

Ein Audit fand: `schema.py` prüft `timeline`, den Elementtyp von `authors` und den Typ von `chapter_anchor` gar nicht; negative `first_pct`/`first_seq` und Duplikatnamen rutschen durch beide Vertrags-Kopien. Nur strenger, kein Formatwechsel.

**Files:**
- Modify: `xray_core/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Consumes: nichts.
- Produces: `validate(doc) -> list[str]` — Signatur unverändert, nur mehr Meldungen.

- [ ] **Step 1: Write the failing tests**

`tests/test_schema.py` nutzt die `minimal_doc`-Fixture aus `tests/conftest.py`. Anhängen:

```python
def test_valid_doc_still_passes(minimal_doc):
    assert validate(minimal_doc) == []


def test_timeline_entries_are_validated(minimal_doc):
    minimal_doc["timeline"] = [{"chapter": "K1", "event": "e", "pct": 150}]
    assert any("timeline[0].pct" in p for p in validate(minimal_doc))

    minimal_doc["timeline"] = ["kein objekt"]
    assert any("timeline[0]" in p for p in validate(minimal_doc))

    minimal_doc["timeline"] = [{"chapter": "K1", "event": "e"}]
    assert any("pct" in p for p in validate(minimal_doc))


def test_authors_must_be_strings(minimal_doc):
    minimal_doc["book_fingerprint"]["authors"] = ["ok", 42]
    assert any("authors[1]" in p for p in validate(minimal_doc))


def test_chapter_anchor_type_is_checked(minimal_doc):
    minimal_doc["checkpoints"][0]["chapter_anchor"] = "Kapitel 12"
    assert any("chapter_anchor" in p for p in validate(minimal_doc))

    minimal_doc["checkpoints"][0]["chapter_anchor"] = None
    assert validate(minimal_doc) == []  # null ist erlaubt


def test_negative_first_pct_and_seq_are_rejected(minimal_doc):
    minimal_doc["checkpoints"][0]["snapshot"]["characters"][0]["first_pct"] = -1
    assert any("first_pct" in p for p in validate(minimal_doc))

    minimal_doc["checkpoints"][0]["snapshot"]["characters"][0]["first_pct"] = 12
    minimal_doc["checkpoints"][0]["snapshot"]["characters"][0]["first_seq"] = 0
    assert any("first_seq" in p for p in validate(minimal_doc))


def test_duplicate_names_in_a_snapshot_list_are_rejected(minimal_doc):
    chars = minimal_doc["checkpoints"][0]["snapshot"]["characters"]
    chars.append(dict(chars[0], first_seq=2))
    assert any("duplicate" in p.lower() for p in validate(minimal_doc))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_schema.py -v
```

Erwartet: `test_valid_doc_still_passes` grün, die anderen sechs FAIL.

- [ ] **Step 3: Write minimal implementation**

`xray_core/schema.py`, in `validate()` unmittelbar vor `return problems`:

```python
    if isinstance(fingerprint, dict) and isinstance(fingerprint.get("authors"), list):
        problems.extend(
            f"book_fingerprint.authors[{i}] must be a string"
            for i, a in enumerate(fingerprint["authors"])
            if not isinstance(a, str)
        )

    timeline = doc.get("timeline")
    if isinstance(timeline, list):
        problems.extend(_validate_timeline(timeline))
```

Neue Funktion am Dateiende:

```python
def _validate_timeline(timeline: list) -> list[str]:
    """The device reads `timeline` top-level and gates each event on `pct`
    (`xray_import.lua:326-336`); an event without a valid `pct` is silently
    hidden there. Catch it here instead of shipping data the reader never sees."""
    problems: list[str] = []
    for i, ev in enumerate(timeline):
        label = f"timeline[{i}]"
        if not isinstance(ev, dict):
            problems.append(f"{label} must be an object")
            continue
        for field in ("chapter", "event"):
            if not isinstance(ev.get(field), str):
                problems.append(f"{label}.{field} must be a string")
        pct = ev.get("pct")
        if not _is_strict_int(pct) or not (0 <= pct <= 100):
            problems.append(f"{label}.pct must be an int between 0 and 100")
    return problems
```

In `_validate_checkpoints`, nach dem `snippet_anchor`-Block (nach Zeile 118):

```python
        anchor = cp.get("chapter_anchor")
        if anchor is not None:
            if not isinstance(anchor, dict):
                problems.append(f"{label}.chapter_anchor must be an object or null")
            else:
                if not isinstance(anchor.get("toc_title"), str):
                    problems.append(f"{label}.chapter_anchor.toc_title must be a string")
                spine_index = anchor.get("spine_index")
                if not _is_strict_int(spine_index) or spine_index < 0:
                    problems.append(
                        f"{label}.chapter_anchor.spine_index must be a non-negative int"
                    )
```

Im Snapshot-Listen-Loop, direkt nach dem `isinstance(entries, list)`-Check und **vor** dem `_CHRONOLOGY_LISTS`-Block:

```python
            seen_names = set()
            for j, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                key = (entry.get("name") or "").strip().lower()
                if key and key in seen_names:
                    problems.append(
                        f"{label}.snapshot.{list_name}[{j}] duplicate name: {entry['name']!r}"
                    )
                seen_names.add(key)
```

In `_validate_chronology_entry` den `first_pct`/`first_seq`-Block (Zeilen 157-159) ersetzen:

```python
    for field, minimum in (("first_pct", 0), ("first_seq", 1)):
        if field not in entry:
            continue
        value = entry[field]
        if not _is_strict_int(value):
            problems.append(f"{label}.{field} must be an int")
        elif value < minimum:
            problems.append(f"{label}.{field} must be >= {minimum}")
```

- [ ] **Step 4: Suite und echte Daten**

```bash
python3 -m pytest tests/ -q
python3 -c "
import json
from xray_core.schema import validate
for path in ('/tmp/xray.json', 'tests/golden/xray_golden.json'):
    print(path, '->', validate(json.load(open(path))) or 'sauber')"
```

Erwartet: alle grün, und **beide** Dokumente sauber. Meldet der Validator dort etwas, ist entweder die neue Regel zu streng oder es gibt einen echten Datenfehler — die Regel **nicht** aufweichen, ohne die Ursache verstanden zu haben.

- [ ] **Step 5: Commit**

```bash
git add xray_core/schema.py tests/test_schema.py
git commit -m "feat(schema): validate timeline, authors, chapter_anchor, bounds and duplicate names"
```

---

### Task 6: E2E-Fixture deckt die vier Fixes ab, Golden neu erzeugen

Ohne diese Task bleibt das Golden byte-identisch, und die Tasks 1–4 haben **keine** Integrationsabdeckung. Ein Tippfehler in einer Fallback-Kette bliebe unbemerkt.

**Files:**
- Modify: `tests/test_e2e.py` (Fixture `_CHAPTERS` / `_fake_client`)
- Modify: `tests/golden/xray_golden.json`

**Interfaces:**
- Consumes: alles aus Tasks 1–5.
- Produces: ein Golden, das das neue Verhalten einfriert.

**Nicht ins Fixture aufnehmen:** einen leeren TOC-Titel. Er würde die Checkpoint-Zahl verschieben und die bestehende `test_d4_*`-Familie destabilisieren. Task 1 ist dafür unit-getestet.

- [ ] **Step 1: `tests/test_e2e.py` lesen, dann Fixture erweitern**

Erst die Datei ganz lesen — besonders `_CHAPTERS` und `_fake_client`. Dann in den kanonischen Antworten des Fake-Clients ergänzen, ohne bestehende Entitäten anzufassen:

1. **Namenlose Figur** in einem mittleren Kapitel: `{"description": "eine Gestalt im Regen"}` (kein `name`) → erwartet im Snapshot als `Unnamed Character`, da das Fixture `language="en"` fährt.
2. **Ort nur über `place`**: `{"place": "Harborside", "desc": "the old docks"}` → erwartet `name == "Harborside"`, `description == "the old docks"`.
3. **`role`-Wechsel**: eine Figur, die in einem frühen Kapitel `{"name": "Miriam", "role": "innkeeper"}` liefert und in einem späteren `{"name": "Miriam", "role": "spymaster"}` → im letzten Snapshot `spymaster`, im frühen Snapshot `innkeeper` (das ist zugleich ein D4-Beleg).
4. **`role`-Auslass**: dieselbe Figur in einem noch späteren Kapitel ohne `role`, aber mit neuer `description` → `role` bleibt `spymaster`.
5. **Timeline-Ereignis ohne Kapitel**: `{"chapter": "", "event": "sollte verschwinden"}` → taucht im Golden **nicht** auf.

- [ ] **Step 2: Explizite Assertions schreiben, nicht nur aufs Golden verlassen**

Ein Golden-Diff sagt „etwas hat sich geändert", nicht „das Richtige ist passiert". In `tests/test_e2e.py` anhängen:

```python
def test_fixture_exercises_name_placeholder(fixture_result):
    _, doc = fixture_result
    last = doc["checkpoints"][-1]["snapshot"]
    assert "Unnamed Character" in {c["name"] for c in last["characters"]}


def test_fixture_exercises_location_place_key(fixture_result):
    _, doc = fixture_result
    locs = {loc["name"]: loc for loc in doc["checkpoints"][-1]["snapshot"]["locations"]}
    assert "Harborside" in locs
    assert locs["Harborside"]["description"] == "the old docks"


def test_fixture_exercises_role_newest_wins_and_omission(fixture_result):
    _, doc = fixture_result
    cps = doc["checkpoints"]
    early = next(c for cp in cps for c in cp["snapshot"]["characters"] if c["name"] == "Miriam")
    last = next(c for c in cps[-1]["snapshot"]["characters"] if c["name"] == "Miriam")
    assert early["role"] == "innkeeper"   # frueher Snapshot: kein Zukunftswissen
    assert last["role"] == "spymaster"    # newest-non-empty gewinnt, Auslass loescht nicht


def test_fixture_drops_timeline_event_without_chapter(fixture_result):
    _, doc = fixture_result
    assert "sollte verschwinden" not in {ev["event"] for ev in doc["timeline"]}
```

- [ ] **Step 3: Tests gegen das ALTE Golden laufen (müssen rot sein)**

```bash
python3 -m pytest tests/test_e2e.py -q
```

Erwartet: die vier neuen Assertions grün (der Code kann es ja schon), `test_golden_equality` **rot** — das Golden kennt die neuen Entitäten noch nicht. Genau in dieser Reihenfolge: erst beweisen, dass das Verhalten stimmt, dann das Golden nachziehen. Niemals umgekehrt.

- [ ] **Step 4: Golden neu erzeugen und den Diff von Hand lesen**

Der Einzeiler steht im Docstring von `tests/test_e2e.py` und ist der **einzige** Pfad, der diese Datei schreibt:

```bash
python3 -c "
import json, pathlib, sys, tempfile
sys.path.insert(0, 'tests')
from test_e2e import generate_fixture_doc
with tempfile.TemporaryDirectory() as d:
    _, doc = generate_fixture_doc(pathlib.Path(d))
    print(json.dumps(doc, indent=2, ensure_ascii=False))
" > tests/golden/xray_golden.json

git diff tests/golden/xray_golden.json
```

Erwartet werden **ausschließlich**: die neue namenlose Figur, der Ort `Harborside`, Miriam mit `innkeeper` in frühen und `spymaster` in späten Snapshots, und **kein** Timeline-Eintrag `"sollte verschwinden"`. **Nicht** erwartet: geänderte `first_pct`/`first_seq` bestehender Entitäten, geänderte `percent`, geänderte Reihenfolge. Taucht so etwas auf: stoppen, Ursache suchen, nicht committen.

- [ ] **Step 5: Volle Suite, nichts abgewählt**

```bash
python3 -m pytest tests/ -q
```

Erwartet: alle grün, inklusive `test_golden_equality` und der gesamten `test_d4_*`-Familie.

- [ ] **Step 6: D4 an echten Daten gegenprüfen**

Der Chunk-Cache in `/tmp/xray-cache` erlaubt einen vollen Lauf ohne HTTP-Call:

```bash
GEMINI_API_KEY="$(cat ~/.config/calibre-xray/gemini_key)" python3 -m xray_core /tmp/test.epub --language de --workdir /tmp/xray-cache --json-out /tmp/xray_new.json
python3 -c "
import json
from xray_core.schema import validate
d = json.load(open('/tmp/xray_new.json'))
print('validate:', validate(d) or 'sauber')
cps = d['checkpoints']
leaks = [(cp['percent'], e['name']) for cp in cps for L in ('characters','locations')
         for e in cp['snapshot'][L] if e['first_pct'] > cp['percent']]
print('D4-Leaks:', leaks or 'keine')"
```

> **Drei Einschränkungen, die im Abschlussbericht stehen müssen:**
> 1. Der Cache speichert **bereits bereinigte** Chunks. Der Lauf prüft Task 1, 4 und 5 an echten Daten — die Fallback-Ketten (Task 2) und die Namens-Platzhalter (Task 3) werden **umgangen**, weil `clean_response` nicht mehr läuft. Deren Absicherung leisten Unit- und E2E-Tests.
> 2. `__main__.py` verlangt einen API-Key **auch bei vollständigem Cache**. Der Befehl liest ihn deshalb aus der Datei. Der Key darf **nie** ausgegeben werden.
> 3. „Ohne API-Call" gilt nur, solange Task 1 den Checkpoint-Plan dieses Buches nicht verschiebt. Für „Wackelkontakt" verifiziert: 0 leere TOC-Titel, Checkpoints unverändert `[13, 26, 40, 53, 65, 76, 88, 100]`.

- [ ] **Step 7: Commit**

```bash
git add tests/test_e2e.py tests/golden/xray_golden.json
git commit -m "test(e2e): fixture covers the four parity fixes; regenerate golden"
```

---

### Task 7: Doku auf den Stand des Codes bringen

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/2026-07-09-calibre-xray-desktop-generation-design.md`
- Modify: `tools/build_plugin.py` (Docstring, Zeilen 6-9)

**Interfaces:** keine.

- [ ] **Step 1: `CLAUDE.md` — Divergenzen festhalten**

Unter „Regeln" einfügen:

```markdown
- **Bewusste Divergenzen vom Lua** (jede ist im Code kommentiert):
  - **Keine Inhalts-Platzhalter.** `role`/`description`/`biography`/`importance_in_book`/
    `context_in_book` bleiben leer; Lua brennt `"Not Specified"`/`"No Description"` ein
    (`xray_aihelper.lua:2016-2032`). Der Viewer blendet leere Felder aus (`xray_ui.lua:190`,
    `:214`), ein Platzhalter wäre sichtbares Rauschen — und würde spätere Ergänzungen blockieren.
  - **Namens**-Platzhalter dagegen sind lokalisiert und werden in `clean_response` gesetzt;
    `_merge` dedupliziert über `name`, ein leerer Name kollidiert nie.
  - `role` gewinnt vom neuesten **nicht-leeren** Wert; Lua überschreibt bedingungslos
    (`xray_fetch.lua:587`, `:660`) und kann eine bekannte Rolle mit einem leeren Wert löschen.
  - Trunkierung (`role[:40]`) schneidet nach Zeichen, Lua nach Bytes — Python zerschneidet
    nie einen UTF-8-Codepoint.
  - Terms vereinigen Aliase, statt sie zu überschreiben (`xray_fetch.lua:737`).
- **`clean_response` erwartet normalisierte Schlüssel** (`gemini.normalize_keys`), genau wie
  Lua die beiden koppelt (`xray_aihelper.lua:1997`). Ein Aufrufer, der das überspringt,
  verliert stillschweigend Felder.
- **`schema.py` ist der verlässliche Vertrag**, nicht `schema/xray.schema.json`: die D4-Regel
  `first_pct <= checkpoint.percent` ist eine Cross-Field-Bedingung, die JSON Schema nicht
  ausdrücken kann. Wer den Vertrag prüft, prüft `schema.py`.
```

- [ ] **Step 2: Design-Spec korrigieren**

In `docs/2026-07-09-calibre-xray-desktop-generation-design.md` die Stelle, die `events` **pro Checkpoint im Snapshot** zeichnet (um Zeile 80), auf den implementierten Stand bringen: `timeline` liegt **top-level** und trägt pro Ereignis ein `pct`; der KOReader-Importer (`xray_import.lua:326-336`) mappt `pct → Seite` und blendet Ereignisse ohne gültiges `pct` aus. Ein Satz Begründung, warum das D4 nicht verletzt: das Gerät staffelt.

- [ ] **Step 3: `tools/build_plugin.py`-Docstring richtigstellen**

Die Zeilen 6-9 behaupten, `xray_core.generate` lese `VERSION` zur Laufzeit als Zip-Root-Geschwister. Das ist falsch: calibre lädt Plugins per Zip-Importer, `Path(__file__).parent.parent` landet auf der Zip-Datei selbst, und `_generator_version()` fällt über `except OSError` auf die hartkodierte `"0.1.0"` zurück. Ersetzen durch eine Beschreibung, die das sagt, plus den Hinweis, dass ein Versions-Bump **vier** Stellen berührt: `VERSION`, `XRayGeneratorPlugin.version` (`calibre_plugin/__init__.py:39`), den README-Badge und die Fallback-Konstante in `generate.py:239`.

- [ ] **Step 4: Verifizieren**

```bash
python3 -m pytest tests/ -q
python3 tools/build_plugin.py
```

Erwartet: Tests grün, Zip baut.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/2026-07-09-calibre-xray-desktop-generation-design.md tools/build_plugin.py
git commit -m "docs: record deliberate Lua divergences, fix stale spec and build docstring"
```

---

## Was dieser Plan bewusst NICHT tut

- **Kein `schema_version`-Bump, keine Änderung an `schema/xray.schema.json`.** Die JSON-Schema-Kopie bleibt schwächer als `schema.py`. Das wird dokumentiert (Task 7), nicht behoben — sie zu verschärfen ginge nur mit Cross-Field-Konstrukten, die draft-07 nicht kennt.
- **Kein Fix für den Plugin-Temp-Leak** in `calibre_plugin/ui.py` (verwaiste `.epub`, wenn `embed_xray` nach `mkstemp` wirft; `workdir` bleibt bei permanenten Fehlern liegen). Vom Nutzer aus dem Umfang genommen.
- **Kein Test für den `detailed`/Phase-C-Pfad an echten Daten.** Er ist nie gelaufen, und genau dort saß der ursprüngliche Spoiler-Leak. Bleibt offener Punkt.
- **Kein Versions-Bump und kein Tag.** CLAUDE.md sperrt das bis zur End-to-End-Verifikation mit dem KOReader-Importer.
- **Kein leerer TOC-Titel im E2E-Fixture.** Er würde die Checkpoint-Planung des Fixtures verschieben; Task 1 ist unit-getestet.
