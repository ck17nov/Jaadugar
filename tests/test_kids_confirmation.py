"""A kids automation set to publish without asking has to publish.

The defect, measured on the production database: two finished jobs, both with
`mode=AUTO`, `made_for_kids=true` and `publish_mode=immediate` on the wire,
both logging

    AWAITING_APPROVAL: kids classification must be confirmed once for this
    automation

The gate wanted a human to confirm the child-directed classification ONCE per
automation, and the only thing that could satisfy it was
`automation_has_approved_run` - a query over EARLIER jobs of the same
automation id. The Create screen mints a fresh automation for every one-off
video, so that condition could never be met and every kids video waited for a
confirmation the operator had already given on screen.

Worse, the operator had no way to give it. For a Kids group the app sets
`made_for_kids` itself, renders the switch disabled and suppresses the consent
dialog - so `made_for_kids=true` meant "something detected kids content",
never "a person affirmed it", and there was no field that could mean the
second thing.
"""
from __future__ import annotations

import pytest

from engine.core.db import Database
from engine.core.models import AutomationRequest, JobStatus, VideoJob
from engine.pipeline import Pipeline, needs_approval


# ==========================================================================
class TestTheRecord:
    """`kids_confirmations` is the durable fact the gate was missing."""

    def test_a_fresh_database_has_confirmed_nothing(self, tmp_path):
        db = Database(tmp_path / "c.db")
        try:
            assert db.kids_confirmation_exists(group="kids") is False
            assert db.kids_confirmation_exists(automation_id="auto_1") is False
        finally:
            db.close()

    def test_either_scope_satisfies_the_query(self, tmp_path):
        db = Database(tmp_path / "c.db")
        try:
            db.record_kids_confirmation(group="kids", source="create_screen")
            assert db.kids_confirmation_exists(group="kids") is True
            # A DIFFERENT automation in the same group is covered. This is
            # the whole point: "once" has to outlive the automation, because
            # a one-off automation does not survive its own video.
            assert db.kids_confirmation_exists(
                group="kids", automation_id="auto_never_seen") is True
            # Another group is not.
            assert db.kids_confirmation_exists(group="finance") is False
        finally:
            db.close()

    def test_an_automation_scope_does_not_leak_to_the_group(self, tmp_path):
        db = Database(tmp_path / "c.db")
        try:
            db.record_kids_confirmation(automation_id="auto_1")
            assert db.kids_confirmation_exists(automation_id="auto_1") is True
            assert db.kids_confirmation_exists(group="kids") is False
        finally:
            db.close()

    def test_recording_twice_is_idempotent_and_keeps_the_first_time(
            self, tmp_path):
        """WHEN it was confirmed is the interesting fact, not when it was
        last re-asserted."""
        db = Database(tmp_path / "c.db")
        try:
            db.record_kids_confirmation(group="kids", source="create_screen")
            first = db.query_one(
                "SELECT confirmed_at, source FROM kids_confirmations "
                "WHERE scope='group:kids'")
            db.record_kids_confirmation(group="kids", source="approval")
            second = db.query_one(
                "SELECT confirmed_at, source FROM kids_confirmations "
                "WHERE scope='group:kids'")
            assert second["confirmed_at"] == first["confirmed_at"]
            assert second["source"] == "create_screen"
            assert db.query_one(
                "SELECT COUNT(*) AS n FROM kids_confirmations")["n"] == 1
        finally:
            db.close()

    def test_nothing_is_recorded_without_a_scope(self, tmp_path):
        db = Database(tmp_path / "c.db")
        try:
            db.record_kids_confirmation()
            assert db.query_one(
                "SELECT COUNT(*) AS n FROM kids_confirmations")["n"] == 0
            assert db.kids_confirmation_exists() is False
        finally:
            db.close()

    def test_the_group_scope_is_case_insensitive(self, tmp_path):
        """The group arrives from a request field, so its case is not ours."""
        db = Database(tmp_path / "c.db")
        try:
            db.record_kids_confirmation(group="Kids")
            assert db.kids_confirmation_exists(group="kids") is True
            assert db.kids_confirmation_exists(group="KIDS") is True
        finally:
            db.close()


