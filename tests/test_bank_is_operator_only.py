"""The bank holds only what the owner supplied.

An autofill loop used to top the bank up from the free LLM tier - Gemini and
Groq - and it put 110 entries in before the owner said plainly that they
supply every script themselves. The model may still write at render time,
but only for an automation whose script source is "live" ("Write a new one
each time").

These tests pin both halves of that, because both are easy to undo by
accident: the door refusal, and the fact that live generation has never
written to the bank and must not start.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.content import bank_import
from engine.core.db import Database


def _entry(**over) -> dict:
    """A minimal entry that passes the gates, so a rejection is the guard."""
    base = {
        "group": "kids",
        "language": "en",
        "topic": "kids alphabet learning",
        "shape": "drill",
        "video_format": "SHORT",
        "made_for_kids": True,
        "title": "Say the letter B with me",
        # Three scenes minimum, and an `en` entry's captions must be in the
        # other script - the importer checks both, and a fixture that failed
        # them would make every test here pass for the wrong reason.
        "scenes": [
            {"beat": "open", "narration": "B is for ball. Say it with me: B.",
             "caption": "बी से बॉल", "image_brief": "a red ball"},
            {"beat": "drill", "narration": "B is for bus. Say it with me: B.",
             "caption": "बी से बस", "image_brief": "a yellow bus"},
            {"beat": "close", "narration": "B is for bird. Say it with me: B.",
             "caption": "बी से बर्ड", "image_brief": "a small blue bird"},
        ],
    }
    base.update(over)
    return base


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def _write(tmp_path: Path, *rows: dict) -> Path:
    p = tmp_path / "kids-en-short-kids-alphabet-learning.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
                 + "\n", encoding="utf-8")
    return p


# ==========================================================================
class TestTheDoorRefusesAutofill:

    def test_an_autofilled_entry_is_refused(self, db, tmp_path):
        path = _write(tmp_path, _entry(provenance={
            "tool": "groq:openai/gpt-oss-120b", "batch": "autofill",
            "written_at": "2026-09-12T08:00:00Z"}))
        report = bank_import.import_file(path, db, expect_group="kids")
        assert report.stored == 0, "an autofilled entry must not be stored"
        assert any("[autofill]" in r for r in report.rejected), report.rejected

    def test_the_same_entry_lands_when_the_owner_supplied_it(self, db,
                                                             tmp_path):
        """The control. Identical content, honest provenance - so a failure
        here would mean the guard rejects on something other than the
        marker."""
        path = _write(tmp_path, _entry(provenance={
            "tool": "gemini (external, operator-supplied)",
            "batch": "Kids_scriptbank_gemini.txt"}))
        report = bank_import.import_file(path, db, expect_group="kids")
        assert report.stored == 1, report.rejected

    def test_an_entry_with_no_provenance_still_lands(self, db, tmp_path):
        """The spec tells authors to omit provenance and let the importer
        fill it, so absent must not be treated as suspect."""
        path = _write(tmp_path, _entry())
        report = bank_import.import_file(path, db, expect_group="kids")
        assert report.stored == 1, report.rejected

    def test_a_model_written_batch_the_owner_asked_for_still_lands(self, db,
                                                                   tmp_path):
        """The guard is deliberately narrow - it keys on the autofill marker,
        not on whether a model was involved. The owner commissioned 276
        machine-written entries on purpose and those must keep working."""
        path = _write(tmp_path, _entry(provenance={
            "tool": "claude-opus-5",
            "batch": "kids-en-short-kids-alphabet-learning.jsonl"}))
        report = bank_import.import_file(path, db, expect_group="kids")
        assert report.stored == 1, report.rejected

    def test_the_refusal_is_reported_before_the_craft_gates(self, db,
                                                            tmp_path):
        """One reason, not a pile. A rejected autofill entry should say why
        it was rejected, not also lecture about its refrain."""
        path = _write(tmp_path, _entry(provenance={"batch": "autofill"}))
        report = bank_import.import_file(path, db, expect_group="kids")
        reasons = [r for r in report.rejected if "REJECT" in r]
        assert len(reasons) == 1, reasons
        assert "[autofill]" in reasons[0]


# ==========================================================================
class TestShippedBankHoldsNoMachineFill:
    """The 110 that got in before the rule existed are gone. This fails if
    any of them comes back through a merge, a revert or a stale staged file.
    """

    def _rows(self, root: str):
        for path in sorted(Path(root).glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    yield path.name, json.loads(line)

    @pytest.mark.parametrize("root", ["banks", "banks/gen"])
    def test_no_shipped_entry_was_autofilled(self, root):
        bad = [(name, row.get("title", "")[:40])
               for name, row in self._rows(root)
               if (row.get("provenance") or {}).get("batch") == "autofill"]
        assert not bad, f"{len(bad)} autofilled entries in {root}: {bad[:5]}"

    @pytest.mark.parametrize("root", ["banks", "banks/gen"])
    def test_no_shipped_entry_credits_a_free_tier_provider(self, root):
        """Belt and braces: the autofill stamped `provider:model`, so a
        colon-separated tool from groq or the gemini API is the same thing
        under a different batch name. The owner's own Gemini batches say
        `gemini (external, operator-supplied)`, which has no colon.
        """
        bad = []
        for name, row in self._rows(root):
            tool = ((row.get("provenance") or {}).get("tool") or "").lower()
            if tool.startswith(("groq:", "gemini:", "openai:", "google:")):
                bad.append((name, tool))
        assert not bad, (f"{len(bad)} entries credited to a live provider: "
                         f"{bad[:5]}")


# ==========================================================================
class TestLiveGenerationNeverBanks:
    """Live generation writes to the `scripts` table, which is render
    history. It has never written to `bank_entries` and must not start -
    otherwise "write a new one each time" would quietly refill the bank.
    """

    def test_the_pipeline_never_saves_a_bank_entry(self):
        src = Path("engine/pipeline.py").read_text(encoding="utf-8")
        for forbidden in ("save_bank_entry", "import_file", "bank_import"):
            assert forbidden not in src, (
                f"engine/pipeline.py references {forbidden!r} - the render "
                f"path must not be able to insert bank entries")

    def test_no_http_endpoint_writes_the_bank(self):
        src = Path("backend/api/main.py").read_text(encoding="utf-8")
        assert "save_bank_entry" not in src
        assert "bank_import" not in src

    def test_the_autofill_scripts_are_gone(self):
        for name in ("bank_autofill.py", "bank_publish.py",
                     "bank_collect.py"):
            assert not Path("scripts", name).exists(), (
                f"scripts/{name} is back - it exists only to put "
                f"machine-written entries in the bank")

    def test_the_nightly_unit_does_not_read_outside_the_checkout(self):
        unit = Path("deploy/oracle/autotube-bankrebuild.service")
        lines = [ln for ln in unit.read_text(encoding="utf-8").splitlines()
                 if ln.startswith("ExecStart") or ln.startswith("    --")]
        assert "--extra-stage" not in " ".join(lines), (
            "the nightly rebuild must not import a staging directory "
            "outside git - that was how autofilled entries reached the bank")


# ==========================================================================
class TestARefrainHasToBeARefrain:
    """Gate 2 (story shape) runs for narrative and poem only, so every
    drill, explainer and procedure - 540 of 893 banked entries - got no
    craft check at all. That is where 119 of the 122 entries the owner
    rejected as "not engaging" were sitting.

    Only one measured difference became a gate, and deliberately so. Most of
    them cannot: provenance is perfectly collinear with the verdict (no Groq
    entry was kept, no Claude or Gemini entry was rejected), so a measured
    difference identifies the model as readily as the flaw - curly
    apostrophes separate the two sets as well as any craft feature.

    A refrain declared and then spoken once is different in kind. The entry
    contradicts its own field. Measured: 11 of the 32 rejected entries with
    a declared refrain, against 0 of the 721 kept ones.
    """

    def _drill(self, refrain: str, lines: list[str]) -> dict:
        return {
            "group": "kids", "language": "en",
            "topic": "kids alphabet learning", "shape": "drill",
            "video_format": "SHORT", "made_for_kids": True,
            "title": "Say the letter B with me", "refrain": refrain,
            "scenes": [{"beat": "call", "narration": n,
                        "caption": "बी से बॉल", "image_brief": "a red ball"}
                       for n in lines],
        }

    def test_a_refrain_spoken_once_is_refused(self, db, tmp_path):
        path = _write(tmp_path, self._drill("B says buh", [
            "B says buh. Say it with me.",
            "B is for ball, round and red.",
            "B is for bus, big and yellow."]))
        report = bank_import.import_file(path, db, expect_group="kids")
        assert report.stored == 0
        assert any("[refrain_not_repeated]" in r for r in report.rejected), \
            report.rejected

    def test_a_refrain_spoken_twice_lands(self, db, tmp_path):
        path = _write(tmp_path, self._drill("B says buh", [
            "B says buh. Say it with me.",
            "B is for ball, round and red.",
            "B says buh. Say it again."]))
        report = bank_import.import_file(path, db, expect_group="kids")
        assert report.stored == 1, report.rejected

    def test_a_shape_with_no_refrain_is_not_asked_for_one(self, db,
                                                          tmp_path):
        """The check must only fire on a DECLARED refrain, or it would
        reject every explainer and procedure in the bank."""
        entry = self._drill("", [
            "B is for ball, round and red.",
            "B is for bus, big and yellow.",
            "B is for bird, small and blue."])
        entry["shape"] = "explainer"
        entry["group"] = "tech"
        entry["topic"] = "windows tips and tricks"
        entry["made_for_kids"] = False
        entry["language"] = "en"
        for scene in entry["scenes"]:
            scene["caption"] = scene["narration"][:20]
        path = _write(tmp_path, entry)
        report = bank_import.import_file(path, db, expect_group="tech")
        assert not any("refrain_not_repeated" in r for r in report.rejected), \
            report.rejected

    def test_grading_the_child_warns_but_does_not_block(self, db, tmp_path):
        """Every one of the 10 rejected Hindi drills had सही or बिल्कुल in
        its refrain. Advisory, because a celebration line after the answer
        is a legitimate choice - only using it AS the refrain is the
        mistake, and that judgement is the owner's."""
        path = _write(tmp_path, self._drill("Correct, that is B!", [
            "Correct, that is B! Say it with me.",
            "B is for ball, round and red.",
            "Correct, that is B! Say it again."]))
        report = bank_import.import_file(path, db, expect_group="kids")
        assert report.stored == 1, report.rejected
        assert any("[refrain_grades_the_child]" in w
                   for w in report.warnings), report.warnings

    def test_the_whole_kept_bank_passes_both_checks(self):
        """The measurement that decides whether these checks are safe.

        A check that rejects what the owner chose to keep is worse than no
        check. Zero of 893 when this was written - and stated as zero
        rather than a rate, because unlike a content property this one is
        the gate's own behaviour on a fixed corpus.
        """
        from engine.content.bank import BankEntry
        from engine.content.bank_import import _SELF_VERDICT

        blocked = []
        for path in sorted(Path("banks").glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = BankEntry.from_dict(json.loads(line))
                if not (entry.refrain and entry.narrations()):
                    continue
                if " ".join(entry.narrations()).count(entry.refrain) < 2:
                    blocked.append((path.name, entry.entry_id,
                                    entry.refrain[:40]))
        assert not blocked, (
            f"{len(blocked)} entries the owner kept would now be refused: "
            f"{blocked[:5]}")
        assert _SELF_VERDICT.search("सही") is not None, "pattern alive"
