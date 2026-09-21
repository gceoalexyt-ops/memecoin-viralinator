"""Choosing what to write, and which draft of it to keep.

Two separate decisions:

  pick_format()    — which format to write in. Driven by measured performance
                     once learn.py has data, with an exploration bonus so a
                     format is never abandoned on one bad sample.

  pick_candidate() — which of the surviving candidates to publish. Before
                     metrics exist this is weak heuristics, and it is honest to
                     say so: its main real job is avoiding repetition of what
                     was posted recently.
"""

from __future__ import annotations

import math
import random
import re

from .formats import Format, selectable
from .generate import Candidate
from .store import Store

# Formats with few observations get an optimism bonus so exploration continues.
EXPLORATION_WEIGHT = 0.6

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "it", "to", "of", "in", "on",
    "for", "my", "i", "you", "this", "that", "at", "be", "was", "not", "with",
}


def _weight(fmt: Format, scores: dict[str, tuple[int, float]]) -> float:
    n, mean = scores.get(fmt.key, (0, 0.0))
    # Unseen formats get the full exploration bonus; it decays as n grows.
    bonus = EXPLORATION_WEIGHT / math.sqrt(n + 1)
    return max(0.01, fmt.base_weight * (1.0 + mean) + bonus)


def pick_format(store: Store, tier: str, rng: random.Random | None = None) -> Format:
    r = rng or random
    options = selectable(tier)
    scores = store.get_format_scores()
    weights = [_weight(f, scores) for f in options]
    return r.choices(options, weights=weights, k=1)[0]


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z']+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def _similarity(a: str, b: str) -> float:
    """Jaccard overlap on content words. Cheap, and good enough to catch a
    candidate that is essentially last Tuesday's post again."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)


def _heuristic_score(text: str, recent: list[str]) -> float:
    score = 0.0

    # A line break creates a beat before the payoff, which holds attention.
    if "\n" in text.strip():
        score += 0.15

    # Short posts travel further, all else equal.
    length = len(text)
    if length <= 120:
        score += 0.2
    elif length <= 200:
        score += 0.1

    # House style is at most one emoji.
    emoji = len(EMOJI_RE.findall(text))
    if emoji == 0:
        score += 0.05
    elif emoji > 1:
        score -= 0.3 * (emoji - 1)

    # Repetition is the main thing worth actively avoiding.
    if recent:
        worst = max(_similarity(text, r) for r in recent)
        score -= worst * 1.5

    return score


def pick_candidate(
    candidates: list[Candidate], store: Store, lookback: int = 30
) -> Candidate | None:
    if not candidates:
        return None

    recent_rows = store.posted_since("0000")[-lookback:]
    recent = [r.body for r in recent_rows]

    return max(candidates, key=lambda c: _heuristic_score(c.text, recent))
