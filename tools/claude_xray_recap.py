"""Recap pass for the Claude-backed X-Ray skill.

Two subcommands mirroring the extraction flow: `plan` turns a finished
xray.json into one prompt file per selected stage, subagents write the prose
next to them, `fold` folds that prose back into the document.

Prose as plain text, not JSON: a recap is 400 words of free-running text, and
routing it through JSON escaping buys nothing but a parse failure mode.

This pass is structurally the phase C that the 2026-07-25 rebuild removed, and
it carries phase C's original bug as its main risk: it must read the FROZEN
snapshot of each stage and never a living end-state. Everything here works off
the written document, where every snapshot is already frozen.

Stdlib + xray_core only.
"""
import argparse
import json
import os
import re

from xray_core.prompts import build_recap_prompt
from xray_core.schema import validate

# A novel carries ~57 stages. One recap each would be ~20k words of prose in a
# file the device unzips on e-ink hardware; the reader walks back to the newest
# recap at or below their position (XRayDoc.recap), so the gaps never show.
MAX_RECAPS = 12

# Below this, a "name" collides with ordinary words often enough that the scan
# would cost more good recaps than it saves.
MIN_SCANNED_NAME = 4


def _events_up_to(doc, percent):
    """The timeline cut for one stage -- the D4 boundary, in one place so the
    planner and the leak scan cannot drift apart."""
    return [ev for ev in doc.get("timeline") or []
            if isinstance(ev.get("pct"), int) and ev["pct"] <= percent]


def select_stages(doc):
    """Indices into doc["checkpoints"] that get a recap.

    The final stage is excluded on purpose: it is pinned at percent=100
    (generate.py) and the device's selectCheckpoint only reaches it at exactly
    100%, because its threshold is min(percent + MARGIN, 100). A recap there
    would be invisible for the whole book. `or stages[:1]` keeps a degenerate
    single-stage document from selecting nothing at all.
    """
    checkpoints = doc.get("checkpoints") or []
    stages = list(range(len(checkpoints)))
    candidates = stages[:-1] or stages[:1]
    if len(candidates) <= MAX_RECAPS:
        chosen = candidates
    else:
        last = len(candidates) - 1
        chosen = [candidates[round(i * last / (MAX_RECAPS - 1))] for i in range(MAX_RECAPS)]

    # A stage with no timeline events behind it has nothing to recap -- on a
    # real book the earliest one sits at 1%. Skipping costs nothing: the device
    # walks back and reports "no recap", which is the truthful answer there.
    return [i for i in chosen if _events_up_to(doc, checkpoints[i].get("percent") or 0)]


def _stage_names(stage_idx, percent):
    """Index AND percent in the filename: the index keeps names unique (two
    chunk stages can round to the same percent), the percent keeps them
    readable when someone looks into a workdir."""
    return {
        "prompt_file": f"recap_{stage_idx:03d}_{percent:03d}.prompt.txt",
        "out_file": f"recap_{stage_idx:03d}_{percent:03d}.txt",
    }


