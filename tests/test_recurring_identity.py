"""A recurring automation must stay ONE automation.

Every Start minted a fresh `auto_...` id, including the runs WorkManager
fires on a schedule, so a daily automation became a new automation every
day. Three things broke on that, and only the third was reported:

1. The Schedule tab listed one row per RUN rather than one per schedule.
2. Stop cancelled a row that would never fire again, while the real
   schedule carried on producing.
3. The Made-for-Kids confirmation - deliberately required ONCE per
   automation - was required every single time. So a kids automation set to
   "publish without asking" never published without asking, which is how it
   was reported: "while creating automation i selected to publish
   immediately and in settings also it is set to auto but still it didn't
   publish automatically and is waiting for approval."
"""
from __future__ import annotations

import os

import pytest

TEST_TOKEN = "test-token-do-not-use-in-production-0123456789"
os.environ.setdefault("AUTOTUBE_API_TOKEN", TEST_TOKEN)
os.environ.setdefault("DRY_RUN", "true")

from backend.api.main import AutomationBody               # noqa: E402
from engine.core.db import Database                       # noqa: E402
from engine.core.models import JobStatus, Mode, VideoJob  # noqa: E402
from engine.pipeline import needs_approval                # noqa: E402


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    yield database
    database.close()


# ---------------------------------------------------------------------------
# The id survives the wire
# ---------------------------------------------------------------------------
def test_a_run_can_declare_the_automation_it_belongs_to():
    body = AutomationBody(niche="kids bedtime stories", id="auto_daily_kids")
    assert body.to_request().id == "auto_daily_kids"


def test_no_id_still_means_a_new_automation():
    """The Create screen sends nothing, and must keep getting a fresh id."""
    first = AutomationBody(niche="kids bedtime stories").to_request()
    second = AutomationBody(niche="kids bedtime stories").to_request()
    assert first.id and second.id
    assert first.id != second.id
    assert first.id.startswith("auto_")


def test_a_blank_id_is_not_written_through_as_empty():
    """An empty string would collide every automation onto one row."""
    assert AutomationBody(niche="science", id="").to_request().id != ""
    assert AutomationBody(niche="science", id="   ").to_request().id.strip()


def test_reusing_the_id_updates_rather_than_duplicates(db):
    request = AutomationBody(niche="kids bedtime stories",
                             id="auto_daily_kids", frequency="daily").to_request()
    db.save_automation(request)
    again = AutomationBody(niche="kids moral stories",
                           id="auto_daily_kids", frequency="daily").to_request()
    db.save_automation(again)

    rows = db.list_automations(include_cancelled=True, limit=50)
    mine = [r for r in rows if r["id"] == "auto_daily_kids"]
    assert len(mine) == 1, "a recurring run created a second automation"
    assert mine[0]["niche"] == "kids moral stories"


# ---------------------------------------------------------------------------
# What that unlocks: the kids confirmation actually being once
# ---------------------------------------------------------------------------
def test_the_first_kids_video_is_held_for_confirmation():
    """This part is deliberate and stays: somebody confirms the
    classification once, because publishing child-directed content
    unflagged is not recoverable by editing it afterwards."""
    hold, reason = needs_approval(
        mode=Mode.AUTO.value, config_default_requires_approval=False,
        made_for_kids=True, kids_already_confirmed=False,
        fact_requires_approval=False)
    assert hold is True
    assert "confirmed once" in reason


def test_the_second_one_publishes_by_itself():
    hold, reason = needs_approval(
        mode=Mode.AUTO.value, config_default_requires_approval=False,
        made_for_kids=True, kids_already_confirmed=True,
        fact_requires_approval=False)
    assert hold is False, reason


def test_an_approved_run_confirms_the_automation_for_the_next_one(db):
    """The join that makes "once" mean once - and it only works when the
    recurring run reuses the id."""
    automation_id = "auto_daily_kids"
    assert db.automation_has_approved_run(automation_id) is False

    published = VideoJob(job_id="j1", automation_id=automation_id)
    published.status = JobStatus.PUBLISHED.value
    db.save_job(published)

    assert db.automation_has_approved_run(automation_id) is True
    # And a run that came in under a DIFFERENT id learns nothing from it,
    # which is exactly what was happening every day.
    assert db.automation_has_approved_run("auto_a_new_one_each_time") is False


def test_a_factual_risk_is_never_auto_published():
    """Unchanged, and must stay that way: correctness is not a preference."""
    hold, reason = needs_approval(
        mode=Mode.AUTO.value, config_default_requires_approval=False,
        made_for_kids=False, kids_already_confirmed=True,
        fact_requires_approval=True)
    assert hold is True
    assert "factual" in reason
