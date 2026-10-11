"""Command line entry point.

    viralinator verify     check config, credentials, and the Buffer connection
    viralinator run        top the Buffer queue back up (this is the cron job)
    viralinator draft      generate + guard, print everything, publish nothing
    viralinator measure    pull metrics, update format weights
    viralinator status     budget, queue depth, recent posts, format scores
    viralinator pause      stop all publishing
    viralinator resume     undo pause
    viralinator reply      draft guarded replies to someone else's post
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from . import learn, rank
from .budget import Budget, BudgetExceeded, month_start_iso
from .config import REPO_ROOT, ConfigError, load
from .guard import Guard
from .store import Store

PAUSE_FILE = REPO_ROOT / "PAUSED"


def _client():
    import anthropic

    return anthropic.Anthropic()


def _paused() -> bool:
    return PAUSE_FILE.exists()


# --- commands --------------------------------------------------------------------


def cmd_verify(args) -> int:
    from .publisher import BufferPublisher, PublishError

    print("config ... ", end="")
    try:
        cfg = load()
    except ConfigError as exc:
        print("FAIL")
        print(exc)
        return 1
    print(f"ok ({cfg.coin.ticker}, tier={cfg.guard.tier})")

    print(f"facts ... {len(cfg.usable_facts)} usable")
    if not cfg.usable_facts:
        print("  note: with no facts, the guard will reject any post stating anything")

    from .bank import Bank

    bank = Bank.load()
    unused = len(bank.unused())
    days = bank.days_remaining(cfg.cadence.posts_per_day)
    print(f"bank ... {unused} unused (~{days:.0f} days at {cfg.cadence.posts_per_day}/day)")
    if unused == 0:
        print("  FAIL: bank is empty, nothing can be posted")
        return 1
    if days < 14:
        print("  low — ask Claude to refill before it runs out")

    # Not required at runtime. The cron posts from the bank; Anthropic access is
    # only needed to refill it, which happens in a session with a human present.
    print("anthropic ... ", end="")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("not configured (fine — only needed for `bank fill`)")
    else:
        try:
            _client().messages.create(
                model=cfg.guard.judge_model,
                max_tokens=16,
                messages=[{"role": "user", "content": "reply with: ok"}],
            )
            print("ok")
        except Exception as exc:  # noqa: BLE001
            print(f"unavailable: {exc}")
            print("  (not fatal — the cron does not use it)")

    try:
        pub = BufferPublisher()
    except PublishError as exc:
        print(f"buffer ... FAIL\n  {exc}")
        return 1

    if args.introspect:
        print("\nbuffer schema introspection:")
        try:
            print(json.dumps(pub.introspect(), indent=2)[:8000])
        except PublishError as exc:
            print(f"  introspection failed: {exc}")

    print("buffer ... ", end="")
    try:
        channels = pub.channels()
        print(f"ok ({len(channels)} channels)")
        for ch in channels:
            print(f"  - {ch.service}: {ch.name} [{ch.id}]")
        cid = pub.resolve_channel()
        print(f"  target channel: {cid}")
        print(f"  queue depth: {pub.queue_depth()}")
    except PublishError as exc:
        # The error is printed last, on purpose. A schema dump is long enough to
        # push the actual message out of the log tail readable from a workflow
        # run, which is how this gets debugged. Re-run with --introspect when
        # the schema is what you need.
        print("FAIL")
        print(
            "\n  The GraphQL in viralinator/publisher.py is corrected against a\n"
            "  live introspection dump. If this is a field error, the queries are\n"
            "  module-level constants meant to be edited in place; re-run with\n"
            "  --introspect to see the schema.\n"
        )
        print(f"buffer error: {exc}")
        return 1

    print("\nall checks passed")
    return 0


def _generate_one(cfg, store, guard, generator, verbose=True):
    """Generate a batch, guard it, return the best survivor or None."""
    fmt = rank.pick_format(store, cfg.guard.tier)
    if verbose:
        print(f"\nformat: {fmt.key} — {fmt.description}")

    candidates = generator.generate(fmt)
    if not candidates:
        if verbose:
            print("  generator returned nothing")
        return None

    survivors = []
    for cand in candidates:
        post_id = store.record_draft(cand.format_key, cand.text)
        verdict = guard.check(cand.text)
        if verdict.ok:
            survivors.append((post_id, cand))
            if verbose:
                print(f"  PASS  {cand.text[:70]!r}")
        else:
            store.mark_rejected(post_id, str(verdict))
            if verbose:
                print(f"  {verdict}")

    if not survivors:
        if verbose:
            print("  no candidates survived the guard")
        return None

    best = rank.pick_candidate([c for _, c in survivors], store)
    post_id = next(pid for pid, c in survivors if c is best)
    return post_id, best


def cmd_draft(args) -> int:
    from .generate import Generator

    cfg = load()
    store = Store()
    budget = Budget(cfg, store)
    client = _client()
    guard = Guard(cfg, client=client, budget=budget)
    generator = Generator(cfg, client=client, budget=budget)

    for _ in range(args.n):
        result = _generate_one(cfg, store, guard, generator)
        if result:
            _, cand = result
            print(f"\n  SELECTED:\n  {cand.text}")
            if cand.image_note:
                print(f"  [image: {cand.image_note}]")

    print(f"\nspend this month: {budget.status()}")
    print("(draft mode — nothing was published)")
    return 0


def cmd_run(args) -> int:
    """The cron job. Draws from the pre-screened bank — no LLM, no API key."""
    from .bank import Bank
    from .publisher import BufferPublisher, PublishError

    if _paused():
        print("PAUSED file present — not publishing. Run `viralinator resume`.")
        return 0

    cfg = load()
    store = Store()
    bank = Bank.load()

    remaining = len(bank.unused())
    if remaining == 0:
        print(
            "bank is empty — nothing to post.\n"
            "Ask Claude to refill it (`viralinator bank fill -n 100`) and commit."
        )
        return 1

    try:
        pub = BufferPublisher()
        depth = pub.queue_depth()
    except PublishError as exc:
        print(f"buffer: {exc}")
        return 1

    target = cfg.publish.target_queue_depth
    need = max(0, target - depth)
    print(f"queue {depth}/{target}; bank has {remaining} unused; pushing {need}")
    if need == 0:
        return 0

    published = 0
    for _ in range(need):
        fmt = rank.pick_format(store, cfg.guard.tier)
        entry = bank.take(fmt.key)
        if entry is None:
            print("  bank exhausted mid-run")
            break

        post_id = store.record_draft(entry.format_key, entry.text)
        try:
            pub.add_to_queue(entry.text)
        except PublishError as exc:
            # Put it back — an unpublished post should not be marked used.
            entry.used_at = None
            store.mark_failed(post_id, str(exc))
            print(f"  publish failed: {exc}")
            break

        store.mark_posted(post_id, f"buffer:{entry.id}")
        published += 1
        print(f"  queued [{entry.format_key}]: {entry.text[:70]!r}")

    bank.save()

    left = len(bank.unused())
    days = bank.days_remaining(cfg.cadence.posts_per_day)
    print(f"\nqueued {published}; bank has {left} left (~{days:.0f} days)")
    if days < 14:
        print("  LOW: ask Claude to refill the bank soon")
    return 0


def cmd_bank_fill(args) -> int:
    """Generate and screen new posts into the bank. Needs Anthropic access."""
    from .bank import Bank
    from .generate import Generator
    from .formats import selectable

    cfg = load()
    store = Store()
    budget = Budget(cfg, store)
    client = _client()
    guard = Guard(cfg, client=client, budget=budget)
    generator = Generator(cfg, client=client, budget=budget)
    bank = Bank.load()

    formats = selectable(cfg.guard.tier)
    added = 0
    rejected = 0
    dupes = 0

    # Spread evenly across formats so the bank doesn't end up all one shape.
    rounds = max(1, args.n // (len(formats) * cfg.generation.candidates) + 1)

    for _ in range(rounds):
        for fmt in formats:
            if added >= args.n:
                break
            for cand in generator.generate(fmt):
                if added >= args.n:
                    break
                verdict = guard.check(cand.text)
                if not verdict.ok:
                    rejected += 1
                    print(f"  {verdict}")
                    continue
                if bank.contains_similar(cand.text):
                    dupes += 1
                    continue
                bank.add(cand.text, cand.format_key, cand.image_note)
                added += 1
                print(f"  + [{fmt.key}] {cand.text[:66]!r}")

    bank.save()
    print(
        f"\nadded {added}, rejected {rejected}, skipped {dupes} near-duplicates\n"
        f"bank now holds {len(bank.unused())} unused "
        f"(~{bank.days_remaining(cfg.cadence.posts_per_day):.0f} days)\n"
        f"spend: {budget.status()}\n"
        f"commit config/bank.json to make this live."
    )
    return 0


def cmd_reply(args) -> int:
    """Draft replies to someone else's post, screen them, print the survivors.

    Draft-only by design: replies cannot go through Buffer (its Twitter
    metadata has `thread` and `retweet` but no reply field), and routing them
    through X's API is a paid path that was declined. So this produces text to
    paste, and the guard still runs — the value here is that nothing shilling
    or generic reaches the clipboard.

    Takes the post's TEXT, not a URL: nothing here can fetch a tweet.
    """
    from .replies import ReplyGenerator, ReplyGuard, ReplyTarget

    cfg = load()
    store = Store()
    budget = Budget(cfg, store)
    client = _client()

    target = ReplyTarget(
        tweet_id=args.id or "",
        author=(args.author or "someone").lstrip("@"),
        text=args.text,
    )

    print(f"replying to @{target.author}:")
    print(f"  {target.text}\n")

    drafts = ReplyGenerator(cfg, client=client, budget=budget).draft(target)
    if not drafts:
        print("no drafts — the generator found nothing worth saying here.")
        print("that is a valid outcome; most posts have no good cube reply in them.")
        return 0

    guard = ReplyGuard(cfg, client=client, budget=budget)
    kept = []
    for d in drafts:
        verdict = guard.check(target, d.text)
        if verdict.ok:
            kept.append(d)
        else:
            print(f"  rejected: {d.text!r}\n    {verdict}")

    if not kept:
        print("\nnothing survived the guard.")
        return 0

    print("\n" + "=" * 60)
    for d in kept:
        print(f"\n{d.text}")
        if d.responds_to:
            print(f"    [answers: {d.responds_to}]")
    print("\n" + "=" * 60)
    print(f"\n{len(kept)} draft(s) to paste. spend this month: {budget.status()}")
    return 0


def cmd_bank_status(args) -> int:
    from .bank import Bank

    cfg = load()
    bank = Bank.load()
    unused = len(bank.unused())
    print(f"bank: {unused} unused / {len(bank.entries)} total")
    print(f"      ~{bank.days_remaining(cfg.cadence.posts_per_day):.0f} days at "
          f"{cfg.cadence.posts_per_day}/day\n")
    for key, (u, total) in sorted(bank.counts().items()):
        print(f"  {key:<22} {u:>3} unused / {total:>3}")
    return 0


def cmd_measure(args) -> int:
    cfg = load()
    store = Store()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    pending = store.posts_awaiting_measurement(cutoff)
    print(f"{len(pending)} posts awaiting measurement")

    if pending:
        print(
            "\nNOTE: metrics collection is not wired up. Buffer's free tier may not\n"
            "expose per-post analytics over the API; if it does not, connect a\n"
            "metrics source or read them manually. Until then format weights stay\n"
            "at their priors and the pipeline still works — it just doesn't learn."
        )

    applied = learn.update_weights(store)
    if applied:
        print(f"\nupdated {len(applied)} format scores")
    print("\n" + learn.report(store))
    return 0


def cmd_status(args) -> int:
    from .publisher import BufferPublisher, PublishError

    cfg = load()
    store = Store()
    budget = Budget(cfg, store)

    print(f"tier:    {cfg.guard.tier}")
    print(f"paused:  {_paused()}")
    print(f"budget:  {budget.status()}")
    for service, kind, total in store.spend_breakdown_since(month_start_iso()):
        print(f"           {service}/{kind}: ${total:.3f}")

    try:
        print(f"queue:   {BufferPublisher().queue_depth()}")
    except PublishError as exc:
        print(f"queue:   unavailable ({exc})")

    recent = store.posted_since("0000")[-5:]
    if recent:
        print("\nrecent:")
        for p in recent:
            print(f"  [{p.format_key}] {p.body[:60]!r}")

    print("\n" + learn.report(store))
    return 0


def cmd_pause(args) -> int:
    PAUSE_FILE.write_text("paused\n")
    print(f"paused — {PAUSE_FILE} created. Nothing will publish until resumed.")
    return 0


def cmd_resume(args) -> int:
    if PAUSE_FILE.exists():
        PAUSE_FILE.unlink()
    print("resumed")
    return 0


# --- wiring ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="viralinator", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify")
    v.add_argument(
        "--introspect",
        action="store_true",
        help="dump Buffer's GraphQL schema (use when a field name looks wrong)",
    )
    v.set_defaults(fn=cmd_verify)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    sub.add_parser("measure").set_defaults(fn=cmd_measure)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("pause").set_defaults(fn=cmd_pause)
    sub.add_parser("resume").set_defaults(fn=cmd_resume)

    d = sub.add_parser("draft")
    d.add_argument("-n", type=int, default=1, help="how many slots to draft")
    d.set_defaults(fn=cmd_draft)

    r = sub.add_parser("reply", help="draft guarded replies to someone else's post")
    r.add_argument("text", help="the post's TEXT (a URL alone cannot be fetched)")
    r.add_argument("--author", help="the poster's handle, for context")
    r.add_argument("--id", help="tweet id, recorded only")
    r.set_defaults(fn=cmd_reply)

    bank = sub.add_parser("bank", help="manage the pre-screened content bank")
    bank_sub = bank.add_subparsers(dest="bank_cmd", required=True)
    bf = bank_sub.add_parser("fill", help="generate + screen new posts (needs Anthropic)")
    bf.add_argument("-n", type=int, default=50, help="how many posts to add")
    bf.set_defaults(fn=cmd_bank_fill)
    bank_sub.add_parser("status", help="how much runway is left").set_defaults(
        fn=cmd_bank_status
    )

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"config error:\n{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
