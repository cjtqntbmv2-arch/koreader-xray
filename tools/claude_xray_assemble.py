"""Assembler for the Claude-backed X-Ray extraction skill.

Reads subagent-produced chunk_<cp>_<idx>.raw.json, cleans them into the
chunk cache generate_xray reads, then merges and writes the deliverables.
Stdlib + xray_core only.
"""
import argparse
import json
import os

from xray_core.embed import embed_xray
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


def assemble(epub_path, workdir, out_dir, embed_append=False, title=None):
    base = os.path.basename(epub_path)
    final_path = os.path.join(out_dir, base)
    if os.path.realpath(final_path) == os.path.realpath(epub_path):
        raise SystemExit(
            "assemble aborted -- --out resolves to the source EPUB's own "
            f"directory ({os.path.abspath(epub_path)!r}). The embedded copy "
            "would overwrite (and truncate) the source EPUB while it is "
            "still being read. Pass a different --out directory."
        )

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

    # The KOReader importer gates on title (book_fingerprint.title vs the OPF
    # title of the book as it lands on-device). calibre rewrites the OPF title
    # to its LIBRARY title on send, which can differ from the raw EPUB OPF title
    # generate_xray read -- so allow overriding it with calibre's library title.
    if title:
        doc["book_fingerprint"]["title"] = title

    os.makedirs(out_dir, exist_ok=True)
    raw_json = os.path.join(out_dir, "xray.json")
    with open(raw_json, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    # companion: byte-identical to xray.json, append-form name (cross-repo contract)
    companion = os.path.join(out_dir, base + ".xray.json")
    with open(companion, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    # embedded copy: identical original filename, source untouched. append=True
    # leaves the source's head bytes intact so KOReader's partialMD5 (its
    # statistics/progress key) survives replacing the file on-device -- at the
    # cost of OPF-manifest registration (does not survive calibre Convert Book).
    embed_xray(epub_path, doc, final_path, append=embed_append, title=title)
    return doc


def main(argv=None):
    p = argparse.ArgumentParser(prog="claude_xray_assemble")
    p.add_argument("book")
    p.add_argument("--workdir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument(
        "--embed-mode", choices=["full", "append"], default="full",
        help="full (default): register xray in the OPF, survives calibre Convert Book. "
             "append: leave the source bytes untouched so KOReader reading statistics "
             "survive replacing the file on-device (does not survive Convert Book).",
    )
    p.add_argument(
        "--title",
        help="Align BOTH book_fingerprint.title and the embedded EPUB's OPF "
             "<dc:title> to this value (full embed mode). The importer gates on "
             "title (fingerprint vs the on-device OPF title); calibre rewrites the "
             "OPF to its LIBRARY title on send, which often differs from the EPUB's "
             "own OPF title. Pass calibre's library title so all three agree and "
             "the data is not rejected as 'does not match this book'.",
    )
    args = p.parse_args(argv)
    assemble(args.book, args.workdir, args.out,
             embed_append=args.embed_mode == "append", title=args.title)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
