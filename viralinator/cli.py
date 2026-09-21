"""Command line entry point.

    viralinator verify     check config, credentials, and the Buffer connection
    viralinator run        top the Buffer queue back up (this is the cron job)
    viralinator draft      generate + guard, print everything, publish nothing
    viralinator measure    pull metrics, update format weights
    viralinator status     budget, queue depth, recent posts, format scores
    viralinator pause      stop all publishing
    viralinator resume     undo pause
"""

from __future__ import annotations

import argparse
import json
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

    print("anthropic ... ", end="")
    try:
        c = _client()
        c.messages.create(
            model=cfg.guard.judge_model,
            max_tokens=16,
            messages=[{"role": "user", "content": "reply with: ok"}],
        )
        print("ok")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}")
        return 1

    pub = BufferPublisher()

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
        print("FAIL")
        print(f"  {exc}")
        print(
            "\n  The GraphQL in viralinator/publisher.py was written from Buffer's\n"
            "  documented shape rather than read off the schema — their docs are\n"
            "  unreachable from the build sandbox. If this is a field error, the\n"
            "  queries are module-level constants meant to be corrected in place.\n"
            "  Dumping the real schema so it can be fixed against the truth:"
        )
        try:
            print(json.dumps(pub.introspect(), indent=2)[:8000])
        except PublishError as inner:
            print(f"  introspection also failed: {inner}")
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
    from .generate import Generator
    from .publisher import BufferPublisher, PublishError

    if _paused():
        print("PAUSED file present — not publishing. Run `viralinator resume`.")
        return 0

    cfg = load()
    store = Store()
    budget = Budget(cfg, store)

    try:
        budget.check()
    except BudgetExceeded as exc:
        print(f"budget: {exc}")
        return 1

    try:
        pub = BufferPublisher()
        depth = pub.queue_depth()
    except PublishError as exc:
        print(f"buffer: {exc}")
        return 1

    target = cfg.publish.target_queue_depth
    need = max(0, target - depth)
    print(f"queue depth {depth}/{target}; generating {need}")
    if need == 0:
        return 0

    client = _client()
    guard = Guard(cfg, client=client, budget=budget)
    generator = Generator(cfg, client=client, budget=budget)

    published = 0
    for _ in range(need):
        try:
            budget.check()
        except BudgetExceeded as exc:
            print(f"budget: {exc}")
            break

        result = _generate_one(cfg, store, guard, generator)
        if not result:
            continue
        post_id, cand = result
        try:
            pub.add_to_queue(cand.text)
        except PublishError as exc:
            store.mark_failed(post_id, str(exc))
            print(f"  publish failed: {exc}")
            break
        store.mark_posted(post_id, f"buffer:{post_id}")
        published += 1
        print(f"  queued: {cand.text[:70]!r}")

    print(f"\nqueued {published}; spend this month: {budget.status()}")
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

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"config error:\n{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
