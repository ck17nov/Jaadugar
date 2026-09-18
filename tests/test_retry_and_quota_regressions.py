"""Four bugs found in production on 18 September 2026, two of them mine.

Reported together: automations overlapping and failing on quota, failed logs
not clearing, a recurring automation flickering out of the schedule, and the
same script published repeatedly.

They were four separate causes, and the evidence for each is in the test that
pins it.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("AUTOTUBE_WORKSPACE", tempfile.mkdtemp())

from engine.content.bank import BankEntry          # noqa: E402
from engine.core.db import Database                # noqa: E402
from engine.core.models import JobStatus, VideoJob  # noqa: E402


# ==========================================================================
class TestRetryMustNotRewriteTheAutomation:
    """THE WORST OF THE FOUR, and self-inflicted.

    The retry endpoint rebuilds its request from the failed job, which
    carries the ORIGINAL automation's id. It then set frequency="once" and
    called a submit that persists - and `save_automation` upserts on that id
    with `ON CONFLICT(id) DO UPDATE SET frequency=excluded.frequency`.

    So every retry rewrote the operator's live `daily` automation to `once`,
    which hid it from the schedule as "finished". It came back only when the
    phone next POSTed the daily request, so the list appeared to flicker; the
    one automation that did not fire again (17:00) stayed lost and was
    repaired by hand.
    """

    def test_submit_can_run_without_persisting(self):
        src = Path("backend/api/main.py").read_text(encoding="utf-8")
        assert "def submit(self, request: AutomationRequest,\n" \
               "               *, persist: bool = True)" in src, \
            "submit must offer a non-persisting path for retry"

    def test_the_retry_endpoint_uses_it(self):
        src = Path("backend/api/main.py").read_text(encoding="utf-8")
        start = src.index('@app.post("/jobs/{job_id}/retry"')
        block = src[start:src.index("@app.post", start + 10)]
        assert "WORKER.submit(request, persist=False)" in block, \
            "a retry must not write to the automations table"
        assert "WORKER.submit(request)\n" not in block

    def test_save_automation_still_upserts_on_id(self):
        """The property that made the bug possible. If this ever stops being
        true the fix above is still correct, but the reasoning changes."""
        src = Path("engine/core/db.py").read_text(encoding="utf-8")
        assert "ON CONFLICT(id) DO UPDATE SET" in src
        assert "frequency=excluded.frequency" in src


# ==========================================================================
class TestTheRebuildMustNotForgetWhatWasPublished:
    """The duplicate-video cause, also self-inflicted.

    `bank_rebuild.py` archives, DELETES every bank row, then re-imports from
    JSONL - and the delivery JSONL carries no used_at. `save_bank_entry`
    preserves usage by reading the row it replaces, so with the row gone the
    whole catalogue came back UNUSED.

    Running nightly, that made every script claimable again every night. The
    live channel shows one title published four times and two more three
    times, with 1,193 of 1,193 entries reading "unused" against 27 uploads.
    """

    @pytest.fixture()
    def db(self, tmp_path):
        d = Database(tmp_path / "t.db")
        yield d
        d.close()

    def _entry(self, db, suffix: str) -> str:
        e = BankEntry.from_dict({
            "group": "kids", "language": "en",
            "topic": "kids alphabet learning", "shape": "drill",
            "video_format": "SHORT", "made_for_kids": True,
            "title": f"Say {suffix} with me",
            "scenes": [
                {"beat": "open", "narration": f"{suffix} is for apple here.",
                 "caption": "क", "image_brief": "an apple"},
                {"beat": "drill", "narration": f"{suffix} is for ball here.",
                 "caption": "ख", "image_brief": "a ball"},
                {"beat": "close", "narration": f"{suffix} is for cat here.",
                 "caption": "ग", "image_brief": "a cat"},
            ],
        })
        e.recompute()
        db.save_bank_entry(e)
        return e.entry_id

    def test_a_snapshot_survives_a_clear_and_reimport(self, db):
        kept = self._entry(db, "A")
        spare = self._entry(db, "B")
        assert db.claim_specific_bank_entry(kept, "job_1") is not None

        usage = db.bank_usage_snapshot()
        assert kept in usage and spare not in usage

        # What the rebuild does: delete everything, then re-import.
        for row in db.bank_entries(limit=1000):
            db.delete_bank_entry(row["entry_id"])
        self._entry(db, "A")
        self._entry(db, "B")
        assert not db.bank_usage_snapshot(), \
            "a re-import without the restore loses every claim - this is the bug"

        assert db.restore_bank_usage(usage) == 1
        back = db.bank_usage_snapshot()
        assert kept in back, "the published script must stay used"
        assert spare not in back, "an unused script must stay claimable"

    def test_a_newer_claim_is_not_overwritten(self, db):
        """An entry claimed by a render that started DURING the rebuild must
        keep the newer claim, not have an older one stamped over it."""
        eid = self._entry(db, "C")
        usage = {eid: (1.0, "old_job")}
        assert db.claim_specific_bank_entry(eid, "new_job") is not None
        assert db.restore_bank_usage(usage) == 0, \
            "restore must skip rows that are already claimed"
        row = db.query_one("SELECT used_job_id FROM bank_entries "
                           "WHERE entry_id=?", (eid,))
        assert row["used_job_id"] == "new_job"

    def test_the_rebuild_captures_before_clearing_and_restores_after(self):
        src = Path("scripts/bank_rebuild.py").read_text(encoding="utf-8")
        snap = src.index("usage = live.bank_usage_snapshot()")
        first_clear = src.index("live.delete_bank_entry")
        assert snap < first_clear, \
            "the snapshot must be taken before anything is deleted"
        # BOTH exit paths restore: the server returns early on --no-promote.
        assert src.count("restore_bank_usage(usage)") >= 2, \
            "the --no-promote path returns early and needs its own restore"


# ==========================================================================
class TestQuotaIsCheckedBeforeRendering:
    """The operator's day and Google's quota day OVERLAP.

    Midnight in Asia/Kolkata is 11:30 the previous morning in
    America/Los_Angeles, where quota resets. So automations at 16:00-19:00
    IST and one just after midnight IST land in the SAME Pacific quota day.
    Measured: four uploads at 23:57 IST plus two at 05:11 and 05:18 IST were
    six in one Pacific day - 12,300 units against 10,000 - and the runs died
    at the last step with "video_insert needs 1600 units, 285 available",
    after a full render had been paid for.
    """

    def test_the_preflight_asks_whether_an_upload_would_fit(self):
        src = Path("engine/pipeline.py").read_text(encoding="utf-8")
        assert "quota.per_upload" in src
        assert "respect_reserve=False" in src, \
            "the upload reserve exists to protect THIS upload"
        assert "not enough YouTube quota left to publish" in src

    def test_the_quota_refusal_actually_blocks_the_run(self):
        """The blocking filter is substring-based, and the new message
        matched none of the original tokens - so it would have been reported
        and then ignored, rendering anyway."""
        src = Path("engine/pipeline.py").read_text(encoding="utf-8")
        assert 'blocking_tokens = ("ffmpeg", "limit", "YOUTUBE_API_KEY", "quota")' \
            in src
        assert "not enough YouTube quota left" in src
        # The message must contain a blocking token, or it is advisory only.
        assert "quota" in "not enough YouTube quota left to publish"

    def test_a_quota_probe_failure_cannot_stop_a_run(self):
        src = Path("engine/pipeline.py").read_text(encoding="utf-8")
        start = src.index("not enough YouTube quota left to publish")
        after = src[start:start + 700]
        assert "except Exception" in after, \
            "an unreadable quota ledger must not be the reason a run fails"


# ==========================================================================
class TestClearingFailedJobs:
    """Reported as "clear does not clear them". It does - but only the
    "Clear all now" button. "Older than 7 days" correctly keeps a failure
    from today, and then looks like it did nothing.
    """

    def test_failed_is_not_protected_from_clearing(self):
        src = Path("engine/core/db.py").read_text(encoding="utf-8")
        start = src.index("active = (JobStatus.IDEA.value")
        block = src[start:start + 500]
        assert "JobStatus.FAILED" not in block, \
            "a FAILED job is history and must be clearable"
        assert "JobStatus.READY.value" in block
        assert "JobStatus.SCHEDULED.value" in block

    def test_clear_all_sends_no_age_cutoff(self):
        src = Path("backend/api/main.py").read_text(encoding="utf-8")
        assert ("cutoff = (time.time() - body.older_than_days * 86400.0\n"
                "              if body.older_than_days else None)") in src, \
            "older_than_days=0 must mean no age filter at all"


# ==========================================================================
class TestUsageCanBeRederivedFromTheJobHistory:
    """The snapshot is not enough on its own.

    `bank_usage_snapshot` reads the live table, so it cannot help when that
    table is ALREADY wrong - which is exactly the state the nightly rebuild
    left behind. On the live server only 5 of 27 published scripts could be
    recovered by title, because the metadata stage rewrites titles.

    So the job now records `bank_entry_id` in its payload. The job history
    outlives both the live table and the run directory that `idea.json`
    lives in.
    """

    @pytest.fixture()
    def db(self, tmp_path):
        d = Database(tmp_path / "t.db")
        yield d
        d.close()

    def _job(self, db, entry_id: str, status: str) -> VideoJob:
        job = VideoJob(status=status, bank_entry_id=entry_id)
        db.save_job(job)
        return job

    def test_a_published_job_counts_as_used(self, db):
        self._job(db, "kids-hi-aaaaaaaaaa", JobStatus.PUBLISHED.value)
        spent = db.bank_usage_from_jobs()
        assert "kids-hi-aaaaaaaaaa" in spent

    def test_a_failed_job_does_not(self, db):
        """It released its claim on the way out, so the script is unused and
        must stay claimable - retiring it would lose a good script."""
        self._job(db, "kids-hi-bbbbbbbbbb", JobStatus.FAILED.value)
        self._job(db, "kids-hi-cccccccccc", JobStatus.REJECTED.value)
        assert db.bank_usage_from_jobs() == {}

    def test_a_scheduled_job_counts_as_used(self, db):
        """SCHEDULED means the upload already happened - it is published with
        a future visibility date, not pending."""
        self._job(db, "kids-hi-dddddddddd", JobStatus.SCHEDULED.value)
        assert "kids-hi-dddddddddd" in db.bank_usage_from_jobs()

    def test_a_job_with_no_entry_is_ignored(self, db):
        """Live-generated videos claim nothing."""
        self._job(db, "", JobStatus.PUBLISHED.value)
        assert db.bank_usage_from_jobs() == {}

    def test_the_pipeline_records_it_on_the_job(self):
        src = Path("engine/pipeline.py").read_text(encoding="utf-8")
        assert "job.bank_entry_id = claim.entry_id" in src

    def test_the_rebuild_uses_both_sources_on_both_paths(self):
        src = Path("scripts/bank_rebuild.py").read_text(encoding="utf-8")
        assert src.count("bank_usage_from_jobs()") >= 2,             "the --no-promote path the server takes needs it too"


# ==========================================================================
class TestARetryReRunsTheScriptThatFailed:
    """Reported as duplicates.

    A failed job releases its bank claim, so the retry used to claim whatever
    was next in the pool - and the released entry then went to the following
    scheduled run. The operator saw a retry produce a different video and the
    next run produce the one they had retried.
    """

    @pytest.fixture()
    def db(self, tmp_path):
        d = Database(tmp_path / "t.db")
        yield d
        d.close()

    def _entry(self, db, suffix: str, *, approved: bool = True) -> str:
        e = BankEntry.from_dict({
            "group": "kids", "language": "en",
            "topic": "kids alphabet learning", "shape": "drill",
            "video_format": "SHORT", "made_for_kids": True,
            "title": f"Say {suffix} with me",
            "scenes": [
                {"beat": "open", "narration": f"{suffix} is for apple here.",
                 "caption": "क", "image_brief": "an apple"},
                {"beat": "drill", "narration": f"{suffix} is for ball here.",
                 "caption": "ख", "image_brief": "a ball"},
                {"beat": "close", "narration": f"{suffix} is for cat here.",
                 "caption": "ग", "image_brief": "a cat"},
            ],
        })
        e.recompute()
        if approved:
            e.review = {"kind": "machine", "verdict": "approved",
                        "by": "test", "at": 1.0}
        db.save_bank_entry(e)
        return e.entry_id

    def _claim(self, db, **kw):
        from engine.content import bank_use
        kw.setdefault("require_review", False)
        return bank_use.claim(db, group="kids", language="en",
                              video_format="SHORT", job_id="job_r", **kw)

    def test_the_preferred_entry_is_the_one_claimed(self, db):
        first = self._entry(db, "A")
        wanted = self._entry(db, "Z")
        claim = self._claim(db, prefer_entry=wanted)
        assert claim is not None
        assert claim.entry_id == wanted,             "a retry must re-run its own script, not the next in the pool"
        assert first != wanted

    def test_an_unavailable_preference_falls_back(self, db):
        """A retry must never be refused because one entry went away."""
        spare = self._entry(db, "B")
        claim = self._claim(db, prefer_entry="kids-en-doesnotexist")
        assert claim is not None and claim.entry_id == spare

    def test_an_already_claimed_preference_falls_back(self, db):
        taken = self._entry(db, "C")
        spare = self._entry(db, "D")
        assert db.claim_specific_bank_entry(taken, "other_job") is not None
        claim = self._claim(db, prefer_entry=taken)
        assert claim is not None and claim.entry_id == spare

    def test_a_preference_cannot_bypass_the_review_gate(self, db):
        """The gates are the whole argument for a bank. A retry asking for a
        specific entry must not be a way round them."""
        ungated = self._entry(db, "E", approved=False)
        claim = self._claim(db, prefer_entry=ungated, require_review=True)
        assert claim is None or claim.entry_id != ungated

    def test_the_retry_endpoint_passes_the_failed_entry(self):
        src = Path("backend/api/main.py").read_text(encoding="utf-8")
        start = src.index('@app.post("/jobs/{job_id}/retry"')
        block = src[start:src.index("@app.post", start + 10)]
        assert "request.prefer_bank_entry = str(" in block
        assert 'getattr(job, "bank_entry_id", "")' in block

    def test_the_pipeline_forwards_it(self):
        src = Path("engine/pipeline.py").read_text(encoding="utf-8")
        assert 'prefer_entry=getattr(request, "prefer_bank_entry", "")' in src
