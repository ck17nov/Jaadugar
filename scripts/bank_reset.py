#!/usr/bin/env python
"""Archive the script bank and empty it. Backup first, always.

    python scripts/bank_reset.py --backup-only
    python scripts/bank_reset.py --confirm

A bank entry is not regenerable: it was written once, gated once, and its
`used_at` records which video it became. So this writes a complete archive -
every row's full payload, plus the state the JSONL files do not carry - and
only then deletes. Without `--confirm` it does the archive and stops.

Archives land in `banks/archive/<timestamp>/`, which is inside the repo on
purpose: the JSONL half travels with git, so a clear is recoverable from any
checkout rather than from one laptop.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.core.config import load_config                   # noqa: E402
from engine.core.db import Database                           # noqa: E402

BANKS = Path(__file__).resolve().parents[1] / "banks"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true",
                        help="actually delete, after archiving")
    parser.add_argument("--backup-only", action="store_true",
                        help="archive and stop (the default behaviour)")
    args = parser.parse_args()

    cfg = load_config()
    db = Database(cfg.workspace / "autotube.db")
    try:
        rows = db.bank_entries(limit=100_000)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        out = BANKS / "archive" / stamp
        out.mkdir(parents=True, exist_ok=True)

        # 1. The rows, WITH their state. `used_at` and `used_job_id` say
        # which entry became which video, and no JSONL file carries them.
        with (out / "bank_entries.jsonl").open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(dict(row), ensure_ascii=False) + "\n")

        # 2. The delivery files as they stand.
        for path in sorted(BANKS.glob("*.jsonl")):
            shutil.copy2(path, out / path.name)

        used = sum(1 for r in rows if (r["used_at"] or 0) > 0)
        print(f"archived {len(rows)} entries ({used} already used) "
              f"and {len(list(BANKS.glob('*.jsonl')))} files to")
        print(f"  {out}")

        if not args.confirm:
            print("\nnothing deleted (pass --confirm to empty the bank)")
            return 0

        # 3. Only now.
        deleted = 0
        for row in rows:
            if db.delete_bank_entry(row["entry_id"]):
                deleted += 1
        for path in sorted(BANKS.glob("*.jsonl")):
            path.unlink()

        left = len(db.bank_entries(limit=10))
        print(f"\ndeleted {deleted} entries; {left} remain")
        return 0 if left == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
