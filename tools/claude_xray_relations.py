"""Relations pass for the Claude-backed X-Ray skill.

Two subcommands mirroring the recap flow: `plan` turns a finished xray.json
into one prompt, a subagent writes the answer next to it, `fold` filters that
answer and writes it onto the document as a flat `relations` list.

One call per book, not one per stage. The edges never travel through the merge
-- they are derived from the finished document -- so `clean_response`,
`BookState._merge` and the chunk cache are all untouched by this feature. The
flip side is that the model sees the whole cast while generating, which means
D4 is NOT established here: it holds on the device, which shows an edge only
when both endpoints resolve inside the snapshot the reader can already see.
What this fold does is narrower and purely defensive -- drop anything that does
not resolve against the finished book at all, i.e. hallucinated names.

Because it runs after the document is written, existing books can be retrofitted
without re-fetching a single chunk of book text.

Stdlib + xray_core only.
"""
import argparse
import collections
import json
import os

from xray_core.prompts import MAX_RELATIONS_PER_FIGURE, build_relations_prompt
from xray_core.schema import validate

ANSWER_FILE = "relations.json"
PROMPT_FILE = "relations.prompt.txt"
MANIFEST_FILE = "relations_manifest.json"


def _text(value) -> str:
    """Strip, and treat a then-empty string as missing.

    Same rule as `_str`/`_first_nonempty` in xray_core/merge.py (project
    CLAUDE.md, "bewusste Divergenzen vom Lua"): bool("   ") is true in Python,
    so without stripping a whitespace-only name passes every truthiness check
    and then resolves to nothing at all.
    """
    return value.strip() if isinstance(value, str) else ""


def _last_snapshot(doc) -> dict:
    checkpoints = doc.get("checkpoints") or []
    if not checkpoints:
        return {}
    return checkpoints[-1].get("snapshot") or {}


def _resolver(doc) -> dict:
    """lowercased spelling -> canonical name, built from the LAST stage.

    Characters resolve by name AND alias; historical figures by name only,
    because `clean_response` builds that category without an `aliases` key at
    all (xray_core/merge.py, unlike the character branch). Offering an alias
    rule there would promise a match the data cannot support.

    Names are indexed before aliases, and `setdefault` keeps the first writer:
    a figure's own name must never be shadowed by another figure's alias.
    """
    snapshot = _last_snapshot(doc)
    index: dict[str, str] = {}

    for entry in snapshot.get("characters") or []:
        if isinstance(entry, dict):
            name = _text(entry.get("name"))
            if name:
                index.setdefault(name.lower(), name)
    for entry in snapshot.get("historical_figures") or []:
        if isinstance(entry, dict):
            name = _text(entry.get("name"))
            if name:
                index.setdefault(name.lower(), name)

    for entry in snapshot.get("characters") or []:
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("name"))
        if not name:
            continue
        for alias in entry.get("aliases") or []:
            alias = _text(alias)
            if alias:
                index.setdefault(alias.lower(), name)

    return index


def filter_relations(raw, doc):
    """Apply the filter chain. Returns (relations, warnings).

    Order matters, and one step in particular: **normalisation runs before**
    the self-edge, duplicate and cap checks. The prompt hands the model both
    names and aliases, so it will legitimately refer to one figure by two
    spellings. Normalising last -- as the first draft of the plan did -- lets
    "Ned"->X and "Eddard Stark"->X through as two distinct edges that are only
    then rewritten to the same name: the shipped document ends up with two
    contradictory edges for one pair, a figure carrying twice the cap, and
    "Ned" -> "Eddard Stark" surviving as a self-edge that draws the centre node
    as its own neighbour. Measured, with validate() green throughout.
    """
    resolve = _resolver(doc)
    kept = []
    seen = set()
    per_figure: collections.Counter = collections.Counter()

    for item in raw or []:
        if not isinstance(item, dict):
            continue
        source, target, label = (
            _text(item.get("from")), _text(item.get("to")), _text(item.get("label")))
        if not (source and target and label):
            continue

        canon_source = resolve.get(source.lower())
        canon_target = resolve.get(target.lower())
        if not canon_source or not canon_target:
            continue  # hallucinated or dropped by extraction
        if canon_source == canon_target:
            continue
        pair = (canon_source, canon_target)
        if pair in seen:
            continue
        if per_figure[canon_source] >= MAX_RELATIONS_PER_FIGURE:
            continue

        seen.add(pair)
        per_figure[canon_source] += 1
        kept.append({"from": canon_source, "to": canon_target, "label": label})

    # Unreciprocated edges are reported, never dropped: the edge is correct in
    # one figure's net, it is only missing from the other's. Silently passing
    # them makes the net asymmetric with nobody the wiser.
    warnings = [
        f"unreciprocated: {source} -> {target} has no counterpart edge "
        f"{target} -> {source}"
        for source, target in sorted(seen)
        if (target, source) not in seen
    ]
    return kept, warnings


