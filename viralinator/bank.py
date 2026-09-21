"""The content bank.

Posts are written and screened ahead of time, in a session with a human
present, and stored in the repo. At runtime the cron does no generation at
all — it pops an unused post and hands it to Buffer.

This exists because every runtime dependency is a liability for an unattended
job. No API key in CI, no OAuth profile to expire, no metered call that can
fail or overrun a budget. The bot can keep posting for months with nothing but
a Buffer token.

Consumption is tracked in the bank file itself, not the database, and the
workflow commits the file back after each run. The Actions cache can be
evicted; git cannot. That also makes `git log config/bank.json` a readable
history of what went out and when.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import REPO_ROOT

BANK_PATH = REPO_ROOT / "config" / "bank.json"

# Bump when the entry shape changes so a stale bank fails loudly.
BANK_VERSION = 1


@dataclass
class BankEntry:
    id: str
    text: str
    format_key: str
    image_note: str = ""
    generated_at: str = ""
    used_at: str | None = None

    @property
    def used(self) -> bool:
        return self.used_at is not None


@dataclass
class Bank:
    version: int = BANK_VERSION
    entries: list[BankEntry] = field(default_factory=list)

    # --- io --------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> Bank:
        p = path or BANK_PATH
        if not p.exists():
            return cls()
        data = json.loads(p.read_text())
        version = data.get("version", 0)
        if version != BANK_VERSION:
            raise ValueError(
                f"{p} is version {version}, this code expects {BANK_VERSION}. "
                "Regenerate it with `viralinator bank fill`."
            )
        return cls(
            version=version,
            entries=[BankEntry(**e) for e in data.get("entries", [])],
        )

    def save(self, path: Path | None = None) -> None:
        p = path or BANK_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {"version": self.version, "entries": [asdict(e) for e in self.entries]},
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )

    # --- querying --------------------------------------------------------------

    def unused(self, format_key: str | None = None) -> list[BankEntry]:
        out = [e for e in self.entries if not e.used]
        if format_key:
            out = [e for e in out if e.format_key == format_key]
        return out

    def counts(self) -> dict[str, tuple[int, int]]:
        """format_key -> (unused, total)."""
        out: dict[str, tuple[int, int]] = {}
        for e in self.entries:
            unused, total = out.get(e.format_key, (0, 0))
            out[e.format_key] = (unused + (0 if e.used else 1), total + 1)
        return out

    def days_remaining(self, posts_per_day: int) -> float:
        if posts_per_day <= 0:
            return float("inf")
        return len(self.unused()) / posts_per_day

    # --- mutation --------------------------------------------------------------

    def add(self, text: str, format_key: str, image_note: str = "") -> BankEntry:
        entry = BankEntry(
            id=f"{format_key}-{len(self.entries):04d}",
            text=text,
            format_key=format_key,
            image_note=image_note,
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self.entries.append(entry)
        return entry

    def take(
        self, format_key: str | None = None, rng: random.Random | None = None
    ) -> BankEntry | None:
        """Claim an unused post. Falls back to any format if the requested one
        is exhausted — running dry on one format should not stop the account."""
        r = rng or random
        pool = self.unused(format_key) or self.unused()
        if not pool:
            return None
        chosen = r.choice(pool)
        chosen.used_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return chosen

    def contains_similar(self, text: str, threshold: float = 0.7) -> bool:
        """Cheap duplicate check so a refill doesn't re-add existing jokes."""
        from .rank import _similarity

        return any(_similarity(text, e.text) >= threshold for e in self.entries)
