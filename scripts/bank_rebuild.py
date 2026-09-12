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
import os
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


class Busy(RuntimeError):
    """Another rebuild holds the lock."""


def _lock_path() -> Path:
    return load_config().workspace / "bank_rebuild.lock"


def _acquire() -> Path:
    """Refuse to run while another rebuild is in flight.

    Both steps of a rebuild are destructive - it clears the live database and
    rewrites banks/ - so two overlapping runs interleave and the result is
    neither. Observed: two cycles minutes apart, 138 entries then 134, from a
    staged set that only grows.

    O_EXCL, so the check and the create are one operation.
    """
    lock = _lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        held = ""
        try:
            held = lock.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        raise Busy(f"another rebuild is running ({held or 'unknown pid'}). "
                   f"If it is dead, delete {lock}") from None
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"pid={os.getpid()} at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    return lock


def _snapshot(files: list[Path], into: Path) -> list[Path]:
    """Copy the staged batches, dropping a truncated final line.

    The authors are still appending while this runs, so a file read
    mid-append ends in a partial JSON object. That is a partial write, not a
    corrupt batch: keep every line that parses and say how many were left
    behind, rather than letting one silently vanish from the delivery copy.
    """
    into.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for path in files:
        keep = []
        dropped = 0
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                json.loads(raw)
            except ValueError:
                dropped += 1
                continue
            keep.append(raw)
        if dropped:
            print(f"  {path.name}: skipped {dropped} partially written "
                  f"line(s) - an author is still appending")
        if not keep:
            continue
        target = into / path.name
        target.write_text("\n".join(keep) + "\n", encoding="utf-8")
        out.append(target)
    return out


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
    try:
        lock = _acquire()
    except Busy as exc:
        print(exc)
        return 2
    try:
        return _run()
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true",
                        help="write banks/ and rebuild the live database")
    parser.add_argument("--extra-stage", action="append", default=[],
                        metavar="DIR",
                        help="another staging directory to import, e.g. the "
                             "server's workspace/bank-gen. Repeatable.")
    parser.add_argument("--no-promote", action="store_true",
                        help="do not rewrite banks/. Required on the server, "
                             "where banks/ is the git checkout and dirtying "
                             "it breaks git pull --ff-only.")
    parser.add_argument("--reviewer", default="claude-opus-5")
    parser.add_argument("--tool", default="claude-opus-5")
    args = parser.parse_args()

    staged = sorted(GEN.glob("*.jsonl"))
    for extra in args.extra_stage:
        found = sorted(Path(extra).glob("*.jsonl"))
        print(f"  + {len(found)} batches from {extra}")
        staged += found
    if not staged:
        print(f"nothing staged in {GEN}")
        return 1
    print(f"{len(staged)} staged batches, "
          f"{sum(len(_lines_of(p)) for p in staged)} authored entries\n")

    if not args.confirm:
        print("DRY RUN - pass --confirm to rebuild. Importing into a "
              "throwaway database to show what would land:\n")
        with tempfile.TemporaryDirectory() as tmp:
            snap = _snapshot(staged, Path(tmp) / "gen")
            landed = _verify(snap, reviewer=args.reviewer, tool=args.tool)
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

        # 2. SNAPSHOT, then one deterministic pass. The authors are still
        # writing banks/gen, and a file read mid-append loses its last
        # entry - which is how two rebuilds minutes apart produced 138 and
        # then 134 from a staged set that only grows.
        snapdir = tempfile.mkdtemp(prefix="bank-snap-")
        try:
            snap = _snapshot(staged, Path(snapdir) / "gen")
            print("importing staged batches:")
            landed = _import_all(live, snap, reviewer=args.reviewer,
                                 tool=args.tool)

            # 3. Write the delivery copy: only what landed.
            #
            # Skipped on the server: banks/ there IS the git checkout, and a
            # dirty tree makes the next `git pull --ff-only` fail.
            for path in ([] if args.no_promote else snap):
                # ONE LINE PER STORED ROW. `landed` is a set of entry_ids,
                # so a membership filter kept BOTH copies whenever two
                # staged lines hashed the same - while the database, keyed
                # on entry_id, held one row. An outside batch with 15
                # repeated scripts therefore produced a 1020-line delivery
                # copy against 1005 rows, and step 5 correctly called it a
                # mismatch. First occurrence wins, so the result stays
                # deterministic in file order.
                want = landed.get(path.name, set())
                seen: set[str] = set()
                keep = []
                for raw in _lines_of(path):
                    entry_id = _id_of(raw)
                    if entry_id in want and entry_id not in seen:
                        seen.add(entry_id)
                        keep.append(raw)
                if len(seen) != len(want):
                    print(f"  {path.name}: {len(want) - len(seen)} stored "
                          f"row(s) had no line to write")
                target = BANKS / path.name
                if keep:
                    target.write_text("\n".join(keep) + "\n",
                                      encoding="utf-8")
                elif target.exists():
                    target.unlink()
        finally:
            shutil.rmtree(snapdir, ignore_errors=True)
        # ON THE SERVER, STOP HERE - and write nothing.
        #
        # `delivered` used to be set to `staged` under --no-promote,
        # and the prune loop below rewrites and unlinks every path in
        # it. So the one flag whose whole job is "do not touch the
        # checkout" deleted six files out of banks/gen and modified six
        # more - exactly the state that makes the next
        # `git pull --ff-only` fail. It also pruned workspace/bank-gen,
        # where the autofill's only copy of an entry lives until
        # `bank_publish.py` commits it.
        #
        # Pruning is a DEVELOPER operation: it ends in a commit, and
        # there is nothing here to commit to. So verify read-only, say
        # what a fresh database would do with the staged input, and
        # leave the live database as step 2 built it - which is the
        # point of --extra-stage, since the autofilled entries exist
        # nowhere else yet.
        if args.no_promote:
            print(f"\nnot promoting (banks/ is the checkout here); "
                  f"{len(staged)} staged batches are the source")
            fresh = _verify(staged, reviewer=args.reviewer,
                            tool=args.tool)
            offered = sum(len(_lines_of(path)) for path in staged)
            would = sum(len(ids) for ids in fresh.values())
            stored = len(live.bank_entries(limit=100_000))
            print(f"\nstaged lines : {offered}")
            print(f"fresh import : {would} would land")
            print(f"live database: {stored} entries")
            if would != stored:
                print(f"NOTE: the live database and a fresh import of "
                      f"the staged set differ by {stored - would}; run "
                      f"bank_publish.py to commit what this box "
                      f"generated")
            return 0

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
            # Re-glob because a file that lost every line was
            # unlinked. Only reachable on the developer path - the
            # server returns above, before anything is written.
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
