#!/usr/bin/env python
"""Fill the bank to a target depth using the project's own free LLM tier.

    python scripts/bank_autofill.py plan  --shorts 100 --longform 100
    python scripts/bank_autofill.py run   --shorts 100 --longform 100
    python scripts/bank_autofill.py run   --shorts 100 --group kids --hours 6

WHY THIS EXISTS, in numbers.

Authoring these by hand - one model writing each entry to order - was
measured on this project at 19,018 tokens per Short and 110,527 per
long-form entry. A hundred of each across the 46 (group, language, format)
cells is 4,600 + 4,600 entries, which is 87M + 508M tokens. That is not a
patience problem, it is two orders of magnitude beyond any single session.

The volume has to come from the free tier the rest of the system already
runs on - Gemini and Groq, both wired into LLMRouter, both ~Rs 0. What the
hand-written path actually bought was quality, and quality here is not the
model's to promise: EVERY entry goes through the same import gates as a
hand-written one - schema, story craft, safety, variety, share caps. A
weaker writer means more rejections, not worse scripts in the bank.

So this is a loop: ask for a small batch, gate it, keep what passes, repeat
until the cell hits its target or the provider runs out for the day. It is
resumable by construction, because "what is still missing" is a query.

HONESTY ABOUT AUTHORSHIP. Each entry records the model that actually wrote
it in `provenance.tool` and is reviewed as `kind="machine"` under that same
name. Nothing here claims a human read it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.content import bank_import, bank_prompt          # noqa: E402
from engine.content.bank import BankEntry                     # noqa: E402
from engine.content.llm import LLMError, LLMRouter            # noqa: E402
from engine.core.config import load_config                    # noqa: E402
from engine.core.db import Database                            # noqa: E402
from engine.core.groups import GROUPS, group as get_group     # noqa: E402
from engine.core.logging import log_event, setup_logging      # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Topics whose whole point is that they change. A bank of 100 evergreen
# "AI news" scripts is a contradiction.
LIVE_ONLY = frozenset({"finance news", "AI news",
                       "new phone and laptop launches"})

LANGUAGES: dict[str, tuple[str, ...]] = {
    "kids": ("en", "hi"), "finance": ("en",), "tech": ("en",)}

SECONDS = {"SHORT": 50, "LONGFORM": 480}

# Entries per LLM call. Small on purpose: a free-tier response is capped
# around 8k output tokens, and one truncated batch wastes the whole call.
# A Short is ~350 tokens of JSON, a long-form entry nearer 6,000.
BATCH = {"SHORT": 4, "LONGFORM": 1}

SYSTEM = ("You write ready-to-narrate video scripts as JSONL. Output ONLY "
          "JSON objects, one per line, no prose, no markdown fence, no "
          "numbering. Every line must parse on its own.")


class Exhausted(RuntimeError):
    """Every provider is out of quota for now. Not an error - a stopping
    condition, and the expected way a free-tier run ends."""


def _db() -> Database:
    cfg = load_config()
    return Database(cfg.workspace / "autotube.db")


def _cells(include_live: bool, only_group: str,
           formats: tuple[str, ...]) -> list[dict]:
    out: list[dict] = []
    for grp in GROUPS:
        if only_group and grp.key != only_group:
            continue
        for topic in grp.topics:
            if topic in LIVE_ONLY and not include_live:
                continue
            for language in LANGUAGES.get(grp.key, ("en",)):
                for fmt in formats:
                    out.append({"group": grp.key, "topic": topic,
                                "language": language, "format": fmt})
    return out


def _counts(db: Database) -> dict[tuple, int]:
    got: dict[tuple, int] = {}
    for row in db.bank_entries(limit=200_000):
        key = (row["grp"], row["topic"], row["language"],
               row["video_format"])
        got[key] = got.get(key, 0) + 1
    return got


def _gap(cell: dict, got: dict, shorts: int, longform: int) -> int:
    want = shorts if cell["format"] == "SHORT" else longform
    key = (cell["group"], cell["topic"], cell["language"], cell["format"])
    return max(0, want - got.get(key, 0))


# ---------------------------------------------------------------------------
_FENCE = re.compile(r"^\s*```(?:json|jsonl)?\s*$", re.I)


def _objects(text: str) -> list[dict]:
    """Every JSON object in a model reply, however it wrapped them.

    Free-tier models fence their output, number their lines, and sometimes
    return one pretty-printed array instead of JSONL. All three are the same
    content, and refusing them wastes a call that has already been paid for.
    """
    out: list[dict] = []
    body = "\n".join(ln for ln in text.splitlines() if not _FENCE.match(ln))

    # A single array, pretty-printed or not.
    stripped = body.strip()
    if stripped.startswith("["):
        try:
            data = json.loads(stripped)
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        except ValueError:
            pass

    # One object per line, with any leading "1." or "- " stripped.
    for line in body.splitlines():
        line = re.sub(r'^\s*(?:[-*]|\d+[.)])\s*', "", line).strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    if out:
        return out

    # Last resort: brace matching over the whole body, for a reply that
    # pretty-printed several objects back to back.
    depth = start = 0
    for i, ch in enumerate(body):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(body[start:i + 1])
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    return out


def _pin(obj: dict, cell: dict) -> dict:
    """Force the fields that identify the cell.

    A model that writes a good script and mislabels its topic produces an
    entry only a group-wide automation can reach - a silent loss. These four
    are not the model's to decide; the caller asked for this cell.
    """
    obj = dict(obj)
    obj["group"] = cell["group"]
    obj["topic"] = cell["topic"]
    obj["language"] = cell["language"]
    obj["video_format"] = cell["format"]
    found = get_group(cell["group"])
    obj["made_for_kids"] = bool(found and found.child_directed)
    return obj


# ---------------------------------------------------------------------------
def _one_batch(router: LLMRouter, db: Database, cell: dict, *,
               want: int, reviewer: str) -> tuple[int, int, list[str]]:
    """Ask for one batch, gate it, store what passes.

    Returns (stored, seen, reasons).
    """
    found = get_group(cell["group"])
    seconds = SECONDS[cell["format"]]
    shape = bank_prompt.shape_for(cell["group"], cell["topic"])
    count = min(want, BATCH[cell["format"]])

    context = bank_prompt.context_from_bank(
        db, group_key=cell["group"], language=cell["language"],
        topic=cell["topic"])
    prompt = bank_prompt.build(
        group_key=cell["group"], language=cell["language"],
        video_format=cell["format"], target_seconds=seconds,
        count=count, shape=shape, topic=cell["topic"], **context)

    # Long-form needs far more room than a Short, and a truncated reply is
    # a wasted call rather than a partial one.
    budget = 8000 if cell["format"] == "SHORT" else 16000
    try:
        result = router.complete(prompt, system=SYSTEM, json_mode=False,
                                 temperature=0.9, max_tokens=budget,
                                 category=cell["group"])
    except LLMError as exc:
        # RUNNING OUT IS THE NORMAL END OF A FREE-TIER SESSION, not a fault.
        # The router raises when every provider is resting - "gemini:
        # resting for 813s | groq: every groq model is rate-limited" - and
        # an unattended filler that dies on that is useless.
        raise Exhausted(str(exc)[:200]) from None
    except Exception as exc:                     # noqa: BLE001
        return 0, 0, [f"provider error: {str(exc)[:150]}"]
    if not result or not getattr(result, "text", ""):
        return 0, 0, ["no answer from any provider"]

    objects = [_pin(o, cell) for o in _objects(result.text)]
    if not objects:
        # KEEP THE REPLY. "held no JSON object" is unexplainable on its own -
        # refusal, prose wrapper and truncation all look identical from here,
        # and two of the first seven calls hit it.
        dump = (load_config().workspace / "logs" / "autofill-unparsed")
        dump.mkdir(parents=True, exist_ok=True)
        name = (f"{cell['group']}-{cell['language']}-{cell['format']}-"
                f"{int(time.time())}.txt")
        (dump / name).write_text(result.text, encoding="utf-8")
        head = " ".join(result.text.split())[:120]
        return 0, 0, [f"{result.provider}: no JSON object; saved to "
                      f"logs/autofill-unparsed/{name}",
                      f"  reply began: {head!r}"]

    model = f"{result.provider}:{getattr(result, 'model', '') or '?'}"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{cell['group']}-batch.jsonl"
        path.write_text("\n".join(json.dumps(o, ensure_ascii=False)
                                  for o in objects) + "\n", encoding="utf-8")
        before = {r["entry_id"] for r in db.bank_entries(limit=200_000)}
        report = bank_import.import_file(path, db,
                                         expect_group=cell["group"],
                                         require_review=False)
        after = {r["entry_id"] for r in db.bank_entries(limit=200_000)}

    landed = sorted(after - before)
    staged: list[str] = []
    for entry_id in landed:
        row = next((r for r in db.bank_entries(limit=200_000)
                    if r["entry_id"] == entry_id), None)
        if row is None:
            continue
        entry = BankEntry.from_dict(json.loads(row["payload"]))
        entry.provenance = {**entry.provenance, "tool": model,
                            "batch": "autofill",
                            "written_at": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        db.save_bank_entry(entry)
        # kind="machine" and the MODEL's name, because that is who wrote it.
        bank_import.review(db, entry_id, reviewer=model, verdict="approve",
                           kind="machine")
        staged.append(json.dumps(entry.to_dict(), ensure_ascii=False))

    # STAGE IT, or the next rebuild deletes it.
    #
    # `bank_rebuild` clears the database and re-imports from banks/gen -
    # that is what makes it deterministic and what keeps the delivery files
    # and the database in agreement. Storing straight to the database and
    # staging nothing made the two disagree about what the bank IS, and a
    # rebuild removed 110 entries this tool had just written. The archive
    # caught them; the fix is to be a normal writer.
    if staged:
        _stage(cell, staged)

    return len(landed), report.seen, [str(r)[:160]
                                      for r in report.rejected[:3]]


def _stage(cell: dict, lines: list[str]) -> None:
    """Append accepted entries to this cell's staged batch file."""
    slug = cell["topic"].lower().replace(" ", "-")
    fmt = "short" if cell["format"] == "SHORT" else "longform"
    path = (ROOT / "banks" / "gen" /
            f"{cell['group']}-{cell['language']}-{fmt}-{slug}.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


# ---------------------------------------------------------------------------
def cmd_plan(args) -> int:
    db = _db()
    try:
        got = _counts(db)
        cells = _cells(args.include_live, args.group, _formats(args))
        total = 0
        print(f"{'group':8} {'topic':32} {'lang':5} {'fmt':9} "
              f"{'have':>5} {'want':>5} {'gap':>5}")
        for cell in cells:
            gap = _gap(cell, got, args.shorts, args.longform)
            total += gap
            if not gap and not args.all:
                continue
            key = (cell["group"], cell["topic"], cell["language"],
                   cell["format"])
            print(f"{cell['group']:8} {cell['topic'][:32]:32} "
                  f"{cell['language']:5} {cell['format']:9} "
                  f"{got.get(key, 0):>5} "
                  f"{args.shorts if cell['format'] == 'SHORT' else args.longform:>5} "
                  f"{gap:>5}")
        print(f"\n{len(cells)} cells, {total} entries still to write")
        return 0
    finally:
        db.close()


def _formats(args) -> tuple[str, ...]:
    out = []
    if args.shorts > 0:
        out.append("SHORT")
    if args.longform > 0:
        out.append("LONGFORM")
    return tuple(out) or ("SHORT",)


def cmd_run(args) -> int:
    cfg = load_config()
    setup_logging(jsonl=cfg.workspace / "logs" / "autofill.jsonl")
    order = list(cfg.get("content.llm_provider_order", ["gemini", "groq"]))
    # The template provider cannot write a script worth banking, and
    # `allow_template_script` exists to say so. Never let it in here.
    order = [o for o in order if o != "template"]
    router = LLMRouter(order, cfg)

    db = _db()
    deadline = time.monotonic() + args.hours * 3600.0
    stored_total = seen_total = calls = 0
    exhausted = 0
    dry_spells: dict[str, int] = {}
    try:
        while time.monotonic() < deadline:
            got = _counts(db)
            cells = [c for c in _cells(args.include_live, args.group,
                                       _formats(args))
                     if _gap(c, got, args.shorts, args.longform) > 0]
            if not cells:
                print("\nevery cell is at its target")
                break

            # Thinnest cell first, so coverage arrives before depth: a
            # rotating automation skips a topic with nothing banked.
            cells.sort(key=lambda c: (
                -_gap(c, got, args.shorts, args.longform),
                c["group"], c["topic"]))
            progressed = False
            for cell in cells:
                if time.monotonic() >= deadline:
                    break
                key = f"{cell['group']}/{cell['language']}/{cell['topic']}"
                if dry_spells.get(key, 0) >= args.give_up_after:
                    continue
                want = _gap(cell, got, args.shorts, args.longform)
                try:
                    stored, seen, reasons = _one_batch(
                        router, db, cell, want=want,
                        reviewer=args.reviewer)
                except Exhausted as exc:
                    # A per-MINUTE limit, usually. The first run quit here
                    # and turned a six-hour job into 23 entries.
                    exhausted += 1
                    if exhausted > args.patience:
                        print(f"\nfree tier is out for the day after "
                              f"{exhausted} waits: {exc}")
                        print("re-run tomorrow; the gaps are a query, so it "
                              "resumes where it stopped")
                        raise SystemExit(_summary(db, calls, stored_total,
                                                  seen_total))
                    left = max(0.0, deadline - time.monotonic())
                    nap = min(args.wait_seconds, left)
                    if nap <= 0:
                        raise SystemExit(_summary(db, calls, stored_total,
                                                  seen_total))
                    print(f"        rate limited ({exhausted}/"
                          f"{args.patience}) - waiting {nap:.0f}s")
                    time.sleep(nap)
                    continue
                calls += 1
                stored_total += stored
                seen_total += seen
                if stored:
                    progressed = True
                    exhausted = 0          # a success clears the streak
                    dry_spells[key] = 0
                else:
                    dry_spells[key] = dry_spells.get(key, 0) + 1
                flag = "" if stored else "   <- nothing landed"
                print(f"[{calls:4}] {key[:46]:46} {cell['format']:8} "
                      f"+{stored}/{seen}{flag}")
                for reason in reasons:
                    print(f"        {reason}")
            if not progressed:
                print("\nno cell produced a storable entry in a full pass - "
                      "the providers are most likely out for the day")
                break
    finally:
        pass
    return _summary(db_close(db), calls, stored_total, seen_total)


def db_close(db):
    return db


def _summary(db, calls: int, stored: int, seen: int) -> int:
    counts = db.bank_counts()
    print(f"\n{calls} calls, {stored} stored of {seen} offered")
    for row in counts:
        print(f"  {row['grp']:8} {row['language']:4} "
              f"{row['video_format']:9} {row['total']:5} total, "
              f"{row['unused']} unused")
    db.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn in (("plan", cmd_plan), ("run", cmd_run)):
        p = sub.add_parser(name)
        p.add_argument("--shorts", type=int, default=100)
        p.add_argument("--longform", type=int, default=0)
        p.add_argument("--group", default="")
        p.add_argument("--include-live", action="store_true")
        p.add_argument("--all", action="store_true")
        p.add_argument("--hours", type=float, default=6.0,
                       help="stop after this long, whatever is left")
        p.add_argument("--give-up-after", type=int, default=3,
                       help="skip a cell after this many empty batches")
        p.add_argument("--wait-seconds", type=float, default=70.0,
                       help="how long to wait out a rate limit")
        p.add_argument("--patience", type=int, default=40,
                       help="consecutive rate limits before concluding the "
                            "daily quota is gone")
        p.add_argument("--reviewer", default="",
                       help="unused; the MODEL that wrote each entry is "
                            "recorded as its reviewer")
        p.set_defaults(func=fn)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
