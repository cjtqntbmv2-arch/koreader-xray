import json
import os

import pytest

from xray_core.generate import _chunk_path, chunk_plan

_EMPTY_EXTRACT = {
    "characters": [], "locations": [], "historical_figures": [],
    "terms": [], "timeline": [],
}


def write_chunk_cache(book, workdir, language, detail_level, responses=()):
    """Write one chunk-result file per planned chunk, picking the first
    response whose needle occurs in that chunk's text; chunks matching nothing
    get an empty extraction.

    This replaces the FakeClient the tests used while generate_xray still
    drove a Gemini client: extraction now happens outside the pipeline and
    generate_xray only reads these files. Keying on a needle inside the chunk
    text is the same trick the fake client used (it matched a needle in the
    prompt, which embedded the chunk verbatim), so fixtures port over
    unchanged. The files hold raw extraction dicts -- generate_xray runs
    clean_response over them on load.
    """
    os.makedirs(workdir, exist_ok=True)
    for cp_idx, (_cp, chunk_list) in enumerate(chunk_plan(book)):
        for chunk_idx, text in enumerate(chunk_list):
            data = dict(_EMPTY_EXTRACT)
            for needle, response in responses:
                if needle in text:
                    data = response
                    break
            path = _chunk_path(workdir, cp_idx, chunk_idx, language, detail_level)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)


@pytest.fixture
def chunk_cache_writer():
    return write_chunk_cache


@pytest.fixture
def minimal_doc():
    """A minimal valid xray.json v2 document (fresh dict per test).

    One checkpoint, one character; last checkpoint's percent matches
    last_percent/complete so this passes validate() unmodified. Other tests
    reuse this fixture too -- keep it in sync with schema/xray.schema.json.
    """
    return {
        "schema_version": 2,
        "generator": "calibre-xray",
        "generator_version": "26.7.18",
        "detail_level": "normal",
        "language": "de",
        "book_fingerprint": {
            "calibre_uuid": "11111111-2222-3333-4444-555555555555",
            "title": "Die Beispielgeschichte",
            "authors": ["Autor Beispiel"],
            "text_hash": "sha256:" + "0" * 64,
        },
        "complete": True,
        "last_percent": 100,
        "book_type": "fiction",
        "timeline": [
            {"chapter": "Kapitel 1", "event": "Die Heldin bricht auf.", "pct": 12},
        ],
        "checkpoints": [
            {
                "percent": 100,
                "snapshot": {
                    "characters": [
                        {
                            "name": "Jane Doe",
                            "role": "protagonist",
                            "description": "Die Heldin der Geschichte.",
                            "gender": "female",
                            "occupation": "Forscherin",
                            "aliases": ["Janie"],
                            "first_pct": 12,
                            "first_seq": 1,
                        }
                    ],
                    "locations": [],
                    "terms": [],
                    "historical_figures": [],
                },
            }
        ],
    }
