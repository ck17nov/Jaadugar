#!/usr/bin/env python
"""Absorb every staged batch in banks/gen, and promote the clean ones.

    python scripts/bank_absorb_all.py
    python scripts/bank_absorb_all.py --promote

Generation writes to `banks/gen/` rather than straight to `banks/`, because a
half-written file in the catalogue breaks the invariant that every shipped
JSONL imports cleanly - and `tests/test_bank.py` checks exactly that. This
imports each staged file with the full gate, then (with `--promote`) rewrites
it into `banks/` containing ONLY the entries that actually landed, so the
delivery copy and the database agree.

Serial on purpose. The variety gate compares a candidate against everything
already stored, so importing two files at once would let each of them pass
against a catalogue that does not yet contain the other.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.content import bank_import                       # noqa: E402
from engine.content.bank import BankEntry                    # noqa: E402
from engine.core.config import load_config                   # noqa: E402
from engine.core.db import Database                           # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "banks" / "gen"


def _group_of(path: Path) -> str:
    return path.name.split("-", 1)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewer", default="claude-opus-5")
    parser.add_argument("--tool", default="claude-opus-5")
    parser.add_argument("--promote", action="store_true",
                        help="copy the entries that landed into banks/")
    parser.add_argument("--min-lines", type=int, default=1,
                        help="skip files with fewer lines - still being written")
    args = parser.parse_args()

    cfg = load_config()
    db = Database(cfg.workspace / "autotube.db")
    total_stored = total_rejected = 0
    try:
        for path in sorted(GEN.glob("*.jsonl")):
            lines = [ln for ln in
                     path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if len(lines) < args.min_lines:
                print(f"{path.name}: {len(lines)} lines, skipped")
                continue

            group = _group_of(path)
            before = {r["entry_id"] for r in db.bank_entries(limit=100_000)}
            report = bank_import.import_file(path, db, expect_group=group,
                                             require_review=False)
            after = {r["entry_id"] for r in db.bank_entries(limit=100_000)}
            touched = sorted((after - before) | set(report.replaced))

            for entry_id in touched:
                row = db.bank_entry(entry_id) if hasattr(db, "bank_entry") \
                    else next((r for r in db.bank_entries(limit=100_000)
                               if r["entry_id"] == entry_id), None)
                if row is None:
                    continue
                entry = BankEntry.from_dict(json.loads(row["payload"]))
                if not entry.provenance.get("tool"):
                    entry.provenance = {**entry.provenance,
                                        "tool": args.tool,
                                        "batch": path.name}
                    db.save_bank_entry(entry)
                bank_import.review(db, entry_id, reviewer=args.reviewer,
                                   verdict="approve", kind="machine")

            total_stored += report.stored
            total_rejected += len(report.rejected)
            print(f"{path.name}: {report.stored}/{report.seen} stored, "
                  f"{len(report.rejected)} rejected")
            for reason in report.rejected[:5]:
                print("    reject:", str(reason)[:170])

            if args.promote and touched:
                # ONLY what landed. A rejected line left in the delivery copy
                # would fail the catalogue-wide check in tests/test_bank.py,
                # and would look like shipped content that cannot be used.
                keep = []
                for raw in lines:
                    try:
                        candidate = BankEntry.from_dict(json.loads(raw))
                    except Exception:              # noqa: BLE001
                        continue
                    candidate.recompute()
                    if candidate.entry_id in after:
                        keep.append(raw)
                if keep:
                    out = ROOT / "banks" / path.name
                    out.write_text("\n".join(keep) + "\n", encoding="utf-8")
                    print(f"    -> banks/{path.name} ({len(keep)} entries)")

        counts = db.bank_counts()
        print("\nbank now holds:")
        for row in counts:
            print(f"  {row['grp']:8} {row['language']:4} "
                  f"{row['video_format']:9} {row['total']:4} "
                  f"({row['unused']} unused)")
        print(f"\n{total_stored} stored, {total_rejected} rejected in total")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
