"""Tests for the xray_core CLI (Task 8): argparse plumbing, JSON output, and
the --transport-fixture test seam (also reused by later e2e tests, per the
plan) that drives generate_xray() with zero network access.
"""
import json
import sys

import pytest
from epub_fixture import build_epub

from xray_core import __main__ as cli
from xray_core.embed import read_embedded

_EMPTY_EXTRACTION = {
    "characters": [], "locations": [], "historical_figures": [],
    "terms": [], "timeline": [],
}


@pytest.fixture(autouse=True)
def _no_rate_limit_sleep(monkeypatch):
    """Same fix as tests/test_generate.py's fixture of the same name: the
    real RateLimiter paces ~6s apart (per_minute=10) *regardless of thread
    count* (it books one shared slot at a time), which would make a fixture
    -backed CLI run for many real seconds per checkpoint for no reason here."""
    monkeypatch.setattr("xray_core.generate.RateLimiter.acquire", lambda self: None)


def _canned_gemini_response(data, finish_reason="STOP"):
    """Raw Gemini API response envelope wrapping `data` as the model's JSON
    text -- the exact shape GeminiClient._parse_response expects (see
    tests/test_gemini.py's _ok_response)."""
    body = {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps(data)}]}, "finishReason": finish_reason}
        ]
    }
    return json.dumps(body).encode("utf-8")


def _write_fixture(tmp_path, data=_EMPTY_EXTRACTION):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "resp.json").write_bytes(_canned_gemini_response(data))
    return fixture_dir


def test_cli_json_out(tmp_path, monkeypatch):
    book = build_epub(tmp_path, [
        ("Chapter One", "<p>A short test chapter with enough words to anchor a snippet.</p>"),
    ])
    fixture_dir = _write_fixture(tmp_path)
    out_path = tmp_path / "xray.json"

    monkeypatch.setattr(sys, "argv", [
        "xray_core", str(book), "--api-key", "unused",
        "--json-out", str(out_path), "--transport-fixture", str(fixture_dir),
    ])

    exit_code = cli.main()

    assert exit_code == 0
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc["complete"] is True
    assert doc["language"] == "en"


def test_cli_exit_code_2_on_incomplete(tmp_path, monkeypatch, minimal_doc):
    """Not from the brief's named list, but the brief's own step 3 requires
    'exits 2 on partial (complete=False)' -- a distinct branch from the happy
    path above, so it gets its own test rather than shipping unverified."""
    book = build_epub(tmp_path, [("One", "<p>Hello world.</p>")])
    out_path = tmp_path / "xray.json"
    incomplete = {**minimal_doc, "complete": False}
    monkeypatch.setattr(cli, "generate_xray", lambda *a, **k: incomplete)

    exit_code = cli.main([str(book), "--api-key", "unused", "--json-out", str(out_path)])

    assert exit_code == 2
    assert json.loads(out_path.read_text(encoding="utf-8"))["complete"] is False


def test_cli_embed_flag_embeds_into_book(tmp_path):
    """Not from the brief's named list either -- added during self-review:
    embed_xray() itself is thoroughly covered by test_embed.py, but nothing
    else exercised the CLI's own --embed wiring (temp-file + os.replace back
    onto the book path)."""
    book = build_epub(tmp_path, [
        ("Chapter One", "<p>A short test chapter with enough words to anchor a snippet.</p>"),
    ])
    fixture_dir = _write_fixture(tmp_path)
    out_path = tmp_path / "xray.json"

    exit_code = cli.main([
        str(book), "--api-key", "unused", "--json-out", str(out_path),
        "--transport-fixture", str(fixture_dir), "--embed",
    ])

    assert exit_code == 0
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert read_embedded(book) == doc
