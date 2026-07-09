"""Task 10: end-to-end golden run + D4 invariant sweep.

Builds a 6-chapter fixture EPUB with entities pinned to known chapters (one
deliberately introduced only in the very last chapter, so the D4 "no future
leak" check is meaningfully exercised rather than vacuous), drives the real
`generate_xray()` pipeline with a fake needle-keyed Gemini client (same
FakeClient shape as tests/test_generate.py -- no network, no cassette
files), and checks the result against a hand-reviewed golden file plus the
D4/anchor invariants from the task-10 brief.

This is the whole-pipeline integration test: its output doc is also the real
xray.json shape the future KOReader-side importer will consume.

Regenerating the golden (deliberately, only when the pipeline changes --
this test file itself never writes it):

    python3 -c "
import json, pathlib, sys, tempfile
sys.path.insert(0, 'tests')
from test_e2e import generate_fixture_doc
with tempfile.TemporaryDirectory() as d:
    _, doc = generate_fixture_doc(pathlib.Path(d))
    print(json.dumps(doc, indent=2, ensure_ascii=False))
" > tests/golden/xray_golden.json

Then read the diff by hand before committing.
"""
import json
from pathlib import Path

import pytest
from epub_fixture import build_epub

from xray_core.epub import normalize_text, read_epub
from xray_core.gemini import GenResult
from xray_core.generate import generate_xray
from xray_core.schema import validate

_GOLDEN_PATH = Path(__file__).parent / "golden" / "xray_golden.json"
_CALIBRE_UUID = "e2e00000-1111-2222-3333-444455556666"

_SNAPSHOT_LISTS = ("characters", "locations", "terms", "historical_figures")
_CHRONOLOGY_LISTS = ("characters", "locations")


@pytest.fixture(autouse=True)
def _no_rate_limit_sleep(monkeypatch):
    """Same fix as test_generate.py/test_cli.py: don't let the real
    RateLimiter pace ~6s apart for a test that never touches the network."""
    monkeypatch.setattr("xray_core.generate.RateLimiter.acquire", lambda self: None)


# ---------------------------------------------------------------------------
# Fixture book: 6 chapters. Chapters 1-5 are short (each well under the 15%
# max-checkpoint-gap so they land as a single, un-split checkpoint); chapter
# 6 is deliberately long so it exceeds that gap and gets densified into
# several checkpoints -- exercising the same non-chapter-aligned checkpoint
# path a real long final chapter would hit. Each chapter carries one unique
# ALLCAPS marker word right next to the sentence that introduces its pinned
# entity, so the fake client below can key its canned response off it (the
# same needle-in-prompt trick as FakeClient in test_generate.py, since
# build_prompt embeds segment_text verbatim into the user prompt).
# ---------------------------------------------------------------------------

_CHAPTERS = [
    ("The Harbor at Dawn", """
<p>GULLMARK Alice Merrow stepped off the gangway into Thornwick Harbor just as the gulls began circling overhead.</p>
<p>She had drawn a hundred coastlines from memory, but none of them prepared her for the real salt air.</p>
<p>A dockhand pointed her toward the chart-maker's guild without being asked twice.</p>
"""),
    ("The Stranger's Ledger", """
<p>COINMARK Bram Voss counted his coins twice before he trusted the innkeeper's change.</p>
<p>He had given up smuggling years ago, or so he told anyone who asked at the bar.</p>
<p>Alice recognized his old sea-scarred hands from a dozen dockside stories.</p>
"""),
    ("Old Debts", """
<p>BINDMARK The Ledgerbind was older than the harbor itself, a contract no debtor could break by dying.</p>
<p>Queen Yssa the Elder had signed the first Ledgerbind three centuries before, binding her own treasury to it.</p>
<p>Bram still owed his share, and everyone in the guild knew it.</p>
"""),
    ("The Hidden Cove", """
<p>COVEMARK Corvin Hale unrolled a rival chart across the table, daring Alice to find its errors.</p>
<p>He had mapped the hidden cove twice and gotten the depth soundings wrong both times.</p>
<p>Alice said nothing, but she already knew where his numbers failed.</p>
"""),
    ("Storm Warning", """
<p>STORMARK Dahlia Rees lit the lighthouse lamp early, watching the sky darken over Shadow Pass.</p>
<p>No ship had crossed Shadow Pass in a storm and come back whole in eleven years.</p>
<p>She rang the warning bell twice, long practice steadying her hand.</p>
"""),
    ("The Last Reckoning", """
<p>Alice and Bram carried the corrected charts back through the guild hall at first light.</p>
<p>Corvin's errors were quietly struck from the record, and no one thanked him for it.</p>
<p>The guild elders argued for an hour about who should pay for the wasted parchment.</p>
<p>Dahlia sent word by gull that the storm over Shadow Pass had finally broken.</p>
<p>Outside, the harbor slowly came back to its ordinary morning noise and business.</p>
<p>Alice began sketching the hidden cove properly while the argument wore itself out.</p>
<p>Bram fell asleep in a corner chair, still clutching his tally of old debts.</p>
<p>No one noticed the side door open until the draft guttered every candle at once.</p>
<p>ENDMARK Only then did Emeric Thale step out of the shadows to claim the Ledgerbind was his to hold now.</p>
"""),
]


