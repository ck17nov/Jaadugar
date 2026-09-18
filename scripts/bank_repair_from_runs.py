"""Re-derive bank usage from run directories, for jobs that predate the fix.

WHY THIS EXISTS
---------------
`bank_rebuild.py` used to clear `bank_entries` and re-import from the delivery
JSONL, which carries no `used_at`. Running nightly, that handed the whole
catalogue back as unused every night: 1,193 of 1,193 entries read "unused"
against 27 uploads, and the live channel ended up with one title published
four times and two more three times.

The rebuild now snapshots and restores, and every job written since records
`bank_entry_id` in its payload (`Database.bank_usage_from_jobs`). Neither
helps the jobs that ran BEFORE that: their claim is gone from the table and
was never on the job.

What did survive is `idea.json` in each run directory, which the pipeline
writes as `{"source": "bank:<entry_id>"}`. This script reads those and marks
the entries used, so already-published scripts stop being claimable.

A FAILED or REJECTED job is SKIPPED: it released its claim on the way out
(`Pipeline._release_bank`), so its script is genuinely unused, and retiring it
would lose a good script nobody has seen. Any other status is treated as
spent - conservative, because the cost of a false positive is one unused
script and the cost of a false negative is a duplicate upload.

Read-only without `--confirm`.

    python scripts/bank_repair_from_runs.py            # report
    python scripts/bank_repair_from_runs.py --confirm  # write
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.core.config import load_config          # noqa: E402
from engine.core.db import Database                 # noqa: E402
from engine.core.models import JobStatus            # noqa: E402

RELEASED = {JobStatus.FAILED.value, JobStatus.REJECTED.value}


def entry_from_idea(path: Path) -> str:
    """The entry id `idea.json` records, or "" if it was not bank-backed."""
    try:
        source = str(json.loads(path.read_text(encoding="utf-8")
                                ).get("source") or "")
    except Exception:                               # noqa: BLE001
        return ""
    return source.split("bank:", 1)[1].strip() if source.startswith("bank:") \
        else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--confirm", action="store_true",
                    help="write the markers; without it, report only")
    args = ap.parse_args()

    cfg = load_config()
    db = Database(cfg.workspace / "autotube.db")
    try:
        jobs = db.list_jobs(limit=100_000)

        found: dict[str, tuple[float, str]] = {}
        skipped_released = 0
        seen_dirs: set[Path] = set()

        # Preferred pass: walk the JOBS, so every run directory arrives with
        # its status attached. The directory is named
        # `{stamp}_{slug}_{job_id[-6:]}`, not after the job, so going the
        # other way cannot identify the job reliably.
        for job in jobs:
            if not job.dir:
                continue
            idea = Path(job.dir) / "idea.json"
            if not idea.is_file():
                continue
            seen_dirs.add(idea.resolve())
            entry_id = entry_from_idea(idea)
            if not entry_id:
                continue
            if job.status in RELEASED:
                skipped_released += 1
                continue
            found[entry_id] = (job.updated_at or idea.stat().st_mtime,
                               job.job_id)

        # Second pass: run directories whose job row is gone. Their status is
        # unknowable, so they count as spent - one unused script is cheaper
        # than one duplicate upload.
        for idea in sorted(cfg.workspace.rglob("idea.json")):
            if idea.resolve() in seen_dirs:
                continue
            entry_id = entry_from_idea(idea)
            if entry_id:
                found.setdefault(entry_id, (idea.stat().st_mtime, ""))

        # The job history covers everything written since the fix landed.
        from_jobs = db.bank_usage_from_jobs()
        for entry_id, value in from_jobs.items():
            found.setdefault(entry_id, value)

        total = len(db.bank_entries(limit=100_000))
        used_now = len(db.bank_usage_snapshot())
        missing = {e: v for e, v in found.items()
                   if e not in db.bank_usage_snapshot()}

        print(f"bank          : {total} entries, {used_now} marked used")
        print(f"run dirs      : {len(found)} entry id(s) recovered "
              f"({skipped_released} skipped as released)")
        print(f"job history   : {len(from_jobs)} entry id(s)")
        print(f"missing marker: {len(missing)}")
        for entry_id in sorted(missing)[:12]:
            print(f"   {entry_id}")
        if len(missing) > 12:
            print(f"   ... and {len(missing) - 12} more")

        if not args.confirm:
            print("\ndry run. re-run with --confirm to write.")
            return 0

        applied = db.restore_bank_usage(
            {e: (v[0] or time.time(), v[1]) for e, v in missing.items()})
        print(f"\nmarked {applied} entr{'y' if applied == 1 else 'ies'} used")
        print(f"bank now      : {len(db.bank_usage_snapshot())} of {total} used")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
