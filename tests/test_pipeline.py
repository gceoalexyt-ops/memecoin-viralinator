"""Wiring tests for everything that isn't the guard.

No network. These exist to catch the boring failures — a config that validates
when it shouldn't, a budget cap that doesn't stop anything, a ranker that
happily posts the same joke twice.
"""

from __future__ import annotations

import random

import pytest

from viralinator import learn, rank
from viralinator.budget import Budget, BudgetExceeded
from viralinator.config import ConfigError, load
from viralinator.generate import Candidate
from viralinator.store import Store

from .test_guard import cfg  # noqa: F401  — reuse the fixture


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "test.db")


# --- config validation -----------------------------------------------------------


def test_shipped_config_loads() -> None:
    """The repo's own config must be valid — CI catches a broken brand.toml
    before the cron does."""
    cfg = load()
    assert cfg.coin.ticker == "$TUFFTUNG4"
    assert cfg.voice.premise.strip(), "voice.premise must not be blank"
    assert cfg.voice.tone.strip(), "voice.tone must not be blank"


def test_shipped_config_asserts_no_facts() -> None:
    """The cube voice never states anything checkable. An empty facts list is
    the safest configuration and this test exists so nobody adds one casually."""
    assert load().usable_facts == []


def test_missing_voice_is_rejected(tmp_path) -> None:
    (tmp_path / "brand.toml").write_text(
        """
[coin]
ticker = "$X"
handle = "x"
launched = true
[voice]
premise = ""
tone = ""
[cadence]
posts_per_day = 1
allowed_hours_utc = [12]
"""
    )
    with pytest.raises(ConfigError) as exc:
        load(tmp_path)
    assert "voice.premise" in str(exc.value)


def test_config_rejects_oversized_queue_target(tmp_path) -> None:
    (tmp_path / "brand.toml").write_text(
        """
[coin]
ticker = "$X"
handle = "x"
launched = true
[voice]
premise = "p"
tone = "t"
[cadence]
posts_per_day = 1
allowed_hours_utc = [12]
[publish]
target_queue_depth = 50
"""
    )
    with pytest.raises(ConfigError) as exc:
        load(tmp_path)
    assert "queue cap of 10" in str(exc.value)


def test_config_rejects_links_in_post(tmp_path) -> None:
    (tmp_path / "brand.toml").write_text(
        """
[coin]
ticker = "$X"
handle = "x"
launched = true
[voice]
premise = "p"
tone = "t"
[cadence]
posts_per_day = 1
allowed_hours_utc = [12]
[links]
allow_in_post = true
"""
    )
    with pytest.raises(ConfigError) as exc:
        load(tmp_path)
    assert "allow_in_post" in str(exc.value)


# --- store -----------------------------------------------------------------------


def test_draft_lifecycle(store: Store) -> None:
    pid = store.record_draft("lore_serial", "hello")
    store.mark_posted(pid, "buffer:1")
    posted = store.posted_since("0000")
    assert len(posted) == 1
    assert posted[0].status == "posted"


def test_rejected_drafts_are_not_posted(store: Store) -> None:
    pid = store.record_draft("lore_serial", "bad")
    store.mark_rejected(pid, "guard said no")
    assert store.posted_since("0000") == []


def test_format_score_is_a_running_mean(store: Store) -> None:
    store.update_format_score("lore_serial", 1.0)
    store.update_format_score("lore_serial", 0.0)
    n, mean = store.get_format_scores()["lore_serial"]
    assert n == 2
    assert mean == pytest.approx(0.5)


# --- budget ----------------------------------------------------------------------


def test_budget_blocks_once_cap_is_reached(cfg, store: Store) -> None:  # noqa: F811
    b = Budget(cfg, store)
    b.check()  # fine at zero
    store.record_spend("anthropic", "claude-opus-5", 25.0, "test")
    with pytest.raises(BudgetExceeded):
        b.check()


