"""The day boundary, and re-running a failed job.

Both came from one real failure on 16 September 2026: an automation refused
with "daily video limit reached" while the quota endpoint reported 0/10000,
and there was no way to run the failed job again.

The cause was measured, not guessed. Four videos reached PUBLISHED at
2026-09-15 20:32 IST with the limit at 4, and the limit was enforced against
`time.time() - 86400` - so every automation from then until 20:32 the
following day was refused, a full calendar day later, while the quota ledger
(which buckets by Pacific day, because that is when Google resets) had long
since rolled over.
"""
from __future__ import annotations

import datetime
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from engine.core.util import local_day_start, pacific_day


# ==========================================================================
class TestTodayMeansToday:

    def test_midnight_is_midnight_in_the_named_zone(self):
        for tz_name in ("Asia/Kolkata", "America/Los_Angeles", "UTC",
                        "Europe/London"):
            tz = ZoneInfo(tz_name)
            start = local_day_start(tz_name)
            as_local = datetime.datetime.fromtimestamp(start, tz)
            assert (as_local.hour, as_local.minute, as_local.second) == (0, 0, 0), \
                f"{tz_name} midnight came back as {as_local}"

    def test_it_is_todays_midnight_not_yesterdays(self):
        tz = ZoneInfo("Asia/Kolkata")
        start = local_day_start("Asia/Kolkata")
        assert datetime.datetime.fromtimestamp(start, tz).date() == \
            datetime.datetime.now(tz).date()
        assert start <= time.time(), "midnight cannot be in the future"

    def test_it_is_not_a_rolling_window(self):
        """The whole point. A rolling window is always exactly 24h wide; a
        calendar boundary is however far into the day we happen to be."""
        start = local_day_start("Asia/Kolkata")
        elapsed = time.time() - start
        assert 0 <= elapsed < 86400 + 3600, elapsed          # +1h for DST slack
        rolling = time.time() - 86400
        # They coincide only in the single second after midnight.
        if elapsed > 120:
            assert start > rolling, (
                "the calendar boundary must be LATER than a rolling 24h "
                "window once the day is under way - that difference is the "
                "bug this fixes")

    def test_an_unknown_zone_falls_back_instead_of_raising(self):
        """A typo in config must not take the pipeline down at preflight."""
        assert local_day_start("Not/ARealZone") > 0

    def test_the_quota_ledger_still_uses_pacific(self):
        """These are different questions and must stay different. Quota
        resets at midnight Pacific because that is Google's boundary; the
        operator's daily cap is about the operator's calendar."""
        assert len(pacific_day()) == 10 and pacific_day().count("-") == 2

    def test_the_preflight_uses_the_calendar_helper(self):
        src = Path("engine/pipeline.py").read_text(encoding="utf-8")
        assert "local_day_start(tz_name)" in src, \
            "the daily limit must be measured from the operator's midnight"
        assert "since = time.time() - 86400" not in src, \
            "the rolling 24-hour window is back"

    def test_the_refusal_message_says_when_it_resets(self):
        """The original message named neither the window nor the reset, so
        "daily video limit reached" next to "quota 0/10000" was unexplainable
        from the outside."""
        src = Path("engine/pipeline.py").read_text(encoding="utf-8")
        assert "resets at midnight" in src


# ==========================================================================
class TestRetryingAFailedJob:

    def test_the_endpoint_exists_and_is_authenticated(self):
        src = Path("backend/api/main.py").read_text(encoding="utf-8")
        assert '@app.post("/jobs/{job_id}/retry"' in src
        block = src[src.index('@app.post("/jobs/{job_id}/retry"'):]
        assert "require_api_key" in block[:200], "retry must need the API key"

    def test_only_failed_or_rejected_can_be_retried(self):
        """A PUBLISHED job must never be retried - that is a second upload of
        the same video, and there is no undo for it."""
        src = Path("backend/api/main.py").read_text(encoding="utf-8")
        start = src.index('@app.post("/jobs/{job_id}/retry"')
        block = src[start:src.index("@app.post", start + 10)]
        assert "JobStatus.FAILED.value" in block
        assert "JobStatus.REJECTED.value" in block
        assert "not_retryable" in block
        assert "JobStatus.PUBLISHED" not in block

    def test_a_retry_does_not_rearm_the_schedule(self):
        """Carrying the original frequency would add a second daily trigger
        on top of the one the phone already owns."""
        for path in ("backend/api/main.py", "backend/cli.py"):
            src = Path(path).read_text(encoding="utf-8")
            start = src.index("retry")
            assert 'frequency = "once"' in src, f"{path} re-arms the schedule"

    def test_the_failed_job_is_kept_not_rewritten(self):
        """The failure carries the error that explains it. Resetting the row
        would erase the evidence while a retry failed for the same reason."""
        src = Path("backend/api/main.py").read_text(encoding="utf-8")
        start = src.index('@app.post("/jobs/{job_id}/retry"')
        block = src[start:src.index("@app.post", start + 10)]
        assert "previous_error" in block
        assert "save_job" not in block and "update_job" not in block

    def test_the_cli_command_exists(self):
        src = Path("backend/cli.py").read_text(encoding="utf-8")
        assert "def retry(" in src
        assert "--force" in src[src.index("def retry("):src.index("def retry(") + 900]

    def test_the_android_layers_are_all_wired(self):
        """A button with no repository method behind it is worse than no
        button. Check every layer.

        LIMIT OF THIS TEST, stated because it bit once: these are string
        checks, so they passed on Kotlin that did not compile. The repository
        referenced RetryAckDto without importing it and only
        `gradlew assembleDebug` caught it. Treat a green suite here as
        necessary and not sufficient for the Android side.
        """
        base = Path("android/app/src/main/java/com/autotube/ai")
        checks = {
            "data/remote/ApiService.kt": 'jobs/{jobId}/retry',
            "data/remote/Dtos.kt": "RetryAckDto",
            "data/repo/AutoTubeRepository.kt": "suspend fun retryJob",
            "ui/vm/ViewModels.kt": "fun retry(jobId: String)",
            "ui/screens/DashboardScreen.kt": "RETRYABLE",
        }
        for rel, needle in checks.items():
            text = (base / rel).read_text(encoding="utf-8")
            assert needle in text, f"{rel} is missing {needle!r}"

    def test_the_retry_button_is_not_offered_for_published_jobs(self):
        src = Path("android/app/src/main/java/com/autotube/ai/ui/screens/"
                   "DashboardScreen.kt").read_text(encoding="utf-8")
        start = src.index("private val RETRYABLE")
        block = src[start:start + 200]
        assert "FAILED" in block and "REJECTED" in block
        assert "PUBLISHED" not in block and "SCHEDULED" not in block
