# memecoin-viralinator

An unattended content pipeline for the $TUFFTUNG4 X account. It writes posts,
checks them against a claims gate, and pushes them into a Buffer queue that
posts them to X on schedule. Nobody has to be present.

```
cron ─▶ generate (Claude) ─▶ guard ─▶ rank ─▶ Buffer queue ─▶ X
                               │
                               └─▶ rejected, logged, never posted
```

## Why Buffer and not X's API

X's API is pay-per-use — roughly $0.015 a post, and $0.20 if the post contains
a link. Buffer is an official X API partner whose free plan includes 3
channels, a 10-post queue, and 3,000 API requests a month. The cron tops the
queue up and Buffer does the posting, so publishing costs nothing.

The only recurring cost is Claude: about **$4/month** at 3 posts a day with 5
candidates per slot. `config/brand.toml` has a hard monthly cap and the
pipeline refuses to run past it.

## Setup

**1. Connect X to Buffer.** Make a Buffer account, connect the $TUFFTUNG4 X
channel, and create an API key. This is the only step that needs a browser.

**2. Add GitHub Actions secrets** (Settings → Secrets and variables → Actions):

| Secret | What it is |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `BUFFER_ACCESS_TOKEN` | Buffer API key |
| `BUFFER_CHANNEL_ID` | Optional — only needed if you connect more than one X channel |

Nothing goes in the repo. `.gitignore` covers the usual shapes and CI fails the
build if a credential-looking string shows up in a tracked file.

**3. Fill in `config/brand.toml`.** Required fields are blank on purpose and
config validation refuses to load until they're set:

- `coin.handle` — the X handle
- `voice.premise` — what the joke is, what the lore is, who is posting
- `voice.tone` — three to six adjectives

**4. Fill in `config/facts.toml`.** This is the **only** set of facts the
generator may assert. Anything not in here — a holder count, a milestone, a
listing, a partnership — gets the post rejected. Leave it near-empty and the
bot simply writes jokes instead of claims, which is the safer default.

**5. Verify.**

```bash
pip install -e ".[dev]"
pytest -q                      # guard suite must be green
python -m viralinator.cli verify
python -m viralinator.cli draft -n 3   # generates and guards, publishes nothing
```

**6. Enable cron.** Uncomment the `schedule:` block in
`.github/workflows/post.yml`. Do this only after a `draft` run looks right.

## The guard

`viralinator/guard.py` is the safety-critical part. At `tier2` nothing human
reviews a post before it goes out, so the guard is the only thing between a
generation and a public timeline attached to a financial asset. It runs two
layers and **fails closed** — anything it can't clear is rejected:

1. **Deterministic rules.** Return multiples, price targets, guarantees, buy
   imperatives, exchange listings, partnerships, pre-launch framing,
   engagement bait, wallet solicitation, links in the post body, and mentions
   of anyone not on an allowlist.
2. **A semantic judge.** A separate Claude call that sees only the candidate
   text — never the generation prompt — and flags any assertion not covered by
   `facts.toml`. This catches the paraphrases regexes miss.

It deliberately does **not** block ordinary crypto-meme register. A guard
strict enough to kill the account's voice is a guard that gets switched off.
It blocks specific, falsifiable, financial claims; it leaves tone alone.

`tests/test_guard.py` is the important test file. `MUST_REJECT` is a corpus of
things that must never ship. `MUST_PASS` documents the calibration — if those
start failing, the guard has become too strict.

## Autonomy tiers

Set `guard.tier` in `config/brand.toml`:

- `tier2` — everything passing the guard posts unattended. **Current setting.**
- `tier1` — claim-bearing formats aren't generated at all; only safe formats run.
- `tier0` — nothing publishes; use `draft` and post by hand.

## Commands

```bash
viralinator verify     # config, credentials, Buffer connection
viralinator run        # top up the Buffer queue (the cron job)
viralinator draft -n 3 # generate + guard, print everything, publish nothing
viralinator measure    # pull metrics, update format weights
viralinator status     # budget, queue depth, recent posts, format scores
viralinator pause      # stop publishing immediately
viralinator resume
```

`pause` creates a `PAUSED` file that `run` checks before doing anything. It's
the kill switch — use it if the account starts posting something you don't like.

## What learns, and what doesn't yet

`formats.py` holds ten post formats with starting weights based on how X ranks
content — replies and bookmarks weigh far more than likes, media beats text,
external links are suppressed. `learn.py` scores posts against the account's
own average and shifts the weights toward what works.

**The loop is not closed yet.** Buffer's free tier may not expose per-post
analytics over its API, and their developer docs were unreachable from the
build environment, so metrics collection is unimplemented. Until it's wired up
the weights sit at their priors and the pipeline still runs — it just doesn't
improve. `viralinator measure` says so when you run it.

## Known unverified bits

Buffer's docs are blocked from where this was built, so the GraphQL in
`publisher.py` is written from their documented shape rather than read off the
schema. Confirmed: the endpoint, bearer auth, `createPost(input:
CreatePostInput!)`, the `addToQueue` mode, and the `MutationError` branch. Not
confirmed: the success-branch type name and the channel-listing query. The
queries are module-level constants meant to be corrected in place, and
`viralinator verify` exercises all of them so a wrong field name fails at setup
rather than silently in production.

## What this deliberately doesn't do

No sockpuppet accounts, no engagement pods, no reply or DM spam, no bot
networks to inflate metrics, and no driving X's private endpoints with session
cookies to dodge the API. One real account, real posts.
