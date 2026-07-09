"""Tests for the generation orchestrator (Task 7): parallel extraction,
ordered-merge D4 barrier, sequential enrichment, quota handling, resume.
"""
import os
import time

import pytest

from xray_core.checkpoints import plan_checkpoints
from xray_core.epub import BookText, TocEntry
from xray_core.gemini import GenResult, QuotaError
from xray_core.generate import generate_xray
from xray_core.schema import validate


@pytest.fixture(autouse=True)
def _no_rate_limit_sleep(monkeypatch):
    """RateLimiter.acquire() would otherwise pace real requests ~6s apart
    (per_minute=10) -- tests use a fake client and don't want to actually
    wait for it. Patches the RateLimiter.acquire *method* specifically
    (not time.sleep globally) -- `time` is a process-wide singleton module,
    so patching xray_core.generate.time.sleep would also silently neuter a
    fake client's own deliberately-real time.sleep calls used below to force
    specific fetch-completion orderings."""
    monkeypatch.setattr("xray_core.generate.RateLimiter.acquire", lambda self: None)


def _book(full_text, toc=()):
    """Minimal BookText -- fields plan_checkpoints/generate_xray don't touch
    (spine_offsets) get a throwaway value. Empty toc -> the checkpoint
    planner's fixed 10%-grid fallback (exactly 10 checkpoints, no
    densification surprises); this is used by most tests below for
    predictability."""
    return BookText(
        title="Test Book",
        authors=["Author One"],
        language="en",
        full_text=full_text,
        spine_offsets=[e.offset for e in toc] or [0],
        toc=list(toc),
        text_hash="sha256:" + "0" * 64,
    )


def _two_chapter_book(ch1_text, ch2_text):
    toc = [
        TocEntry(title="Chapter One", spine_index=0, offset=0),
        TocEntry(title="Chapter Two", spine_index=1, offset=len(ch1_text)),
    ]
    return _book(ch1_text + ch2_text, toc)


def _ok(data):
    return GenResult(data=data, truncated=False)


class FakeClient:
    """Returns the first canned response whose needle substring is found in
    the user_prompt -- build_prompt embeds segment_text verbatim into the
    prompt (xray_core/prompts.py), so canned responses are naturally "keyed
    by chunk text" via a distinctive marker string placed in that text.
    raise_quota_for: an optional needle; generate() raises QuotaError once
    user_prompt contains it, instead of returning any canned response."""

    def __init__(self, responses=(), raise_quota_for=None):
        self.responses = list(responses)
        self.raise_quota_for = raise_quota_for
        self.calls = []

    def generate(self, system_instruction, user_prompt, max_output_tokens=16384):
        self.calls.append(user_prompt)
        if self.raise_quota_for and self.raise_quota_for in user_prompt:
            raise QuotaError("quota exceeded")
        for needle, result in self.responses:
            if needle in user_prompt:
                return result
        return _ok({"characters": [], "locations": [], "historical_figures": [],
                    "terms": [], "timeline": []})


def _filler_book(reps=400):
    full_text = "Filler prose continues along nicely for quite some time in this book. " * reps
    return _book(full_text, toc=[])


# ---------------------------------------------------------------------------
# End-to-end + D4 ordering
# ---------------------------------------------------------------------------


def test_end_to_end_two_checkpoints():
    ch1 = "Alice walks through the CH1MARKER village at dawn, greeting everyone she meets today. " * 5
    ch2 = "Bob arrives at the CH2MARKER harbor just as the tide turns for the evening light. " * 5
    book = _two_chapter_book(ch1, ch2)

    client = FakeClient([
        ("CH2MARKER", _ok({"characters": [{"name": "Bob"}]})),
        ("CH1MARKER", _ok({"characters": [{"name": "Alice"}]})),
    ])

    doc = generate_xray(book, client, "en", "normal")

    assert validate(doc) == []
    assert doc["complete"] is True
    names_last = {c["name"] for c in doc["checkpoints"][-1]["snapshot"]["characters"]}
    names_first = {c["name"] for c in doc["checkpoints"][0]["snapshot"]["characters"]}
    assert names_last >= {"Alice", "Bob"}
    assert "Bob" not in names_first  # snapshot 2 accumulates snapshot 1's entities


