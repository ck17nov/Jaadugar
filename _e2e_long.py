"""Long-form LIVE render: disclaimer, chapters, thumbnail, explainer shape."""
import json, os, sys
from pathlib import Path
from engine.core.config import load_config
from engine.core.models import AutomationRequest
from engine.pipeline import Pipeline, PipelineError

cfg = load_config(); cfg.set("dry_run", True)
pipe = Pipeline(cfg)
request = AutomationRequest(
    niche="mutual funds explained", audience="25-44", language="en",
    video_format="LONGFORM", duration_seconds=300,
    style="educational and clear", script_source="live",
    niche_group="finance", mode="APPROVAL")
try:
    result = pipe.run(request)
except PipelineError as exc:
    print("PIPELINE FAILED:", exc); pipe.close(); sys.exit(1)
job = result.job; d = Path(job.dir)
print("=" * 70)
print("status  :", job.status)
print("quality :", f"{result.quality.score:.0f}/100" if result.quality else "-")
print("video   :", os.path.getsize(job.video_path) if job.video_path and os.path.exists(job.video_path) else "MISSING")
print("thumb   :", job.thumbnail_path or "MISSING",
      os.path.getsize(job.thumbnail_path) if job.thumbnail_path and os.path.exists(job.thumbnail_path) else "")
meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
print("TITLE   :", meta.get("title"), f"({meta.get('title_score')})")
print("CHAPTERS:", len(meta.get("chapters") or []))
sc = json.loads((d / "script.json").read_text(encoding="utf-8"))
print("disclaimer field:", sc.get("disclaimer"))
print("scenes  :", len(sc["scenes"]), "| retention", sc.get("retention_score"))
print("scene 0 :", sc["scenes"][0]["narration"][:140])
print("scene 0 role:", sc["scenes"][0]["role"], "on_screen:", sc["scenes"][0].get("on_screen_text"))
if (d / "thumbnail_report.json").exists():
    tr = json.loads((d / "thumbnail_report.json").read_text(encoding="utf-8"))
    print("thumb variants:", [(v["style"], v["score"], v["text"]) for v in tr["variants"]])
if result.quality:
    print("blockers:", result.quality.blockers)
    print("warnings:", result.quality.warnings[:5])
pipe.close()
