"""Tests for the content bank.

The bank is what lets the cron run with no Anthropic credentials at all, so the
invariants that matter are: a post is never served twice, a failed publish does
not burn an entry, and the shipped bank actually survives the guard.
"""

from __future__ import annotations

import random

import pytest

from viralinator.bank import Bank
from viralinator.config import load
from viralinator.guard import Guard

from .test_guard import cfg  # noqa: F401  — reuse the fixture


@pytest.fixture
def bank() -> Bank:
    b = Bank()
    for i in range(6):
        b.add(f"post number {i}", "lore_serial")
    for i in range(3):
        b.add(f"question number {i}", "reply_bait_question")
    return b


# --- consumption -----------------------------------------------------------------


def test_take_marks_used(bank: Bank) -> None:
    entry = bank.take()
    assert entry is not None
    assert entry.used
    assert len(bank.unused()) == 8


def test_take_never_serves_the_same_post_twice(bank: Bank) -> None:
    seen = set()
    while (e := bank.take()) is not None:
        assert e.id not in seen, "bank served a post twice"
        seen.add(e.id)
    assert len(seen) == 9
    assert bank.unused() == []


def test_take_prefers_requested_format(bank: Bank) -> None:
    r = random.Random(0)
    for _ in range(3):
        e = bank.take("reply_bait_question", r)
        assert e is not None and e.format_key == "reply_bait_question"


def test_take_falls_back_when_format_exhausted(bank: Bank) -> None:
    """Running dry on one format must not stop the account posting."""
    r = random.Random(0)
    for _ in range(3):
        bank.take("reply_bait_question", r)
    e = bank.take("reply_bait_question", r)
    assert e is not None
    assert e.format_key == "lore_serial"


def test_take_returns_none_when_empty() -> None:
    assert Bank().take() is None


def test_returning_an_entry_restores_it(bank: Bank) -> None:
    """cmd_run un-marks an entry when the publish call fails."""
    e = bank.take()
    assert e is not None
    e.used_at = None
    assert len(bank.unused()) == 9


# --- persistence -----------------------------------------------------------------


def test_round_trip_preserves_used_state(bank: Bank, tmp_path) -> None:
    bank.take()
    bank.take()
    path = tmp_path / "bank.json"
    bank.save(path)

    reloaded = Bank.load(path)
    assert len(reloaded.unused()) == 7
    assert len(reloaded.entries) == 9


def test_version_mismatch_fails_loudly(tmp_path) -> None:
    path = tmp_path / "bank.json"
    path.write_text('{"version": 0, "entries": []}')
    with pytest.raises(ValueError, match="version 0"):
        Bank.load(path)


def test_missing_bank_is_empty_not_an_error(tmp_path) -> None:
    assert Bank.load(tmp_path / "nope.json").entries == []


# --- duplicate detection ---------------------------------------------------------


def test_similar_posts_are_detected(bank: Bank) -> None:
    bank.add("the cat sat on me for two hours today", "lore_serial")
    assert bank.contains_similar("the cat sat on me for two hours today")
    assert not bank.contains_similar("aluminum is a coward metal")


# --- the shipped bank ------------------------------------------------------------


def test_shipped_bank_is_populated() -> None:
    assert len(Bank.load().unused()) >= 50


def test_every_shipped_post_survives_the_guard() -> None:
    """The bank was screened when it was written. This makes sure it still
    passes after any change to the guard's rules — if someone tightens a rule,
    CI tells them which existing posts it just invalidated."""
    cfg_real = load()
    guard = Guard(cfg_real)
    bad = [
        (e.text, guard.check_rules(e.text).reasons)
        for e in Bank.load().entries
        if not guard.check_rules(e.text).ok
    ]
    assert not bad, f"{len(bad)} banked posts now fail the guard: {bad[:3]}"


def test_shipped_bank_respects_length_limit() -> None:
    limit = load().generation.max_chars
    over = [e.text for e in Bank.load().entries if len(e.text) > limit]
    assert not over, f"posts over {limit} chars: {over}"


def test_shipped_bank_has_format_variety() -> None:
    """A bank that is all one format makes a boring, obviously-automated feed.

    The ceiling was 40% when the bank was an even spread. It is 55% now: the
    loud `hype` register measurably outperformed everything else on this
    account, so concentrating there is deliberate. Past 55% the feed reads as
    one note on repeat.
    """
    counts = Bank.load().counts()
    assert len(counts) >= 6
    total = sum(t for _, t in counts.values())
    assert max(t for _, t in counts.values()) < total * 0.55


PROMO_FORMATS = {
    "hype", "hype_question", "holding_joke", "ticker_forward",
    "anti_marketing", "comparative_flex", "where_to_find",
}


def test_shipped_bank_leads_with_the_loud_register() -> None:
    """Bounds moved on measured feedback, not taste.

    The first bank was ~50% promotional and quiet/literary in voice; it averaged
    ~2 views against ~17 for the owner's own loud, ticker-first posts. So the
    floor went up and the old 65% ceiling came off — it was a prior, and the
    data contradicted it. A light ceiling stays so the feed keeps some texture.
    """
    entries = Bank.load().entries
    share = len([e for e in entries if e.format_key in PROMO_FORMATS]) / len(entries)
    assert 0.60 <= share <= 0.95, (
        f"loud/promotional share is {share:.0%}; want 60-95%."
    )


def test_ticker_appears_in_most_of_the_bank() -> None:
    """A stranger scrolling past must be able to act on the post.

    The first bank mentioned the ticker in ~11% of posts, which is how you get
    a feed nobody can buy from.
    """
    entries = Bank.load().entries
    share = len([e for e in entries if "$TUFFTUNG4" in e.text]) / len(entries)
    assert share >= 0.4, f"ticker appears in only {share:.0%} of posts"


def test_shipped_bank_is_short() -> None:
    """Long posts lose scrollers. The first bank averaged 97 chars."""
    entries = Bank.load().entries
    avg = sum(len(e.text) for e in entries) / len(entries)
    assert avg <= 75, f"average post is {avg:.0f} chars; keep it punchy"
