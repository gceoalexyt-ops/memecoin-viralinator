"""Typed configuration load and validation.

Config errors surface here, loudly, at startup — not three steps into an
unattended cron run that has already spent money.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"

VALID_TIERS = ("tier0", "tier1", "tier2")


class ConfigError(Exception):
    """Raised when config is missing or internally inconsistent."""


@dataclass(frozen=True)
class Coin:
    ticker: str
    handle: str
    launched: bool
    url: str


@dataclass(frozen=True)
class Voice:
    premise: str
    tone: str
    never: list[str]
    running_bits: list[str]


@dataclass(frozen=True)
class Cadence:
    posts_per_day: int
    allowed_hours_utc: list[int]
    min_gap_minutes: int


@dataclass(frozen=True)
class Generation:
    model: str
    candidates: int
    max_chars: int


@dataclass(frozen=True)
class Guard:
    tier: str
    judge_model: str
    mention_allowlist: list[str]


@dataclass(frozen=True)
class Links:
    allow_in_post: bool
    attach_as_reply: bool
    reply_text: str


@dataclass(frozen=True)
class Replies:
    enabled: bool
    # Accounts whose posts are candidates to reply to. Empty = replies off.
    watchlist: list[str]
    # Hard ceiling per day. Low on purpose: a handful of thoughtful replies is
    # a person participating, a hundred is a bot whatever they say.
    max_per_day: int
    candidates: int
    max_chars: int


@dataclass(frozen=True)
class Publish:
    backend: str
    # How full to keep Buffer's queue. The free plan caps it at 10, so this
    # stays under that — a full queue makes createPost fail.
    target_queue_depth: int


@dataclass(frozen=True)
class Budget:
    monthly_usd_cap: float
    x_cost_per_post: float
    x_cost_per_post_with_url: float
    x_cost_per_owned_read: float


@dataclass(frozen=True)
class Fact:
    key: str
    statement: str
    verified_on: str

    @property
    def usable(self) -> bool:
        """A fact with no statement is a placeholder, not a fact."""
        return bool(self.statement.strip())


@dataclass(frozen=True)
class Config:
    coin: Coin
    voice: Voice
    cadence: Cadence
    generation: Generation
    guard: Guard
    links: Links
    replies: Replies
    publish: Publish
    budget: Budget
    facts: list[Fact] = field(default_factory=list)

    @property
    def usable_facts(self) -> list[Fact]:
        return [f for f in self.facts if f.usable]


def _require(value: str, path: str, errors: list[str]) -> str:
    if not str(value).strip():
        errors.append(f"{path} is required but empty (see config/brand.toml)")
    return value


def load(config_dir: Path | None = None) -> Config:
    """Load and validate brand.toml + facts.toml.

    Raises ConfigError listing *every* problem found, so a misconfigured repo
    can be fixed in one pass rather than one error at a time.
    """
    cdir = config_dir or CONFIG_DIR
    brand_path = cdir / "brand.toml"
    facts_path = cdir / "facts.toml"

    if not brand_path.exists():
        raise ConfigError(f"missing {brand_path}")

    with brand_path.open("rb") as fh:
        raw = tomllib.load(fh)

    facts_raw: dict = {}
    if facts_path.exists():
        with facts_path.open("rb") as fh:
            facts_raw = tomllib.load(fh)

    errors: list[str] = []

    coin_r = raw.get("coin", {})
    coin = Coin(
        ticker=_require(coin_r.get("ticker", ""), "coin.ticker", errors),
        handle=_require(coin_r.get("handle", ""), "coin.handle", errors),
        launched=bool(coin_r.get("launched", True)),
        url=coin_r.get("url", ""),
    )

    voice_r = raw.get("voice", {})
    voice = Voice(
        premise=_require(voice_r.get("premise", ""), "voice.premise", errors),
        tone=_require(voice_r.get("tone", ""), "voice.tone", errors),
        never=list(voice_r.get("never", [])),
        running_bits=list(voice_r.get("running_bits", [])),
    )

    cad_r = raw.get("cadence", {})
    cadence = Cadence(
        posts_per_day=int(cad_r.get("posts_per_day", 3)),
        allowed_hours_utc=list(cad_r.get("allowed_hours_utc", [])),
        min_gap_minutes=int(cad_r.get("min_gap_minutes", 90)),
    )

    gen_r = raw.get("generation", {})
    generation = Generation(
        model=gen_r.get("model", "claude-opus-5"),
        candidates=int(gen_r.get("candidates", 5)),
        max_chars=int(gen_r.get("max_chars", 270)),
    )

    guard_r = raw.get("guard", {})
    guard = Guard(
        tier=guard_r.get("tier", "tier2"),
        judge_model=guard_r.get("judge_model", "claude-opus-5"),
        mention_allowlist=[m.lstrip("@").lower() for m in guard_r.get("mention_allowlist", [])],
    )

    links_r = raw.get("links", {})
    links = Links(
        allow_in_post=bool(links_r.get("allow_in_post", False)),
        attach_as_reply=bool(links_r.get("attach_as_reply", True)),
        reply_text=links_r.get("reply_text", ""),
    )

    rep_r = raw.get("replies", {})
    replies = Replies(
        enabled=bool(rep_r.get("enabled", False)),
        watchlist=[h.lstrip("@").lower() for h in rep_r.get("watchlist", [])],
        max_per_day=int(rep_r.get("max_per_day", 8)),
        candidates=int(rep_r.get("candidates", 3)),
        max_chars=int(rep_r.get("max_chars", 200)),
    )

    pub_r = raw.get("publish", {})
    publish = Publish(
        backend=pub_r.get("backend", "buffer"),
        target_queue_depth=int(pub_r.get("target_queue_depth", 8)),
    )

    bud_r = raw.get("budget", {})
    budget = Budget(
        monthly_usd_cap=float(bud_r.get("monthly_usd_cap", 25.0)),
        x_cost_per_post=float(bud_r.get("x_cost_per_post", 0.015)),
        x_cost_per_post_with_url=float(bud_r.get("x_cost_per_post_with_url", 0.20)),
        x_cost_per_owned_read=float(bud_r.get("x_cost_per_owned_read", 0.001)),
    )

    facts = [
        Fact(
            key=f.get("key", ""),
            statement=f.get("statement", ""),
            verified_on=f.get("verified_on", ""),
        )
        for f in facts_raw.get("fact", [])
    ]

    # --- cross-field validation -------------------------------------------------
    if guard.tier not in VALID_TIERS:
        errors.append(f"guard.tier must be one of {VALID_TIERS}, got {guard.tier!r}")

    if not coin.launched:
        errors.append(
            "coin.launched is false, but this coin has already launched. "
            "The guard rejects pre-launch framing; leave this true."
        )

    if cadence.posts_per_day > len(cadence.allowed_hours_utc):
        errors.append(
            f"cadence.posts_per_day ({cadence.posts_per_day}) exceeds the number of "
            f"allowed_hours_utc ({len(cadence.allowed_hours_utc)}) — add hours or post less"
        )

    if any(not 0 <= h <= 23 for h in cadence.allowed_hours_utc):
        errors.append("cadence.allowed_hours_utc must all be in 0..23")

    if generation.candidates < 1:
        errors.append("generation.candidates must be at least 1")

    if generation.max_chars > 280:
        errors.append("generation.max_chars cannot exceed 280")

    if links.allow_in_post:
        errors.append(
            "links.allow_in_post is true. Posts with URLs cost ~13x more and are "
            "organically suppressed by X. Set false and use attach_as_reply."
        )

    if budget.monthly_usd_cap <= 0:
        errors.append("budget.monthly_usd_cap must be positive")

    if replies.enabled and not replies.watchlist:
        errors.append(
            "replies.enabled is true but replies.watchlist is empty — there is "
            "nothing to reply to"
        )

    if replies.max_per_day > 25:
        errors.append(
            f"replies.max_per_day is {replies.max_per_day}. Automated replies at "
            "volume are the fastest route to a suspension; keep this low."
        )

    if replies.max_chars > 280:
        errors.append("replies.max_chars cannot exceed 280")

    if publish.backend not in ("buffer",):
        errors.append(f"publish.backend {publish.backend!r} is not implemented")

    if publish.target_queue_depth > 10:
        errors.append(
            "publish.target_queue_depth exceeds Buffer's free-plan queue cap of 10; "
            "createPost fails once the queue is full"
        )

    for f in facts:
        if f.usable and not f.verified_on.strip():
            errors.append(f"fact {f.key!r} has a statement but no verified_on date")

    if errors:
        raise ConfigError(
            "config is not ready:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    return Config(
        coin=coin,
        voice=voice,
        cadence=cadence,
        generation=generation,
        guard=guard,
        links=links,
        replies=replies,
        publish=publish,
        budget=budget,
        facts=facts,
    )