def write_plan(doc, epub_path, workdir):
    """Write one prompt per selected stage plus the manifest fold reads."""
    os.makedirs(workdir, exist_ok=True)

    fingerprint = doc.get("book_fingerprint") or {}
    title = fingerprint.get("title") or ""
    author = ", ".join(a for a in (fingerprint.get("authors") or []) if a) or "unknown"
    language = doc.get("language") or "en"

    stages = []
    for stage_idx in select_stages(doc):
        cp = doc["checkpoints"][stage_idx]
        percent = cp.get("percent") or 0

        # The D4 cut. build_recap_prompt takes what it is given -- a prompt
        # builder that filtered silently would hide where the guarantee lives.
        events = _events_up_to(doc, percent)
        characters = ((cp.get("snapshot") or {}).get("characters")) or []

        _system, prompt = build_recap_prompt(
            language, title, author, percent, events, characters)

        names = _stage_names(stage_idx, percent)
        with open(os.path.join(workdir, names["prompt_file"]), "w", encoding="utf-8") as f:
            f.write(prompt)
        stages.append({"stage_idx": stage_idx, "percent": percent, **names})

    manifest = {
        # Stage indices are a function of the chunk count, not of the book: the
        # same title at a different detail level renumbers them, and folding
        # recap_5 into a renumbered document would hang prose covering 0-67%
        # onto a stage that claims 51%. fold refuses when either of these two
        # no longer matches.
        "text_hash": fingerprint.get("text_hash") or "",
        # Nowhere in the document -- assemble derives it from the EPUB path,
        # and globbing *.xray.json is unsafe because --out may be the book's
        # own directory (SKILL.md), which can hold several.
        "companion_name": os.path.basename(epub_path) + ".xray.json",
        "stages": stages,
    }
    with open(os.path.join(workdir, "recap_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def _names_in(cp):
    """Every name and alias one stage's snapshot knows."""
    names = set()
    snapshot = cp.get("snapshot") or {}
    for list_name in ("characters", "locations", "terms", "historical_figures"):
        for entry in snapshot.get(list_name) or []:
            if not isinstance(entry, dict):
                continue
            name = (entry.get("name") or "").strip()
            if name:
                names.add(name)
            for alias in entry.get("aliases") or []:
                alias = (alias or "").strip()
                if alias:
                    names.add(alias)
    return names


def _supplied_material_text(doc, stage_idx, percent):
    """Everything the prompt fed this stage, as one blob to search against.

    Mirrors the cut write_plan makes -- both the timeline events up to this
    percent and the character block. Descriptions count as much as events:
    Bilbo's entry names Thorin Oakenshield at 16% of a real book, while Thorin
    only becomes an entity of his own many stages later. If the two ever drift
    apart, the scan starts discarding recaps for quoting their own material.
    """
    parts = [f"{ev.get('chapter') or ''} {ev.get('event') or ''}"
             for ev in _events_up_to(doc, percent)]
    snapshot = (doc.get("checkpoints") or [])[stage_idx].get("snapshot") or {}
    for entry in snapshot.get("characters") or []:
        if isinstance(entry, dict):
            parts.append(f"{entry.get('name') or ''} {entry.get('description') or ''}")
    return " ".join(parts)


def find_leaked_names(doc, stage_idx, text):
    """Proper names that exist only beyond `stage_idx` and appear in `text`.

    No test can decide semantically whether prose spoils a plot -- but the
    dominant, mechanically catchable leak class is a name the reader has not
    met yet, and that needs no model and no network to find.

    ponytail: heuristic with a known ceiling. It catches the named leak, not
    the paraphrased one ("the true heir turns out to be someone else"). False
    positives cost a good recap and look exactly like a real leak in the
    warning, so it errs conservative: word boundaries rather than substring
    (otherwise "Robb" fires inside "Robbers"), case-sensitive because these are
    proper nouns, and names under MIN_SCANNED_NAME characters skipped. If this
    proves too coarse in practice, the next step is a per-stage name list from
    the model rather than a text search.
    """
    checkpoints = doc.get("checkpoints") or []
    here = _names_in(checkpoints[stage_idx])
    later = set()
    for cp in checkpoints[stage_idx + 1:]:
        later |= _names_in(cp)

    # Whatever the prompt itself supplied is allowed, by definition -- the scan
    # is looking for what the model brought along from its own knowledge, not
    # for what it was handed. Timeline events are part of that material, and an
    # entity routinely appears in an event well before extraction records it as
    # a character: on a real book the Balrog shows up in a Moria event and only
    # lands in a snapshot several stages later. Without this the scan discards
    # a correct recap and reports it as a spoiler.
    supplied = _supplied_material_text(
        doc, stage_idx, checkpoints[stage_idx].get("percent") or 0)

    leaked = []
    for name in sorted(later - here):
        if len(name) < MIN_SCANNED_NAME:
            continue
        pattern = r"(?<!\w)" + re.escape(name) + r"(?!\w)"
        if re.search(pattern, supplied):
            continue
        if re.search(pattern, text):
            leaked.append(name)
    return leaked


def _refuse_on_drift(doc, manifest):
    """The recap files are bound to the document they were planned against.

    Stage indices follow the chunk count, not the book: the same title at a
    different detail level renumbers them, and folding stage 5's prose into a
    renumbered document would hang a recap covering 0-67% onto a stage that
    claims 51%. That is a D4 violation the name scan cannot see, so it is
    refused outright -- the same bet assemble makes on text_hash.
    """
    actual = (doc.get("book_fingerprint") or {}).get("text_hash") or ""
    expected = manifest.get("text_hash") or ""
    if expected and actual != expected:
        raise SystemExit(
            "recap fold aborted -- text_hash mismatch: this is not the document "
            f"the recaps were planned against (document {actual!r}, manifest "
            f"{expected!r}). Re-run `recap plan` and the subagent wave."
        )

    checkpoints = doc.get("checkpoints") or []
    for stage in manifest.get("stages") or []:
        idx = stage["stage_idx"]
        if idx >= len(checkpoints) or checkpoints[idx].get("percent") != stage["percent"]:
            raise SystemExit(
                f"recap fold aborted -- stage {idx} no longer sits at its planned "
                f"percent ({stage['percent']}). Stage indices follow the chunk "
                "count; re-run `recap plan` against this document."
            )


def fold(doc, manifest, workdir):
    """Fold each written recap into `doc`. Returns a list of warnings.

    Mutates `doc` in place. A recap the name scan rejects is DROPPED -- the key
    is left absent, never set to "". Dropping rather than aborting is
    deliberate: aborting would throw away a whole pass after its budget is
    spent, the same trap generate_xray's late validation has for the main run.
    """
    _refuse_on_drift(doc, manifest)

    warnings = []
    for stage in manifest.get("stages") or []:
        path = os.path.join(workdir, stage["out_file"])
        if not os.path.exists(path):
            continue  # interrupted wave; partial coverage is normal
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
        if not text:
            continue

        leaked = find_leaked_names(doc, stage["stage_idx"], text)
        if leaked:
            warnings.append(
                f"stage {stage['stage_idx']} ({stage['percent']}%): recap names "
                f"{', '.join(leaked)} -- dropped"
            )
            continue

        doc["checkpoints"][stage["stage_idx"]]["recap"] = text
    return warnings


def run_fold(doc_path, workdir, out_dir):
    """Fold, validate, and write both filenames. Returns (doc, warnings)."""
    doc = _read_doc(doc_path)
    with open(os.path.join(workdir, "recap_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)

    warnings = fold(doc, manifest, workdir)

    problems = validate(doc)
    if problems:
        raise SystemExit(
            "recap fold aborted -- the folded document fails validation:\n  "
            + "\n  ".join(problems)
        )

    os.makedirs(out_dir, exist_ok=True)
    # Two names, same bytes -- as assemble does. The USB companion route has no
    # second validation after this point, so both have to be written here.
    payload = json.dumps(doc, ensure_ascii=False, indent=2)
    for name in ("xray.json", manifest["companion_name"]):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(payload)
    return doc, warnings


def _read_doc(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    p = argparse.ArgumentParser(prog="claude_xray_recap")
    sub = p.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="write one recap prompt per selected stage")
    plan.add_argument("book", help="path to the EPUB (used for the companion filename)")
    plan.add_argument("--doc", required=True, help="path to the generated xray.json")
    plan.add_argument("--workdir", required=True)

    fold_p = sub.add_parser("fold", help="fold the written prose back into the document")
    fold_p.add_argument("--doc", required=True, help="path to the generated xray.json")
    fold_p.add_argument("--workdir", required=True)
    fold_p.add_argument("--out", required=True)

    args = p.parse_args(argv)
    if args.command == "plan":
        manifest = write_plan(_read_doc(args.doc), args.book, args.workdir)
        print(f"{len(manifest['stages'])} recap prompts written to {args.workdir}")
    else:
        doc, warnings = run_fold(args.doc, args.workdir, args.out)
        folded = sum(1 for cp in doc["checkpoints"] if cp.get("recap"))
        print(f"{folded} recaps folded into {args.out}")
        for warning in warnings:
            print("  warning: " + warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