def test_d4_no_future_entities():
    ch1 = "Alice explores the CH1MARKER ruins alone, searching for something long lost today. " * 5
    ch2 = "Bob discovers the CH2MARKER treasure hidden beneath the old stone archway nearby. " * 5
    book = _two_chapter_book(ch1, ch2)

    client = FakeClient([
        ("CH2MARKER", _ok({"characters": [{"name": "Bob"}]})),
        ("CH1MARKER", _ok({"characters": [{"name": "Alice"}]})),
    ])

    doc = generate_xray(book, client, "en", "normal")

    checkpoints = doc["checkpoints"]
    first_with_bob = next(
        (cp for cp in checkpoints
         if any(c["name"] == "Bob" for c in cp["snapshot"]["characters"])),
        None,
    )
    assert first_with_bob is not None
    earlier = checkpoints[: checkpoints.index(first_with_bob)]
    assert all(
        not any(c["name"] == "Bob" for c in cp["snapshot"]["characters"])
        for cp in earlier
    )
    bob = next(c for c in first_with_bob["snapshot"]["characters"] if c["name"] == "Bob")
    assert bob["first_pct"] == first_with_bob["percent"]


def test_d4_holds_under_out_of_order_fetch():
    """The chapter-2 (later) chunk resolves FIRST (short real sleep) while
    the chapter-1 (earlier) chunk is still in flight (long real sleep) --
    max_workers is set >= the maximum possible checkpoint count so every
    chunk starts essentially simultaneously, making the completion order
    purely a function of these sleep durations, not submission order. This
    proves the merge barrier orders by (checkpoint, chunk) index, not
    fetch-completion order: an early-arriving future for a LATER checkpoint
    must never enter an EARLIER snapshot."""
    ch1 = "Alice wanders the CH1MARKER hills for hours, thinking quietly of home again today. " * 5
    ch2 = "Bob uncovers the CH2MARKER vault at last, breathless with sudden anticipation now. " * 5
    book = _two_chapter_book(ch1, ch2)
    ch1_len = len(ch1)

    class SlowFastClient:
        def __init__(self):
            self.calls = []
            self.completion_order = []  # proves genuine out-of-order fetch

        def generate(self, system_instruction, user_prompt, max_output_tokens=16384):
            self.calls.append(user_prompt)
            if "CH2MARKER" in user_prompt:
                time.sleep(0.001)
                self.completion_order.append("ch2")
                return _ok({"characters": [{"name": "Bob"}]})
            time.sleep(0.05)
            self.completion_order.append("ch1")
            return _ok({"characters": [{"name": "Alice"}]})

    client = SlowFastClient()
    doc = generate_xray(book, client, "en", "normal", max_workers=12)

    # The whole point of this test: prove completion order really was
    # reversed relative to checkpoint (submission) order -- otherwise the
    # D4 assertions below would hold vacuously (the barrier is unconditional
    # by construction) without ever having exercised the risky path.
    assert client.completion_order[0] == "ch2"
    assert "ch1" in client.completion_order

    assert validate(doc) == []
    assert doc["complete"] is True
    cps = plan_checkpoints(book)
    assert len(cps) == len(doc["checkpoints"])
    for cp_obj, cp_doc in zip(cps, doc["checkpoints"]):
        if cp_obj.offset <= ch1_len:
            names = {c["name"] for c in cp_doc["snapshot"]["characters"]}
            assert "Bob" not in names
    assert "Bob" in {c["name"] for c in doc["checkpoints"][-1]["snapshot"]["characters"]}


# ---------------------------------------------------------------------------
# Sub-chunking + truncation retry
# ---------------------------------------------------------------------------


def test_oversized_segment_subchunked():
    para = "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt."
    full_text = "\n\n".join([para] * 20000)  # ~1.84M chars -> ~184k per 10%-grid checkpoint
    book = _book(full_text, toc=[])

    client = FakeClient([("Lorem", _ok({"characters": [{"name": "Filler"}]}))])
    doc = generate_xray(book, client, "en", "normal", max_workers=4)

    assert validate(doc) == []
    # Each 10%-grid segment is ~130k chars > FULL_TEXT_BUDGET (120k), so at
    # least one checkpoint's segment required >1 client.generate() call.
    assert len(client.calls) > len(doc["checkpoints"])