def _refuse_on_drift(doc, manifest):
    """The answer is bound to the document it was planned against.

    Same bet as the recap fold and as assemble: a mismatching text_hash means
    these edges were derived from a different book, and their names would
    resolve against the wrong cast.
    """
    actual = (doc.get("book_fingerprint") or {}).get("text_hash") or ""
    expected = manifest.get("text_hash") or ""
    if expected and actual != expected:
        raise SystemExit(
            "relations fold aborted -- text_hash mismatch: this is not the "
            f"document the relations were planned against (document {actual!r}, "
            f"manifest {expected!r}). Re-run `relations plan`."
        )


def _parse_answer(text):
    """Tolerate the two shapes a subagent actually writes.

    A bare list instead of {"relations": [...]}, and a ```json fence around it.
    Being strict here would mean losing the whole pass after its budget is
    spent -- the same trap generate_xray's late validation has for the main run.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("relations") or []
    return []


def write_plan(doc, epub_path, workdir):
    """Write the single relations prompt plus the manifest fold reads."""
    os.makedirs(workdir, exist_ok=True)

    fingerprint = doc.get("book_fingerprint") or {}
    title = fingerprint.get("title") or ""
    author = ", ".join(a for a in (fingerprint.get("authors") or []) if a) or "unknown"
    language = doc.get("language") or "en"
    snapshot = _last_snapshot(doc)

    _system, prompt = build_relations_prompt(
        language, title, author,
        snapshot.get("characters") or [],
        snapshot.get("historical_figures") or [],
    )
    with open(os.path.join(workdir, PROMPT_FILE), "w", encoding="utf-8") as f:
        f.write(prompt)

    manifest = {
        "text_hash": fingerprint.get("text_hash") or "",
        # Not in the document -- assemble derives it from the EPUB path, and
        # globbing *.xray.json is unsafe because --out may be the book's own
        # directory (SKILL.md), which can hold several.
        "companion_name": os.path.basename(epub_path) + ".xray.json",
        "answer_file": ANSWER_FILE,
    }
    with open(os.path.join(workdir, MANIFEST_FILE), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def fold(doc, manifest, workdir):
    """Filter the written answer onto `doc`. Returns a list of warnings.

    Mutates `doc` in place. A missing answer file is not an error -- an
    interrupted wave simply leaves the document without the feature, which the
    device gates on field presence.
    """
    _refuse_on_drift(doc, manifest)

    path = os.path.join(workdir, manifest.get("answer_file") or ANSWER_FILE)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        raw = _parse_answer(f.read())

    relations, warnings = filter_relations(raw, doc)
    if relations:
        doc["relations"] = relations
    return warnings


def run_fold(doc_path, workdir, out_dir):
    """Fold, validate, and write both filenames. Returns (doc, warnings)."""
    doc = _read_doc(doc_path)
    with open(os.path.join(workdir, MANIFEST_FILE), encoding="utf-8") as f:
        manifest = json.load(f)

    warnings = fold(doc, manifest, workdir)

    problems = validate(doc)
    if problems:
        raise SystemExit(
            "relations fold aborted -- the folded document fails validation:\n  "
            + "\n  ".join(problems)
        )

    os.makedirs(out_dir, exist_ok=True)
    # Two names, same bytes -- as assemble and the recap fold do. The USB
    # companion route has no second validation after this point.
    payload = json.dumps(doc, ensure_ascii=False, indent=2)
    for name in ("xray.json", manifest["companion_name"]):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(payload)
    return doc, warnings


def _read_doc(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    p = argparse.ArgumentParser(prog="claude_xray_relations")
    sub = p.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="write the relations prompt for this book")
    plan.add_argument("book", help="path to the EPUB (used for the companion filename)")
    plan.add_argument("--doc", required=True, help="path to the generated xray.json")
    plan.add_argument("--workdir", required=True)

    fold_p = sub.add_parser("fold", help="filter the answer back into the document")
    fold_p.add_argument("--doc", required=True, help="path to the generated xray.json")
    fold_p.add_argument("--workdir", required=True)
    fold_p.add_argument("--out", required=True)

    args = p.parse_args(argv)
    if args.command == "plan":
        write_plan(_read_doc(args.doc), args.book, args.workdir)
        print(f"relations prompt written to {os.path.join(args.workdir, PROMPT_FILE)}")
    else:
        doc, warnings = run_fold(args.doc, args.workdir, args.out)
        print(f"{len(doc.get('relations') or [])} relations folded into {args.out}")
        for warning in warnings:
            print("  warning: " + warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
