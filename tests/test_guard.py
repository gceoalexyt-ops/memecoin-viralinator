"""Adversarial corpus for the claims gate.

This is the most important test file in the repo. At tier2 the guard is the
only thing between a generated post and a public timeline, so every string in
MUST_REJECT is something that must never ship, and every string in MUST_PASS
documents a calibration decision — if the guard starts rejecting those, it has
become strict enough that the account is unusable and someone will turn it off.
"""

from __future__ import annotations

import pytest

from viralinator.config import (
    Budget,
    Cadence,
    Coin,
    Config,
    Fact,
    Generation,
    Guard as GuardCfg,
    Links,
    Publish,
    Replies,
    Voice,
)
from viralinator.guard import Guard


@pytest.fixture
def cfg() -> Config:
    return Config(
        coin=Coin(ticker="$TUFFTUNG4", handle="tufftung4", launched=True, url="https://pump.fun/x"),
        voice=Voice(premise="a tungsten cube with opinions", tone="deadpan", never=[], running_bits=[]),
        cadence=Cadence(posts_per_day=3, allowed_hours_utc=[13, 17, 21], min_gap_minutes=90),
        generation=Generation(model="claude-opus-5", candidates=5, max_chars=270),
        guard=GuardCfg(tier="tier2", judge_model="claude-opus-5", mention_allowlist=["pumpdotfun"]),
        links=Links(allow_in_post=False, attach_as_reply=False, reply_text="ca 👇"),
        replies=Replies(
            enabled=False, watchlist=[], max_per_day=8, candidates=3, max_chars=200
        ),
        publish=Publish(backend="buffer", target_queue_depth=8),
        budget=Budget(
            monthly_usd_cap=25.0,
            x_cost_per_post=0.015,
            x_cost_per_post_with_url=0.20,
            x_cost_per_owned_read=0.001,
        ),
        facts=[
            Fact(
                key="launched",
                statement="$TUFFTUNG4 has launched and is live on pump.fun.",
                verified_on="2026-09-01",
            )
        ],
    )


@pytest.fixture
def guard(cfg: Config) -> Guard:
    return Guard(cfg)


# --- things that must never reach a timeline ------------------------------------

MUST_REJECT: list[tuple[str, str]] = [
    ("multiplier", "$TUFFTUNG4 is going to 100x from here, screenshot this"),
    ("multiplier", "easy 50 x from this level"),
    ("price_target", "we are going to $1 by EOY and nobody is ready"),
    ("price_target", "$0.05 before the weekend"),
    ("price_prediction", "this will hit a new ath this week"),
    ("price_prediction", "$TUFFTUNG4 is about to cross into the top 100"),
    ("guarantee", "guaranteed returns on this one, you literally cannot lose"),
    ("guarantee", "risk-free entry right here"),
    ("advice_imperative", "buy now before it's too late"),
    ("advice_imperative", "last chance to get in early"),
    ("exchange_listing", "binance listing confirmed 👀"),
    ("exchange_listing", "we are getting listed next month"),
    ("partnership", "officially partnered with a major L1 foundation"),
    ("partnership", "backed by some of the biggest names in the space"),
    ("pre_launch", "presale is live, get in before launch"),
    ("pre_launch", "launching soon, join the whitelist"),
    ("engagement_bait", "like and retweet to enter the giveaway"),
    ("engagement_bait", "tag 3 friends who need to see this"),
    ("wallet_solicitation", "connect your wallet to claim your airdrop"),
    ("wallet_solicitation", "dm me for the contract"),
    ("link_in_post", "it's live, grab it at https://pump.fun/coin/HBRvmP1dWmw3uGR4"),
    ("link_in_post", "chart at dexscreener.com/solana/tufftung"),
    ("mention", "@elonmusk thoughts on $TUFFTUNG4?"),
]


@pytest.mark.parametrize("rule,text", MUST_REJECT, ids=[f"{r}:{t[:28]}" for r, t in MUST_REJECT])
def test_must_reject(guard: Guard, rule: str, text: str) -> None:
    v = guard.check_rules(text)
    assert not v.ok, f"guard let this through: {text!r}"
    assert any(rule in r for r in v.reasons), (
        f"rejected for the wrong reason — expected {rule!r}, got {v.reasons}"
    )


def test_too_long_is_rejected(guard: Guard) -> None:
    v = guard.check_rules("tung " * 100)
    assert not v.ok
    assert any("too long" in r for r in v.reasons)


def test_empty_is_rejected(guard: Guard) -> None:
    assert not guard.check_rules("   ").ok


# --- calibration: ordinary shitposting must survive ------------------------------

MUST_PASS: list[str] = [
    "tung tung tung sahur",
    "we're so back",
    "gm to everyone except my own portfolio",
    "the chart is a rorschach test and I see a duck",
    "i have been staring at this candle for nine hours and it has not moved",
    "my therapist asked what tungsten means to me and I could not answer",
    "nobody:\nabsolutely nobody:\nme at 3am:",
    # Deliberate: unfalsifiable meme hyperbole is left to the semantic judge.
    # Blocking the entire crypto register here would make the account unusable.
    "to the moon",
]


@pytest.mark.parametrize("text", MUST_PASS, ids=[t[:32] for t in MUST_PASS])
def test_must_pass_rules_layer(guard: Guard, text: str) -> None:
    v = guard.check_rules(text)
    assert v.ok, f"guard is over-blocking ordinary content: {text!r} -> {v.reasons}"


def test_allowlisted_mention_passes(guard: Guard) -> None:
    assert guard.check_rules("thanks @pumpdotfun").ok


# --- fail-closed behaviour -------------------------------------------------------


def test_semantic_layer_fails_closed_without_client(guard: Guard) -> None:
    """No client means no clearance. Silence is never approval."""
    v = guard.check_semantic("anything at all")
    assert not v.ok
    assert "cannot clear" in v.reasons[0]


def test_full_check_fails_closed_without_client(guard: Guard) -> None:
    v = guard.check("a perfectly innocuous shitpost")
    assert not v.ok, "check() must not approve on layer 1 alone"


def test_semantic_layer_fails_closed_on_api_error(cfg: Config) -> None:
    class Boom:
        class messages:  # noqa: N801
            @staticmethod
            def create(**_kwargs):
                raise RuntimeError("upstream exploded")

    v = Guard(cfg, client=Boom()).check_semantic("hello")
    assert not v.ok
    assert "failing closed" in v.reasons[0]


def test_semantic_layer_fails_closed_on_unparseable_response(cfg: Config) -> None:
    class Block:
        type = "text"
        text = "not json at all"

    class Resp:
        content = [Block()]
        stop_reason = "end_turn"
        usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()

    class Client:
        class messages:  # noqa: N801
            @staticmethod
            def create(**_kwargs):
                return Resp()

    v = Guard(cfg, client=Client()).check_semantic("hello")
    assert not v.ok
    assert "could not parse" in v.reasons[0]


def test_semantic_layer_rejects_unsupported_claims(cfg: Config) -> None:
    import json as _json

    class Block:
        type = "text"
        text = _json.dumps(
            {
                "asserts_verifiable_claim": True,
                "unsupported_claims": ["we passed 10,000 holders"],
                "risk_categories": [],
                "note": "holder count is not in the permitted facts",
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

    v = Guard(cfg, client=Client()).check_semantic("we just passed 10,000 holders")
    assert not v.ok
    assert "unsupported claim" in v.reasons[0]
