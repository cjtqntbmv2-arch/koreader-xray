import json

import pytest

from xray_core.gemini import (
    GeminiClient,
    QuotaError,
    fix_truncated_json,
    normalize_keys,
    parse_ai_json,
)


def _ok_response(text, finish_reason="STOP"):
    body = {
        "candidates": [
            {
                "content": {"parts": [{"text": text}]},
                "finishReason": finish_reason,
            }
        ]
    }
    return json.dumps(body).encode("utf-8")


def test_request_body_shape():
    """Body must match xray_aihelper.lua:288-298 exactly: contents,
    system_instruction, the four BLOCK_NONE safetySettings, generationConfig."""
    captured = {}

    def fake_transport(url, headers, body_bytes):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(body_bytes)
        return 200, _ok_response('{"ok": true}')

    client = GeminiClient("key123", transport=fake_transport)
    client.generate("system prompt", "user prompt")

    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.5-flash:generateContent"
    )
    assert captured["headers"]["x-goog-api-key"] == "key123"

    body = captured["body"]
    assert body["contents"] == [{"role": "user", "parts": [{"text": "user prompt"}]}]
    assert body["system_instruction"] == {"parts": [{"text": "system prompt"}]}
    assert {s["category"] for s in body["safetySettings"]} == {
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    }
    assert all(s["threshold"] == "BLOCK_NONE" for s in body["safetySettings"])
    assert body["generationConfig"] == {
        "temperature": 0.2,
        "maxOutputTokens": 16384,
        "responseMimeType": "application/json",
    }


def test_thinking_gated_off_by_default():
    captured = {}

    def fake_transport(url, headers, body_bytes):
        captured["body"] = json.loads(body_bytes)
        return 200, _ok_response('{"ok": true}')

    client = GeminiClient("key", model="gemini-3.5-flash", transport=fake_transport)
    client.generate("sys", "user")

    assert "thinkingConfig" not in captured["body"]["generationConfig"]


def test_thinking_only_for_gemini3():
    """use_thinking alone isn't enough -- the model name must contain
    'gemini-3' too (xray_aihelper.lua ~254-286: thinking is model-gated)."""
    captured = {}

    def fake_transport(url, headers, body_bytes):
        captured["body"] = json.loads(body_bytes)
        return 200, _ok_response('{"ok": true}')

    non_gemini3 = GeminiClient(
        "key", model="gemini-2.0-flash", transport=fake_transport, use_thinking=True
    )
    non_gemini3.generate("sys", "user")
    assert "thinkingConfig" not in captured["body"]["generationConfig"]

    captured.clear()
    gemini3 = GeminiClient(
        "key", model="gemini-3.5-flash", transport=fake_transport, use_thinking=True
    )
    gemini3.generate("sys", "user")
    assert captured["body"]["generationConfig"]["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingLevel": "medium",
    }


def test_skips_thought_parts():
    """Parts marked thought:true must be excluded from the concatenated
    text -- xray_aihelper.lua:550 (`if p.text and not p.thought`). The decoy
    thought text is itself brace-shaped JSON so a filtering bug produces a
    concatenation that fails to parse, instead of accidentally still working."""
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": '{"decoy": true}', "thought": True},
                        {"text": '{"a": 1}'},
                    ]
                },
                "finishReason": "STOP",
            }
        ]
    }
    resp = json.dumps(body).encode("utf-8")

    def fake_transport(url, headers, body_bytes):
        return 200, resp

    client = GeminiClient("key", transport=fake_transport)
    result = client.generate("sys", "user")
    assert result.data == {"a": 1}


def test_truncated_flag_on_max_tokens():
    def fake_transport(url, headers, body_bytes):
        return 200, _ok_response('{"a": [1, 2', finish_reason="MAX_TOKENS")

    client = GeminiClient("key", transport=fake_transport)
    result = client.generate("sys", "user")

    assert result.truncated is True
    assert result.data == {"a": [1, 2]}  # fix_truncated_json still salvages it


def test_retries_503_once_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr("xray_core.gemini.time.sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def fake_transport(url, headers, body_bytes):
        calls["n"] += 1
        if calls["n"] == 1:
            return 503, b"overloaded"
        return 200, _ok_response('{"ok": true}')

    client = GeminiClient("key", transport=fake_transport)
    result = client.generate("sys", "user")

    assert result.data == {"ok": True}
    assert calls["n"] == 2
    assert sleeps == [2]


def test_429_backs_off_then_raises_quota(monkeypatch):
    sleeps = []
    monkeypatch.setattr("xray_core.gemini.time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("xray_core.gemini.random.uniform", lambda a, b: 0)
    calls = {"n": 0}

    def fake_transport(url, headers, body_bytes):
        calls["n"] += 1
        return 429, b'{"error": "quota"}'

    client = GeminiClient("key", transport=fake_transport, max_429_retries=3)
    with pytest.raises(QuotaError):
        client.generate("sys", "user")

    assert calls["n"] == 4  # 1 initial attempt + 3 retries
    # Exact shape, not just count: jitter zeroed via the random.uniform
    # monkeypatch above, so this pins the base-2 exponential backoff exactly.
    # A regression that collapses the exponent to a constant must fail here.
    assert sleeps == [2, 4, 8]


def test_other_error_raises_runtime_error():
    def fake_transport(url, headers, body_bytes):
        return 400, b'{"error": {"message": "bad request"}}'

    client = GeminiClient("key", transport=fake_transport)
    with pytest.raises(RuntimeError, match="400"):
        client.generate("sys", "user")


def test_parse_strips_markdown_fences():
    text = '```json\n{"a": 1}\n```'
    assert parse_ai_json(text) == {"a": 1}


def test_fix_truncated_json():
    assert fix_truncated_json('{"a": [1, 2') == '{"a": [1, 2]}'


def test_fix_truncated_json_ignores_brackets_inside_string():
    """Brace/bracket characters that are part of a string's own content must
    not be mistaken for structural JSON brackets -- only the char-wise
    string/escape tracking prevents that. A regression that miscounts
    brackets inside strings would corrupt the string value and/or the
    bracket stack."""
    fixed = fix_truncated_json('{"a": "text with } and ] inside')
    assert json.loads(fixed) == {"a": "text with } and ] inside"}


def test_fix_truncated_json_preserves_trailing_comma_inside_string():
    """A comma that is part of the string's own content, not a JSON
    separator, must survive even though it sits at the very end of the
    truncated fragment. This only works because fix_truncated_json closes
    the dangling string BEFORE stripping a trailing comma -- reversing that
    order would strip the comma as if it were structural."""
    fixed = fix_truncated_json('{"a": "value,')
    assert json.loads(fixed) == {"a": "value,"}


def test_normalize_keys():
    assert normalize_keys({"Full Name": 1}) == {"full_name": 1}


def test_normalize_keys_recurses_into_lists():
    """normalize_keys must recurse into list items, not just dict values --
    Lua's pairs(t) doesn't distinguish JSON objects from arrays, so
    normalizeKeys recurses into both (xray_aihelper.lua:1920-1928). A
    regression that drops the list branch would leave dicts nested inside
    lists un-normalized."""
    assert normalize_keys({"Items": [{"Full Name": 1}]}) == {
        "items": [{"full_name": 1}]
    }