def test_truncated_chunk_split_and_refetched():
    marked_portion = (
        "STARTMARKER Alice begins the tale in the quiet village she calls home, "
        "walking slowly past the old well and the bakery that just opened.\n\n"
        "She continues onward through the square, thinking of the letter she "
        "received, and reaches the edge of town as the church bells ring. ENDMARKER"
    )
    total = len(marked_portion) * 20
    filler = "Later uneventful filler text continues on for quite a long while now. " * 300
    full_text = marked_portion + filler
    assert len(full_text) >= total
    book = _book(full_text, toc=[])  # segment 0 (first 10%) == marked_portion + a bit of filler

    class TruncatingClient:
        def __init__(self):
            self.calls = []

        def generate(self, system_instruction, user_prompt, max_output_tokens=16384):
            self.calls.append(user_prompt)
            has_start = "STARTMARKER" in user_prompt
            has_end = "ENDMARKER" in user_prompt
            if has_start and has_end:
                return GenResult(
                    data={"characters": [{"name": "PartialAlice"}]}, truncated=True
                )
            if has_start:
                return GenResult(
                    data={"characters": [{"name": "Alice"}, {"name": "FirstHalfOnly"}]},
                    truncated=False,
                )
            if has_end:
                return GenResult(
                    data={"characters": [{"name": "Alice"}, {"name": "SecondHalfOnly"}]},
                    truncated=False,
                )
            return GenResult(data={"characters": []}, truncated=False)

    client = TruncatingClient()
    doc = generate_xray(book, client, "en", "normal", max_workers=2)

    assert validate(doc) == []
    whole_calls = [c for c in client.calls if "STARTMARKER" in c and "ENDMARKER" in c]
    first_half_calls = [c for c in client.calls if "STARTMARKER" in c and "ENDMARKER" not in c]
    second_half_calls = [c for c in client.calls if "ENDMARKER" in c and "STARTMARKER" not in c]
    assert len(whole_calls) == 1  # truncated once, never retried whole again
    assert len(first_half_calls) == 1
    assert len(second_half_calls) == 1

    names = {c["name"] for c in doc["checkpoints"][0]["snapshot"]["characters"]}
    assert names == {"Alice", "FirstHalfOnly", "SecondHalfOnly"}
    assert "PartialAlice" not in names  # truncated response never accepted as final


# ---------------------------------------------------------------------------
# Quota handling + resume
# ---------------------------------------------------------------------------


def test_quota_failure_partial_doc():
    book = _filler_book()
    client = FakeClient(raise_quota_for="Reading Progress: 30%")

    doc = generate_xray(book, client, "en", "normal", max_workers=1)

    assert validate(doc) == []
    assert doc["complete"] is False
    assert doc["last_percent"] == 20
    assert len(doc["checkpoints"]) == 2


def test_quota_cancels_pending_futures():
    book = _filler_book()

    class QuotaClient:
        def __init__(self):
            self.calls = []

        def generate(self, system_instruction, user_prompt, max_output_tokens=16384):
            # Real (unpatched) sleep: gives the main thread a genuine window
            # to react to the QuotaError and cancel queued futures before
            # the single worker thread races ahead through the whole queue
            # (verified empirically -- an instant fake drains all 10 tasks
            # before the main thread ever gets scheduled to intervene).
            time.sleep(0.005)
            self.calls.append(user_prompt)
            if "Reading Progress: 20%" in user_prompt:
                raise QuotaError("quota exceeded")
            return GenResult(data={"characters": []}, truncated=False)

    client = QuotaClient()
    doc = generate_xray(book, client, "en", "normal", max_workers=1)

    assert doc["complete"] is False
    called_percents = [
        p for p in range(10, 101, 10)
        if any(f"Reading Progress: {p}%" in c for c in client.calls)
    ]
    assert called_percents[:2] == [10, 20]
    # Comfortably-later checkpoints must never have been dispatched at all.
    assert 80 not in called_percents
    assert 90 not in called_percents
    assert 100 not in called_percents


