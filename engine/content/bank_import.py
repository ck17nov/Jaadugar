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

import re

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
    # How many of `stored` REPLACED an entry that was already banked. An
    # entry_id is stated in the file whenever it came from `stories export`,
    # and storing one is an UPDATE, not an addition - so "26 of 26 stored"
    # while the bank stays at 26 rows was not a contradiction, it was an
    # unreported replacement. Which is fine when it was a correction and a
    # silent loss when the same id turned up twice by accident.
    replaced: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "seen": self.seen, "stored": self.stored,
                "rejected": self.rejected, "warnings": self.warnings,
                "unreviewed": self.unreviewed,
                "replaced": self.replaced, "stats": self.stats}

    def summary(self) -> str:
        added = self.stored - len(self.replaced)
        updated = (f", {len(self.replaced)} of them updates to entries "
                   f"already banked" if self.replaced else "")
        return (f"{self.stored} of {self.seen} stored ({added} new){updated}, "
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

    banked_ids = {e.entry_id for e in existing}
    for entry in entries:
        blockers = _gate(entry, existing, expect_group=expect_group,
                         require_review=require_review, report=report)
        if blockers:
            report.rejected.extend(blockers)
            continue
        if entry.entry_id in banked_ids:
            report.replaced.append(entry.entry_id)
            # Only inside ONE file is this a mistake worth flagging. Across
            # imports it is the correction workflow working as intended.
            if any(e.entry_id == entry.entry_id for e in entries
                   if e is not entry):
                report.warnings.append(
                    f"warn   {entry.entry_id} [entry_id] this id appears "
                    f"twice in this file; only the last one is stored")
        banked_ids.add(entry.entry_id)
        if not dry_run:
            db.save_bank_entry(entry)
        # Added to the in-memory catalogue either way, so two entries INSIDE
        # one file are compared against each other and not just against what
        # was already stored.
        existing.append(entry)
        report.stored += 1
        if not (entry.human or {}).get("reviewer"):
            report.unreviewed += 1

    # De-duplicated by id, because a replaced entry is in `existing` twice -
    # once as it was loaded from the bank and once as it was just imported -
    # and describing that list reported a bank bigger than the table.
    # Measured: "bank now: 31 entries" over a 26-row table.
    by_id: dict[str, Any] = {}
    for one in existing:
        by_id[one.entry_id] = one
    report.stats = variety.describe(list(by_id.values())).to_dict()
    log_event("BANK", "import complete", path=path.name, seen=report.seen,
              stored=report.stored, replaced=len(report.replaced),
              rejected=len(report.rejected), dry_run=dry_run)
    return report


# A runtime LLM handle: "<provider>:<model>", which is what every provider
# in LLMRouter stamps into provenance.tool. The provider list is groq,
# gemini, ollama and template (engine/content/llm.py:266,479,602,677).
_RUNTIME_TOOL = re.compile(r"^(groq|gemini|ollama|template|openai|google)\s*:",
                           re.I)


def _gate(entry: BankEntry, existing: list[BankEntry], *, expect_group: str,
          require_review: bool, report: ImportReport) -> list[str]:
    """Run all the gates. Returns the fatal reasons, or [] to store."""
    fatal: list[str] = []

    # ---- 0. THE BANK IS THE OWNER'S. ----
    #
    # An autofill loop used to top the bank up from the free LLM
    # tier, and the owner has since said plainly that they supply
    # every script themselves - the model may only write at render
    # time, for an automation whose script source is "live".
    #
    # Deleting the generator is not enough. A stale checkout, an old
    # staged file or a copy of the script on another machine would
    # still import cleanly, and the entries would look exactly like
    # the rest of the bank once they were in. So the refusal lives
    # at the door, where every path in has to pass it.
    # Two checks, because one literal is too easy to walk around. The
    # batch label is what the deleted script wrote; the tool handle is what
    # the RUNTIME writes, and it is the part a modified copy cannot avoid -
    # every provider in the router stamps "<provider>:<model>".
    #
    # The anchor is a colon on purpose. The owner's own batches are tagged
    # "gemini (external, operator-supplied)", which starts with a provider
    # name and is NOT a runtime handle; the 276 entries they commissioned
    # say "claude-opus-5". Both must keep importing, and both are covered
    # by controls in tests/test_bank_is_operator_only.py.
    provenance = entry.provenance or {}
    tool = str(provenance.get("tool") or "").strip().lower()
    machine_fill = (provenance.get("batch") == "autofill"
                    or _RUNTIME_TOOL.match(tool) is not None)
    if machine_fill:
        fatal.append(
            f"REJECT {entry.entry_id} [autofill] a model wrote this entry "
            f"({tool or 'batch=autofill'}); the bank holds only what the "
            f"owner supplied. Live generation at render time is "
            f"unaffected.")
        return fatal            # no point running craft gates on it

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
        # Beats passed through: the gate uses them to look at the
        # right scene, and to skip a question the form does not have.
        # Without them every poem was asked for an obstacle beat that
        # a poem does not contain - 21 of 54 banked poems reported
        # "nothing goes wrong anywhere", which is advisory noise that
        # teaches an author to loosen a real check.
        story = evaluate_story(entry.narrations(),
                               words_per_scene_floor=floor,
                               beats=entry.beats())
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

    # ---- 4. content safety, at the cheapest possible moment ----
    #
    # The SAME patterns the quality gate applies to a finished video, run at
    # import instead. Found the hard way: a hand-written kids story said
    # "there was a knife in the kitchen", which is a real kids-policy problem
    # and the gate was right to block it - but it blocked it after a
    # six-minute render, and the entry had already been consumed from the
    # pool. So a story that can never publish burned a claim and a render.
    #
    # Text only, so this cannot replace the render-time gate, which also sees
    # the generated title, description and thumbnail. It just moves the cheap
    # half of the check to where a rejection costs nothing.
    for hit in unsafe(entry):
        fatal.append(f"REJECT {entry.entry_id} [safety:{hit[0]}] {hit[1]}")

    # ---- 5. review ----
    #
    # The same test the claim gate uses, so "importable" and "claimable"
    # cannot drift apart: a REJECTED entry counts as unapproved, not as
    # reviewed.
    from .bank_use import approved
    if require_review and not approved(entry):
        review = entry.human or {}
        fatal.append(
            f"REJECT {entry.entry_id} [review] not approved "
            f"(reviewer={review.get('reviewer') or 'nobody'}, "
            f"verdict={review.get('verdict') or 'none'}) - an entry nobody "
            f"has approved must not publish")

    return fatal


def unsafe(entry: BankEntry) -> list[tuple[str, str]]:
    """Policy patterns this entry's own text trips. (label, detail) pairs.

    Checks the AUTHORED text - narration, titles, on-screen words, the
    description hook - against the same lists the quality gate uses on a
    finished video. The kids list applies only when the entry is
    child-directed, because "knife" in a cooking explainer is a knife and in
    a children's story it is a policy problem.

    Captions are checked too. They are burned into the picture, so a caption
    is on-screen text whatever language it is in.
    """
    import re

    from ..core.groups import group as get_group
    from ..quality.gate import KIDS_PROHIBITED, PROHIBITED_PATTERNS

    parts = [entry.title, entry.description_hook, *entry.title_alts]
    for scene in entry.scenes:
        parts += [scene.narration, scene.caption, scene.on_screen_text]
    haystack = " \n".join(p for p in parts if p)

    found = get_group(entry.group)
    child = bool(entry.made_for_kids or (found and found.child_directed))
    checks = list(PROHIBITED_PATTERNS)
    if child:
        checks += KIDS_PROHIBITED

    from ..quality.gate import violence_in

    hits: list[tuple[str, str]] = []
    for pattern, label in checks:
        # The violence entry needs the gate's own helper, not a bare regex
        # search: "the battery is dead" matches the pattern and is not
        # violence. Import and the render-time gate MUST agree, or an entry
        # imports cleanly and is then blocked after a six-minute render -
        # which is the exact failure this whole check exists to prevent.
        if label == "violence" and not violence_in(haystack):
            continue
        match = re.search(pattern, haystack, re.I)
        if not match:
            continue
        start = max(0, match.start() - 48)
        context = haystack[start:match.end() + 48].replace("\n", " ")
        hits.append((label, f"{match.group(0)!r} in …{context}…"))
    return hits


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
           element: str = "", kind: str = "human") -> bool:
    """Record that someone read this entry.

    `kind` is "human" or "machine", and it is not cosmetic. A model can
    legitimately author and approve a batch, but writing a model's name into
    a field called `human` would make the record claim a review that never
    happened - and the reason the field exists at all is YouTube's rule about
    AI content published "without adding the creator's original, authentic
    insights". `bank_use.reviewed_by_human` is what reads it.

    `element` is the human-authored contribution - a hand-written closing
    line, a chosen refrain, an author's note. YouTube's prohibited bullet is
    about AI content "without adding the creator's original, authentic
    insights", so this field is the thing that answers it, and it is worth
    keeping as data rather than as a claim.
    """
    # Stripped, because an id arrives pasted from a table or piped from
    # another command, and on Windows that carries a trailing carriage
    # return - which produced a bare "no entry" for an id that plainly
    # existed, with nothing on screen to show why.
    entry_id = (entry_id or "").strip()
    rows = db.bank_entries(limit=5000)
    row = next((r for r in rows if r["entry_id"] == entry_id), None)
    if row is None:
        return False
    entry = BankEntry.from_dict(json.loads(row["payload"]))
    entry.human = {"reviewer": reviewer, "verdict": verdict,
                   "kind": "machine" if kind == "machine" else "human",
                   "element": element,
                   "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                time.gmtime())}
    db.save_bank_entry(entry)
    log_event("BANK", "entry reviewed", entry=entry_id, reviewer=reviewer,
              verdict=verdict, kind=entry.human["kind"])
    return True
