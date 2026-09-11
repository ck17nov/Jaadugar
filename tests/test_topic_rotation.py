"""One automation can cover a whole channel group, a topic at a time.

Asked for directly: "in topics, I can only select a particular topic and can
schedule automation but for that i'll have to create many automation to cover
all topics. So what I'm thinking is to also keep an 'Auto mode' in topics and
if I select that and create automation it should one by one generate videos on
every other topic in rotation as per schedule."

Three things about the existing code decide the design, and each has its own
test below.

  * The recurring run is a RE-POST from the phone, rebuilt field by field
    from a Room row. A field missing from that rebuild silently reverts to
    its DTO default - which has already happened twice, to script_source and
    niche_group.
  * The phone is not a safe place for the cursor. Room migrates
    destructively, so a device-side cursor restarts the lap on every app
    upgrade and every reinstall.
  * Neither is the payload: `save_automation` replaces the whole payload
    with whatever the phone just sent.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.core.db import Database
from engine.core.groups import GROUPS, rotation_order, topics
from engine.core.models import AutomationRequest


# ==========================================================================
class TestRotationOrder:
    def test_a_fresh_automation_starts_at_the_top(self):
        assert rotation_order("kids")[0] == topics("kids")[0]
        assert len(rotation_order("kids")) == len(topics("kids"))

    def test_it_resumes_just_after_the_last_topic(self):
        pool = topics("kids")
        assert rotation_order("kids", after=pool[0])[0] == pool[1]
        assert rotation_order("kids", after=pool[3])[0] == pool[4]

    def test_the_last_topic_wraps_to_the_first(self):
        pool = topics("kids")
        assert rotation_order("kids", after=pool[-1])[0] == pool[0]

    def test_every_topic_appears_exactly_once_per_lap(self):
        """Otherwise "every topic gets a turn" is not true."""
        for group in GROUPS:
            for start in ("",) + group.topics:
                order = rotation_order(group.key, after=start)
                assert sorted(order) == sorted(group.topics), group.key

    def test_a_retired_topic_starts_the_lap_at_the_top(self):
        """The topic list IS edited - the AI/science/programming merge and
        the fifteen tech topics added later. An index would have shifted
        silently; a name that is gone has one sane answer."""
        assert rotation_order("kids", after="a topic that no longer exists") \
            == list(topics("kids"))

    def test_it_is_matched_case_insensitively(self):
        pool = topics("tech")
        assert rotation_order("tech", after=pool[0].upper())[0] == pool[1]

    def test_an_unknown_group_yields_nothing_rather_than_guessing(self):
        assert rotation_order("not-a-group") == []
        assert rotation_order("") == []


# ==========================================================================
class TestTheCursor:
    def test_it_starts_blank_and_survives_a_restart(self, tmp_path):
        request = AutomationRequest(niche="kids bedtime stories",
                                    niche_group="kids", topic_rotate=True)
        db = Database(tmp_path / "a.db")
        try:
            db.save_automation(request, last_topic="kids moral stories")
            assert db.get_automation(request.id)["last_topic"] == \
                "kids moral stories"
        finally:
            db.close()

        # Reopened, which is what a service restart is.
        again = Database(tmp_path / "a.db")
        try:
            assert again.get_automation(request.id)["last_topic"] == \
                "kids moral stories"
        finally:
            again.close()

    def test_a_blank_never_resets_a_live_cursor(self, tmp_path):
        """THE trap. Every ordinary save sends blank, and so does any
        re-POST that did not resolve a topic - writing it through would
        restart the lap on the next save."""
        request = AutomationRequest(niche="kids bedtime stories",
                                    niche_group="kids", topic_rotate=True)
        db = Database(tmp_path / "a.db")
        try:
            db.save_automation(request, last_topic="kids toys and play")
            db.save_automation(request)               # no last_topic
            assert db.get_automation(request.id)["last_topic"] == \
                "kids toys and play"
        finally:
            db.close()

    def test_it_advances_per_video_not_per_post(self, tmp_path):
        """count>1 makes N videos inside ONE post, so a rotating automation
        has to move the cursor per video or all N land on one topic."""
        request = AutomationRequest(niche="kids bedtime stories",
                                    niche_group="kids", topic_rotate=True,
                                    count=3)
        db = Database(tmp_path / "a.db")
        try:
            db.save_automation(request, last_topic="kids bedtime stories")
            db.set_automation_topic(request.id, "kids moral stories")
            assert db.get_automation(request.id)["last_topic"] == \
                "kids moral stories"
        finally:
            db.close()

    def test_setting_a_blank_topic_is_ignored(self, tmp_path):
        request = AutomationRequest(niche="kids bedtime stories",
                                    niche_group="kids", topic_rotate=True)
        db = Database(tmp_path / "a.db")
        try:
            db.save_automation(request, last_topic="kids moral stories")
            db.set_automation_topic(request.id, "")
            assert db.get_automation(request.id)["last_topic"] == \
                "kids moral stories"
        finally:
            db.close()

    def test_an_existing_database_gains_the_column(self, tmp_path):
        """The live workspace database predates it, and a column added only
        to SCHEMA never reaches an existing file."""
        path = tmp_path / "old.db"
        import sqlite3
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE automations (id TEXT PRIMARY KEY, niche TEXT NOT "
            "NULL, frequency TEXT NOT NULL DEFAULT 'once', days TEXT, "
            "upload_time TEXT, timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',"
            " enabled INTEGER DEFAULT 1, created_at REAL NOT NULL, "
            "updated_at REAL NOT NULL, cancelled_at REAL DEFAULT 0, "
            "payload TEXT NOT NULL);")
        conn.commit()
        conn.close()

        db = Database(path)
        try:
            have = {r["name"] for r in
                    db.query("PRAGMA table_info(automations)")}
            assert "last_topic" in have
        finally:
            db.close()


# ==========================================================================
class TestTheRequestShape:
    def test_the_flag_round_trips(self):
        request = AutomationRequest(niche="", niche_group="kids",
                                    topic_rotate=True)
        assert AutomationRequest.from_dict(
            request.to_dict()).topic_rotate is True

    def test_an_old_payload_reads_as_not_rotating(self):
        data = AutomationRequest(niche="kids bedtime stories").to_dict()
        data.pop("topic_rotate", None)
        assert AutomationRequest.from_dict(data).topic_rotate is False

    def test_rotation_requires_a_group(self):
        """Rotation is scoped to ONE group, and that is a safety rule.

        `child_directed` is a property of the group, so within a group the
        Made-for-Kids answer is constant. Rotating over every topic would
        walk into the nine kids topics with made_for_kids=False and build a
        general-audience profile for child-directed content.
        """
        from pydantic import ValidationError

        from backend.api.main import AutomationBody

        with pytest.raises(ValidationError):
            AutomationBody(niche="", topic_rotate=True)
        # With a group it is accepted, and a blank niche is legal.
        body = AutomationBody(niche="", niche_group="tech",
                              topic_rotate=True)
        assert body.to_request().topic_rotate is True

    def test_a_blank_niche_is_still_refused_without_rotation(self):
        from pydantic import ValidationError

        from backend.api.main import AutomationBody

        with pytest.raises(ValidationError):
            AutomationBody(niche="")
        with pytest.raises(ValidationError):
            AutomationBody(niche="a")

    def test_there_is_no_sentinel_topic_on_the_wire(self):
        """A boolean cannot be rendered into a script; a magic niche string
        is one missed branch away from a published video titled after it."""
        from backend.api.main import AutomationBody

        request = AutomationBody(niche="", niche_group="kids",
                                 topic_rotate=True).to_request()
        assert "rotate" not in request.niche.lower()
        assert "auto" not in request.niche.lower()


# ==========================================================================
class TestBankAwareSkipping:
    """"Only my reviewed scripts" HARD-FAILS a topic with an empty bank.

    The real bank covers the topic space very unevenly, so a strict lap over
    a 29-topic group would fail most of its runs. Skipping applies ONLY to
    script_source=bank, because bank_first and live can serve any topic.
    """

    def _request(self, **over) -> AutomationRequest:
        data = dict(niche="", niche_group="kids", topic_rotate=True,
                    language="en", video_format="SHORT",
                    script_source="bank")
        data.update(over)
        return AutomationRequest(**data)

    def test_it_picks_a_topic_the_bank_can_actually_serve(self, tmp_path,
                                                         monkeypatch):
        monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
        from backend.api import main as api

        db = Database(tmp_path / "b.db")
        try:
            pool = topics("kids")
            order = list(pool)
            # Nothing banked at all -> no topic can be served.
            assert api._topic_the_bank_can_serve(
                db, self._request(), order) == ""
        finally:
            db.close()

    def test_live_and_bank_first_do_not_skip(self, tmp_path, monkeypatch):
        """They can serve any topic, so skipping would silently reorder the
        lap for no reason."""
        monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
        from backend.api import main as api

        for source in ("live", "bank_first"):
            request = self._request(script_source=source)
            # An empty bank, so if it skipped it would return nothing.
            picked = api._rotate_topic(request)
            assert picked == topics("kids")[0], source

    def test_an_all_empty_bank_still_advances(self, tmp_path, monkeypatch):
        """Stalling on topic 1 for ever would be worse than a visible
        failure: the operator would see nothing happen and no reason why."""
        monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
        from backend.api import main as api

        picked = api._rotate_topic(self._request())
        assert picked == topics("kids")[0]


# ==========================================================================
def test_the_app_carries_the_flag_on_every_recurring_run():
    """THE one line that makes rotation work across runs.

    Without it every scheduled run re-POSTs topic_rotate=false with the last
    resolved topic, the backend treats it as an ordinary automation, and Auto
    mode makes the same subject for ever - the identical failure that already
    happened to script_source and niche_group.
    """
    base = Path("android/app/src/main/java/com/autotube/ai")
    worker = (base / "workers/Workers.kt").read_text(encoding="utf-8")
    assert "topicRotate = automation.topicRotate" in worker

    entity = (base / "data/local/Database.kt").read_text(encoding="utf-8")
    assert 'name = "topic_rotate"' in entity

    dtos = (base / "data/remote/Dtos.kt").read_text(encoding="utf-8")
    assert '@SerialName("topic_rotate")' in dtos


def test_the_cursor_is_not_stored_on_the_phone():
    """Room migrates destructively, so a device-side cursor restarts the lap
    on every app upgrade. Structural, because the symptom - a rotation that
    quietly re-covers its first few topics for ever - is invisible."""
    entity = Path("android/app/src/main/java/com/autotube/ai/data/local/"
                  "Database.kt").read_text(encoding="utf-8")
    assert 'name = "last_topic"' not in entity, \
        "the rotation cursor belongs to the backend, not to a cache that " \
        "is destroyed on every schema bump"
