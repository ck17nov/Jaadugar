"""Two settings that did not do what they said.

1. "Auto - publish without asking" waited for manual approval anyway, because
   the config default was OR-ed in unconditionally and acted as a floor. The
   UI offered a choice it could not honour.
2. A "just once" automation stayed in the Schedule tab after its video
   published, offering a "Stop automation" button with nothing left to stop.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from engine.core.db import Database
from engine.core.models import AutomationRequest, VideoJob
from engine.pipeline import needs_approval


def _base(**over):
    kw = dict(mode="APPROVAL", config_default_requires_approval=True,
              made_for_kids=False, kids_already_confirmed=False,
              fact_requires_approval=False)
    kw.update(over)
    return kw


class TestAutoActuallyPublishes:
    def test_auto_beats_the_config_default(self):
        """THE bug. The config was a floor, so no request could escape it."""
        hold, _why = needs_approval(**_base(mode="AUTO"))
        assert hold is False

    def test_approval_mode_still_holds(self):
        hold, why = needs_approval(**_base(mode="APPROVAL"))
        assert hold is True
        assert why == "approval mode"

    def test_mode_is_case_insensitive(self):
        assert needs_approval(**_base(mode="auto"))[0] is False

    def test_with_the_config_default_off_everything_publishes(self):
        hold, _ = needs_approval(**_base(mode="APPROVAL",
                                         config_default_requires_approval=False))
        assert hold is False


class TestKidsConfirmationIsOncePerAutomation:
    def test_the_first_kids_video_is_held(self):
        """The Made-for-Kids CLASSIFICATION needs a human to confirm it."""
        hold, why = needs_approval(**_base(mode="AUTO", made_for_kids=True))
        assert hold is True
        assert "once for this automation" in why

    def test_later_kids_videos_publish(self):
        """It was unconditional, so a kids automation could NEVER auto-publish
        - and kids is the main channel here."""
        hold, _ = needs_approval(**_base(mode="AUTO", made_for_kids=True,
                                         kids_already_confirmed=True))
        assert hold is False

    def test_approval_mode_still_holds_confirmed_kids(self):
        hold, _ = needs_approval(**_base(mode="APPROVAL", made_for_kids=True,
                                         kids_already_confirmed=True))
        assert hold is True


class TestFactRiskAlwaysHolds:
    @pytest.mark.parametrize("mode", ["AUTO", "APPROVAL"])
    def test_a_flagged_script_is_never_auto_published(self, mode):
        """A correctness risk, not a preference - so it outranks the mode."""
        hold, why = needs_approval(**_base(mode=mode,
                                           fact_requires_approval=True))
        assert hold is True
        assert why == "factual risk needs review"

    def test_it_outranks_the_kids_reason_too(self):
        _hold, why = needs_approval(**_base(mode="AUTO", made_for_kids=True,
                                            fact_requires_approval=True))
        assert why == "factual risk needs review"


class TestOnceAutomationsFinish:
    def _db(self):
        return Database(str(Path(tempfile.mkdtemp()) / "t.db"))

    def test_a_published_run_counts(self):
        db = self._db()
        request = AutomationRequest(niche="kids bedtime stories",
                                    frequency="once")
        db.save_automation(request)
        assert db.automation_finished_runs(request.id) == 0
        db.save_job(VideoJob(job_id="j1", automation_id=request.id,
                             status="PUBLISHED"))
        assert db.automation_finished_runs(request.id) == 1

    def test_a_scheduled_run_counts(self):
        db = self._db()
        db.save_job(VideoJob(job_id="j1", automation_id="a1",
                             status="SCHEDULED"))
        assert db.automation_finished_runs("a1") == 1

    @pytest.mark.parametrize("status", ["AWAITING_APPROVAL", "READY",
                                        "RENDERING", "FAILED"])
    def test_an_unfinished_run_does_not_count(self, status):
        """A once-automation holding a video that still needs the user is NOT
        finished and must stay visible."""
        db = self._db()
        db.save_job(VideoJob(job_id="j1", automation_id="a1", status=status))
        assert db.automation_finished_runs("a1") == 0

    def test_an_unknown_automation_has_no_runs(self):
        assert self._db().automation_finished_runs("nope") == 0
        assert self._db().automation_finished_runs("") == 0

    def test_the_endpoint_hides_completed_once_automations(self):
        """Only "once" can finish; daily is live until cancelled."""
        import inspect

        from backend.api import main as api
        source = inspect.getsource(api.list_automations)
        assert 'frequency", "once")).lower() == "once"' in source
        assert "include_completed" in source
        assert "continue" in source
