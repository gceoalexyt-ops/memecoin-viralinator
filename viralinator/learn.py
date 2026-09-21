"""The feedback loop: measured performance -> format weights.

A post's score is its weighted engagement relative to this account's own
average. Relative, not absolute, because an account with 200 followers and an
account with 200,000 need completely different thresholds for "this worked",
and the only baseline that means anything is the account's own history.

Engagement weights reflect how X's ranking treats each action — replies and
bookmarks are worth far more than likes, and a like is close to noise.
"""

from __future__ import annotations

from dataclasses import dataclass

from .store import PostRow, Store

ENGAGEMENT_WEIGHTS = {
    "replies": 3.0,
    "bookmarks": 2.5,
    "reposts": 2.0,
    "quotes": 2.0,
    "likes": 1.0,
}

# A format's score is clipped so one viral outlier cannot pin the weighting to
# a single format forever.
SCORE_FLOOR = -0.8
SCORE_CEIL = 2.0


@dataclass
class Scored:
    post: PostRow
    raw: float
    rate: float | None


def _weighted_engagement(metrics) -> float:
    return sum(w * (metrics[k] or 0) for k, w in ENGAGEMENT_WEIGHTS.items())


def score_posts(store: Store) -> list[Scored]:
    """Score every posted item that has metrics."""
    out: list[Scored] = []
    for post in store.posted_since("0000"):
        m = store.latest_metrics(post.id)
        if m is None:
            continue
        raw = _weighted_engagement(m)
        impressions = m["impressions"] or 0
        rate = (raw / impressions) if impressions > 0 else None
        out.append(Scored(post=post, raw=raw, rate=rate))
    return out


def update_weights(store: Store) -> dict[str, float]:
    """Recompute format scores from all measured posts.

    Returns the relative score applied per format, for logging.
    """
    scored = score_posts(store)
    if len(scored) < 2:
        # One data point tells you nothing; don't move weights on it.
        return {}

    # Prefer engagement rate when impressions are available — it controls for
    # how many people actually saw the post. Fall back to raw counts when the
    # publishing backend doesn't expose impressions.
    use_rate = all(s.rate is not None for s in scored)
    values = [(s.rate if use_rate else s.raw) for s in scored]
    baseline = sum(values) / len(values)

    if baseline <= 0:
        return {}

    applied: dict[str, float] = {}
    for s, v in zip(scored, values):
        relative = (v / baseline) - 1.0
        relative = max(SCORE_FLOOR, min(SCORE_CEIL, relative))
        store.update_format_score(s.post.format_key, relative)
        applied[s.post.format_key] = relative

    return applied


def report(store: Store) -> str:
    scores = store.get_format_scores()
    if not scores:
        return "no format scores yet — nothing has been measured"
    lines = ["format                 n     score"]
    for key, (n, mean) in sorted(scores.items(), key=lambda kv: -kv[1][1]):
        lines.append(f"{key:<22} {n:>3}   {mean:+.3f}")
    return "\n".join(lines)
