import pytest


@pytest.fixture
def minimal_doc():
    """A minimal valid xray.json v1 document (fresh dict per test).

    One checkpoint, one character; last checkpoint's percent matches
    last_percent/complete so this passes validate() unmodified. Later tasks
    reuse this fixture too — keep it in sync with schema/xray.schema.json.
    """
    return {
        "schema_version": 1,
        "generator": "calibre-xray",
        "generator_version": "0.1.0",
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
                "snippet_anchor": (
                    "Am Ende der Reise kehrte sie zurueck und wusste, dass "
                    "nichts mehr so sein wuerde wie zuvor."
                ),
                "chapter_anchor": {"toc_title": "Kapitel 12", "spine_index": 11},
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
