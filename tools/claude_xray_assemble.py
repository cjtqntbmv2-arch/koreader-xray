"""Assembler for the Claude-backed X-Ray extraction skill.

Reads subagent-produced chunk_<cp>_<idx>.raw.json, cleans them into the chunk
cache generate_xray reads, then merges and writes the document.

It deliberately does NOT embed the result into an EPUB any more. Embedding is
the calibre plugin's job, and the plugin does it with four checks the plain
call here never had (text_hash against the book, schema validation, zip
integrity plus byte round-trip, and partial_md5 before/after so a book never
silently loses its reading statistics). Two embedding paths for one job meant
the unchecked one was always a step away.

Stdlib + xray_core only.
"""
import argparse
import json
import os

from xray_core.epub import read_epub
from xray_core.generate import _chunk_path, generate_xray
from xray_core.merge import clean_response


def _load_manifest(workdir):
    with open(os.path.join(workdir, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def _precheck(workdir, manifest):
    """Every chunk's raw.json must exist AND parse. Fail loud listing offenders."""
    problems = []
    for ch in manifest["chunks"]:
        path = os.path.join(workdir, ch["raw_file"])
        key = f'({ch["cp_idx"]},{ch["chunk_idx"]})'
        if not os.path.exists(path):
            problems.append(f"{key} missing {ch['raw_file']}")
            continue
        try:
            with open(path, encoding="utf-8") as f:
                json.load(f)
        except (ValueError, OSError) as e:
            problems.append(f"{key} unparseable {ch['raw_file']}: {e}")
    if problems:
        raise SystemExit("assemble aborted -- incomplete/invalid chunk cache:\n  " +
                         "\n  ".join(problems))


def assemble(epub_path, workdir, out_dir):
    base = os.path.basename(epub_path)
    book = read_epub(epub_path)
    manifest = _load_manifest(workdir)
    if book.text_hash != manifest["book"]["text_hash"]:
        raise SystemExit(
            "assemble aborted -- text_hash mismatch: the EPUB has changed "
            f"since planning (book text_hash={book.text_hash!r}, manifest "
            f"text_hash={manifest['book']['text_hash']!r}). Re-run the "
            "planner against the current EPUB before assembling."
        )
    detail = manifest["detail_level"]
    _precheck(workdir, manifest)

    # raw.json -> clean_response -> the resume cache. _chunk_path keys the cache
    # by (language, detail) as well, so the write key MUST match generate_xray's
    # read key below (book.language, detail) or every chunk misses and refetches.
    for ch in manifest["chunks"]:
        with open(os.path.join(workdir, ch["raw_file"]), encoding="utf-8") as f:
            raw = json.load(f)
        cleaned = clean_response(raw, book.language)
        cache_path = _chunk_path(workdir, ch["cp_idx"], ch["chunk_idx"], book.language, detail)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cleaned, f)

    doc = generate_xray(book, book.language, detail, workdir)

    os.makedirs(out_dir, exist_ok=True)
    # Two names, same bytes. xray.json is what you hand to the calibre plugin;
    # "<book>.epub.xray.json" is the name the device plugin looks for beside a
    # book, so writing --out into the book's own directory is a valid (and
    # useful) way to deliver over USB. Nothing here can overwrite the source.
    payload = json.dumps(doc, ensure_ascii=False, indent=2)
    for name in ("xray.json", base + ".xray.json"):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(payload)
    return doc


def main(argv=None):
    p = argparse.ArgumentParser(prog="claude_xray_assemble")
    p.add_argument("book")
    p.add_argument("--workdir", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    assemble(args.book, args.workdir, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
