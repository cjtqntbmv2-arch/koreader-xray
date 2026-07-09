"""Gemini API client: request building, injectable transport, JSON repair.

Port of the Gemini-specific slice of KOReader Lua's `xray_aihelper.lua`:
request body (`:288-298`), response unwrapping incl. thought-part skipping
(`:546-554`), 503 retry (`:512-514`), and the JSON-repair pipeline
(`normalizeKeys` `:1920-1928`, `fixTruncatedJSON` `:1930-1957`,
`parseAIResponse`'s fence-strip/brace-extract `:1962-1999`). The 429
exponential-backoff + `QuotaError` is new here (Lua has no multi-provider
fallback to fall back on in this Gemini-only first cut, so the client must
retry itself instead) -- see task brief / Global Constraints.

Stdlib-only on purpose (see `xray_core/epub.py`): `urllib.request` for the
default transport, `json`/`re`/`time`/`random`/`dataclasses` for the rest.
"""

import json
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# HARM_CATEGORY_* order and BLOCK_NONE threshold verbatim from
# xray_aihelper.lua:291-296.
_SAFETY_CATEGORIES = (
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)


class QuotaError(Exception):
    """Raised when 429s persist past `GeminiClient.max_429_retries`."""


@dataclass
class GenResult:
    data: dict
    truncated: bool  # True iff candidates[0].finishReason == "MAX_TOKENS"


def normalize_keys(obj: Any) -> Any:
    """Lowercase dict keys and turn whitespace runs into `_`, recursively
    (into both dict values and list items -- Lua's `pairs(t)` doesn't
    distinguish JSON objects from arrays, so it recurses into both; see
    `normalizeKeys`, xray_aihelper.lua:1920-1928)."""
    if isinstance(obj, dict):
        return {
            (re.sub(r"\s+", "_", k.lower()) if isinstance(k, str) else k): normalize_keys(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [normalize_keys(v) for v in obj]
    return obj


def fix_truncated_json(s: str) -> str:
    """Close out a truncated JSON fragment: track string/escape state and a
    bracket stack char-by-char, close a dangling string, strip a trailing
    comma, then append closers for whatever is still open, innermost first.
    Verbatim port of `AIHelper:fixTruncatedJSON`, xray_aihelper.lua:1930-1957."""
    stack = []
    in_string = False
    escaped = False
    for c in s:
        if escaped:
            escaped = False
        elif c == "\\":
            escaped = True
        elif c == '"':
            in_string = not in_string
        elif not in_string:
            if c in "{[":
                stack.append(c)
            elif c == "}":
                if stack and stack[-1] == "{":
                    stack.pop()
            elif c == "]":
                if stack and stack[-1] == "[":
                    stack.pop()

    res = s
    if in_string:
        res += '"'
    res = re.sub(r",\s*$", "", res)
    for opener in reversed(stack):
        res += "}" if opener == "{" else "]"
    return res


def parse_ai_json(text: str) -> dict:
    """fence-strip -> whole-string decode -> (on failure) brace-extract ->
    fix_truncated_json -> json.loads. Port of `AIHelper:parseAIResponse`'s
    JSON half, xray_aihelper.lua:1962-1997 (its two redundant leading-fence
    gsubs -- one for a literal "json" tag, one generic -- collapse into the
    one `\\w*` regex below; same net effect)."""
    if not text:
        raise ValueError("empty response text")

    json_text = text.strip()
    if json_text.startswith("```"):
        json_text = re.sub(r"^```\w*\s*", "", json_text)
        json_text = re.sub(r"```\s*$", "", json_text)

    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        pass

    first_brace = json_text.find("{")
    first_bracket = json_text.find("[")
    first = first_brace if first_brace != -1 else first_bracket
    if first == -1:
        raise ValueError("no JSON object/array found in response")

    # Mirror the Lua: cut at whichever of the last '}' / last ']' comes
    # EARLIER, not later. That looks like it drops legitimate trailing
    # closers, but fix_truncated_json re-derives exactly those closers from
    # the bracket stack -- and this way, any non-bracket trailing prose
    # ("Hope that helps!") past the real JSON is reliably excluded too.
    last_brace = json_text.rfind("}")
    last_bracket = json_text.rfind("]")
    candidates = [p for p in (last_brace, last_bracket) if p != -1]
    last = min(candidates) if candidates else len(json_text) - 1

    extracted = json_text[first:last + 1]
    fixed = fix_truncated_json(extracted)
    return json.loads(fixed)


class GeminiClient:
    def __init__(self, api_key, model="gemini-3.5-flash", transport=None,
                 timeout=180, use_thinking=False, max_429_retries=4):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.use_thinking = use_thinking
        self.max_429_retries = max_429_retries
        self.transport = transport or self._default_transport

    def _default_transport(self, url, headers, body_bytes):
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _build_body(self, system_instruction, user_prompt, max_output_tokens):
        gen_config = {
            "temperature": 0.2,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        }
        # Gated: off by default (Lua parity, xray_aihelper.lua:254), and only
        # for gemini-3* models (thinking shares the maxOutputTokens budget).
        if self.use_thinking and "gemini-3" in self.model:
            gen_config["thinkingConfig"] = {"includeThoughts": True, "thinkingLevel": "medium"}
        return {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "safetySettings": [
                {"category": cat, "threshold": "BLOCK_NONE"} for cat in _SAFETY_CATEGORIES
            ],
            "generationConfig": gen_config,
        }

    def _parse_response(self, data: dict) -> GenResult:
        candidates = data.get("candidates")
        if not candidates:
            raise RuntimeError(f"Gemini response has no candidates: {data!r}")
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        truncated = candidate.get("finishReason", "STOP") == "MAX_TOKENS"
        return GenResult(data=normalize_keys(parse_ai_json(text)), truncated=truncated)

    def generate(self, system_instruction, user_prompt, max_output_tokens=16384) -> GenResult:
        url = _ENDPOINT.format(model=self.model)
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        body_bytes = json.dumps(
            self._build_body(system_instruction, user_prompt, max_output_tokens)
        ).encode("utf-8")

        retried_503 = False
        retries_429 = 0
        while True:
            status, resp_bytes = self.transport(url, headers, body_bytes)

            if status == 200:
                return self._parse_response(json.loads(resp_bytes.decode("utf-8")))

            if status == 503 and not retried_503:
                retried_503 = True
                time.sleep(2)
                continue

            if status == 429:
                retries_429 += 1
                if retries_429 > self.max_429_retries:
                    raise QuotaError(
                        f"Gemini quota exceeded after {self.max_429_retries} retries"
                    )
                time.sleep(2 ** retries_429 + random.uniform(0, 1))
                continue

            excerpt = resp_bytes[:200].decode("utf-8", errors="replace")
            raise RuntimeError(f"Gemini API error {status}: {excerpt}")
