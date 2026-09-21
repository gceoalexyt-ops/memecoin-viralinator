"""Candidate generation.

One call produces N candidates for a chosen format. Generating a batch in a
single request is both cheaper and better than N separate calls — the model can
see its own earlier attempts and deliberately vary them instead of returning
five paraphrases of the same joke.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import anthropic

from .config import Config
from .formats import Format

SYSTEM = """\
You write posts for the X account of {ticker}, a memecoin that has ALREADY \
LAUNCHED and is trading. It is not launching, not in presale, not about to \
launch. Never use pre-launch framing.

The account's premise:
{premise}

Tone: {tone}

This account never does these things:
{never}

Hard rules, which exist because a separate reviewer rejects anything that \
breaks them:

- Never mention price, market cap, returns, multiples, or where the token is \
  going. Not as a prediction, not as a joke, not ironically.
- Never claim a listing, partnership, endorsement, backer, or collaboration.
- Never state a number, date, or milestone unless it appears verbatim in the \
  permitted facts you are given. If you have no fact for it, write about \
  something else.
- Never include a URL. Links are attached separately as a reply.
- Never ask for likes, reposts, or follows.
- Never @-mention anyone.
- At most one emoji, and usually zero.

Write like a person with a specific sense of humour, not like a brand account. \
Short beats long. Specific beats general. A post that makes one person screenshot \
it beats a post that makes a hundred people scroll past it.

Do not number the candidates, do not explain them, and do not write anything \
that reads like marketing copy.\
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The post body exactly as it would appear.",
                    },
                    "image_note": {
                        "type": "string",
                        "description": (
                            "One line describing the image this post wants, or an "
                            "empty string if it needs none."
                        ),
                    },
                },
                "required": ["text", "image_note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


@dataclass
class Candidate:
    text: str
    format_key: str
    image_note: str = ""


class Generator:
    def __init__(self, cfg: Config, client: anthropic.Anthropic | None = None, budget=None) -> None:
        self.cfg = cfg
        self.client = client or anthropic.Anthropic()
        self.budget = budget

    def _system(self) -> str:
        never = "\n".join(f"- {n}" for n in self.cfg.voice.never) or "- (nothing specified)"
        return SYSTEM.format(
            ticker=self.cfg.coin.ticker,
            premise=self.cfg.voice.premise,
            tone=self.cfg.voice.tone,
            never=never,
        )

    def _facts_block(self) -> str:
        facts = self.cfg.usable_facts
        if not facts:
            return (
                "PERMITTED FACTS: none. You may not state any checkable fact, "
                "number, date, or milestone in this post."
            )
        lines = "\n".join(f"- {f.statement}" for f in facts)
        return f"PERMITTED FACTS (the only checkable things you may state):\n{lines}"

    def generate(self, fmt: Format, n: int | None = None) -> list[Candidate]:
        n = n or self.cfg.generation.candidates
        bits = self.cfg.voice.running_bits
        bits_block = (
            "Running bits this account uses:\n" + "\n".join(f"- {b}" for b in bits)
            if bits
            else ""
        )

        prompt = (
            f"{self._facts_block()}\n\n"
            f"{bits_block}\n\n"
            f"FORMAT: {fmt.description}\n{fmt.instruction}\n\n"
            f"Write {n} genuinely different candidates in this format. They should "
            f"differ in angle and structure, not just wording — if two of them could "
            f"be swapped without anyone noticing, one of them is wasted.\n"
            f"Hard limit: {self.cfg.generation.max_chars} characters each."
        )

        with self.client.messages.stream(
            model=self.cfg.generation.model,
            max_tokens=8000,
            system=self._system(),
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        ) as stream:
            resp = stream.get_final_message()

        if self.budget is not None:
            self.budget.record_llm(
                self.cfg.generation.model,
                resp.usage.input_tokens,
                resp.usage.output_tokens,
                note=f"generate:{fmt.key}",
            )

        if getattr(resp, "stop_reason", None) == "refusal":
            return []

        try:
            block = next(b for b in resp.content if b.type == "text")
            data = json.loads(block.text)
        except (StopIteration, json.JSONDecodeError):
            return []

        return [
            Candidate(
                text=c["text"].strip(),
                format_key=fmt.key,
                image_note=c.get("image_note", "").strip(),
            )
            for c in data.get("candidates", [])
            if c.get("text", "").strip()
        ]
