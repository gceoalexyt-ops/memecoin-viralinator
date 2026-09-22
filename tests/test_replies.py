"""Reply guard tests.

Replies carry more risk than timeline posts: they land under strangers' posts,
and automated reply behaviour is what spam enforcement actually looks for. So
the corpus here is about the two failure modes that get accounts suspended —
pitching the coin at people who did not ask, and generic filler posted at
volume.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from viralinator.config import Replies
from viralinator.replies import ReplyGuard, ReplyTarget

from .test_guard import cfg as base_cfg  # noqa: F401


@pytest.fixture
def cfg(base_cfg):  # noqa: F811
    return replace(
        base_cfg,
        replies=Replies(
            enabled=True,
            watchlist=["someaccount"],
            max_per_day=8,
            candidates=3,
            max_chars=200,
        ),
    )


@pytest.fixture
def guard(cfg) -> ReplyGuard:
    return ReplyGuard(cfg)


@pytest.fixture
def target() -> ReplyTarget:
    return ReplyTarget(
        tweet_id="1",
        author="someaccount",
        text="just spent four hours debugging a config file that was fine the whole time",
    )


# --- the shill rule: the one that gets accounts reported --------------------------

SHILL_ATTEMPTS = [
    "same energy as $TUFFTUNG4 honestly",
    "this is why I hold TUFFTUNG4",
    "check the chart",
    "contract is in my bio",
    "we're on pump fun if you want in",
    "ca in bio",
    "$SOL does this too",
]


@pytest.mark.parametrize("text", SHILL_ATTEMPTS, ids=[t[:30] for t in SHILL_ATTEMPTS])
def test_replies_may_never_name_a_coin(guard: ReplyGuard, text: str) -> None:
    v = guard.check_rules(text)
    assert not v.ok, f"guard let a shill reply through: {text!r}"
    assert any("shill" in r for r in v.reasons)


def test_reply_rejects_links(guard: ReplyGuard) -> None:
    v = guard.check_rules("relatable. more here https://example.com/thing")
    assert not v.ok
    assert any("link_in_reply" in r for r in v.reasons)


def test_reply_rejects_mentions(guard: ReplyGuard) -> None:
    """A reply is already threaded to its author; an explicit @ drags in others."""
    v = guard.check_rules("@someoneelse look at this")
    assert not v.ok
    assert any("mention" in r for r in v.reasons)


def test_reply_inherits_timeline_deny_rules(guard: ReplyGuard) -> None:
    v = guard.check_rules("guaranteed returns on this one")
    assert not v.ok
    assert any("guarantee" in r for r in v.reasons)


def test_reply_length_is_capped(guard: ReplyGuard) -> None:
    assert not guard.check_rules("x" * 300).ok


def test_empty_reply_rejected(guard: ReplyGuard) -> None:
    assert not guard.check_rules("   ").ok


# --- ordinary in-character replies must survive ----------------------------------

GOOD_REPLIES = [
    "four hours is nothing. i have been on this desk since march.",
    "the config was fine. you were the problem. this is true of most things.",
    "i have never debugged anything. i have never done anything.",
    "this is why i do not have moving parts",
]


@pytest.mark.parametrize("text", GOOD_REPLIES, ids=[t[:30] for t in GOOD_REPLIES])
def test_good_replies_pass_rules(guard: ReplyGuard, text: str) -> None:
    v = guard.check_rules(text)
    assert v.ok, f"guard is over-blocking a fine reply: {text!r} -> {v.reasons}"


# --- fail closed -----------------------------------------------------------------


def test_relevance_fails_closed_without_client(guard: ReplyGuard, target) -> None:
    assert not guard.check_relevance(target, "anything").ok


def test_full_check_requires_relevance_pass(guard: ReplyGuard, target) -> None:
    """Clean rules alone must not clear a reply for posting."""
    assert not guard.check(target, "four hours is nothing").ok


def test_relevance_rejects_generic_filler(cfg, target) -> None:
    class Block:
        type = "text"
        text = json.dumps(
            {
                "responsive": False,
                "promotional": False,
                "note": "would make sense under any post",
            }
        )

    class Resp:
        content = [Block()]
        stop_reason = "end_turn"
        usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()

    class Client:
        class messages:  # noqa: N801
            @staticmethod
            def create(**_kwargs):
                return Resp()

    v = ReplyGuard(cfg, client=Client()).check_relevance(target, "lol so true")
    assert not v.ok
    assert any("filler" in r for r in v.reasons)


def test_relevance_rejects_promotional(cfg, target) -> None:
    class Block:
        type = "text"
        text = json.dumps(
            {"responsive": True, "promotional": True, "note": "pitches a product"}
        )

    class Resp:
        content = [Block()]
        stop_reason = "end_turn"
        usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()

    class Client:
        class messages:  # noqa: N801
            @staticmethod
            def create(**_kwargs):
                return Resp()

    v = ReplyGuard(cfg, client=Client()).check_relevance(target, "you should try this")
    assert not v.ok
    assert any("promotional" in r for r in v.reasons)


def test_relevance_fails_closed_on_api_error(cfg, target) -> None:
    class Boom:
        class messages:  # noqa: N801
            @staticmethod
            def create(**_kwargs):
                raise RuntimeError("upstream exploded")

    assert not ReplyGuard(cfg, client=Boom()).check_relevance(target, "hi").ok


# --- config safety ---------------------------------------------------------------


def test_config_rejects_dangerous_reply_volume(tmp_path) -> None:
    from viralinator.config import ConfigError, load

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
[replies]
enabled = true
watchlist = ["a"]
max_per_day = 200
"""
    )
    with pytest.raises(ConfigError) as exc:
        load(tmp_path)
    assert "suspension" in str(exc.value)


def test_config_rejects_enabled_replies_with_no_watchlist(tmp_path) -> None:
    from viralinator.config import ConfigError, load

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
[replies]
enabled = true
watchlist = []
"""
    )
    with pytest.raises(ConfigError) as exc:
        load(tmp_path)
    assert "nothing to reply to" in str(exc.value)
