#!/usr/bin/env python
"""Rebuild the bank from the staged batches, deterministically.

    python scripts/bank_rebuild.py
    python scripts/bank_rebuild.py --confirm

Why this exists, and why absorbing incrementally is not enough.

Two of the import gates are SHARE caps - no arc_variant above 20% of its
group, no outcome_class above 30%. A share is a property of the whole
catalogue, so whether a given entry passes depends on what is already
stored. Absorbing file by file into a bank that already holds 46 entries
therefore gives a different answer from importing the same files into an
empty one: measured on the same batch, 8 rejections one way and 1 the
other, and a delivery file with 53 lines against a database holding 52.

That difference matters because `banks/*.jsonl` is what a deploy copies to
the server, and the server imports into a fresh database. If the two
disagree, the files ship entries that will never be claimable and the app's
"reviewed scripts ready" count is wrong.

So the canonical operation is a REBUILD: clear, import every staged batch
in one deterministic pass, write the delivery copy from what actually
landed, then VERIFY by importing that delivery copy into a throwaway
database - which is exactly what the server will do. Pruning changes the
shares, so verification repeats until it is stable.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.content import bank_import                       # noqa: E402
from engine.content.bank import BankEntry                    # noqa: E402
from engine.core.config import load_config                   # noqa: E402
from engine.core.db import Database                           # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "banks" / "gen"
BANKS = ROOT / "banks"
MAX_PASSES = 4


def _group_of(name: str) -> str:
    return name.split("-", 1)[0]


def _import_all(db: Database, files: list[Path], *,
                reviewer: str, tool: str) -> dict[str, set[str]]:
    """Import every file in order. Returns landed entry ids per file."""
    landed: dict[str, set[str]] = {}
    for path in files:
        before = {r["entry_id"] for r in db.bank_entries(limit=100_000)}
        report = bank_import.import_file(path, db,
                                         expect_group=_group_of(path.name),
                                         require_review=False)
        after = {r["entry_id"] for r in db.bank_entries(limit=100_000)}
        touched = (after - before) | set(report.replaced)
        landed[path.name] = touched
        for entry_id in sorted(touched):
            row = next((r for r in db.bank_entries(limit=100_000)
                        if r["entry_id"] == entry_id), None)
            if row is None:
                continue
            entry = BankEntry.from_dict(json.loads(row["payload"]))
            if not entry.provenance.get("tool"):
                entry.provenance = {**entry.provenance, "tool": tool,
                                    "batch": path.name}
                db.save_bank_entry(entry)
            bank_import.review(db, entry_id, reviewer=reviewer,
                               verdict="approve", kind="machine")
        if report.rejected:
            print(f"  {path.name}: {report.stored}/{report.seen} "
                  f"({len(report.rejected)} rejected)")
            for reason in report.rejected[:3]:
                print("      ", str(reason)[:150])
        else:
            print(f"  {path.name}: {report.stored}/{report.seen}")
    return landed


def _lines_of(path: Path) -> list[str]:
    return [ln for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def _id_of(raw: str) -> str | None:
    try:
        entry = BankEntry.from_dict(json.loads(raw))
    except Exception:                            # noqa: BLE001
        return None
    entry.recompute()
    return entry.entry_id


def _verify(files: list[Path], *, reviewer: str, tool: str) -> dict[str, set[str]]:
    """Import the delivery copy into a throwaway database.

    This is what the server does on a deploy, so whatever it rejects here
    is an entry that would ship and never be claimable.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "verify.db")
        try:
            return _import_all(db, files, reviewer=reviewer, tool=tool)
        finally:
            db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true",
                        help="write banks/ and rebuild the live database")
    parser.add_argument("--reviewer", default="claude-opus-5")
    parser.add_argument("--tool", default="claude-opus-5")
    args = parser.parse_args()

    staged = sorted(GEN.glob("*.jsonl"))
    if not staged:
        print(f"nothing staged in {GEN}")
        return 1
    print(f"{len(staged)} staged batches, "
          f"{sum(len(_lines_of(p)) for p in staged)} authored entries\n")

    if not args.confirm:
        print("DRY RUN - pass --confirm to rebuild. Importing into a "
              "throwaway database to show what would land:\n")
        landed = _verify(staged, reviewer=args.reviewer, tool=args.tool)
        print(f"\nwould land {sum(len(v) for v in landed.values())}")
        return 0

    cfg = load_config()
    live = Database(cfg.workspace / "autotube.db")
    try:
        # 1. Archive, then clear. The archive carries used_at/used_job_id,
        # which no JSONL file holds.
        rows = live.bank_entries(limit=100_000)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        out = BANKS / "archive" / stamp
        out.mkdir(parents=True, exist_ok=True)
        with (out / "bank_entries.jsonl").open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
        for row in rows:
            live.delete_bank_entry(row["entry_id"])
        print(f"archived and cleared {len(rows)} entries -> {out.name}\n")

        # 2. One deterministic pass over the staged batches.
        print("importing staged batches:")
        landed = _import_all(live, staged, reviewer=args.reviewer,
                             tool=args.tool)

        # 3. Write the delivery copy: only what landed.
        for path in staged:
            keep = [raw for raw in _lines_of(path)
                    if _id_of(raw) in landed.get(path.name, set())]
            target = BANKS / path.name
            if keep:
                target.write_text("\n".join(keep) + "\n", encoding="utf-8")
            elif target.exists():
                target.unlink()
        delivered = sorted(BANKS.glob("*.jsonl"))
        print(f"\nwrote {len(delivered)} delivery files")

        # 4. Verify against a FRESH database, the way the server will, and
        # prune. Pruning changes the shares, so repeat until stable.
        for attempt in range(1, MAX_PASSES + 1):
            print(f"\nverify pass {attempt} (fresh database, as on deploy):")
            landed = _verify(delivered, reviewer=args.reviewer,
                             tool=args.tool)
            dropped = 0
            for path in delivered:
                keep = [raw for raw in _lines_of(path)
                        if _id_of(raw) in landed.get(path.name, set())]
                if len(keep) != len(_lines_of(path)):
                    dropped += len(_lines_of(path)) - len(keep)
                    if keep:
                        path.write_text("\n".join(keep) + "\n",
                                        encoding="utf-8")
                    else:
                        path.unlink()
            delivered = sorted(BANKS.glob("*.jsonl"))
            if not dropped:
                print("  stable - every delivered entry lands on a fresh "
                      "database")
                break
            print(f"  pruned {dropped}; re-verifying because removing "
                  f"entries changes the shares")
        else:
            print("  WARNING: still unstable after "
                  f"{MAX_PASSES} passes")

        # 5. Make the live database match the delivery copy exactly.
        rows = live.bank_entries(limit=100_000)
        for row in rows:
            live.delete_bank_entry(row["entry_id"])
        print("\nrebuilding the live database from the delivery copy:")
        _import_all(live, delivered, reviewer=args.reviewer, tool=args.tool)

        total = sum(len(_lines_of(p)) for p in delivered)
        stored = len(live.bank_entries(limit=100_000))
        print(f"\ndelivery copy: {total} entries in {len(delivered)} files")
        print(f"live database: {stored} entries")
        for row in live.bank_counts():
            print(f"  {row['grp']:8} {row['language']:4} "
                  f"{row['video_format']:9} {row['total']:4} total, "
                  f"{row['unused']} unused")
        if total != stored:
            print("MISMATCH - the files and the database disagree")
            return 1
        print("the files and the database agree")
        return 0
    finally:
        live.close()


if __name__ == "__main__":
    sys.exit(main())
