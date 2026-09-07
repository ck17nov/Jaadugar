"""Stopping work, and choosing a narrator.

Both exist because the app offered neither: an automation could be started and
then only waited out, and every video came out in the same female voice
whatever was asked for.
"""
from __future__ import annotations

from engine.core.config import load_config
from engine.core.models import AutomationRequest, JobStatus, VideoJob
from engine.tts.engine import VoiceEngine


class TestCancellation:
    def test_cancelled_is_terminal(self):
        """So the retry wrapper does not restart what the user just stopped."""
        assert JobStatus.CANCELLED.terminal is True

    def test_cancelled_is_distinct_from_failed(self):
        """A cancelled job is not evidence of a fault and is not retried."""
        assert JobStatus.CANCELLED.value != JobStatus.FAILED.value
        assert JobStatus.FAILED.terminal is True

    def test_the_pipeline_stops_at_the_next_stage_boundary(self, tmp_path):
        from engine.pipeline import JobCancelled, Pipeline
        cfg = load_config()
        cfg.set("app.workspace", str(tmp_path))
        pipeline = Pipeline(cfg)
        job = VideoJob(automation_id="auto-1")
        pipeline.cancel_check = lambda j: True
        try:
            pipeline._advance(job, JobStatus.SCRIPT, "x")
        except JobCancelled:
            pass
        else:
            raise AssertionError("expected JobCancelled")
        assert job.status == JobStatus.CANCELLED.value
        assert any("CANCELLED" in line for line in job.logs)

    def test_no_cancel_check_means_business_as_usual(self, tmp_path):
        from engine.pipeline import Pipeline
        cfg = load_config()
        cfg.set("app.workspace", str(tmp_path))
        pipeline = Pipeline(cfg)
        job = VideoJob(automation_id="auto-1")
        pipeline._advance(job, JobStatus.SCRIPT, "x")
        assert job.status == JobStatus.SCRIPT.value

    def test_the_retry_wrapper_does_not_swallow_a_cancellation(self, tmp_path):
        """Retrying a cancelled stage would restart the work being stopped."""
        from engine.pipeline import JobCancelled, Pipeline
        cfg = load_config()
        cfg.set("app.workspace", str(tmp_path))
        cfg.set("automation.max_retries", 3)
        pipeline = Pipeline(cfg)
        job = VideoJob(automation_id="auto-1")
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise JobCancelled("job-1")

        try:
            pipeline._retry("script", boom, job)
        except JobCancelled:
            pass
        else:
            raise AssertionError("expected JobCancelled to propagate")
        assert calls["n"] == 1, "a cancellation must not be retried"

    def test_a_queued_automation_can_be_dropped(self):
        """The queue has no remove(); it is rebuilt instead."""
        import backend.api.main as api
        worker = api.Worker()
        keep = AutomationRequest(niche="science")
        drop = AutomationRequest(niche="kids bedtime stories")
        worker.queue.put(keep)
        worker.queue.put(drop)

        dropped = worker.cancel_automation(drop.id)

        assert dropped == 1
        assert worker.queue.qsize() == 1
        assert worker.queue.get_nowait().id == keep.id

    def test_cancelling_an_automation_also_flags_its_running_job(self):
        import backend.api.main as api
        worker = api.Worker()
        worker.cancel_automation("auto-9")
        job = VideoJob(automation_id="auto-9")
        assert worker._is_cancelled(job) is True

    def test_cancelling_a_job_does_not_flag_its_siblings(self):
        import backend.api.main as api
        worker = api.Worker()
        worker.cancel_job("job-1")
        mine = VideoJob(automation_id="auto-1")
        mine.job_id = "job-1"
        other = VideoJob(automation_id="auto-1")
        other.job_id = "job-2"
        assert worker._is_cancelled(mine) is True
        assert worker._is_cancelled(other) is False


class TestNarratorVoice:
    def _engine(self):
        return VoiceEngine(load_config())

    def test_male_and_female_differ(self):
        """Gender was recorded on the spec and then ignored."""
        engine = self._engine()
        female = engine.voice_spec("hi", gender="female").voice_id
        male = engine.voice_spec("hi", gender="male").voice_id
        assert female and male and female != male

    def test_every_indian_language_has_both_genders(self):
        engine = self._engine()
        for lang in ("hi", "en-IN", "ta", "te", "bn", "mr", "gu"):
            female = engine.voice_spec(lang, gender="female").voice_id
            male = engine.voice_spec(lang, gender="male").voice_id
            assert female, f"{lang} has no female voice"
            assert male, f"{lang} has no male voice"
            assert female != male, f"{lang} maps both genders to one voice"

    def test_english_child_uses_the_real_child_voice(self):
        spec = self._engine().voice_spec("en", gender="child")
        assert spec.voice_id == "en-US-AnaNeural"

    def test_hindi_child_is_an_approximation_not_an_invented_voice(self):
        """No child voice exists for Hindi; naming one would fail at synthesis."""
        engine = self._engine()
        child = engine.voice_spec("hi", gender="child")
        female = engine.voice_spec("hi", gender="female")
        assert child.voice_id == female.voice_id
        assert child.pitch != female.pitch, "expected the pitch to be raised"

    def test_the_child_approximation_slows_the_pace(self):
        engine = self._engine()
        child = engine.voice_spec("hi", gender="child")
        female = engine.voice_spec("hi", gender="female")
        assert _pct(child.rate) < _pct(female.rate)

    def test_a_regional_code_falls_back_to_the_base_language(self):
        """en-GB is not mapped; it must still get an English voice."""
        assert self._engine().voice_spec("en-GB", gender="male").voice_id

    def test_the_request_carries_the_choice(self):
        assert AutomationRequest().voice_gender == "female"
        assert AutomationRequest(voice_gender="male").voice_gender == "male"

    def test_the_api_accepts_only_known_values(self):
        from backend.api.main import AutomationBody
        body = AutomationBody(niche="science", voice_gender="child")
        assert body.voice_gender == "child"
        try:
            AutomationBody(niche="science", voice_gender="robot")
        except Exception:
            pass
        else:
            raise AssertionError("an unknown voice must be rejected")


def _pct(rate: str) -> float:
    return float(str(rate).strip().rstrip("%"))
