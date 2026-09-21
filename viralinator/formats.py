"""The format library — the actual growth strategy, encoded.

Each format is a distinct shape of post with its own instruction to the
generator. rank.py weights them by measured performance, so the ones that work
for THIS account get used more over time. The starting weights below are
priors, not conclusions.

What the weights encode about how X ranks content:

  - Replies and conversation depth are weighted heavily. Formats that invite a
    genuine response beat formats that invite a like.
  - Bookmarks are a strong positive signal and an underused one — "save this"
    content punches above its like count.
  - Dwell time matters, which is why setups with a beat before the payoff
    (multi-line, line breaks) outperform one-liners of the same content.
  - External links are organically suppressed, which is why no format here
    produces one. The link goes in a self-reply.
  - Early velocity matters: the first ~30 minutes of engagement shapes how far
    a post travels, which is why cadence.allowed_hours_utc exists.
  - Engagement bait ("like if...") is detected and penalised, and is blocked
    outright by the guard.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Format:
    key: str
    description: str
    instruction: str
    # "safe" = asserts nothing checkable. "claims" = may reference facts.toml,
    # so tier1 holds it for review.
    category: str
    base_weight: float
    # Formats that read better with an image attached. The generator notes this
    # so you can pair one manually; images materially outperform plain text.
    wants_visual: bool = False


FORMATS: list[Format] = [
    Format(
        key="lore_serial",
        description="An installment in an ongoing story",
        instruction=(
            "Write the next beat in the coin's ongoing lore as if the audience "
            "already knows the story. Do not explain the premise or recap. "
            "Treat it as episode 47 of something. Serialised narrative is the "
            "single strongest retention format — people follow accounts to find "
            "out what happens next."
        ),
        category="safe",
        base_weight=1.3,
    ),
    Format(
        key="absurd_observation",
        description="Deadpan non-sequitur, in character",
        instruction=(
            "One absurd observation delivered completely straight. No setup, no "
            "punchline signposting, no explanation. The humour comes from the "
            "flatness of the delivery. Shorter is better here."
        ),
        category="safe",
        base_weight=1.2,
    ),
    Format(
        key="relatable_degen",
        description="The shared experience of being a trader",
        instruction=(
            "Name a specific, slightly embarrassing experience that everyone "
            "holding a memecoin has had but nobody says out loud. Specificity is "
            "what makes this travel — 'checking the chart at 4am' is generic, "
            "'I have the chart open in a tab I pretend is work' is not."
        ),
        category="safe",
        base_weight=1.25,
    ),
    Format(
        key="reply_bait_question",
        description="A real question that invites a real answer",
        instruction=(
            "Ask something the audience genuinely wants to answer — a preference, "
            "a confession, a ranking, a 'what would you do'. It must be a real "
            "question with interesting possible answers, not a prompt to agree "
            "with you. Never ask for likes, follows, or reposts. Replies are the "
            "heaviest positive signal there is."
        ),
        category="safe",
        base_weight=1.4,
    ),
    Format(
        key="running_bit",
        description="The recurring format the audience expects",
        instruction=(
            "Write an installment of one of the account's running bits. If the "
            "brand config lists running_bits, use one of those. A predictable "
            "recurring format trains people to come back, and gives the "
            "community something to imitate."
        ),
        category="safe",
        base_weight=1.15,
    ),
    Format(
        key="self_aware_meta",
        description="Jokes about being a memecoin",
        instruction=(
            "Be openly, cheerfully aware that this is a memecoin. Self-awareness "
            "reads as honesty and is disarming in a space full of accounts "
            "pretending to be serious companies. Do not be cynical about holders "
            "— the joke is never at the audience's expense."
        ),
        category="safe",
        base_weight=1.1,
    ),
    Format(
        key="visual_caption",
        description="A caption built for an image",
        instruction=(
            "Write a caption designed to sit under an image or meme, and describe "
            "the image it needs in one line prefixed with 'IMAGE:'. The caption "
            "must not restate the image. Posts with media substantially "
            "outperform plain text."
        ),
        category="safe",
        base_weight=1.35,
        wants_visual=True,
    ),
    Format(
        key="community_spotlight",
        description="Celebrate something a holder made",
        instruction=(
            "Write a post that hands the spotlight to the community — reacting to "
            "holder-made art, a joke someone else started, or a bit the replies "
            "invented. Keep it warm and specific. This is what converts an "
            "audience into participants."
        ),
        category="safe",
        base_weight=1.2,
    ),
    Format(
        key="contrarian_take",
        description="Mild, arguable spice",
        instruction=(
            "Take a genuinely arguable position about internet culture, memes, or "
            "crypto social dynamics that a reasonable person could disagree with. "
            "It must be about culture, never about price, returns, or any "
            "specific token's prospects. Disagreement drives reply depth."
        ),
        category="safe",
        base_weight=1.1,
    ),
    Format(
        key="holding_joke",
        description="Not moving is not selling — the core bit",
        instruction=(
            "The cube does not move. That is also what holders do. Land that "
            "joke without ever explaining it. Diamond hands, paper hands, "
            "conviction, never selling — all of it reframed as the physical "
            "properties of a very heavy object. This is the account's single "
            "strongest promotional format because the premise and the pitch are "
            "the same sentence. Never mention price, returns, or what the token "
            "will do — the joke is about immobility, not gains."
        ),
        category="safe",
        base_weight=1.5,
    ),
    Format(
        key="ticker_forward",
        description="Names the ticker, stays a joke",
        instruction=(
            "Use the ticker in the post. Say it plainly, then undercut it — the "
            "name is too long, it is hard to type, nobody chose it well. Naming "
            "the thing you are is not marketing spin, and a feed where the "
            "ticker never appears is a feed nobody can act on. Make no claim "
            "about the token beyond its existence."
        ),
        category="safe",
        base_weight=1.35,
    ),
    Format(
        key="anti_marketing",
        description="Marketing, performed badly, on purpose",
        instruction=(
            "Do promotion openly and terribly. Admit there is no utility, no "
            "roadmap, no team, no plan. The honesty IS the pitch — it reads as "
            "trustworthy in a space full of accounts overpromising. Never "
            "actually promise anything, and never tell anyone to buy."
        ),
        category="safe",
        base_weight=1.4,
    ),
    Format(
        key="comparative_flex",
        description="Versus every other coin, favourably, absurdly",
        instruction=(
            "Compare this account to other memecoins — they have dogs, frogs, "
            "founders doing podcasts, whitepapers, roadmaps. This one has a "
            "heavy object that stays put. Punch at the category, never at a "
            "specific named token or person, and make no claim about any "
            "token's prospects including this one."
        ),
        category="safe",
        base_weight=1.3,
    ),
    Format(
        key="where_to_find",
        description="Points at the bio without a link",
        instruction=(
            "Direct people to the contract or chart, which live in the bio and "
            "the pinned post. The cube cannot point, having no arms and no "
            "intention of moving. Never include a URL in the post body — links "
            "are organically suppressed and the guard rejects them. Never tell "
            "anyone to buy; just say where the information is."
        ),
        category="safe",
        base_weight=1.15,
    ),
    Format(
        key="milestone",
        description="Mark something that actually happened",
        instruction=(
            "Mark a real milestone. You may ONLY reference facts supplied to you "
            "in the permitted-facts list. If no fact supports a milestone right "
            "now, say so instead of inventing one — an invented milestone is a "
            "false public statement about a financial asset."
        ),
        category="claims",
        base_weight=0.9,
    ),
]

BY_KEY: dict[str, Format] = {f.key: f for f in FORMATS}


def get(key: str) -> Format:
    return BY_KEY[key]


def safe_formats() -> list[Format]:
    return [f for f in FORMATS if f.category == "safe"]


def selectable(tier: str) -> list[Format]:
    """Which formats a given autonomy tier is allowed to generate.

    At tier1 the claim-bearing formats would be held for human review, and
    nobody is reviewing, so they are simply not generated.
    """
    if tier == "tier2":
        return list(FORMATS)
    return safe_formats()
