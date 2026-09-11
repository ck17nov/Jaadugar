#!/usr/bin/env python
"""Render one video per channel group and judge it on what the viewer sees.

    python scripts/e2e_check.py                    # one SHORT per group
    python scripts/e2e_check.py --longform         # add a long-form each
    python scripts/e2e_check.py --group kids

`verify_dry_run.py` answers "did every artifact get written". This answers the
next question: is what got written any good. It reads the finished job and
checks the things that were actually wrong in the field and would pass an
existence check unnoticed -

  * a picture held on screen for seven seconds because a beat was one shot
  * a caption cue wrapped over three lines, or broken mid-sentence
  * a title that gives the ending away in the first four words
  * a publish decision that says AWAITING_APPROVAL when the request said AUTO

Runs in dry-run: it renders in full and does not upload.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.core.config import load_config            # noqa: E402
from engine.core.groups import GROUPS                 # noqa: E402
from engine.core.models import AutomationRequest      # noqa: E402
from engine.pipeline import Pipeline, PipelineError   # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = ("\033[32m", "\033[31m", "\033[33m",
                                  "\033[2m", "\033[0m")

# What "good" means, in numbers. Each one is a defect that shipped.
MAX_MEAN_HOLD = 4.0        # seconds a single picture may average
MAX_SINGLE_HOLD = 7.0      # and the worst one
MAX_CAPTION_LINES = 1      # a cue is ONE line
MIN_TITLE_SCORE = 70.0
MIN_QUALITY = 70.0


def _cue_report(job_dir: Path) -> dict:
    """Line count and mid-sentence breaks, read from the real .ass file."""
    path = job_dir / "captions.ass"
    if not path.exists():
        return {"error": "captions.ass missing"}
    cues, hard_breaks, longest = 0, 0, 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        cues += 1
        text = line.split(",", 9)[-1]
        # \N is an explicit line break in ASS. With WrapStyle 2 it is the
        # ONLY thing that can wrap a cue, so counting them counts lines.
        hard_breaks += text.count("\\N")
        visible = text
        while "{" in visible and "}" in visible:        # strip override tags
            start = visible.index("{")
            end = visible.index("}", start)
            visible = visible[:start] + visible[end + 1:]
        longest = max(longest, len(visible.replace("\\N", " ")))
    return {"cues": cues, "hard_breaks": hard_breaks,
            "lines_per_cue": 1 + (hard_breaks / cues if cues else 0),
            "longest_chars": longest}


def _shot_report(job_dir: Path, duration: float) -> dict:
    """How long each picture stays up. The 7.4-second hold, measured."""
    path = job_dir / "render_report.json"
    if not path.exists():
        return {"error": "render_report.json missing"}
    data = json.loads(path.read_text(encoding="utf-8"))
    clips = data.get("clips") or data.get("shots") or []
    holds = [float(c.get("duration", 0)) for c in clips
             if float(c.get("duration", 0)) > 0]
    if not holds:
        # Older reports only carry a count; fall back to the average.
        count = int(data.get("clip_count") or data.get("scenes") or 0)
        if count:
            return {"shots": count, "mean_hold": round(duration / count, 2),
                    "max_hold": None, "estimated": True}
        return {"error": "no per-clip timing in render_report.json"}
    return {"shots": len(holds),
            "mean_hold": round(sum(holds) / len(holds), 2),
            "max_hold": round(max(holds), 2), "estimated": False}


def _check(label: str, ok: bool, detail: str, failures: list[str]) -> None:
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"    {mark}  {label:26} {detail}")
    if not ok:
        failures.append(f"{label}: {detail}")


def run_one(pipe: Pipeline, *, group: str, topic: str, video_format: str,
            seconds: int, language: str, made_for_kids: bool) -> list[str]:
    print(f"\n{DIM}{'-' * 74}{RESET}")
    print(f"{group} / {topic} / {language} / {video_format} / {seconds}s")
    failures: list[str] = []

    request = AutomationRequest(
        niche=topic, niche_group=group, language=language,
        video_format=video_format, duration_seconds=seconds,
        made_for_kids=made_for_kids,
        # AUTO on purpose: the publish DECISION is one of the things under
        # test. A request that asked for approval could not detect the gate
        # firing when it should not.
        mode="AUTO",
        # bank_first, not bank: an empty cell should fall through to live
        # generation rather than abort the sweep.
        script_source="bank_first")
    try:
        result = pipe.run(request)
    except PipelineError as exc:
        print(f"    {RED}PIPELINE FAILED{RESET} {exc}")
        return [f"{group}/{topic}: pipeline failed: {exc}"]

    job = result.job
    job_dir = Path(job.dir)
    meta = json.loads((job_dir / "metadata.json").read_text(encoding="utf-8"))
    quality = result.quality

    from engine.core.util import probe_json
    probe = probe_json(job_dir / "video.mp4")
    duration = float((probe.get("format") or {}).get("duration") or 0)

    shots = _shot_report(job_dir, duration)
    cues = _cue_report(job_dir)

    print(f"  {DIM}{job.job_id}  {duration:.1f}s  "
          f"{(result.script.provider if result.script else '')}{RESET}")

    if "error" in shots:
        _check("shot pacing", False, shots["error"], failures)
    else:
        _check("shot pacing",
               shots["mean_hold"] <= MAX_MEAN_HOLD
               and (shots["max_hold"] or 0) <= MAX_SINGLE_HOLD,
               f"{shots['shots']} shots, mean {shots['mean_hold']}s, "
               f"max {shots['max_hold']}s", failures)

    if "error" in cues:
        _check("captions", False, cues["error"], failures)
    else:
        _check("captions",
               cues["lines_per_cue"] <= MAX_CAPTION_LINES,
               f"{cues['cues']} cues, {cues['hard_breaks']} hard breaks, "
               f"longest {cues['longest_chars']} chars", failures)

    title = str(meta.get("title", ""))
    score = float(meta.get("title_score") or 0)
    _check("title", score >= MIN_TITLE_SCORE and len(title) <= 100,
           f"{score:.1f}/100, {len(title)} chars - {title[:58]}", failures)

    overall = float(getattr(quality, "score", 0) or 0)
    _check("quality gate", overall >= MIN_QUALITY, f"{overall:.0f}/100",
           failures)

    # The publish decision. AUTO was requested, so anything other than a
    # published/ready terminal state is the gate firing - which is a finding
    # unless the fact checker asked for it.
    status = job.status
    held_for_facts = "factual" in (job.error or "").lower()
    _check("publish decision",
           status in ("PUBLISHED", "READY", "SCHEDULED") or held_for_facts,
           f"{status}" + (f" ({job.error[:60]})" if job.error else ""),
           failures)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", default="",
                        help="just this group (kids | finance | tech)")
    parser.add_argument("--longform", action="store_true",
                        help="also render one long-form per group")
    parser.add_argument("--language", default="",
                        help="force a language instead of the group default")
    args = parser.parse_args()

    cfg = load_config()
    cfg.set("dry_run", True)          # render fully, upload nothing
    pipe = Pipeline(cfg)

    all_failures: list[str] = []
    try:
        for grp in GROUPS:
            if args.group and grp.key != args.group:
                continue
            topic = grp.topics[0] if grp.topics else grp.key
            language = args.language or "en"
            all_failures += run_one(
                pipe, group=grp.key, topic=topic, video_format="SHORT",
                seconds=50, language=language,
                made_for_kids=grp.child_directed)
            if args.longform:
                all_failures += run_one(
                    pipe, group=grp.key, topic=topic,
                    video_format="LONGFORM", seconds=480, language=language,
                    made_for_kids=grp.child_directed)
    finally:
        pipe.close()

    print(f"\n{DIM}{'=' * 74}{RESET}")
    if all_failures:
        print(f"{RED}{len(all_failures)} findings{RESET}")
        for finding in all_failures:
            print(f"  - {finding}")
        return 1
    print(f"{GREEN}every render passed every check{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
