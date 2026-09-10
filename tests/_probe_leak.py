"""Probes: does a claimed entry survive a post-upload failure?"""
import os, sys, json, time
import pytest
from engine.core.models import AutomationRequest, VideoJob, JobStatus
from engine.pipeline import Pipeline, PipelineError, JobCancelled


@pytest.fixture()
def pipe(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("AUTOTUBE_DRY_RUN", "1")
    return Pipeline()


def _seed(db, n=2):
    for i in range(n):
        db.execute(
            "INSERT OR REPLACE INTO bank_entries(entry_id,grp,topic,shape,language,"
            "video_format,made_for_kids,title,content_hash,est_seconds,imported_at,"
            "used_at,used_job_id,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"e{i}", "kids", "kids bedtime stories", "narrative", "en", "SHORT", 1,
             f"T{i}", f"h{i}", 45.0, 1000.0 + i, 0, "",
             json.dumps({"entry_id": f"e{i}", "group": "kids",
                         "topic": "kids bedtime stories", "language": "en",
                         "video_format": "SHORT", "title": f"T{i}",
                         "refrain": "r", "arc_variant": "alone",
                         "outcome_class": "got_it",
                         "scenes": [{"beat": "want", "narration": "a b c d",
                                     "caption": "क ख", "image_brief": "x"}] * 3,
                         "human": {"reviewer": "me", "verdict": "approve"}})))


def test_release_happens_after_a_successful_upload(pipe, monkeypatch):
    """The video is on YouTube, then something later raises."""
    _seed(pipe.db)
    request = AutomationRequest(niche="kids bedtime stories",
                                niche_group="kids", language="en",
                                video_format="SHORT", duration_seconds=45,
                                script_source="bank", mode="auto")
    # Everything up to publish is stubbed; publish "succeeds" then blows up
    # exactly where publish_now does its post-upload bookkeeping.
    monkeypatch.setattr(pipe, "preflight", lambda r: [])
    monkeypatch.setattr(pipe, "stage_research", lambda *a, **k: [])
    monkeypatch.setattr(pipe, "stage_script", lambda *a, **k: object())
    monkeypatch.setattr(pipe, "stage_voice", lambda *a, **k: (None, 1.0, []))
    monkeypatch.setattr(pipe, "stage_visuals", lambda *a, **k: [])
    monkeypatch.setattr(pipe, "stage_render", lambda *a, **k: None)

    def fake_finalize(job, *a, **k):
        class Q:
            score = 90.0; passed = True; blockers = []
        return None, Q()
    monkeypatch.setattr(pipe, "stage_finalize", fake_finalize)

    uploaded = {}
    def fake_publish(job, request, meta, quality, **k):
        uploaded["video_id"] = "YTID123"      # the upload really happened
        job.youtube_video_id = "YTID123"
        job.status = JobStatus.PUBLISHED.value
        pipe.db.save_job(job)
        raise OSError("could not delete the reclaimed media")   # post-upload
    monkeypatch.setattr(pipe, "stage_publish", fake_publish)
    monkeypatch.setattr(pipe, "stage_idea",
                        lambda job, req, prof, vids, claim=None: (
                            __import__("engine.content.bank_use", fromlist=["x"])
                            .to_idea(claim.entry), ""))

    with pytest.raises(PipelineError):
        pipe.run(request)

    assert uploaded.get("video_id") == "YTID123", "upload must have happened"
    rows = {r["entry_id"]: r for r in pipe.db.bank_entries()}
    print("\nPUBLISHED to YouTube:", uploaded)
    for k, r in rows.items():
        print("  ", k, "used_at=", r["used_at"], "job=", r["used_job_id"])
    assert rows["e0"]["used_at"] > 0, (
        "LEAK: the entry was released back into the pool after publishing")
