"""Planner for the Claude-backed X-Ray extraction skill.

Computes the SAME chunks generate_xray computes (by importing its functions,
never reimplementing), and emits one prompt file per chunk plus a manifest.
Stdlib + xray_core only.
"""
import argparse
import json
import os

from xray_core.checkpoints import plan_checkpoints
from xray_core.epub import read_epub
from xray_core.generate import _chunk_segment
from xray_core.prompts import build_prompt

SELF_GLEAN_LINE = (
    "\n\nAFTER you have listed the entities, RE-SCAN the BOOK TEXT CONTEXT once "
    "more specifically for any character who speaks or acts but that you did not "
    "yet list -- especially minor and single-scene figures -- and ADD them. Do "
    "not omit anyone. Then output the final combined JSON object only."
)


def plan_chunks(book):
    cps = plan_checkpoints(book)
    chunks = []
    prev = 0
    for cp_idx, cp in enumerate(cps):
        segment = book.full_text[prev:cp.offset]
        for chunk_idx, text in enumerate(_chunk_segment(segment)):
            chunks.append({
                "cp_idx": cp_idx, "chunk_idx": chunk_idx,
                "percent": cp.percent, "text": text,
            })
        prev = cp.offset
    return chunks


def write_plan(epub_path, detail_level, workdir):
    book = read_epub(epub_path)
    os.makedirs(workdir, exist_ok=True)
    author = ", ".join(book.authors)
    chunks_meta = []
    for c in plan_chunks(book):
        cp_idx, chunk_idx, percent = c["cp_idx"], c["chunk_idx"], c["percent"]
        system, user = build_prompt(
            book.language, detail_level, book.title, author, percent, c["text"], mode="extract"
        )
        prompt_text = system + "\n\n" + user + SELF_GLEAN_LINE
        prompt_file = f"chunk_{cp_idx}_{chunk_idx}.prompt.txt"
        raw_file = f"chunk_{cp_idx}_{chunk_idx}.raw.json"
        with open(os.path.join(workdir, prompt_file), "w", encoding="utf-8") as f:
            f.write(prompt_text)
        chunks_meta.append({
            "cp_idx": cp_idx, "chunk_idx": chunk_idx, "percent": percent,
            "prompt_file": prompt_file, "raw_file": raw_file,
        })
    manifest = {
        "book": {"title": book.title, "authors": book.authors,
                 "language": book.language, "text_hash": book.text_hash},
        "detail_level": detail_level,
        "chunks": chunks_meta,
    }
    manifest_path = os.path.join(workdir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest_path


def main(argv=None):
    p = argparse.ArgumentParser(prog="claude_xray_plan")
    p.add_argument("book")
    p.add_argument("--workdir", required=True)
    p.add_argument("--detail", choices=["normal", "detailed"], default="detailed")
    args = p.parse_args(argv)
    manifest_path = write_plan(args.book, args.detail, args.workdir)
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
