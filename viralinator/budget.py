"""Spend ceiling enforcement.

X's API is pay-per-use and this pipeline runs unattended on a cron. That
combination is how people wake up to a bill they did not agree to. Every
billable call goes through here, and the pipeline hard-stops at the cap.

Anthropic token pricing (USD per million tokens), current as of 2026-06:
    claude-opus-5    $5.00 in  / $25.00 out
    claude-sonnet-5  $2.00 in  / $10.00 out
    claude-haiku-4-5 $1.00 in  / $5.00 out
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Config
from .store import Store

ANTHROPIC_RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


class BudgetExceeded(Exception):
    """Raised when an action would push this month's spend past the cap."""


@dataclass
class BudgetStatus:
    spent: float
    cap: float

    @property
    def remaining(self) -> float:
        return max(0.0, self.cap - self.spent)

    @property
    def pct(self) -> float:
        return (self.spent / self.cap * 100) if self.cap else 0.0

    def __str__(self) -> str:
        return f"${self.spent:.3f} / ${self.cap:.2f} ({self.pct:.1f}%)"


def month_start_iso(now: datetime | None = None) -> str:
    n = now or datetime.now(timezone.utc)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(
        timespec="seconds"
    )


class Budget:
    def __init__(self, cfg: Config, store: Store) -> None:
        self.cfg = cfg
        self.store = store

    def status(self) -> BudgetStatus:
        return BudgetStatus(
            spent=self.store.spend_since(month_start_iso()),
            cap=self.cfg.budget.monthly_usd_cap,
        )

    def check(self, projected_usd: float = 0.0) -> None:
        """Raise if we are at the cap, or if `projected_usd` would cross it."""
        st = self.status()
        if st.spent >= st.cap:
            raise BudgetExceeded(
                f"monthly cap reached: {st}. Raise budget.monthly_usd_cap in "
                f"config/brand.toml or wait for the month to roll over."
            )
        if projected_usd and st.spent + projected_usd > st.cap:
            raise BudgetExceeded(
                f"this action (~${projected_usd:.3f}) would cross the cap: {st}"
            )

    # --- recording -------------------------------------------------------------

    def post_cost(self, has_url: bool) -> float:
        return (
            self.cfg.budget.x_cost_per_post_with_url
            if has_url
            else self.cfg.budget.x_cost_per_post
        )

    def record_post(self, has_url: bool, note: str = "") -> float:
        usd = self.post_cost(has_url)
        kind = "post_with_url" if has_url else "post"
        self.store.record_spend("x", kind, usd, note)
        return usd

    def record_reads(self, count: int, note: str = "") -> float:
        usd = count * self.cfg.budget.x_cost_per_owned_read
        self.store.record_spend("x", "owned_read", usd, note or f"{count} reads")
        return usd

    def record_llm(self, model: str, input_tokens: int, output_tokens: int, note: str = "") -> float:
        rate_in, rate_out = ANTHROPIC_RATES.get(model, ANTHROPIC_RATES["claude-opus-5"])
        usd = (input_tokens / 1_000_000 * rate_in) + (output_tokens / 1_000_000 * rate_out)
        self.store.record_spend("anthropic", model, usd, note)
        return usd
