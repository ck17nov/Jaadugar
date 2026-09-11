#!/usr/bin/env python
"""Fill the script bank topic by topic, in resumable batches.

    python scripts/bank_fill.py plan
    python scripts/bank_fill.py prompt --group kids --topic "kids bedtime stories" \
        --format SHORT --language en --out batch.txt
    python scripts/bank_fill.py absorb batch.jsonl --group kids

Why a separate tool when `autotube stories prompt` already exists. That command
asks for a batch per GROUP and tells the model to spread across the group's
topics. A model obeying that writes six bedtime stories and one of everything
else, every time - so the thin topics stay thin however many batches you run,
and there is no way to ask "what is still missing". This one works a single
(group, topic, language, format) cell at a time and can say exactly which
cells are short, which is what makes filling 40 topics a resumable job rather
than a guess.

The writing itself is not done here. `prompt` emits the text; something that
can write - a model, a person - answers with JSONL; `absorb` validates, gates
and stores it. That split is deliberate: the gates are the same ones a
hand-written batch goes through, so nothing enters the bank unchecked just
because it was generated in bulk.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.content import bank_import, bank_prompt          # noqa: E402
from engine.core.config import load_config                   # noqa: E402
from engine.core.db import Database                          # noqa: E402
from engine.core.groups import GROUPS, group as get_group    # noqa: E402

# How many entries each cell should eventually hold. Deliberately modest
# next to "200 per topic": at four uploads a day, 40 topics x 35 entries is
# well over a year of daily posting, and a bank nobody can finish is worth
# less than one that is complete and can be topped up.
DEFAULT_SHORTS = 25
DEFAULT_LONGFORM = 10

# Which languages each group is banked in. Kids is the only bilingual
# channel; writing Hindi finance explainers nobody asked for would just
# spend the budget twice.
LANGUAGES: dict[str, tuple[str, ...]] = {
    "kids": ("en", "hi"),
    "finance": ("en",),
    "tech": ("en",),
}

# Topics whose whole point is that they change. A bank of 200 evergreen
# "AI news" scripts is a contradiction: by the time the twentieth is
# claimed it is reporting last season. These are served by live generation
# and are skipped unless asked for explicitly.
LIVE_ONLY_TOPICS = frozenset({
    "finance news",
    "AI news",
    "new phone and laptop launches",
})

# Target seconds per format. A Short is judged against YouTube's 60s ceiling
# with room for the outro; long-form is where the research says the watch
# time is, without being so long that one entry costs a whole batch budget.
SECONDS = {"SHORT": 50, "LONGFORM": 480}


def _db() -> Database:
    cfg = load_config()
    return Database(cfg.workspace / "autotube.db")


def _cells(include_live: bool) -> list[dict]:
    """Every (group, topic, language, format) the bank should cover."""
    out: list[dict] = []
    for grp in GROUPS:
        for topic in grp.topics:
            if topic in LIVE_ONLY_TOPICS and not include_live:
                continue
            for language in LANGUAGES.get(grp.key, ("en",)):
                for fmt in ("SHORT", "LONGFORM"):
                    out.append({"group": grp.key, "topic": topic,
                                "language": language, "format": fmt})
    return out


def _have(db: Database) -> dict[tuple[str, str, str, str], int]:
    """Count what is banked, per cell."""
    counts: dict[tuple[str, str, str, str], int] = {}
    for row in db.bank_entries(limit=100_000):
        key = (row["grp"], row["topic"], row["language"],
               row["video_format"])
        counts[key] = counts.get(key, 0) + 1
    return counts


def cmd_plan(args) -> int:
    db = _db()
    try:
        have = _have(db)
        cells = _cells(args.include_live)
        short_target = args.shorts
        long_target = args.longform

        print(f"{'group':8} {'topic':34} {'lang':5} {'fmt':9} "
              f"{'have':>5} {'want':>5} {'short':>6}")
        total_missing = 0
        for cell in cells:
            want = short_target if cell["format"] == "SHORT" else long_target
            key = (cell["group"], cell["topic"], cell["language"],
                   cell["format"])
            got = have.get(key, 0)
            missing = max(0, want - got)
            total_missing += missing
            if missing == 0 and not args.all:
                continue
            print(f"{cell['group']:8} {cell['topic'][:34]:34} "
                  f"{cell['language']:5} {cell['format']:9} "
                  f"{got:>5} {want:>5} {missing:>6}")
        print(f"\n{len(cells)} cells, {total_missing} entries still to write")
        return 0
    finally:
        db.close()


def cmd_prompt(args) -> int:
    found = get_group(args.group)
    if found is None:
        print(f"unknown group {args.group!r}", file=sys.stderr)
        return 2
    if args.topic and args.topic not in found.topics:
        # A warning, not an error: a topic that is not on the list is still
        # reachable by a group-wide automation, and the operator may be
        # seeding one deliberately.
        print(f"note: {args.topic!r} is not a listed topic of "
              f"{found.key}", file=sys.stderr)

    seconds = args.seconds or SECONDS[args.format.upper()]
    shape = args.shape or bank_prompt.shape_for(args.group, args.topic)
    count = args.count or bank_prompt.recommended_count(
        group_key=args.group, target_seconds=seconds, shape=shape,
        made_for_kids=found.child_directed, topic=args.topic)

    db = _db()
    try:
        context = bank_prompt.context_from_bank(db, group_key=args.group,
                                                language=args.language,
                                                topic=args.topic)
    finally:
        db.close()

    text = bank_prompt.build(
        group_key=args.group, language=args.language,
        video_format=args.format.upper(), target_seconds=seconds,
        count=count, shape=shape, topic=args.topic, **context)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"{args.out}  ({count} x {shape}, {seconds}s)")
    else:
        print(text)
    return 0


def cmd_absorb(args) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 2
    db = _db()
    try:
        before = {row["entry_id"] for row in db.bank_entries(limit=100_000)}
        report = bank_import.import_file(
            path, db, expect_group=args.group,
            require_review=False)          # reviewed below, in this run
        after = {row["entry_id"] for row in db.bank_entries(limit=100_000)}

        reviewed = 0
        if args.reviewer:
            # Recorded as a MACHINE review, because that is what it is.
            # Writing a model's name into a field called `human` would make
            # the row claim a person read it.
            #
            # Only the ids this file actually added. Reviewing everything in
            # `after` would rubber-stamp the whole bank on every absorb.
            for entry_id in sorted(after - before):
                if bank_import.review(db, entry_id, reviewer=args.reviewer,
                                      verdict="approve", kind="machine",
                                      element=args.element):
                    reviewed += 1

        print(report.summary())
        if args.reviewer:
            print(f"  {reviewed} marked machine-reviewed by {args.reviewer}")
        for warning in report.warnings[:20]:
            print("  warn:", warning[:160])
        for reason in report.rejected[:40]:
            print("  reject:", str(reason)[:200])
        return 0 if report.stored else 1
    finally:
        db.close()


def cmd_check(args) -> int:
    """Validate a batch WITHOUT storing it.

    Generation is worth nothing if the batch is rejected, and the rejection
    reasons are specific enough to fix - a word count, a missing image
    brief, a refrain already used. This is the same gate `absorb` runs, with
    the write turned off, so an author can iterate before spending a claim.
    """
    path = Path(args.path)
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 2
    db = _db()
    try:
        report = bank_import.import_file(path, db, expect_group=args.group,
                                         dry_run=True, require_review=False)
        print(report.summary())
        for reason in report.rejected:
            print("  reject:", str(reason)[:240])
        for warning in report.warnings[:30]:
            print("  warn:", str(warning)[:200])
        return 0 if not report.rejected else 1
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="what is still missing, per cell")
    p.add_argument("--shorts", type=int, default=DEFAULT_SHORTS)
    p.add_argument("--longform", type=int, default=DEFAULT_LONGFORM)
    p.add_argument("--include-live", action="store_true",
                   help="include news topics, which are normally live-only")
    p.add_argument("--all", action="store_true",
                   help="show full cells too, not just the short ones")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("prompt", help="the batch prompt for one cell")
    p.add_argument("--group", required=True)
    p.add_argument("--topic", default="")
    p.add_argument("--language", default="en")
    p.add_argument("--format", default="SHORT")
    p.add_argument("--seconds", type=int, default=0)
    p.add_argument("--shape", default="")
    p.add_argument("--count", type=int, default=0)
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_prompt)

    p = sub.add_parser("check", help="validate a batch without storing it")
    p.add_argument("path")
    p.add_argument("--group", default="")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("absorb", help="validate, gate and store a JSONL batch")
    p.add_argument("path")
    p.add_argument("--group", default="")
    p.add_argument("--reviewer", default="",
                   help="record a machine review under this name")
    p.add_argument("--element", default="",
                   help="the human-authored contribution, if any")
    p.set_defaults(func=cmd_absorb)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
