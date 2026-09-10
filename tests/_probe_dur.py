import json, pytest
from engine.core.models import AutomationRequest, VideoJob
from engine.pipeline import Pipeline
from engine.content.bank import words_per_second

@pytest.fixture()
def pipe(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("AUTOTUBE_DRY_RUN", "1")
    return Pipeline()

def test_duration_filter_vs_actual(pipe, tmp_path):
    # A kids entry of exactly 90 words -> table pace 2.0 -> est_seconds 45.0,
    # dead centre of a 45s request's filter window.
    narr = " ".join(["word"] * 30)
    payload = {"entry_id": "k1", "group": "kids", "topic": "kids bedtime stories",
               "language": "en", "video_format": "SHORT", "made_for_kids": True,
               "title": "K", "refrain": "r", "arc_variant": "alone",
               "outcome_class": "got_it",
               "scenes": [{"beat": "want", "narration": narr, "caption": "क",
                           "image_brief": "x"}] * 3,
               "human": {"reviewer": "me"}}
    from engine.content.bank import BankEntry
    e = BankEntry.from_dict(payload)
    print("\nword_count", e.word_count, "table pace",
          words_per_second("kids", True, "kids bedtime stories"),
          "est_seconds", e.estimated_seconds)
    pipe.db.save_bank_entry(e)

    # The project's own measured edge-tts rate for kids English.
    spec = pipe.voice_engine.voice_spec("en", "energetic", gender="female")
    pipe.db.set_setting(pipe.SPEECH_RATE_KEY,
                        {pipe._speech_rate_key("en", spec): 3.21})

    req = AutomationRequest(niche="kids bedtime stories", niche_group="kids",
                            language="en", video_format="SHORT",
                            duration_seconds=45, script_source="bank")
    job = VideoJob(job_id="j1"); job.dir = str(tmp_path)
    claim = pipe.stage_bank(job, req)
    print("claimed:", claim.entry_id if claim else None)
    print("filter window for 45s:", 45*0.75, "-", 45*1.25, "(table seconds)")
    print("requested 45s -> pipeline will render:", req.duration_seconds, "s")
    assert abs(req.duration_seconds - 45) <= 45*0.25, (
        f"the duration FILTER accepted this entry for a 45s request but the "
        f"render target is {req.duration_seconds}s")
