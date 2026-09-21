# memecoin-viralinator

An unattended content pipeline for the $TUFFTUNG4 X account. Posts are written
and screened ahead of time; a cron pushes them into a Buffer queue that posts
them to X on schedule. Nobody has to be present, and the only credential in
play is a Buffer token.

```
config/bank.json ─▶ cron ─▶ rank ─▶ Buffer queue ─▶ X
  (pre-screened)              │
                             └─▶ marks used, commits back
```

## The account

The account **is** a tungsten cube. First person, deadpan, immovable. It sits
on a man's desk, it is extremely dense, and that is its entire personality. It
narrates what happens around it with total flatness. People pick it up, make a
noise, and put it back down; it has catalogued the noises.

This premise does real work beyond being funny. A tungsten cube has no opinions
about markets — the voice physically cannot discuss price without breaking
character. The single thing most likely to get a token account in trouble is
ruled out by the premise itself, not just by the guard. "Holding" is a joke
about being heavy, and never about anything else.

Voice config is in `config/brand.toml`. Running bits: the *day N* diary, the
noise catalogue, unfavourable reviews of other household objects, and the
coaster.

## Why there's no LLM at runtime

Every runtime dependency is a liability for a job nobody is watching. An API
key can overrun a budget; an OAuth profile expires and needs a human to
re-authenticate; a metered call can rate-limit at 3am. So generation happens
**ahead of time**, in a session with a human present, and the results are
committed to the repo.

The cron does no generation. It pops an unused post, hands it to Buffer, marks
it used, and commits that back. It can keep running for months on a Buffer
token alone.

## Why Buffer and not X's API

X's API is pay-per-use — roughly $0.015 a post, $0.20 if the post contains a
link. Buffer is an official X API partner whose free plan covers 3 channels, a
10-post queue, and 3,000 API requests a month. The cron tops the queue up and
Buffer posts. **Total recurring cost: $0.**

## Setup

**1. Connect X to Buffer.** Create a Buffer account, connect the $TUFFTUNG4 X
channel, and generate an API key. This is the only step needing a browser.

**2. Add GitHub Actions secrets** (Settings → Secrets and variables → Actions):

| Secret | Needed for |
|---|---|
| `BUFFER_ACCESS_TOKEN` | Everything. The only runtime credential. |
| `BUFFER_CHANNEL_ID` | Only if more than one X channel is connected |
| `ANTHROPIC_API_KEY` | Optional — `bank fill` only, never the cron |

Nothing goes in the repo. CI fails the build if a credential-shaped string
appears in a tracked file.

**3. Check `coin.handle`** in `config/brand.toml`. It is set to `tufftung4` as
a guess — correct it if the real handle differs. It only affects metrics
lookups, not where posts land.

**4. Verify.** Run the **verify** workflow from the Actions tab with
`introspect` enabled. It runs the tests, checks the bank, confirms the Buffer
connection, and prints Buffer's actual GraphQL schema into the log. Nothing
posts.

**5. Enable cron.** Uncomment the `schedule:` block in
`.github/workflows/post.yml`.

## The guard

`viralinator/guard.py` is the safety-critical part. At `tier2` nothing human
reviews a post before it goes out, so the guard is the only thing between a
post and a public timeline attached to a financial asset. Two layers, **fails
closed**:

1. **Deterministic rules** — return multiples, price targets, guarantees, buy
   imperatives, exchange listings, partnerships, pre-launch framing, engagement
   bait, wallet solicitation, links in the post body, unapproved mentions.
2. **A semantic judge** — a separate Claude call seeing only the candidate
   text, never the generation prompt, flagging any assertion not backed by
   `config/facts.toml`.

It deliberately does **not** block ordinary crypto-meme register. A guard
strict enough to kill the account's voice is one that gets switched off. It
blocks specific, falsifiable, financial claims; it leaves tone alone.

Every post in the bank was screened when written, and
`tests/test_bank.py::test_every_shipped_post_survives_the_guard` re-checks the
whole bank on every CI run — so tightening a rule immediately tells you which
existing posts it just invalidated.

`config/facts.toml` is **empty on purpose**. The cube asserts nothing
checkable, so the safest possible configuration is the correct one.

## Refilling the bank

The bank holds ~18 days at 3 posts/day. `run` warns below 14 days, and
`viralinator bank status` shows runway at any time.

To refill, ask Claude in a session — that's what `bank fill` is for:

```bash
python -m viralinator.cli bank fill -n 100   # generates, guards, dedupes
git add config/bank.json && git commit && git push
```

This is the only step that ever needs Anthropic access, and it happens with a
human present.

## Commands

```bash
viralinator verify [--introspect]  # config, bank, credentials, Buffer
viralinator run                    # push to Buffer queue (the cron job)
viralinator bank status            # runway remaining, by format
viralinator bank fill -n 100       # write new posts (needs Anthropic)
viralinator draft -n 3             # generate + guard, print, publish nothing
viralinator status                 # queue depth, recent posts, format scores
viralinator pause / resume         # kill switch
```

`pause` creates a `PAUSED` file that `run` checks first. Use it if the account
starts posting something you don't like.

## What learns, and what doesn't yet

`formats.py` holds nine post formats with starting weights reflecting how X
ranks content — replies and bookmarks weigh far more than likes, media beats
text, external links are suppressed. `rank.py` picks which format to draw from
and `learn.py` shifts the weights toward what performs.

**The loop is not closed yet.** Buffer's free tier may not expose per-post
analytics over its API, so metrics collection is unimplemented and the weights
sit at their priors. The pipeline runs fine; it just doesn't improve.
`viralinator measure` says so when you run it.

## Known unverified bits

Buffer's docs and API are blocked by the build sandbox's network policy, so the
GraphQL in `publisher.py` is written from their documented shape rather than
read off the schema. Confirmed: endpoint, bearer auth, `createPost(input:
CreatePostInput!)`, `addToQueue` mode, `MutationError` branch. Not confirmed:
the success-branch type name and the channel-listing query.

The queries are module-level constants meant to be corrected in place, and
`verify --introspect` prints the real schema from a runner, so one dispatch
tells you whether they're right and what to change if not.

## What this deliberately doesn't do

No sockpuppet accounts, no engagement pods, no reply or DM spam, no bot
networks to inflate metrics, and no driving X's private endpoints with session
cookies to dodge the API. One real account, real posts.
