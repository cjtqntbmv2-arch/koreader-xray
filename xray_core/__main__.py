"""calibre-free CLI: dev/power-user entry point, and the seam the calibre job
(Task 9) calls into. Stdlib-only on purpose (argparse, json, os, sys,
itertools, threading) -- see xray_core/epub.py.

    python3 -m xray_core BOOK.epub --api-key KEY [--model M] [--language de]
        [--detail normal|detailed] [--json-out xray.json] [--embed]
        [--workdir DIR]
"""

import argparse
import itertools
import json
import os
import sys
import threading

from xray_core.embed import embed_xray
from xray_core.epub import read_epub
from xray_core.gemini import GeminiClient
from xray_core.generate import generate_xray


def _fixture_transport(fixture_dir):
    """TEST-ONLY: round-robins canned raw Gemini response bodies read from
    `fixture_dir` instead of making real HTTP calls -- the seam behind
    --transport-fixture (and reused by later e2e tests, per the plan) that
    drives generate_xray() with zero network access. Files are served in
    name-sorted order as a GeminiClient transport response (200, <bytes>),
    cycling if there are fewer files than calls."""
    paths = sorted(
        os.path.join(fixture_dir, name) for name in os.listdir(fixture_dir)
    )
    if not paths:
        raise ValueError(f"--transport-fixture directory has no files: {fixture_dir}")
    lock = threading.Lock()
    remaining = itertools.cycle(paths)  # generate_xray fetches from a thread pool

    def transport(url, headers, body_bytes):
        with lock:
            path = next(remaining)
        with open(path, "rb") as f:
            return 200, f.read()

    return transport


def _progress(done, total):
    print(f"\r{done}/{total} chunks fetched", end="", file=sys.stderr, flush=True)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="xray_core", description="Generate xray.json for an EPUB (Gemini-backed)."
    )
    parser.add_argument("book", help="path to the EPUB file")
    # Env default so the key never has to appear in argv (visible to `ps`) or
    # in shell history. --api-key still wins when given explicitly.
    parser.add_argument("--api-key", default=os.environ.get("GEMINI_API_KEY"),
                        help="Gemini API key (default: $GEMINI_API_KEY)")
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--language", default="en")
    parser.add_argument("--detail", choices=["normal", "detailed"], default="normal")
    parser.add_argument("--json-out", default="xray.json", help="where to write xray.json")
    parser.add_argument("--embed", action="store_true", help="also embed xray.json into the EPUB")
    parser.add_argument("--workdir", default=None, help="per-chunk cache dir; resumes if present")
    parser.add_argument(
        "--transport-fixture", default=None,
        help="TEST-ONLY: read canned Gemini responses from this dir instead of the network",
    )
    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.api_key:
        parser.error("no API key: pass --api-key or set GEMINI_API_KEY")

    book = read_epub(args.book)
    transport = _fixture_transport(args.transport_fixture) if args.transport_fixture else None
    client = GeminiClient(args.api_key, model=args.model, transport=transport)

    doc = generate_xray(
        book, client, args.language, args.detail,
        progress_cb=_progress, workdir=args.workdir,
    )
    print(file=sys.stderr)  # newline after the last in-place progress update

    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    if args.embed:
        tmp_out = f"{args.book}.xray-tmp"
        embed_xray(args.book, doc, tmp_out)
        os.replace(tmp_out, args.book)  # atomic swap -- never a half-written book

    if not doc["complete"]:
        print(
            f"warning: xray.json is incomplete (stopped at {doc['last_percent']}% -- "
            "quota or an error interrupted generation; rerun with --workdir to resume)",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
