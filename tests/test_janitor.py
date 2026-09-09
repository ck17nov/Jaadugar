"""The scheduled storage sweep.

The gap: `reclaim_after_days: 7` was in config, `sweep_storage()` was
implemented, and the only caller was the `/maintenance/reclaim` endpoint -
which nothing invoked. Publishing freed its own media immediately; jobs that
died mid-pipeline kept theirs forever.
"""
from __future__ import annotations

import inspect

import pytest

from engine.core.config import load_config
from engine.core.storage import may_reclaim


@pytest.fixture()
def worker(monkeypatch):
    """The real Worker, with the pipeline and sleeping stubbed out."""
    from backend.api import main as api
    w = api.Worker()
    monkeypatch.setattr(api.time, "sleep", lambda _s: None)
    return w, api


class TestItIsActuallyStarted:
    def test_startup_starts_the_janitor(self):
        """The whole bug was that nothing called the sweep."""
        from backend.api import main as api
        assert "start_janitor" in inspect.getsource(api.on_startup)

    def test_it_runs_in_its_own_daemon_thread(self):
        """It must not block the request loop, and must not keep the process
        alive on shutdown."""
        from backend.api import main as api
        source = inspect.getsource(api.Worker.start_janitor)
        assert "daemon=True" in source
        assert "autotube-janitor" in source

    def test_it_can_be_switched_off(self, monkeypatch):
        from backend.api import main as api
        w = api.Worker()
        monkeypatch.setattr(api.CFG, "get",
                            lambda key, default=None:
                            False if key == "storage.auto_sweep"
                            else load_config().get(key, default))
        w.start_janitor()
        assert w.janitor is None

    def test_starting_twice_does_not_start_two_threads(self, worker):
        w, _api = worker
        w.start_janitor()
        first = w.janitor
        w.start_janitor()
        assert w.janitor is first


class TestSweepBehaviour:
    def test_a_sweep_failure_does_not_kill_the_thread(self, worker):
        """A janitor that can kill the process is worse than a full disk."""
        w, _api = worker

        class _Boom:
            def sweep_storage(self, **_kw):
                raise RuntimeError("disk on fire")

        w.pipeline = _Boom()
        assert w._sweep_once(after_days=7.0, reason="test") == 0.0

    def test_it_reports_megabytes_freed(self, worker):
        w, _api = worker

        class _Fake:
            def sweep_storage(self, **_kw):
                return [{"freed_mb": 12.5}, {"freed_mb": 7.5}]

        w.pipeline = _Fake()
        assert w._sweep_once(after_days=7.0, reason="test") == pytest.approx(20.0)

    def test_free_space_is_readable(self, worker):
        w, _api = worker
        assert w._free_gb() > 0

    def test_an_unreadable_path_does_not_trigger_a_panic_sweep(self, worker,
                                                               monkeypatch):
        """Returning 0 free on an OSError would escalate on every tick."""
        w, api = worker
        monkeypatch.setattr(api.shutil, "disk_usage",
                            lambda _p: (_ for _ in ()).throw(OSError("nope")))
        assert w._free_gb() == float("inf")


class TestDiskPressure:
    def test_low_disk_escalates_to_a_shorter_age(self, worker, monkeypatch):
        """One long-form render can add gigabytes between two six-hourly
        ticks, so the schedule alone does not prevent a full volume."""
        w, api = worker
        calls: list[tuple[float, str]] = []

        class _Fake:
            def sweep_storage(self, *, after_days=7.0):
                return []

        w.pipeline = _Fake()
        monkeypatch.setattr(w, "_sweep_once",
                            lambda *, after_days, reason:
                            (calls.append((after_days, reason)), 0.0)[1])
        monkeypatch.setattr(w, "_free_gb", lambda: 1.0)     # under the floor

        # One pass of the loop body, then stop.
        def _stop(_seconds):
            raise KeyboardInterrupt
        monkeypatch.setattr(api.time, "sleep",
                            lambda s: None if not calls else _stop(s))
        with pytest.raises(KeyboardInterrupt):
            w._janitor_loop()

        reasons = [r for _d, r in calls]
        assert "scheduled" in reasons
        assert "low disk" in reasons
        days = dict((r, d) for d, r in calls)
        assert days["low disk"] < days["scheduled"]

    def test_plenty_of_disk_does_not_escalate(self, worker, monkeypatch):
        w, api = worker
        calls: list[str] = []
        monkeypatch.setattr(w, "_sweep_once",
                            lambda *, after_days, reason:
                            (calls.append(reason), 0.0)[1])
        monkeypatch.setattr(w, "_free_gb", lambda: 500.0)

        def _stop(_seconds):
            raise KeyboardInterrupt
        monkeypatch.setattr(api.time, "sleep",
                            lambda s: None if not calls else _stop(s))
        with pytest.raises(KeyboardInterrupt):
            w._janitor_loop()
        assert calls == ["scheduled"]


class TestWhatItMustNeverDelete:
    def test_pending_approvals_survive_even_under_pressure(self):
        """An unwatched video with no file is worse than a full disk, and the
        janitor runs unattended - so this is enforced in the policy rather
        than in the caller."""
        assert may_reclaim("AWAITING_APPROVAL") is False
        assert may_reclaim("AWAITING_APPROVAL", age_days=999.0,
                           after_days=1.0) is False

    def test_approved_but_unsent_videos_survive(self):
        """READY means the upload has not happened; deleting it makes the
        upload impossible."""
        assert may_reclaim("READY", age_days=999.0, after_days=1.0) is False

    def test_published_media_is_still_freed(self):
        """The feature has to keep working - that is the point of it."""
        assert may_reclaim("PUBLISHED") is True
        assert may_reclaim("SCHEDULED") is True


class TestConfig:
    @pytest.mark.parametrize("key", [
        "storage.auto_sweep", "storage.sweep_interval_hours",
        "storage.reclaim_after_days", "storage.min_free_gb",
        "storage.urgent_reclaim_after_days"])
    def test_every_knob_has_a_default(self, key):
        assert load_config().get(key) is not None

    def test_the_urgent_age_is_shorter_than_the_normal_one(self):
        cfg = load_config()
        assert (float(cfg.get("storage.urgent_reclaim_after_days"))
                < float(cfg.get("storage.reclaim_after_days")))