def test_budget_blocks_projected_overrun(cfg, store: Store) -> None:  # noqa: F811
    b = Budget(cfg, store)
    store.record_spend("anthropic", "claude-opus-5", 24.9, "test")
    with pytest.raises(BudgetExceeded):
        b.check(projected_usd=0.5)


def test_llm_cost_is_priced_per_model(cfg, store: Store) -> None:  # noqa: F811
    b = Budget(cfg, store)
    opus = b.record_llm("claude-opus-5", 1_000_000, 0)
    haiku = b.record_llm("claude-haiku-4-5", 1_000_000, 0)
    assert opus == pytest.approx(5.0)
    assert haiku == pytest.approx(1.0)


# --- ranking ---------------------------------------------------------------------


def test_pick_format_only_returns_safe_formats_below_tier2(store: Store) -> None:
    r = random.Random(0)
    for _ in range(50):
        assert rank.pick_format(store, "tier1", r).category == "safe"


def test_pick_format_favours_proven_formats(store: Store) -> None:
    for _ in range(40):
        store.update_format_score("reply_bait_question", 2.0)
        store.update_format_score("self_aware_meta", -0.8)

    r = random.Random(7)
    picks = [rank.pick_format(store, "tier2", r).key for _ in range(400)]
    assert picks.count("reply_bait_question") > picks.count("self_aware_meta")


def test_pick_candidate_avoids_repeating_recent_posts(store: Store) -> None:
    pid = store.record_draft("lore_serial", "the tungsten cube has filed a grievance")
    store.mark_posted(pid, "buffer:1")

    dupe = Candidate(text="the tungsten cube has filed a grievance", format_key="lore_serial")
    fresh = Candidate(text="my portfolio and I are no longer speaking", format_key="lore_serial")

    assert rank.pick_candidate([dupe, fresh], store) is fresh


def test_pick_candidate_penalises_emoji_spam(store: Store) -> None:
    clean = Candidate(text="a quiet observation about nothing", format_key="absurd_observation")
    spam = Candidate(text="a quiet observation 🚀🚀🔥🔥💎", format_key="absurd_observation")
    assert rank.pick_candidate([spam, clean], store) is clean


def test_pick_candidate_handles_empty_list(store: Store) -> None:
    assert rank.pick_candidate([], store) is None


# --- learning --------------------------------------------------------------------


def _measured(store: Store, fmt: str, body: str, **metrics) -> None:
    pid = store.record_draft(fmt, body)
    store.mark_posted(pid, f"buffer:{pid}")
    store.record_metrics(pid, **metrics)


def test_weights_do_not_move_on_a_single_datapoint(store: Store) -> None:
    _measured(store, "lore_serial", "a", likes=100, impressions=1000)
    assert learn.update_weights(store) == {}


def test_better_performing_format_scores_higher(store: Store) -> None:
    _measured(store, "reply_bait_question", "a", replies=50, likes=10, impressions=1000)
    _measured(store, "self_aware_meta", "b", replies=0, likes=2, impressions=1000)

    applied = learn.update_weights(store)
    assert applied["reply_bait_question"] > applied["self_aware_meta"]


def test_replies_outweigh_likes(store: Store) -> None:
    _measured(store, "reply_bait_question", "a", replies=10, impressions=1000)
    _measured(store, "self_aware_meta", "b", likes=10, impressions=1000)

    applied = learn.update_weights(store)
    assert applied["reply_bait_question"] > applied["self_aware_meta"]


def test_learning_falls_back_to_raw_counts_without_impressions(store: Store) -> None:
    """Buffer's free tier may not expose impressions; the loop must still work."""
    _measured(store, "reply_bait_question", "a", replies=20)
    _measured(store, "self_aware_meta", "b", likes=1)

    applied = learn.update_weights(store)
    assert applied["reply_bait_question"] > 0
    assert applied["self_aware_meta"] < 0
