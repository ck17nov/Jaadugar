"""The claim lifecycle. Every test here is a defect that was real.

The bank's whole value is that a script was written and read once, and that
value is destroyed by any bug which consumes an entry without producing a
video - or worse, releases one after a video has published. These were found
by an adversarial audit of the implementation, and each reproduces.
"""
from __future__ import annotations

import time

import pytest

from engine.content import bank_import, bank_use
from engine.core.db import Database
from engine.core.models import JobStatus, VideoJob
from tests.test_bank import _write, kids_entry


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    yield database
    database.close()


def _unapproved(db, n: int, tmp_path) -> None:
    for i in range(n):
        entry = kids_entry(name=f"Kid{i}", refrain=f"Step {i}, and step again",
                           setting=f"place_{i}", domain=f"problem_{i}")
        entry.human = {}
        entry.recompute()
        db.save_bank_entry(entry)


# ---------------------------------------------------------------------------
def test_a_failed_claim_consumes_nothing(db, tmp_path):
    """The worst defect found. One render destroyed the whole pool.

    claim() used to mark a row used and THEN test it, moving on when the test
    failed - so every entry it declined stayed claimed. Three unapproved
    entries, one claim call, all three gone; and save_bank_entry preserves
    used state, so reviewing them afterwards could not bring them back.
    """
    _unapproved(db, 3, tmp_path)
    before = db.bank_counts()[0]["unused"]

    assert bank_use.claim(db, group="kids", language="en",
                          video_format="SHORT", job_id="j") is None
    assert db.bank_counts()[0]["unused"] == before


def test_reviewing_after_a_failed_claim_makes_it_claimable(db, tmp_path):
    """Which is what the CLI promises, and what used to be impossible."""
    _unapproved(db, 3, tmp_path)
    assert bank_use.claim(db, group="kids", language="en",
                          video_format="SHORT", job_id="j") is None

    first = db.bank_entries()[0]["entry_id"]
    bank_import.review(db, first, reviewer="chandan")
    got = bank_use.claim(db, group="kids", language="en",
                         video_format="SHORT", job_id="j2")
    assert got is not None and got.entry_id == first


def test_an_unparseable_row_does_not_stop_the_search(db, tmp_path):
    """It used to be claimed and the loop moved on, burning it."""
    db.save_bank_entry(kids_entry())
    db.execute(
        "INSERT INTO bank_entries(entry_id,grp,topic,shape,language,"
        "video_format,made_for_kids,title,content_hash,est_seconds,"
        "imported_at,used_at,used_job_id,payload) "
        "VALUES('broken','kids','','narrative','en','SHORT',1,'B','h',45,"
        "0.5,0,'','{not json')")
    got = bank_use.claim(db, group="kids", language="en",
                         video_format="SHORT", job_id="j")
    assert got is not None, "a broken row blocked a good one"
    assert got.entry_id != "broken"
    row = next(r for r in db.bank_entries() if r["entry_id"] == "broken")
    assert row["used_at"] == 0, "the broken row was consumed"


def test_only_one_entry_is_consumed_per_claim(db, tmp_path):
    db.save_bank_entry(kids_entry())
    db.save_bank_entry(kids_entry(name="Asha", refrain="Up and up, we go up",
                                  setting="library", domain="lost_item"))
    assert bank_use.claim(db, group="kids", language="en",
                          video_format="SHORT", job_id="j") is not None
    assert db.bank_counts()[0]["unused"] == 1


def test_require_human_does_not_burn_machine_approved_entries(db):
    entry = kids_entry()
    entry.human = {"reviewer": "claude", "verdict": "approve",
                   "kind": "machine"}
    db.save_bank_entry(entry)
    assert bank_use.claim(db, group="kids", language="en",
                          video_format="SHORT", job_id="j",
                          require_human=True) is None
    assert db.bank_counts()[0]["unused"] == 1


# ---------------------------------------------------------------------------
# Orphan reclaim
# ---------------------------------------------------------------------------
def _job(db, job_id: str, status: str, *, idle_hours: float = 0.0) -> None:
    """Record a job, optionally aged.

    The UPDATE is separate because `save_job` stamps `updated_at` with the
    current time itself - which is right for real use and means a test cannot
    age a job through it.
    """
    job = VideoJob(job_id=job_id)
    job.status = status
    db.save_job(job)
    if idle_hours:
        db.execute("UPDATE video_jobs SET updated_at=? WHERE job_id=?",
                   (time.time() - idle_hours * 3600, job_id))


def test_a_killed_render_is_reclaimed(db):
    """The case that happened: OOM-killed mid-encode, exit 137.

    The job froze at RENDERING and looked exactly like work in progress, so
    status alone could not tell it from a slow render. Only staleness can.
    """
    db.save_bank_entry(kids_entry())
    claim = bank_use.claim(db, group="kids", language="en",
                           video_format="SHORT", job_id="dead-job")
    assert claim is not None
    _job(db, "dead-job", JobStatus.RENDERING.value, idle_hours=5)

    orphans = db.orphaned_bank_entries()
    assert [o["entry_id"] for o in orphans] == [claim.entry_id]
    assert "RENDERING" in orphans[0]["reason"]


def test_a_slow_render_is_not_reclaimed(db):
    """Reclaiming from a render that is merely slow publishes it twice."""
    db.save_bank_entry(kids_entry())
    bank_use.claim(db, group="kids", language="en", video_format="SHORT",
                   job_id="busy-job")
    _job(db, "busy-job", JobStatus.RENDERING.value, idle_hours=0.2)
    assert db.orphaned_bank_entries() == []


def test_an_approved_video_is_never_reclaimed(db):
    """The entry is legitimately spent - the job is waiting on the user."""
    db.save_bank_entry(kids_entry())
    bank_use.claim(db, group="kids", language="en", video_format="SHORT",
                   job_id="waiting")
    _job(db, "waiting", JobStatus.AWAITING_APPROVAL.value, idle_hours=99)
    assert db.orphaned_bank_entries() == []


def test_a_published_video_is_never_reclaimed(db):
    db.save_bank_entry(kids_entry())
    bank_use.claim(db, group="kids", language="en", video_format="SHORT",
                   job_id="live")
    _job(db, "live", JobStatus.PUBLISHED.value, idle_hours=99)
    assert db.orphaned_bank_entries() == []


def test_a_failed_job_is_reclaimed_immediately(db):
    """No need to wait: it has already reached a terminal state."""
    db.save_bank_entry(kids_entry())
    bank_use.claim(db, group="kids", language="en", video_format="SHORT",
                   job_id="failed")
    _job(db, "failed", JobStatus.FAILED.value)
    assert len(db.orphaned_bank_entries()) == 1


def test_a_claim_with_no_job_record_is_reclaimed(db):
    """The job row is only written at stage boundaries."""
    db.save_bank_entry(kids_entry())
    bank_use.claim(db, group="kids", language="en", video_format="SHORT",
                   job_id="never-recorded")
    orphans = db.orphaned_bank_entries()
    assert len(orphans) == 1
    assert orphans[0]["reason"] == "job never recorded"