def _ok(data):
    return GenResult(data=data, truncated=False)


_EMPTY = {"characters": [], "locations": [], "historical_figures": [], "terms": [], "timeline": []}


class FakeClient:
    """Same shape as tests/test_generate.py's FakeClient: returns the first
    canned response whose needle substring is found in the user prompt,
    else the empty extraction (used by the densified filler sub-segments of
    chapter 6 that don't happen to contain its marker)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, system_instruction, user_prompt, max_output_tokens=16384):
        self.calls.append(user_prompt)
        for needle, result in self.responses:
            if needle in user_prompt:
                return result
        return _ok(dict(_EMPTY))


def _fake_client():
    return FakeClient([
        ("GULLMARK", _ok({
            "characters": [{
                "name": "Alice Merrow", "role": "protagonist",
                "description": "A young cartographer newly arrived at Thornwick Harbor.",
                "gender": "female", "occupation": "cartographer",
            }],
            "locations": [{
                "name": "Thornwick Harbor",
                "description": "A busy trade harbor where the story begins.",
                "importance": "primary setting",
            }],
            "timeline": [{"chapter": "The Harbor at Dawn", "event": "Alice Merrow arrives at Thornwick Harbor."}],
        })),
        ("COINMARK", _ok({
            "characters": [{
                "name": "Bram Voss", "role": "supporting",
                "description": "A retired smuggler with sea-scarred hands.",
                "gender": "male", "occupation": "former smuggler",
            }],
            "timeline": [{"chapter": "The Stranger's Ledger", "event": "Bram Voss meets Alice at the inn."}],
        })),
        ("BINDMARK", _ok({
            "terms": [{
                "name": "the Ledgerbind", "expanded": "The Ledgerbind Contract",
                "category": "legal/magical",
                "definition": "An unbreakable debt contract older than the harbor.",
            }],
            "historical_figures": [{
                "name": "Queen Yssa the Elder",
                "biography": "Signed the first Ledgerbind three centuries ago.",
                "role": "historical monarch",
                "importance_in_book": "origin of the Ledgerbind",
                "context_in_book": "referenced as the contract's originator.",
            }],
            "timeline": [{"chapter": "Old Debts", "event": "The origin of the Ledgerbind is revealed."}],
        })),
        ("COVEMARK", _ok({
            "characters": [{
                "name": "Corvin Hale", "role": "rival",
                "description": "A rival cartographer with a flawed hidden-cove chart.",
                "gender": "male", "occupation": "cartographer",
            }],
            "timeline": [{"chapter": "The Hidden Cove", "event": "Corvin Hale's chart errors are exposed."}],
        })),
        ("STORMARK", _ok({
            "characters": [{
                "name": "Dahlia Rees", "role": "supporting",
                "description": "The lighthouse keeper who watches over Shadow Pass.",
                "gender": "female", "occupation": "lighthouse keeper",
            }],
            "locations": [{
                "name": "Shadow Pass",
                "description": "A treacherous strait where storms sink ships.",
                "importance": "recurring hazard",
            }],
            "timeline": [{"chapter": "Storm Warning", "event": "Dahlia Rees warns of a storm over Shadow Pass."}],
        })),
        ("ENDMARK", _ok({
            "characters": [{
                "name": "Emeric Thale", "role": "antagonist",
                "description": "A stranger who claims the Ledgerbind at the story's end.",
                "gender": "male", "occupation": "unknown",
            }],
            "timeline": [{"chapter": "The Last Reckoning", "event": "Emeric Thale claims the Ledgerbind."}],
        })),
    ])


def generate_fixture_doc(tmp_path):
    """Build the fixture EPUB, run the real generate_xray() pipeline against
    it with the fake client above, and return (book, doc). Shared by the
    tests below and by the golden-regeneration one-liner in this module's
    docstring -- there must be exactly one path that produces this doc."""
    book_path = build_epub(tmp_path, _CHAPTERS, toc=True, epub3=True)
    book = read_epub(book_path)
    doc = generate_xray(book, _fake_client(), "en", "normal", calibre_uuid=_CALIBRE_UUID)
    return book, doc


@pytest.fixture
def fixture_result(tmp_path):
    return generate_fixture_doc(tmp_path)


def _names(snapshot, list_name):
    return {e["name"] for e in snapshot[list_name]}


# ---------------------------------------------------------------------------
# Golden equality
# ---------------------------------------------------------------------------


def test_golden_equality(fixture_result):
    _, doc = fixture_result
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert doc == golden


# ---------------------------------------------------------------------------
# Sanity: prove the fixture is actually D4-meaningful (not vacuous)
# ---------------------------------------------------------------------------


def test_fixture_introduces_entities_late_and_early(fixture_result):
    _, doc = fixture_result
    names_by_cp = [_names(cp["snapshot"], "characters") for cp in doc["checkpoints"]]

    # Emeric Thale is introduced in the book's very last sentence: absent
    # from every checkpoint except the final one.
    assert "Emeric Thale" in names_by_cp[-1]
    assert all("Emeric Thale" not in names for names in names_by_cp[:-1])

    # Alice Merrow, introduced in the book's first sentence, is present from
    # the very first checkpoint through to the last (persistent entity).
    assert "Alice Merrow" in names_by_cp[0]
    assert "Alice Merrow" in names_by_cp[-1]


# ---------------------------------------------------------------------------
# D4 sweep: entities
# ---------------------------------------------------------------------------


def test_d4_entities_cumulative_across_checkpoints(fixture_result):
    """Every entity present in snapshot N is still present (by name) in
    snapshot N+1, for all four snapshot lists -- a spoiler-staged snapshot
    only ever grows."""
    _, doc = fixture_result
    checkpoints = doc["checkpoints"]
    for list_name in _SNAPSHOT_LISTS:
        for prev_cp, next_cp in zip(checkpoints, checkpoints[1:]):
            prev_names = _names(prev_cp["snapshot"], list_name)
            next_names = _names(next_cp["snapshot"], list_name)
            assert prev_names <= next_names, (
                f"{list_name} regressed between checkpoint {prev_cp['percent']}% "
                f"and {next_cp['percent']}%: lost {prev_names - next_names}"
            )


def test_d4_no_entity_first_pct_after_own_checkpoint(fixture_result):
    """No character/location is ever stamped as first appearing AFTER the
    checkpoint percent it's snapshotted in -- the exact shape of a
    future-entity spoiler leak (also enforced structurally by validate())."""
    _, doc = fixture_result
    for cp in doc["checkpoints"]:
        for list_name in _CHRONOLOGY_LISTS:
            for entity in cp["snapshot"][list_name]:
                assert entity["first_pct"] <= cp["percent"], (
                    f"{list_name[:-1]} {entity['name']!r} first_pct="
                    f"{entity['first_pct']} leaks past checkpoint {cp['percent']}%"
                )


# ---------------------------------------------------------------------------
# D4 sweep: timeline
# ---------------------------------------------------------------------------


def test_d4_timeline_never_exceeds_last_checkpoint(fixture_result):
    _, doc = fixture_result
    last_percent = doc["checkpoints"][-1]["percent"]
    offenders = [ev for ev in doc["timeline"] if ev["pct"] > last_percent]
    assert offenders == []


def test_timeline_events_stamped_at_real_checkpoint_percents(fixture_result):
    """Every timeline event's pct must be one of the actual checkpoint
    percents. merge_segment() stamps each event with the exact checkpoint_pct
    of the checkpoint it was merged into (xray_core/merge.py:
    `self.timeline.append({**ev, "pct": checkpoint_pct})`, called from
    generate.py as `state.merge_segment(results[...], cp.percent)`) -- so a
    shuffled/constant/garbage pct that isn't a real checkpoint value must
    fail here.

    (Replaces a tautological predecessor that filtered the one fixed
    timeline list by each checkpoint's ascending percent and asserted the
    filtered sets grew -- true for ANY list contents whenever checkpoint
    percents ascend, so it couldn't fail even if every event were
    mis-stamped, e.g. pct=1 across the board.)"""
    _, doc = fixture_result
    checkpoints = doc["checkpoints"]
    checkpoint_percents = {cp["percent"] for cp in checkpoints}
    last_percent = checkpoints[-1]["percent"]

    assert doc["timeline"], "fixture produced no timeline events to check"

    event_pcts = {ev["pct"] for ev in doc["timeline"]}
    assert event_pcts <= checkpoint_percents, (
        "timeline event(s) stamped with a pct that isn't any checkpoint's "
        f"percent: {sorted(event_pcts - checkpoint_percents)} not in {sorted(checkpoint_percents)}"
    )
    assert all(ev["pct"] <= last_percent for ev in doc["timeline"]), (
        f"timeline event exceeds last checkpoint ({last_percent}%)"
    )


# ---------------------------------------------------------------------------
# Schema validity
# ---------------------------------------------------------------------------


def test_doc_validates_clean(fixture_result):
    _, doc = fixture_result
    assert validate(doc) == []


# ---------------------------------------------------------------------------
# Anchor uniqueness (device-side findText() depends on this)
# ---------------------------------------------------------------------------


def test_snippet_anchors_unique_in_full_text(fixture_result):
    book, doc = fixture_result
    normalized = normalize_text(book.full_text)
    for cp in doc["checkpoints"]:
        anchor = cp["snippet_anchor"]
        assert anchor, f"empty snippet_anchor at checkpoint {cp['percent']}%"
        count = normalized.count(anchor)
        assert count == 1, (
            f"snippet_anchor at checkpoint {cp['percent']}% occurs {count} times "
            f"in full_text (must be exactly 1): {anchor!r}"
        )
