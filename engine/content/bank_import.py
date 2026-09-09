"""Import a bank file: validate, gate, de-duplicate, store.

Four gates, in the order that makes the cheapest one fail first:

  1. SCHEMA - is this a well-formed entry at all (bank.validate).
  2. STORY SHAPE - for narrative content, does it contain a story
     (story_gate.evaluate, the same gate live generation must pass).
  3. VARIETY - is it different enough from what is already banked
     (variety.check_new). This is the one that protects monetisation, and it
     is the one the existing originality checker cannot do.
  4. HUMAN REVIEW - recorded, not performed here. An entry without a review
     is imported but held back from rendering, so the queue can never publish
     something nobody read.

Nothing is stored unless it passes 1-3. Rejections are reported with the
reason and the entry they collided with, because "300 in, 240 stored" is
useless without knowing why sixty were dropped.

The importer is deliberately re-runnable: fix the file, import again, and
already-used entries keep their used state (db.save_bank_entry) so a
correction cannot republish a story that has already gone out.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.logging import log_event
from . import variety
from .bank import BankEntry, load_jsonl, validate
from .story_gate import evaluate as evaluate_story


@dataclass
class ImportReport:
    path: str = ""
    seen: int = 0
    stored: int = 0
    rejected: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unreviewed: int = 0
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "seen": self.seen, "stored": self.stored,
                "rejected": self.rejected, "warnings": self.warnings,
                "unreviewed": self.unreviewed, "stats": self.stats}

    def summary(self) -> str:
        return (f"{self.stored} of {self.seen} stored, "
                f"{len(self.rejected)} rejected, "
                f"{len(self.warnings)} warnings, "
                f"{self.unreviewed} awaiting human review")


def import_file(path: Path, db, *, expect_group: str = "",
                dry_run: bool = False,
                require_review: bool = False) -> ImportReport:
    """Import one JSONL bank file.

    `require_review` refuses to store an entry with no `human.reviewer`. Off
    by default so a test batch can be wired up and rendered before anyone has
    read all 300, but it should be ON for anything that will publish: the
    monetisation checklist needs a human read-and-approve per entry, and it
    also satisfies Anthropic's human-in-the-loop duty for the finance group.
    """
    report = ImportReport(path=str(path))
    entries, file_problems = load_jsonl(path)
    report.seen = len(entries)
    for problem in file_problems:
        report.rejected.append(str(problem))

    # Everything already banked, so variety is judged against the real
    # catalogue rather than only against this file.
    existing = _load_existing(db)

    for entry in entries:
        blockers = _gate(entry, existing, expect_group=expect_group,
                         require_review=require_review, report=report)
        if blockers:
            report.rejected.extend(blockers)
            continue
        if not dry_run:
            db.save_bank_entry(entry)
        # Added to the in-memory catalogue either way, so two entries INSIDE
        # one file are compared against each other and not just against what
        # was already stored.
        existing.append(entry)
        report.stored += 1
        if not (entry.human or {}).get("reviewer"):
            report.unreviewed += 1

    report.stats = variety.describe(existing).to_dict()
    log_event("BANK", "import complete", path=path.name, seen=report.seen,
              stored=report.stored, rejected=len(report.rejected),
              dry_run=dry_run)
    return report


def _gate(entry: BankEntry, existing: list[BankEntry], *, expect_group: str,
          require_review: bool, report: ImportReport) -> list[str]:
    """Run all the gates. Returns the fatal reasons, or [] to store."""
    fatal: list[str] = []

    # ---- 1. schema ----
    problems = validate(entry, expect_group=expect_group)
    for problem in problems:
        (fatal if problem.fatal else report.warnings).append(str(problem))

    # ---- 2. story shape, for narrative content only ----
    #
    # The same gate live generation has to pass. Running it here is the whole
    # point of a curated bank: a weak story costs nothing to reject now and a
    # four-hour render to discover later.
    if entry.shape in ("narrative", "poem") and entry.narrations():
        floor = max(8, (entry.word_count // max(entry.scene_count, 1)) - 6)
        story = evaluate_story(entry.narrations(), words_per_scene_floor=floor)
        for finding in story.blockers:
            fatal.append(f"REJECT {entry.entry_id} [story:{finding.check}] "
                         f"{finding.detail}")
        for finding in story.warnings:
            report.warnings.append(
                f"warn   {entry.entry_id} [story:{finding.check}] "
                f"{finding.detail}")

    # ---- 3. variety, against the catalogue ----
    for issue in variety.check_new(entry, existing):
        (fatal if issue.fatal else report.warnings).append(str(issue))

    # ---- 4. human review ----
    if require_review and not (entry.human or {}).get("reviewer"):
        fatal.append(f"REJECT {entry.entry_id} [review] no human.reviewer - "
                     f"an entry nobody has read must not publish")

    return fatal


def _load_existing(db) -> list[BankEntry]:
    """Every stored entry, as objects, for the variety comparison."""
    out: list[BankEntry] = []
    for row in db.bank_entries(limit=5000):
        try:
            out.append(BankEntry.from_dict(json.loads(row["payload"])))
        except Exception:                       # noqa: BLE001
            continue
    return out


def review(db, entry_id: str, *, reviewer: str, verdict: str = "approve",
           element: str = "") -> bool:
    """Record that a human read this entry.

    `element` is the human-authored contribution - a hand-written closing
    line, a chosen refrain, an author's note. YouTube's prohibited bullet is
    about AI content "without adding the creator's original, authentic
    insights", so this field is the thing that answers it, and it is worth
    keeping as data rather than as a claim.
    """
    rows = db.bank_entries(limit=5000)
    row = next((r for r in rows if r["entry_id"] == entry_id), None)
    if row is None:
        return False
    entry = BankEntry.from_dict(json.loads(row["payload"]))
    entry.human = {"reviewer": reviewer, "verdict": verdict,
                   "element": element,
                   "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                time.gmtime())}
    db.save_bank_entry(entry)
    log_event("BANK", "entry reviewed", entry=entry_id, reviewer=reviewer,
              verdict=verdict)
    return True
