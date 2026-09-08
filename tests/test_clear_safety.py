"""Clearing history must not delete a video that has not been sent.

Reproduction from the deployed server's own log, Sep 07 22:12:13: ten
"[STORAGE] reclaimed job media ... removed=video.mp4,..." lines freeing 987 MB,
immediately followed by "[API] jobs cleared count=10". Approving a video moves
it to READY - rendered, approved, upload still to happen - and READY was
treated as clearable history, so the upload had nothing left to send. That
presents as "publish does nothing", and as a published video with no
thumbnail.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from engine.core.db import Database
from engine.core.models import JobStatus, VideoJob
from engine.core.storage import NEVER_RECLAIM, RECLAIM_ON_SIGHT, may_reclaim

# Everything that means "the work is over and nobody is waiting for it".
FINISHED = ("PUBLISHED", "FAILED", "REJECTED", "CANCELLED")
# Everything that means "something still needs this".
IN_FLIGHT = ("IDEA", "RESEARCH", "SCRIPT", "VOICE", "VISUALS", "RENDERING",
             "QUALITY_CHECK", "AWAITING_APPROVAL", "READY", "SCHEDULED")


def _db_with(statuses):
    db = Database(str(Path(tempfile.mkdtemp()) / "t.db"))
    for status in statuses:
        db.save_job(VideoJob(job_id=f"job_{status.lower()}", status=status,
                             dir=str(Path(tempfile.mkdtemp()))))
    return db


class TestClearKeepsWhatIsStillNeeded:
    def test_ready_is_never_cleared(self):
        """THE bug. READY is approved-and-rendered with the upload still to
        happen, so clearing it destroys the file the upload would send."""
        db = _db_with(["READY"])
        assert db.delete_jobs(keep_active=True) == []
        assert [j.status for j in db.list_jobs()] == ["READY"]

    def test_scheduled_is_never_cleared(self):
        """Already uploaded with a publishAt. Its media is spare, but the row
        is the only record that something is about to go live."""
        db = _db_with(["SCHEDULED"])
        assert db.delete_jobs(keep_active=True) == []

    def test_awaiting_approval_is_never_cleared(self):
        db = _db_with(["AWAITING_APPROVAL"])
        assert db.delete_jobs(keep_active=True) == []

    def test_every_in_flight_state_survives(self):
        db = _db_with(list(IN_FLIGHT))
        assert db.delete_jobs(keep_active=True) == []
        assert len(db.list_jobs(limit=50)) == len(IN_FLIGHT)

    def test_finished_states_are_still_cleared(self):
        """The feature has to keep working - this is the whole point of the
        button."""
        db = _db_with(list(FINISHED))
        removed = {j.status for j in db.delete_jobs(keep_active=True)}
        assert removed == set(FINISHED)
        assert db.list_jobs(limit=50) == []

    def test_an_explicit_id_does_not_override_the_guard(self):
        """Asking for a specific job by id must not bypass the protection -
        the dashboard sends ids, so this is the actual code path."""
        db = _db_with(["READY", "PUBLISHED"])
        removed = [j.status for j in db.delete_jobs(
            job_ids=["job_ready", "job_published"], keep_active=True)]
        assert removed == ["PUBLISHED"]

    def test_keep_active_false_still_clears_everything(self):
        """The flag has to mean something; only the API's default is safe."""
        db = _db_with(["READY", "AWAITING_APPROVAL"])
        assert len(db.delete_jobs(keep_active=False)) == 2


class TestMediaReclaimPolicy:
    def test_ready_media_is_never_reclaimed(self):
        assert JobStatus.READY.value in NEVER_RECLAIM
        assert may_reclaim("READY") is False
        # ...not even when it is ancient. The age sweep was the other route in.
        assert may_reclaim("READY", age_days=999.0, after_days=7.0) is False

    def test_approval_media_is_never_reclaimed(self):
        assert may_reclaim("AWAITING_APPROVAL", age_days=999.0,
                           after_days=7.0) is False

    def test_uploaded_media_is_still_freed_on_sight(self):
        """Disk reclaim has to keep working for the states where the media
        really is spare, or a free-tier box fills up."""
        for status in ("PUBLISHED", "SCHEDULED", "REJECTED", "CANCELLED",
                       "FAILED"):
            assert status in RECLAIM_ON_SIGHT, status
            assert may_reclaim(status) is True, status

    def test_the_two_policies_do_not_contradict(self):
        assert not (NEVER_RECLAIM & RECLAIM_ON_SIGHT)


@pytest.mark.parametrize("status", IN_FLIGHT)
def test_no_in_flight_state_is_reclaim_on_sight(status):
    assert status not in RECLAIM_ON_SIGHT or status in ("SCHEDULED",), status
