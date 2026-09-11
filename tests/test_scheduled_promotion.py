"""A scheduled upload that has gone live must stop reading as pending.

YouTube publishes on the slot it was given; the local record has to catch
up with it. That promotion existed, but only inside `collect_analytics` -
so on an install where nothing collected analytics, every scheduled upload
stayed SCHEDULED for ever. The dashboard's "Published" tile read 0
permanently while "Scheduled" counted videos YouTube had made public weeks
earlier.
"""
from __future__ import annotations

import time

import pytest

from engine.core.models import JobStatus, VideoJob
from engine.pipeline import Pipeline


@pytest.fixture()
def pipe(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("AUTOTUBE_DRY_RUN", "1")
    return Pipeline()


def _scheduled(pipe, job_id: str, offset_seconds: float) -> VideoJob:
    job = VideoJob(job_id=job_id)
    job.status = JobStatus.SCHEDULED.value
    job.scheduled_for = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset_seconds))
    pipe.db.save_job(job)
    return job


def test_a_slot_that_has_passed_is_published(pipe):
    _scheduled(pipe, "j_past", -3600)
    assert pipe.promote_scheduled() == 1
    assert pipe.db.get_job("j_past").status == JobStatus.PUBLISHED.value


def test_a_slot_still_to_come_is_left_alone(pipe):
    _scheduled(pipe, "j_future", +86400)
    assert pipe.promote_scheduled() == 0
    assert pipe.db.get_job("j_future").status == JobStatus.SCHEDULED.value


def test_a_scheduled_job_with_no_timestamp_is_left_alone(pipe):
    """Promoting on a missing timestamp would claim a video is live when
    nobody knows when, or whether, it goes out."""
    job = VideoJob(job_id="j_blank")
    job.status = JobStatus.SCHEDULED.value
    job.scheduled_for = ""
    pipe.db.save_job(job)
    assert pipe.promote_scheduled() == 0
    assert pipe.db.get_job("j_blank").status == JobStatus.SCHEDULED.value


def test_nothing_else_is_touched(pipe):
    """Only SCHEDULED is eligible. An AWAITING_APPROVAL video is waiting
    for a human and must not be promoted past them."""
    for status in (JobStatus.AWAITING_APPROVAL, JobStatus.READY,
                   JobStatus.FAILED):
        job = VideoJob(job_id=f"j_{status.value}")
        job.status = status.value
        job.scheduled_for = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                          time.gmtime(time.time() - 3600))
        pipe.db.save_job(job)

    assert pipe.promote_scheduled() == 0
    for status in (JobStatus.AWAITING_APPROVAL, JobStatus.READY,
                   JobStatus.FAILED):
        assert pipe.db.get_job(f"j_{status.value}").status == status.value


def test_it_is_reachable_without_collecting_analytics():
    """The whole defect: it was only callable through analytics.

    Structural, because reproducing "nobody ever collected analytics" as a
    test is reproducing an absence.
    """
    import inspect

    from backend.api import main

    source = inspect.getsource(main.Worker._janitor_loop)
    assert "promote_scheduled" in source, \
        "the janitor must catch the record up without analytics being run"


def test_the_janitor_survives_a_failing_promotion():
    """A janitor that can kill the process is worse than a stale count."""
    import inspect

    from backend.api import main

    source = inspect.getsource(main.Worker._janitor_loop)
    promote = source.split("promote_scheduled")[0]
    assert "try:" in promote.rsplit("while True:", 1)[-1]
