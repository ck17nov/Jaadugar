"""Render one banked entry end to end and report what actually landed."""
import json, os, sys
from pathlib import Path
from engine.core.config import load_config
from engine.core.models import AutomationRequest
from engine.pipeline import Pipeline, PipelineError

niche, group, fmt, secs, lang = (sys.argv[1], sys.argv[2], sys.argv[3],
                                 int(sys.argv[4]), sys.argv[5])
cfg = load_config(); cfg.set("dry_run", True)
pipe = Pipeline(cfg)
request = AutomationRequest(
    niche=niche, audience="25-44" if group != "kids" else "5-7",
    language=lang, video_format=fmt, duration_seconds=secs,
    made_for_kids=(group == "kids"),
    style="educational and clear" if group != "kids"
          else "gentle and simple (for young children)",
    script_source="bank", niche_group=group, mode="APPROVAL")
try:
    result = pipe.run(request)
except PipelineError as exc:
    print("PIPELINE FAILED:", exc); pipe.close(); sys.exit(1)

job = result.job; d = Path(job.dir)
sc = json.loads((d / "script.json").read_text(encoding="utf-8"))
meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
print("\n" + "=" * 72)
print("status   :", job.status)
print("quality  :", f"{result.quality.score:.0f}/100" if result.quality else "-")
print("bank     :", sc.get("bank", {}).get("entry_id"))
print("video    :", os.path.getsize(job.video_path) if job.video_path
      and os.path.exists(job.video_path) else "MISSING", "bytes")
print("thumb    :", job.thumbnail_path or "none")
print("TITLE    :", meta.get("title"), f"({meta.get('title_score')})")
print("chapters :", len(meta.get("chapters") or []))
print("disclaimer field:", sc.get("disclaimer") or "none")
print("scenes   :", len(sc["scenes"]), "| retention", sc.get("retention_score"))
for s in sc["scenes"][:4]:
    print(f"  {s['index']} [{s['role']:7s}] {s['duration']:5.1f}s")
    print(f"      NARR {s['narration'][:76]}")
    print(f"      CAP  {s.get('caption_text','(none)')[:76]}")
if result.quality:
    print("blockers :", result.quality.blockers or "none")
    print("warnings :", (result.quality.warnings or ["none"])[:4])
print("dir      :", d)
pipe.close()