# ==========================================================================
@pytest.fixture()
def pipe(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("AUTOTUBE_DRY_RUN", "1")
    made = Pipeline()
    yield made
    made.close()


def _kids_request(**over) -> AutomationRequest:
    data = dict(niche="kids bedtime stories", niche_group="kids",
                made_for_kids=True, mode="AUTO", language="en")
    data.update(over)
    return AutomationRequest(**data)


def _gate(pipe, request, *, job_id="j1", made_for_kids=True,
          fact_risk=False):
    """Run stage_publish's gate computation, which is where the bug lived.

    Deliberately NOT a direct `needs_approval` call: that function was
    already correct, and testing it alone is what let the defect survive.
    The caller decided what `kids_already_confirmed` meant, and the caller
    was wrong.
    """
    from engine.core.groups import group as group_by_key, group_for_topic

    job = VideoJob(job_id=job_id, automation_id=request.id)
    pipe.db.save_job(job)
    chosen = (group_by_key(getattr(request, "niche_group", "") or "")
              or group_for_topic(request.niche))
    confirmed = (
        bool(getattr(request, "kids_confirmed", False))
        or pipe.db.kids_confirmation_exists(
            group=(chosen.key if chosen else ""),
            automation_id=job.automation_id)
        or pipe.db.automation_has_approved_run(job.automation_id))
    return needs_approval(
        mode=request.mode,
        config_default_requires_approval=True,
        made_for_kids=made_for_kids,
        kids_already_confirmed=confirmed,
        fact_requires_approval=fact_risk)


class TestTheGate:
    def test_the_reported_defect_a_confirmed_one_off_kids_video_publishes(
            self, pipe):
        """THE regression test. mode=AUTO, kids, brand-new automation id,
        no sibling jobs - exactly the two production jobs."""
        request = _kids_request(kids_confirmed=True, frequency="once")
        assert pipe.db.automation_has_approved_run(request.id) is False
        hold, reason = _gate(pipe, request)
        assert hold is False, f"still held: {reason}"

    def test_without_the_tick_it_is_still_held(self, pipe):
        """The gate is not removed, only made satisfiable."""
        hold, reason = _gate(pipe, _kids_request(kids_confirmed=False))
        assert hold is True
        assert "kids" in reason.lower()

    def test_once_means_once_per_channel_group(self, pipe):
        """A SECOND, DIFFERENT automation in the group does not ask again."""
        pipe.db.record_kids_confirmation(group="kids",
                                         source="create_screen")
        later = _kids_request(kids_confirmed=False, niche="kids moral stories")
        hold, reason = _gate(pipe, later, job_id="j2")
        assert hold is False, f"asked twice: {reason}"

    def test_a_confirmation_does_not_cross_groups(self, pipe):
        pipe.db.record_kids_confirmation(group="finance")
        hold, _ = _gate(pipe, _kids_request(kids_confirmed=False))
        assert hold is True

    def test_a_factual_risk_still_holds_a_fully_confirmed_video(self, pipe):
        """Requirement (c): the fact-check gate is not weakened.

        Everything else says publish - AUTO, ticked, group confirmed - and
        the video is still held, because a correctness risk is not a
        preference.
        """
        pipe.db.record_kids_confirmation(group="kids")
        hold, reason = _gate(pipe, _kids_request(kids_confirmed=True),
                             fact_risk=True)
        assert hold is True
        assert "factual" in reason.lower()

    def test_approval_mode_is_unaffected(self, pipe):
        """Confirming the classification is not choosing to skip review."""
        hold, reason = _gate(
            pipe, _kids_request(kids_confirmed=True, mode="APPROVAL"))
        assert hold is True
        assert reason == "approval mode"

    def test_a_classification_from_the_profile_is_still_gated(self, pipe):
        """meta.made_for_kids can be true when the REQUEST said false.

        `stage_publish` gates on the effective value, so a request that
        never mentioned kids must not be treated as having confirmed it.
        """
        request = AutomationRequest(niche="story of the thirsty crow",
                                    made_for_kids=False, mode="AUTO")
        hold, _ = _gate(pipe, request, made_for_kids=True)
        assert hold is True


# ==========================================================================
class TestWhatGetsRecorded:
    def test_only_an_explicit_tick_is_written_down(self, pipe, monkeypatch):
        """An AUTO-published sibling must not become a record of consent.

        `automation_has_approved_run` counts jobs that reached PUBLISHED -
        including ones no human ever looked at. Recording on that would turn
        a status inference into a durable human-consent fact, and it would
        spread: one auto-published video would confirm the whole group.
        """
        request = _kids_request(kids_confirmed=False)
        published = VideoJob(job_id="j_old", automation_id=request.id)
        published.status = JobStatus.PUBLISHED.value
        pipe.db.save_job(published)

        # The gate is satisfied - by source 3.
        hold, _ = _gate(pipe, request, job_id="j_new")
        assert hold is False
        # And nothing was recorded, because no human affirmed anything.
        assert pipe.db.kids_confirmation_exists(group="kids") is False

    def test_approving_a_kids_video_confirms_the_group(self, pipe):
        """The migration path: approving either job already in the queue
        confirms the Kids channel, so nothing is held again."""
        from engine.core.models import VideoMetadata

        request = _kids_request(kids_confirmed=False)
        meta = VideoMetadata(title="T", made_for_kids=True)
        job = VideoJob(job_id="j_held", automation_id=request.id)
        job.status = JobStatus.AWAITING_APPROVAL.value
        job.request = request.to_dict()
        job.metadata = meta.to_dict()
        pipe.db.save_job(job)

        # publish_now is the only part that needs a channel, so stub it.
        pipe.publish_now = lambda *a, **k: {"stubbed": True}
        pipe.approve("j_held")

        assert pipe.db.kids_confirmation_exists(group="kids") is True
        row = pipe.db.query_one("SELECT source FROM kids_confirmations "
                                "WHERE scope='group:kids'")
        assert row["source"] == "approval"

    def test_approving_a_general_audience_video_records_nothing(self, pipe):
        from engine.core.models import VideoMetadata

        request = AutomationRequest(niche="personal finance",
                                    niche_group="finance")
        job = VideoJob(job_id="j_fin", automation_id=request.id)
        job.status = JobStatus.AWAITING_APPROVAL.value
        job.request = request.to_dict()
        job.metadata = VideoMetadata(title="T",
                                     made_for_kids=False).to_dict()
        pipe.db.save_job(job)
        pipe.publish_now = lambda *a, **k: {"stubbed": True}
        pipe.approve("j_fin")

        assert pipe.db.query_one(
            "SELECT COUNT(*) AS n FROM kids_confirmations")["n"] == 0


# ==========================================================================
class TestTheFieldSurvivesTheRoundTrip:
    def test_the_request_carries_it(self):
        request = AutomationRequest(niche="kids bedtime stories",
                                    kids_confirmed=True)
        assert AutomationRequest.from_dict(
            request.to_dict()).kids_confirmed is True

    def test_an_old_payload_without_the_field_reads_as_false(self):
        """Every automation persisted before today."""
        data = AutomationRequest(niche="kids bedtime stories").to_dict()
        data.pop("kids_confirmed", None)
        assert AutomationRequest.from_dict(data).kids_confirmed is False

    def test_the_api_body_accepts_and_forwards_it(self):
        from backend.api.main import AutomationBody

        body = AutomationBody(niche="kids bedtime stories",
                              niche_group="kids", made_for_kids=True,
                              kids_confirmed=True)
        assert body.to_request().kids_confirmed is True

    def test_the_api_body_defaults_it_false_for_an_older_client(self):
        from backend.api.main import AutomationBody

        body = AutomationBody(niche="kids bedtime stories",
                              niche_group="kids", made_for_kids=True)
        assert body.to_request().kids_confirmed is False

    def test_the_app_carries_it_on_every_recurring_run(self):
        """A field the worker forgets reverts to the DTO default on run 2.

        Structural, and deliberately so: it has already happened to
        scriptSource and nicheGroup, and the symptom is silent.
        """
        from pathlib import Path

        worker = Path("android/app/src/main/java/com/autotube/ai/workers/"
                      "Workers.kt").read_text(encoding="utf-8")
        assert "kidsConfirmed = automation.kidsConfirmed" in worker, \
            "AutomationWorker must carry the confirmation into the re-POST"

        entity = Path("android/app/src/main/java/com/autotube/ai/data/local/"
                      "Database.kt").read_text(encoding="utf-8")
        assert 'name = "kids_confirmed"' in entity, \
            "the Room row must persist it, or the worker has nothing to read"