def test_resume_skips_fetched_chunks(tmp_path):
    book = _filler_book()
    workdir = str(tmp_path / "work")

    class FailAt20Client:
        def __init__(self):
            self.calls = []

        def generate(self, system_instruction, user_prompt, max_output_tokens=16384):
            self.calls.append(user_prompt)
            if "Reading Progress: 20%" in user_prompt:
                raise QuotaError("quota exceeded")
            return GenResult(data={"characters": []}, truncated=False)

    client1 = FailAt20Client()
    doc1 = generate_xray(book, client1, "en", "normal", workdir=workdir, max_workers=1)
    assert doc1["complete"] is False
    assert os.path.exists(os.path.join(workdir, "chunk_0_0.json"))
    assert not os.path.exists(os.path.join(workdir, "chunk_1_0.json"))

    client2 = FakeClient()
    doc2 = generate_xray(book, client2, "en", "normal", workdir=workdir, max_workers=1)

    assert doc2["complete"] is True
    assert not any("Reading Progress: 10%" in c for c in client2.calls)  # loaded from cache
    assert any("Reading Progress: 20%" in c for c in client2.calls)  # re-fetched (had failed)


# ---------------------------------------------------------------------------
# Enrichment (Phase C)
# ---------------------------------------------------------------------------


def test_enrich_updates_recurring_descriptions():
    book = _filler_book()

    class EnrichClient:
        def __init__(self):
            self.enrich_calls = []

        def generate(self, system_instruction, user_prompt, max_output_tokens=16384):
            if "MERGE MODE INSTRUCTIONS" in user_prompt:
                self.enrich_calls.append(user_prompt)
                return GenResult(
                    data={"characters": [{"name": "Alice", "description": "Re-synthesized bio."}]},
                    truncated=False,
                )
            return GenResult(
                data={"characters": [{"name": "Alice", "description": "Original bio."}]},
                truncated=False,
            )

    client = EnrichClient()
    doc = generate_xray(book, client, "en", "detailed", enrich=True, max_workers=4)

    assert validate(doc) == []
    assert len(client.enrich_calls) > 0
    last_cp = doc["checkpoints"][-1]
    alice = next(c for c in last_cp["snapshot"]["characters"] if c["name"] == "Alice")
    assert alice["description"] == "Re-synthesized bio."


def test_enrich_stays_d4_safe():
    early = "Alice continues her long journey through the countryside every single day. " * 500
    late = "LATEFACT Alice finally learns the truth about her long-lost brother today. " * 15
    full_text = early + late
    book = _book(full_text, toc=[])

    class EnrichSpyClient:
        def __init__(self):
            self.enrich_prompts = []

        def generate(self, system_instruction, user_prompt, max_output_tokens=16384):
            if "MERGE MODE INSTRUCTIONS" in user_prompt:
                self.enrich_prompts.append(user_prompt)
                return GenResult(
                    data={"characters": [{"name": "Alice", "description": "Updated bio."}]},
                    truncated=False,
                )
            return GenResult(
                data={"characters": [{"name": "Alice", "description": "Original bio."}]},
                truncated=False,
            )

    client = EnrichSpyClient()
    doc = generate_xray(book, client, "en", "detailed", enrich=True, max_workers=4)

    assert validate(doc) == []
    assert len(client.enrich_prompts) > 1
    non_final_enrich_prompts = client.enrich_prompts[:-1]
    assert all("LATEFACT" not in p for p in non_final_enrich_prompts)

    earlier_cp = doc["checkpoints"][-2]
    alice_earlier = next(c for c in earlier_cp["snapshot"]["characters"] if c["name"] == "Alice")
    assert "LATEFACT" not in alice_earlier["description"]


def test_enrich_does_not_duplicate_timeline_events():
    """The enrich-mode prompt is still the full comprehensive template (plus
    the MERGE MODE addendum) -- a real model's enrich response can still
    include a timeline array. Phase A's own extraction already recorded that
    checkpoint's timeline once; re-merging the enrich response verbatim must
    not append it a second time."""
    book = _filler_book()

    class TimelineEnrichClient:
        def generate(self, system_instruction, user_prompt, max_output_tokens=16384):
            return GenResult(
                data={
                    "characters": [{"name": "Alice", "description": "desc"}],
                    "timeline": [{"chapter": "Ch", "event": "Something happens here."}],
                },
                truncated=False,
            )

    doc = generate_xray(book, TimelineEnrichClient(), "en", "detailed", enrich=True, max_workers=4)

    assert validate(doc) == []
    assert len(doc["timeline"]) == len(doc["checkpoints"])
