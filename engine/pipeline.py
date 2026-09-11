"""The end-to-end pipeline (spec sections 21, 22, 24, 36, 49).

Every stage:
  * persists job state to SQLite before and after, so an app crash, a phone
    reboot or a killed worker resumes instead of losing work;
  * writes its artifact to the job directory, which IS the dry-run output
    (research.json, idea.json, script.json, assets/, voice.wav, video.mp4,
    thumbnail.jpg, metadata.json, quality_report.json);
  * is individually retryable with backoff.

Duration honesty: the target length is enforced against MEASURED narration, not
an estimate.  If the synthesised voice overruns, the pipeline re-synthesises at
a corrected speaking rate rather than shipping a "45 second" video that runs 70.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .analytics.collect import AnalyticsCollector, StrategyLearner
from .content.ideas import IdeaGenerator
from .content.llm import LLMRouter
from .content.metadata import MetadataGenerator
from .content.originality import FactChecker, OriginalityChecker
from .content.retention import analyze as analyze_retention, auto_improve
from .content.script import ScriptGenerator
from .core.config import Config, load_config
from .core.db import Database
from .core.groups import group_for_topic
from .core.logging import log_event, setup_logging
from .core.models import (AutomationRequest, ContentIdea, JobStatus, Mode,
                          QualityReport, ResearchVideo, Scene, Script,
                          VideoJob, VideoMetadata)
from .core.niche import build_profile
from .core.util import (clamp, count_words, ensure_dir, have_ffmpeg,
                        read_json, safe_write_json, sha1, slugify)
from .quality.gate import QualityGate
from .research.gaps import cluster_videos, find_gaps, research_context_block
from .research.youtube import QuotaGuard, YouTubeResearch
from .thumbnail.generator import ThumbnailGenerator
from .tts.engine import VoiceEngine
from .video.captions import CaptionEngine
from .video.compose import (MOTION_CYCLE, SceneTiming, VideoComposer,
                           assign_motion, cleanup_clips)
from .video.music import build_music, build_transition_sfx
from .video.templates import (apply_to_profile, caption_overrides,
                              select_template, video_overrides,
                              visual_overrides)
from .visuals.engine import VisualEngine
from .youtube.auth import YouTubeAuth
from .youtube.upload import YouTubeUploader


class PipelineError(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(f"[{stage}] {message}")
        self.stage = stage


def needs_approval(*, mode: str, config_default_requires_approval: bool,
                   made_for_kids: bool, kids_already_confirmed: bool,
                   fact_requires_approval: bool) -> tuple[bool, str]:
    """Whether to hold a finished video for a human. Returns (hold, reason).

    Extracted and made pure because the previous inline version had two bugs
    that a test would have caught immediately:

    1. The CONFIG default was an unconditional OR, so
       `automation.approval_required: true` acted as a FLOOR and no request
       could escape it. Choosing "Auto - publish without asking" in the app
       therefore did nothing at all, which is worse than either behaviour
       because the UI promised something it could not deliver.
    2. `made_for_kids` forced approval unconditionally, so a kids automation
       could NEVER auto-publish - and kids is the main channel here. The
       thing that needs confirming is the CLASSIFICATION, and a human who
       approved the first video of an automation has confirmed it for the
       rest.

    Honouring auto is safe because `youtube.force_private` pins every upload
    to private, so "auto" means "uploaded privately, waiting in Studio", not
    "live to subscribers unreviewed".

    A fact-check flag is never auto-published, whatever the mode: that is a
    correctness risk rather than a preference.
    """
    if fact_requires_approval:
        return True, "factual risk needs review"
    if made_for_kids and not kids_already_confirmed:
        return True, ("kids classification must be confirmed once for this "
                      "automation")
    if str(mode).upper() == Mode.AUTO.value:
        return False, ""
    if config_default_requires_approval:
        return True, "approval mode"
    return False, ""


class _ThumbnailNotApplicable(Exception):
    """Not an error: this format has nowhere to put a custom thumbnail.

    Raised rather than nesting the whole stage in an `if`, so the one
    `except Exception` below still catches genuine generation failures and
    this case does not get logged as one.
    """


class JobCancelled(RuntimeError):
    """Raised when the user asks for a job to stop.

    Deliberately NOT a PipelineError: the retry wrapper treats those as
    transient and would dutifully restart the stage the user just cancelled.
    """


@dataclass
class PipelineResult:
    job: VideoJob
    artifacts: dict[str, str] = field(default_factory=dict)
    quality: QualityReport | None = None
    uploaded: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.job.status not in (JobStatus.FAILED.value,
                                       JobStatus.REJECTED.value)


class Pipeline:
    def __init__(self, cfg: Config | None = None, db: Database | None = None):
        self.cfg = cfg or load_config()
        self.workspace = self.cfg.workspace
        setup_logging(jsonl=self.workspace / "logs" / "events.jsonl")
        self.db = db or Database(self.workspace / "autotube.db")

        self.router = LLMRouter(
            list(self.cfg.get("content.llm_provider_order",
                              ["groq", "gemini", "ollama", "template"])), self.cfg)
        self.quota = QuotaGuard(self.cfg, self.db)
        self.research_engine = YouTubeResearch(self.cfg, self.db, self.quota)
        self.idea_engine = IdeaGenerator(self.cfg, self.router, self.db)
        self.script_engine = ScriptGenerator(self.cfg, self.router)
        self.metadata_engine = MetadataGenerator(self.cfg, self.router)
        self.voice_engine = VoiceEngine(self.cfg)
        self.visual_engine = VisualEngine(self.cfg)
        self.caption_engine = CaptionEngine(self.cfg)
        self.composer = VideoComposer(self.cfg)
        self.thumbnail_engine = ThumbnailGenerator(self.cfg)
        self.quality_gate = QualityGate(self.cfg)
        self.originality = OriginalityChecker(self.cfg, self.db)
        self.factchecker = FactChecker(self.cfg)
        self.auth = YouTubeAuth(self.cfg)
        self.uploader = YouTubeUploader(self.cfg, self.auth, self.quota)
        self.learner = StrategyLearner(self.cfg, self.db)
        self._motion_cycle: list[str] | None = None
        # Set by the worker. Consulted at every stage boundary so a cancel
        # takes effect within one stage rather than at the end of the render:
        # there is no safe way to interrupt an ffmpeg encode mid-frame, so
        # "stop" means "stop at the next seam".
        self.cancel_check: Callable[[VideoJob], bool] | None = None

    # ==================================================================
    # Job lifecycle helpers
    # ==================================================================
    def _advance(self, job: VideoJob, status: JobStatus, note: str = "") -> None:
        self._raise_if_cancelled(job)
        job.status = status.value
        job.updated_at = time.time()
        if note:
            job.logs.append(f"{time.strftime('%H:%M:%S')} {status.value}: {note}")
        self.db.save_job(job)

    def _raise_if_cancelled(self, job: VideoJob) -> None:
        if self.cancel_check is None or not self.cancel_check(job):
            return
        job.status = JobStatus.CANCELLED.value
        job.updated_at = time.time()
        job.logs.append(f"{time.strftime('%H:%M:%S')} CANCELLED: stopped by user")
        self.db.save_job(job)
        log_event("PIPELINE", "job cancelled", job=job.job_id)
        raise JobCancelled(job.job_id)

    def _job_dir(self, job: VideoJob, request: AutomationRequest) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(job.created_at))
        name = f"{stamp}_{slugify(request.niche, 24)}_{job.job_id[-6:]}"
        path = ensure_dir(self.workspace / "jobs" / name)
        job.dir = str(path)
        return path

    def _retry(self, stage: str, fn: Callable[[], Any], job: VideoJob) -> Any:
        attempts = int(self.cfg.get("automation.max_retries", 3))
        backoff = float(self.cfg.get("automation.retry_backoff_seconds", 20))
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return fn()
            except JobCancelled:
                raise
            except Exception as exc:
                last = exc
                job.retry_count += 1
                job.error = f"{stage}: {str(exc)[:400]}"
                self.db.save_job(job)
                if attempt >= attempts:
                    break
                wait = backoff * attempt
                log_event(stage.upper(), "stage failed, retrying",
                          attempt=f"{attempt}/{attempts}", wait=f"{wait:.0f}s",
                          error=str(exc)[:200])
                time.sleep(wait)
        raise PipelineError(stage, str(last))

    # ==================================================================
    # Preconditions (spec section 46: anti-spam)
    # ==================================================================
    def preflight(self, request: AutomationRequest) -> list[str]:
        problems: list[str] = []
        if not have_ffmpeg():
            problems.append(
                "ffmpeg/ffprobe not found on PATH - required for rendering. "
                "See docs/SETUP.md")
        if not self.research_engine.configured:
            message = ("YOUTUBE_API_KEY not set - research cannot run "
                       "(free, no credit card: docs/YOUTUBE_SETUP.md)")
            if self.cfg.dry_run:
                # Spec section 36 wants a dry run to produce every artifact.
                # Blocking here meant you could not see what the pipeline
                # makes until after signing up for an API key - so in dry run
                # this degrades loudly instead. Ideas then come from the
                # structural builder, with no trend evidence behind them.
                log_event("PREFLIGHT", "dry run without research",
                          reason=message,
                          effect="ideas will be structural, not trend-driven")
            else:
                problems.append(message)
        if not self.router.has_real_llm():
            log_event("PREFLIGHT", "no LLM configured - script quality will be "
                                   "degraded to the template builder",
                      hint="set GROQ_API_KEY or GEMINI_API_KEY, or run ollama")
            # The template builder assembles framing lines, and there are only
            # so many honest ones - it tops out around 200 words. That is a
            # whole 45s Short but nowhere near a 10-minute video, and the gap
            # would be papered over by slowing the narration until it failed
            # the duration check after the full render. Say so first instead.
            if request.duration_seconds > 120:
                problems.append(
                    f"a {request.duration_seconds}s video needs a real LLM: the "
                    f"template builder cannot honestly fill more than about "
                    f"two minutes of narration without repeating itself. Set "
                    f"GROQ_API_KEY or GEMINI_API_KEY (both free, no credit "
                    f"card - docs/API_SETUP.md), or request a shorter video.")

        limit = int(self.cfg.get("automation.daily_video_limit", 3))
        since = time.time() - 86400
        made_today = self.db.count_jobs_since(
            since, (JobStatus.PUBLISHED.value, JobStatus.SCHEDULED.value,
                    JobStatus.READY.value))
        if made_today >= limit:
            problems.append(
                f"daily video limit reached ({made_today}/{limit}) - "
                f"raise automation.daily_video_limit to continue")

        similar = self._recent_similarity_run()
        halt_after = int(self.cfg.get("automation.stop_after_similar_videos", 3))
        if similar >= halt_after:
            problems.append(
                f"automation halted: the last {similar} scripts were near-"
                f"identical (>= "
                f"{float(self.cfg.get('automation.duplicate_similarity_threshold', 0.8)) * 100:.0f}% "
                f"similar). This is what mass-produced spam looks like. Review "
                f"the queue, change the niche or keywords, then continue.")
        return problems

    def _recent_similarity_run(self) -> int:
        """How many of the most recent scripts are near-duplicates of each other.

        Spec section 46: if several videos in a row are effectively the same
        video, stop rather than keep publishing them.
        """
        from .core.util import jaccard
        recent = self.db.recent_script_texts(limit=6)
        # Skip repeats of ONE banked entry: a released-and-reclaimed entry
        # stores the same text twice and would look like a duplicate run.
        seen: set[str] = set()
        texts = []
        for _script_id, text, provider in recent:
            if not text:
                continue
            if provider.startswith("bank:"):
                if provider in seen:
                    continue
                seen.add(provider)
            texts.append(text)
        if len(texts) < 2:
            return 0
        threshold = float(
            self.cfg.get("automation.duplicate_similarity_threshold", 0.80))
        run = 1
        for i in range(1, len(texts)):
            if jaccard(texts[i - 1], texts[i], n=4) >= threshold:
                run += 1
            else:
                break
        return run if run >= 2 else 0

    # ==================================================================
    # Stages
    # ==================================================================
    def stage_research(self, job: VideoJob, request: AutomationRequest,
                      profile) -> list[ResearchVideo]:
        self._advance(job, JobStatus.RESEARCH, f"niche={request.niche}")
        skipped = ""
        if not self.research_engine.configured and self.cfg.dry_run:
            # Preflight already logged why. Record it in the artifact too, so a
            # dry-run job folder never looks like a real research run.
            skipped = "YOUTUBE_API_KEY not set; dry run continued without trends"
            videos: list[ResearchVideo] = []
        else:
            videos = self._retry("research", lambda: self.research_engine.research(
                request.niche, profile, video_format=request.video_format,
                extra_keywords=request.keywords), job)
        safe_write_json(Path(job.dir) / "research.json",
                        {"niche": request.niche,
                         "count": len(videos),
                         "skipped": skipped,
                         "quota_used_today": self.quota.used(),
                         "videos": [v.to_dict() for v in videos]})
        return videos

    # ------------------------------------------------------------------
    # Script bank
    # ------------------------------------------------------------------
    def stage_bank(self, job: VideoJob, request: AutomationRequest):
        """Claim a banked script for this job, or return None.

        Runs before research, because a banked entry makes the research call
        advisory rather than load-bearing: the topic, angle and hook are
        already decided, so the only thing research still contributes is
        title patterns and the trend snapshot in the artifacts.
        """
        source = (request.script_source or "live").strip().lower()
        if source not in ("bank", "bank_first"):
            return None

        from .content import bank_use
        group = (request.niche_group or "").strip().lower()
        if not group:
            found = group_for_topic(request.niche)
            group = found.key if found else ""
        if not group:
            if source == "bank":
                raise PipelineError(
                    "bank", f"script_source=bank but {request.niche!r} does "
                            f"not belong to a known channel group")
            log_event("BANK", "no channel group for this niche; generating live",
                      niche=request.niche)
            return None

        claim = bank_use.claim(
            self.db, group=group, language=request.language,
            video_format=request.video_format, job_id=job.job_id,
            topics=[request.niche], near_seconds=float(request.duration_seconds),
            require_review=bool(self.cfg.get("bank.require_review", True)),
            require_human=bool(self.cfg.get("bank.require_human_review",
                                            False)))
        if claim is None:
            # Retry without the duration filter before giving up: a bank with
            # only 30-second stories in it should still serve a 45-second
            # request, since the entry's own length is what gets used anyway.
            claim = bank_use.claim(
                self.db, group=group, language=request.language,
                video_format=request.video_format, job_id=job.job_id,
                topics=[request.niche], near_seconds=0.0,
                require_review=bool(self.cfg.get("bank.require_review", True)),
                require_human=bool(self.cfg.get("bank.require_human_review",
                                                False)))
        if claim is None:
            counts = {f"{r['grp']}/{r['language']}/{r['video_format']}":
                      f"{r['unused']}/{r['total']}"
                      for r in self.db.bank_counts()}
            if source == "bank":
                raise PipelineError(
                    "bank", f"no unused reviewed entry for {group}/"
                            f"{request.language}/{request.video_format}; "
                            f"have {counts or 'nothing'}")
            log_event("BANK", "bank is empty for this slot; generating live",
                      group=group, language=request.language,
                      video_format=request.video_format, have=str(counts))
            return None

        # THE ENTRY'S OWN LENGTH WINS. The Create screen's duration is a
        # filter when drawing from the bank, not a target: stretching or
        # compressing a written script to hit a requested number is what the
        # speaking-rate re-fit does, and it is audible.
        #
        # Recomputed from the MEASURED speaking rate rather than taken from
        # the entry. `estimated_seconds` is derived at import from a static
        # per-group table, and the table is optimistic: it assumes 2.0 words
        # per second for kids while edge-tts measurably delivers 3.21, so a
        # 135-word story was recorded as 68 seconds and read in 43. The stored
        # value is still the right thing to FILTER on - it is stable and
        # voice-independent - but the request duration has to match what this
        # voice will actually produce.
        entry_seconds = int(round(claim.entry.estimated_seconds))
        try:
            spec = self.voice_engine.voice_spec(
                request.language, "energetic", gender=request.voice_gender)
            measured = self.calibrated_words_per_second(
                request.language, spec, 0.0)
        except Exception:                       # noqa: BLE001
            measured = 0.0
        if measured > 0 and claim.entry.word_count:
            calibrated = int(round(claim.entry.word_count / measured))
            if calibrated and abs(calibrated - entry_seconds) > 2:
                log_event("BANK", "duration recomputed at the measured rate",
                          entry=claim.entry_id,
                          from_table=f"{entry_seconds}s",
                          measured=f"{measured:.2f} wps",
                          using=f"{calibrated}s")
            entry_seconds = calibrated or entry_seconds
        if entry_seconds and entry_seconds != request.duration_seconds:
            log_event("BANK", "duration taken from the banked script",
                      requested=request.duration_seconds, using=entry_seconds,
                      entry=claim.entry_id)
            request.duration_seconds = entry_seconds

        # THE ENTRY'S OWN CLASSIFICATION WINS, and this one is a safety
        # setting rather than a preference.
        #
        # A group-wide kids entry could be claimed by a request that did not
        # say made_for_kids - a custom topic like "little tales" resolves to
        # the kids group and claims a child-directed script, while the
        # request's flag stayed False. The niche profile was then built as
        # general-audience education, so the video lost the stricter kids
        # safety profile AND published without the Made-for-Kids
        # classification. Confirmed by probe.
        #
        # Only ever tightened here. An entry that is not child-directed must
        # not clear a flag the operator set deliberately.
        if claim.entry.made_for_kids and not request.made_for_kids:
            log_event("BANK", "classification taken from the banked script",
                      entry=claim.entry_id, made_for_kids=True,
                      note="the entry is child-directed, so this video is")
            request.made_for_kids = True

        job.request = request.to_dict()
        safe_write_json(Path(job.dir) / "bank_entry.json",
                        claim.entry.to_dict())
        return claim

    # Scene roles a thumbnail may be cut from, in preference order. The hook
    # is what the video is about and the payoff is where it lands; the middle
    # is worked examples and boundaries, which illustrate a step rather than
    # the subject.
    THUMBNAIL_ROLES = ("hook", "payoff")

    def _thumbnail_sources(self, script: Script) -> list[Path]:
        """Which scene assets the thumbnail may be built from.

        REPRESENTATIVE scenes, not all of them. `_frame_interest` scores
        visual busyness - edge energy and colour - so scanning every scene of
        a 72-scene explainer finds the busiest frame in the video rather than
        the most relevant one. Measured: a finance explainer whose own briefs
        asked for coins, jars and fact sheets got a thumbnail of men weaving
        baskets in a village room, because that clip was the only crowded
        frame among seventy clean desks.

        Falls back to everything when no scene carries a preferred role, so a
        shape whose beats map differently still gets a thumbnail.
        """
        scenes = [s for s in script.scene_objects()
                  if s.asset_path and Path(s.asset_path).exists()]
        preferred = [Path(s.asset_path) for s in scenes
                     if s.role in self.THUMBNAIL_ROLES]
        if preferred:
            log_event("THUMBNAIL", "base restricted to representative scenes",
                      considering=len(preferred), of=len(scenes),
                      roles=",".join(self.THUMBNAIL_ROLES))
            return preferred
        return [Path(s.asset_path) for s in scenes]

    def _release_bank(self, claim, exc: BaseException,
                      job: VideoJob | None = None) -> None:
        """Return a claimed entry to the pool, never masking the real error.

        REFUSES once the video exists on YouTube. `stage_publish` is the last
        statement in run()'s try block, so an exception in its own bookkeeping
        - writing upload_result.json, recording analytics - lands in the
        handler AFTER the upload has succeeded. Releasing then puts the script
        back in the pool for a later render to claim and publish a second
        time, which is the one outcome a variety gate cannot undo.
        """
        if claim is None:
            return
        if job is not None:
            published = (getattr(job, "youtube_video_id", "")
                         or job.status in (JobStatus.PUBLISHED.value,
                                           JobStatus.SCHEDULED.value))
            if published:
                log_event("BANK", "keeping the entry used - it has already "
                                  "published",
                          entry=claim.entry_id, job=job.job_id,
                          video=getattr(job, "youtube_video_id", "") or "-",
                          status=job.status, error=str(exc)[:120])
                return
        try:
            from .content import bank_use
            bank_use.release(self.db, claim, reason=str(exc)[:160])
        except Exception as inner:              # noqa: BLE001
            # A failure here must not replace the exception being handled -
            # that would turn "TTS timed out" into "database is locked" and
            # send the diagnosis in entirely the wrong direction.
            log_event("BANK", "could not release the claimed entry",
                      entry=getattr(claim, "entry_id", "?"),
                      error=str(inner)[:120])

    def _banked_idea(self, job: VideoJob, claim,
                     videos: list[ResearchVideo]) -> tuple[ContentIdea, str]:
        """The idea a claimed entry already decided.

        Skipping the generation call is the point: it is one of the three
        independent LLM views of the topic whose disagreement is what made the
        image prompts drift from the script.

        Research still runs and is still written to the artifacts - it feeds
        title patterns at the metadata stage - it just no longer chooses the
        subject.
        """
        from .content import bank_use
        clusters = cluster_videos(videos)
        gaps = find_gaps(clusters, videos)
        context = research_context_block(videos, clusters, gaps)

        best = bank_use.to_idea(claim.entry)
        self._advance(job, JobStatus.IDEA, f"banked: {best.topic[:50]}")
        safe_write_json(Path(job.dir) / "idea.json", {
            "selected": best.to_dict(),
            "source": f"bank:{claim.entry_id}",
            "all_candidates": [],
            "clusters": [c.to_dict() for c in clusters],
            "gaps": [g.to_dict() for g in gaps],
        })
        job.idea = best.to_dict()
        log_event("IDEA", "taken from the script bank",
                  title=best.working_title[:70], entry=claim.entry_id)
        return best, context

    def stage_idea(self, job: VideoJob, request: AutomationRequest, profile,
                   videos: list[ResearchVideo],
                   claim=None) -> tuple[ContentIdea, str]:
        if claim is not None:
            return self._banked_idea(job, claim, videos)

        # ---- live generation, unchanged from before the bank existed ----
        self._advance(job, JobStatus.IDEA, "generating concepts")
        hints = self.learner.hints()
        clusters = cluster_videos(videos)
        gaps = find_gaps(clusters, videos)
        context = research_context_block(videos, clusters, gaps)

        ideas = self._retry("idea", lambda: self.idea_engine.generate(
            request.niche, profile, videos, clusters, gaps,
            research_context=context, strategy_hints=hints), job)
        if not ideas:
            raise PipelineError("idea", "no usable ideas produced")

        best = ideas[0]
        safe_write_json(Path(job.dir) / "idea.json", {
            "selected": best.to_dict(),
            "all_candidates": [i.to_dict() for i in ideas],
            "clusters": [c.to_dict() for c in clusters],
            "gaps": [g.to_dict() for g in gaps],
        })
        job.idea = best.to_dict()
        self.db.mark_idea_used(best.idea_id)
        log_event("IDEA", "concept selected",
                  title=best.working_title[:70],
                  score=f"{best.opportunity_score:.1f}",
                  hook_type=best.hook_type)
        return best, context

    def _finish_script(self, job: VideoJob, request: AutomationRequest,
                       profile, script: Script, *,
                       improve: bool, extra: dict[str, Any]) -> Script:
        """The tail both script paths share: disclaimer, score, save.

        Factored out so the mandatory disclaimer cannot be applied on one
        path and forgotten on the other - it was two near-identical blocks,
        which is exactly how that happens.

        `improve` is off for a banked script: auto_improve rewrites narration
        to hit a retention target, and rewriting a scene invalidates that
        scene's authored caption and authored image brief.
        """
        from .content import disclaimer

        # Before the retention pass, so the score describes the video that
        # will actually be rendered rather than the one without the opener.
        added = disclaimer.apply(
            script, profile, language=request.language,
            caption_language=self._caption_language(request))

        report = analyze_retention(script, profile,
                                   target_duration=request.duration_seconds)
        applied: list[str] = []
        if improve:
            script, applied = auto_improve(script, profile, report)
            if applied:
                report = analyze_retention(
                    script, profile,
                    target_duration=request.duration_seconds)
        script.retention_score = report.score
        script.retention_notes = report.notes

        self.db.save_script(script, sha1(script.script))
        safe_write_json(Path(job.dir) / "script.json", {
            **script.to_dict(),
            "retention_report": report.to_dict(),
            "auto_improvements": applied,
            "disclaimer": disclaimer.family_for(profile) if added else "",
            **extra,
        })
        job.script = script.to_dict()
        self.db.save_job(job)
        return script

    def stage_script(self, job: VideoJob, request: AutomationRequest, profile,
                     idea: ContentIdea, context: str, claim=None) -> Script:
        if claim is not None:
            return self._banked_script(job, request, profile, claim)

        self._advance(job, JobStatus.SCRIPT, idea.topic[:60])
        hints = self.learner.hints()

        # Budget words against what this voice has actually been measured
        # delivering, not the niche profile's guess. The guess ran ~20% fast,
        # which is how a "45 second" request became a 53-second recording that
        # then had to be sped up by 28% to fit.
        spec = self.voice_engine.voice_spec(
            request.language, "energetic",
            gender=request.voice_gender)
        measured = self.calibrated_words_per_second(
            request.language, spec, profile.words_per_second)
        if abs(measured - profile.words_per_second) > 0.05:
            log_event("SCRIPT", "using measured speech rate for the word budget",
                      profile=f"{profile.words_per_second:.2f} wps",
                      measured=f"{measured:.2f} wps")
            profile.words_per_second = measured

        # Take the mandatory disclaimer out of the budget rather than adding
        # it on top: a "45 second" finance short otherwise lands at 55.
        # Floored at 60% of the request so a very short video cannot have its
        # entire body squeezed out by the opener.
        from .content import disclaimer
        overhead = disclaimer.seconds_for(profile, request.language)
        body_seconds = request.duration_seconds
        if overhead > 0:
            body_seconds = max(int(request.duration_seconds * 0.6),
                               int(request.duration_seconds - overhead))
            log_event("SCRIPT", "reserved time for the mandatory disclaimer",
                      requested=request.duration_seconds,
                      disclaimer=f"{overhead:.1f}s", writing_for=body_seconds)

        script = self._retry("script", lambda: self.script_engine.generate(
            idea, profile, duration=body_seconds,
            language=request.language, video_format=request.video_format,
            research_context=context, strategy_hints=hints), job)

        # Retention pass + safe auto-improvement (spec section 15).
        return self._finish_script(job, request, profile, script,
                                   improve=True, extra={})

    def _banked_script(self, job: VideoJob, request: AutomationRequest,
                       profile, claim) -> Script:
        """Assemble the Script from a claimed entry.

        No generation, no auto_improve. The retention score is still measured
        because it is useful to know, but it no longer edits: rewriting a
        scene's narration would silently invalidate that scene's authored
        caption and authored image brief, which is precisely the drift the
        bank removes.
        """
        from .content import bank_use
        entry = claim.entry
        self._advance(job, JobStatus.SCRIPT, f"banked: {entry.title[:50]}")

        script = bank_use.to_script(
            entry, language=request.language,
            caption_language=self._caption_language(request))
        script.idea_id = job.idea.get("idea_id", "") if job.idea else ""

        script = self._finish_script(job, request, profile, script,
                                     improve=False,
                                     extra={"bank": claim.to_dict()})
        log_event("SCRIPT", "assembled from the script bank",
                  entry=claim.entry_id, scenes=len(script.scenes),
                  words=count_words(script.script),
                  captions="authored" if bank_use.has_authored_captions(entry)
                           else "will be translated",
                  retention=f"{script.retention_score:.0f}/100")
        return script

    # ------------------------------------------------------------------
    SPEECH_RATE_KEY = "measured_words_per_second"

    def _speech_rate_key(self, language: str, spec) -> str:
        return f"{language or 'en'}|{getattr(spec, 'voice_id', '') or 'default'}"

    def _record_speech_rate(self, language: str, spec, script: Script,
                            total: float) -> None:
        """Blend the measured words-per-second into a stored average.

        Deliberately a slow-moving average with a small weight: one script full
        of long words should nudge the estimate, not redefine it. Only recorded
        at the base speaking rate, because a re-fitted clip was deliberately
        sped up or slowed down and says nothing about natural pace.
        """
        from .core.util import count_words
        if total <= 1.0:
            return
        words = count_words(script.script)
        if words < 20:
            return
        measured = words / total
        if not 0.8 <= measured <= 6.0:       # implausible: ignore rather than poison
            return
        key = self._speech_rate_key(language, spec)
        try:
            store = self.db.get_setting(self.SPEECH_RATE_KEY, {}) or {}
            if not isinstance(store, dict):
                store = {}
            previous = store.get(key)
            blended = measured if previous is None else previous * 0.7 + measured * 0.3
            store[key] = round(blended, 3)
            self.db.set_setting(self.SPEECH_RATE_KEY, store)
        except Exception as exc:            # never fail a job over telemetry
            log_event("TTS", "could not store measured speech rate",
                      error=str(exc)[:120])
            return
        log_event("TTS", "measured speech rate recorded", voice=key,
                  measured=f"{measured:.2f} wps",
                  stored=f"{blended:.2f} wps")

    def calibrated_words_per_second(self, language: str, spec,
                                    fallback: float) -> float:
        """The stored measurement for this voice, or the profile's guess."""
        try:
            store = self.db.get_setting(self.SPEECH_RATE_KEY, {}) or {}
            value = float(store.get(self._speech_rate_key(language, spec), 0.0))
        except Exception:
            return fallback
        return value if 0.8 <= value <= 6.0 else fallback

    def stage_voice(self, job: VideoJob, request: AutomationRequest, profile,
                    script: Script) -> tuple[Path, float, list]:
        # Pick the voice from the language the SCRIPT is actually written in,
        # not from the language that was requested.
        #
        # They can disagree: when the LLM is rate limited the English
        # structural template writes the script, and a Hindi voice reading
        # English prose is grotesque - it spelled "smartest" as "smart-a-s-t",
        # read "2024" in Hindi digits, and pronounced "Kids-Invents" letter by
        # letter. Fixing the fallback is the real cure; this makes the
        # mismatch inaudible either way.
        from .tts.engine import language_for_text
        spoken = "\n".join(s.narration for s in script.scene_objects())
        voice_language = language_for_text(spoken, request.language)
        if voice_language != request.language:
            log_event("VOICE", "script language differs from the request, "
                      "voicing what was actually written",
                      requested=request.language, using=voice_language)
        self._advance(job, JobStatus.VOICE, f"lang={voice_language}")
        job_dir = Path(job.dir)
        scenes = script.scene_objects()
        spec = self.voice_engine.voice_spec(
            voice_language, script.voice_style,
            gender=request.voice_gender)

        def synthesize(rate: str | None = None):
            if rate is not None:
                spec.rate = rate
            clips = self.voice_engine.synthesize_scenes(
                scenes, job_dir / "voice_scenes", spec)
            total, offsets = self.voice_engine.concat(clips, job_dir / "voice.wav")
            return clips, total, offsets

        clips, total, offsets = self._retry("voice", synthesize, job)

        # Calibrate the words-per-second estimate from what the voice actually
        # delivered, BEFORE any re-fit: a re-fitted clip was deliberately sped
        # up or slowed down, so it says nothing about natural pace.
        #
        # `words_per_second` in the niche profile is a guess, and it was
        # consistently optimistic: a "45s" Short came out at 53s because 127
        # words were budgeted at 2.9 wps when edge-tts delivered 2.38. The
        # correction was then made by speaking 28% faster, which is the wrong
        # lever - the right one is to budget fewer words next time.
        #
        # Measure against SPEECH time, not the assembled timeline. `total`
        # includes the 0.16s breath gap `concat` inserts between every scene,
        # so with 19 scenes that is 2.9 seconds of silence counted as speaking.
        # More scenes then meant a lower apparent rate, a smaller word budget,
        # and more scenes again - a feedback loop that walked the stored rate
        # from 2.43 down to 1.84 wps over a handful of runs.
        speech_seconds = sum(c.duration for c in clips) or total
        self._record_speech_rate(voice_language, spec, script, speech_seconds)

        # ---- duration re-fit -------------------------------------------
        #
        # A BANKED SCRIPT HAS NO TARGET. Its length is whatever a person
        # wrote, and the requested duration was only ever a filter for
        # choosing it - so there is nothing to correct toward. Measured on a
        # real run: a 135-word story recorded in 42.9s against a 68s
        # "target", and the re-fit slowed the voice by 12% to stretch it,
        # producing a 52s video read unnaturally slowly. Distorting a
        # human-written script to reach a number the number was never meant
        # to be is strictly worse than a video that is 43 seconds long.
        if (script.provider or "").startswith("bank:"):
            log_event("TTS", "no duration re-fit for a banked script",
                      measured=f"{total:.1f}s",
                      requested=f"{request.duration_seconds}s",
                      note="the written script's own length is the duration")
            request.duration_seconds = int(round(total))
        target = float(request.duration_seconds)
        tolerance = float(self.cfg.get("quality.duration_tolerance_pct", 25)) / 100.0
        drift = (total - target) / target if target > 0 else 0.0
        # The trigger used to be 80% of the quality tolerance, i.e. ~20% drift,
        # on the theory that re-synthesising costs a meaningful share of a short
        # job. Measured, it does not: TTS for a 14-scene Short is ~50s of a
        # ~7-minute job, and for long-form ~4 of 65 minutes. Meanwhile the loose
        # trigger let real misses through - a "45s" Short delivered at 49.3s
        # (+9.6%) and a "240s" long-form at 256s (+6.7%). Both were requested
        # precisely, so both are wrong. One cheap extra pass is the better deal.
        trigger = float(self.cfg.get("quality.refit_pct", 8)) / 100.0
        if request.video_format == "LONGFORM":
            trigger = float(self.cfg.get("quality.longform_refit_pct", 7)) / 100.0
        # Never let the trigger exceed what the gate would fail anyway.
        trigger = min(trigger, tolerance * 0.8)

        # Re-fitting works by changing the SPEAKING RATE, so it only helps if
        # the provider that actually voiced the scenes can honour one. gTTS
        # cannot - it has no rate parameter - and Piper as invoked here cannot
        # either. Re-synthesising with those is pure cost for an identical
        # result: on a fresh install where edge-tts had failed and gTTS took
        # over, the pipeline "corrected" a 63.3s recording by +16% and got
        # 63.3s back, then shipped a 41% duration miss. Say so instead.
        used = {c.provider for _, c in offsets} or {"unknown"}
        rate_capable = self.voice_engine.can_change_rate(used)
        if abs(drift) > trigger and not rate_capable:
            log_event("TTS", "duration is off but the voice cannot be re-fitted",
                      providers=",".join(sorted(used)),
                      measured=f"{total:.1f}s", target=f"{target:.0f}s",
                      drift=f"{drift * 100:+.0f}%",
                      note="only edge-tts supports a rate change; the quality "
                           "gate will block this on duration")

        if abs(drift) > trigger and rate_capable:
            # Correct the speaking rate rather than shipping a mistimed video.
            # edge-tts rate is a percentage delta on the base speed.
            base = _parse_rate(spec.rate)
            # Clamped to what still sounds like a person talking. The old
            # bounds allowed +42%, and a real run shipped at +35% - measurably
            # rushed. If the correction needed is larger than this the script
            # is the wrong length, and the duration check (now blocking) should
            # catch it rather than the delivery hiding it.
            lo = float(self.cfg.get("tts.rate_floor", 0.88))
            hi = float(self.cfg.get("tts.rate_ceiling", 1.16))
            wanted = (1.0 + base / 100.0) * (total / target)
            needed = clamp(wanted, lo, hi)
            if abs(wanted - needed) > 0.01:
                log_event("TTS", "rate correction clamped to stay natural",
                          wanted=f"{(wanted - 1) * 100:+.0f}%",
                          using=f"{(needed - 1) * 100:+.0f}%",
                          note="script length is the real problem here")
            new_rate = f"{(needed - 1.0) * 100:+.0f}%"
            log_event("TTS", "re-fitting narration to target duration",
                      measured=f"{total:.1f}s", target=f"{target:.0f}s",
                      old_rate=spec.rate, new_rate=new_rate)
            try:
                clips, total, offsets = synthesize(new_rate)
            except Exception as exc:
                log_event("TTS", "re-fit failed, keeping original narration",
                          error=str(exc)[:160])


        # Record measured per-scene timings back onto the script.
        durations: list[float] = []
        for i, (offset, clip) in enumerate(offsets):
            nxt = offsets[i + 1][0] if i + 1 < len(offsets) else total
            span = nxt - offset
            durations.append(span)
            if clip.scene_index < len(scenes):
                scenes[clip.scene_index].start = offset
                scenes[clip.scene_index].duration = span
        script.scenes = [s.to_dict() for s in scenes]
        script.estimated_duration = round(total, 2)
        job.script = script.to_dict()
        job.voice_path = str(job_dir / "voice.wav")
        self.db.save_job(job)
        return job_dir / "voice.wav", total, offsets

    def _caption_language(self, request: AutomationRequest) -> str:
        """The caption language, or "" to follow the narration.

        DERIVED, not chosen. "why is it required? caption should be just what
        is in audio" was about not having to pick one per automation, and the
        standing rule is "for hindi voice it should be english caption and for
        english voice hindi captions" - so languages.py decides and the Create
        screen has no control for it.

        An explicit `request.caption_language` still wins, because the field
        is on stored automations and the API accepts it; it is just not
        something the app asks for any more.
        """
        if not bool(self.cfg.get("captions.translate", True)):
            return ""
        from .core.languages import caption_for
        from .content.translate import needs_translation
        wanted = (getattr(request, "caption_language", "") or "").strip()
        if not wanted:
            wanted = str(self.cfg.get("captions.language", "") or "").strip()
        if not wanted:
            wanted = caption_for(request.language)
        return wanted if needs_translation(request.language, wanted) else ""

    def _translate_captions(self, job_dir: Path, request: AutomationRequest,
                            script: Script) -> None:
        """Fill Scene.caption_text when the caption language differs.

        Degrades rather than fails: losing the second language is a
        disappointment, losing the video over it is not a trade worth making.
        """
        target = self._caption_language(request)
        if not target:
            return
        from .content.translate import dump, translate_scenes
        scenes = script.scene_objects()
        filled = translate_scenes(scenes, target=target, router=self.router)
        if not filled:
            return
        script.scenes = [s.to_dict() for s in scenes]
        safe_write_json(job_dir / "captions_translated.json", dump(scenes))

    def _character_bible(self, job_dir: Path, script: Script, claim=None):
        """The recurring cast, or None when it would not be used.

        Returns None rather than an empty bible when disabled, so the visual
        engine can skip the per-scene lookup entirely.
        """
        if not bool(self.cfg.get("visuals.character_bible", True)):
            return None
        if not bool(self.cfg.get("visuals.prefer_ai", False)):
            # Stock photography cannot honour a character description, so
            # asking for one is a wasted call.
            return None
        if claim is not None:
            # A banked entry DECLARES its cast, so inferring it from the
            # narration is both a wasted call and strictly worse: inference
            # can miss a character the author named, and the description it
            # invents is not the one the image briefs were written against.
            from .content import bank_use
            bible = bank_use.bible_for(claim.entry)
            if bible:
                bible.save(job_dir / "character_bible.json")
                log_event("VISUALS", "cast taken from the banked script",
                          characters=len(bible.characters),
                          entry=claim.entry_id)
                return bible
            return None
        from .content.characters import build_bible
        scenes = script.scene_objects()
        narration = "\n".join(f"{i}. {s.narration}" for i, s in enumerate(scenes))
        bible = build_bible(narration, self.router)
        if bible:
            bible.save(job_dir / "character_bible.json")
        return bible or None

    # How a beat is covered when it is long enough for more than one shot.
    #
    # Framings of the SAME moment, in an order that reads: establish it, get
    # closer, then the detail that matters. Deliberately not "a different
    # scene" - the narration has not moved on, so neither has the subject.
    SHOT_SCALES = (
        "",                                         # the brief as written
        "closer medium shot, same moment, shallow depth of field",
        "detail insert, hands and the object, close up",
        "wider angle of the same moment, low camera",
    )

    def _plan_shots(self, scenes: list[Scene]) -> list[tuple[Scene, list[Scene]]]:
        """Decide how many images each beat gets, and describe each one.

        The count comes from the beat's MEASURED duration, so a short beat
        keeps its single image and only the long ones are broken up. A shot
        shorter than `video.shot_min_seconds` reads as a flicker, which is
        the opposite failure to the one being fixed.
        """
        floor = float(self.cfg.get("video.shot_min_seconds", 2.0))
        cap = max(1, int(self.cfg.get("video.shots_per_scene_max", 3)))
        cycle = self._motion_cycle or MOTION_CYCLE
        plan: list[tuple[Scene, list[Scene]]] = []
        index = 0
        for scene in scenes:
            span = float(scene.duration or 0.0)
            wanted = 1 if span <= 0 else max(1, min(cap, int(span // floor)))
            shots: list[Scene] = []
            for shot in range(wanted):
                suffix = self.SHOT_SCALES[shot % len(self.SHOT_SCALES)]
                brief = scene.visual_prompt or scene.narration
                shots.append(Scene(
                    # A FRESH index per shot: the image seed is derived from
                    # it, so without this every shot of a beat would be the
                    # same picture generated three times.
                    index=index,
                    narration=scene.narration,
                    visual_prompt=f"{brief}, {suffix}" if suffix else brief,
                    visual_keywords=list(scene.visual_keywords),
                    on_screen_text=scene.on_screen_text if shot == 0 else "",
                    role=scene.role,
                    caption_text=scene.caption_text,
                    duration=(span / wanted) if span > 0 else 0.0,
                    # Alternating moves, so consecutive shots of one beat do
                    # not all drift the same way.
                    motion=cycle[index % len(cycle)],
                ))
                index += 1
            plan.append((scene, shots))
        total = sum(len(s) for _, s in plan)
        if total > len(scenes):
            log_event("VISUAL", "covering beats with several shots",
                      beats=len(scenes), shots=total,
                      mean_hold=f"{sum(float(s.duration or 0) for s in scenes) / max(total, 1):.1f}s")
        return plan

    def _collect_shots(self, plan: list[tuple[Scene, list[Scene]]]) -> None:
        """Copy the generated paths back onto the real scenes."""
        for scene, shots in plan:
            paths = [s.asset_path for s in shots if s.asset_path]
            if not paths:
                continue
            scene.asset_path = paths[0]
            scene.extra_assets = paths[1:]
            # The first shot's motion becomes the scene's, for anything that
            # still reads one motion per scene.
            scene.motion = shots[0].motion

    def stage_visuals(self, job: VideoJob, request: AutomationRequest, profile,
                      script: Script, claim=None) -> list:
        self._advance(job, JobStatus.VISUALS, f"scenes={len(script.scenes)}")
        job_dir = Path(job.dir)
        scenes = script.scene_objects()
        assign_motion(scenes, self._motion_cycle)
        w, h = self.composer.resolution(request.video_format)

        # Fix the cast before any image is generated.
        #
        # Only worth the extra LLM call when the images are drawn: a stock
        # photograph is not going to honour a character description, so for
        # factual content this is pure cost. Built once and reused for every
        # scene, because the entire point is that the descriptions do not
        # change between shots.
        bible = self._character_bible(job_dir, script, claim)

        # MORE PICTURES PER BEAT, same narration timing.
        #
        # A beat is six to nine seconds of speech, and one still held for all
        # of it is a slideshow: measured on a real Short, 7 images over 51.8
        # seconds, 7.4s each. The beat keeps its span and its brief; it is
        # now covered by two or three FRAMINGS of the same moment - a wide,
        # a medium, a detail - which is what the channels this imitates do.
        #
        # Built as extra scenes before generation rather than in a second
        # pass, so the existing thread pool, per-shot seeding and provider
        # fallback all apply unchanged.
        shot_plan = self._plan_shots(scenes)
        to_generate = [shot for _, shots in shot_plan for shot in shots]

        assets = self._retry("visuals", lambda: self.visual_engine.generate(
            # The ART DIRECTION, not the tone.
            #
            # This passed `request.style`, which is one of the six phrases in
            # the app's Style dropdown - "fast-paced, curiosity-driven",
            # "gentle and simple (for young children)". Those describe PACING
            # and are the right input for the script writer. None of them
            # describes a picture, and every one of them landed in the
            # dedicated art-direction slot of every image prompt, diluting the
            # template's own look with a phrase the model cannot draw.
            #
            # `profile.visual_style` is what the template set for exactly this
            # purpose (templates.py apply_to_profile).
            to_generate, job_dir / "assets", style=profile.visual_style,
            made_for_kids=profile.made_for_kids, width=w, height=h,
            durations=[s.duration or 0.0 for s in to_generate],
            bible=bible), job)

        self._collect_shots(shot_plan)
        script.scenes = [s.to_dict() for s in scenes]
        job.script = script.to_dict()
        job.assets = [a.to_dict() for a in assets]
        self.db.save_assets(job.job_id, assets)
        self.db.save_job(job)

        # Rewrite script.json with the measured timings and the chosen
        # assets. It was written at the script stage, before the voice was
        # measured and before any image existed, so every scene on disk read
        # `duration: 0.0` and `asset_path: ""` - the one artifact somebody
        # opens to ask "why does scene 3 not match its picture" could not
        # answer the question.
        existing = read_json(job_dir / "script.json", {}) or {}
        safe_write_json(job_dir / "script.json",
                        {**existing, **script.to_dict()})
        return assets

    def stage_render(self, job: VideoJob, request: AutomationRequest, profile,
                    script: Script, voice: Path, total: float,
                    offsets: list) -> Path:
        self._advance(job, JobStatus.RENDERING, f"{total:.1f}s")
        job_dir = Path(job.dir)
        w, h = self.composer.resolution(request.video_format)
        scenes = script.scene_objects()

        # ---- captions ---------------------------------------------------
        # A per-video choice beats a global default here: the same channel
        # wants karaoke on a Short and nothing at all on a long-form narration
        # piece, which is what the reference videos do.
        requested_style = (getattr(request, "caption_style", "") or "").strip()
        caption_style = requested_style or (
            profile.caption_style
            if self.cfg.get("captions.style") == "karaoke"
            else str(self.cfg.get("captions.style")))
        # Captions in a DIFFERENT language from the narration, when asked for.
        #
        # Word-level karaoke is impossible here: the timings come from the
        # synthesiser and describe the words the VOICE says, so translated text
        # has no per-word timing. One block per scene, on the span the scene
        # actually occupies.
        # Captions off: no burn-in, but the render still has to happen.
        #
        # This block used to `return` here, which exited stage_render - whose
        # contract is to return the finished video Path - so choosing "no
        # captions" produced no video at all. It sets ass_path to None and
        # falls through instead; finalize() already treats None as "do not
        # burn subtitles".
        ass_path: Path | None = None
        srt_path = job_dir / "captions.srt"
        groups = 0
        if caption_style == "none":
            log_event("CAPTION", "captions off for this video",
                      reason="requested" if requested_style else "configured")
            # The SRT is still written and uploaded, so viewers can turn
            # captions on and the video is still indexed on its words.
            srt_path.write_text(self.caption_engine.srt_only(offsets),
                                encoding="utf-8")
            job.subtitle_path = str(srt_path)

        caption_language = self._caption_language(request)
        # Translate here rather than at the script stage: the blocks are timed
        # to scene spans, and scene.start/duration are only filled in once the
        # voice has been measured.
        #
        # Not when captions are off: `scene.caption_text` is read by nothing
        # else, so on a captions-off template every translation call was made
        # and then discarded - a per-scene LLM round trip per video, bought
        # and thrown away.
        if caption_language and caption_style != "none":
            self._translate_captions(job_dir, request, script)
        scenes_now = script.scene_objects()
        translated = [
            (scene.start, scene.start + scene.duration,
             getattr(scene, "caption_text", ""))
            for scene in scenes_now
            if getattr(scene, "caption_text", "").strip()
        ]
        # COVERAGE, not "any". A single captioned scene must not commit the
        # whole video to the block path.
        #
        # The disclaimer scene always carries a caption, so on a live
        # finance or health render `translated` was never empty even when the
        # translation failed completely - and the video then shipped with one
        # cue, the disclaimer, and every other scene uncaptioned. Before the
        # bank work a failed translation left every caption empty, so the
        # karaoke fallback took over and the video was captioned in the
        # narration language, which is the correct degradation.
        covered = (len(translated) / len(scenes_now)) if scenes_now else 0.0
        enough = covered >= float(
            self.cfg.get("captions.translated_coverage_floor", 0.6))
        if caption_language and translated and not enough:
            log_event("CAPTION", "too few scenes translated; falling back to "
                                 "captions in the narration language",
                      translated=len(translated), scenes=len(scenes_now),
                      covered=f"{covered:.0%}")
        if caption_style != "none":
            if caption_language and translated and enough:
                ass_path, srt_path, groups =                     self.caption_engine.build_translated(
                        translated, job_dir / "captions.ass",
                        job_dir / "captions.srt", w, h,
                        language=caption_language)
            else:
                ass_path, srt_path, groups = self.caption_engine.build(
                    offsets, job_dir / "captions.ass",
                    job_dir / "captions.srt", w, h,
                    style_override=caption_style, language=request.language)
            job.subtitle_path = str(srt_path)

        # ---- music + sfx ------------------------------------------------
        # Both are switchable. A synthesised bed is a taste call, not a
        # requirement: plenty of good Shorts have voice only, and if the pad
        # bothers you, turning it off is a better answer than fighting it.
        boundaries = [off for off, _ in offsets][1:]
        music_path, music_src = None, "none"
        if bool(self.cfg.get("video.music_enabled", True)):
            music_path, music_src = self._retry("music", lambda: build_music(
                total, job_dir / "music.wav", mood_text=profile.music_mood,
                seed=job.job_id), job)
        else:
            log_event("MUSIC", "music disabled in config")

        sfx_path = None
        if bool(self.cfg.get("video.sfx_enabled", True)):
            try:
                sfx_path = build_transition_sfx(
                    boundaries, total, job_dir / "sfx.wav", seed=job.job_id)
            except Exception as exc:
                log_event("MUSIC", "SFX skipped", error=str(exc)[:140])

        # ---- master mix -------------------------------------------------
        master, audio_stats = self._retry("audio", lambda: self.composer.mix_audio(
            voice, job_dir / "master.wav", music=music_path, sfx=sfx_path), job)

        # ---- video ------------------------------------------------------
        # ONE CLIP PER SHOT, not per beat.
        #
        # A beat's measured span is divided between its shots, so the total
        # is unchanged to the millisecond and the audio, the captions and
        # the chapter marks all still line up - they are timed in absolute
        # seconds and know nothing about clips. What changes is how often
        # the picture does.
        motion_cycle = self._motion_cycle or MOTION_CYCLE
        timings: list[SceneTiming] = []
        durations: list[float] = []
        for scene in scenes:
            if scene.duration <= 0:
                continue
            shots = scene.shot_paths()
            if not shots:
                continue
            share = scene.duration / len(shots)
            for offset, path in enumerate(shots):
                durations.append(share)
                timings.append(SceneTiming(
                    index=len(timings), image=Path(path), duration=share,
                    motion=(scene.motion if offset == 0 else
                            motion_cycle[len(timings) % len(motion_cycle)])))
        if not durations:
            raise PipelineError("render", "no measured scene durations")
        if len(timings) != len(durations):        # pragma: no cover - paired above
            raise PipelineError("render",
                                f"{len(durations)} timed shots but "
                                f"{len(timings)} have images")
        timed = sum(s.duration for s in scenes if s.duration > 0)
        if abs(sum(durations) - timed) > 0.05:
            raise PipelineError(
                "render",
                f"the shots total {sum(durations):.2f}s against {timed:.2f}s "
                f"of measured narration - the picture would drift from the "
                f"voice")

        # Hold the opening frame on a SHORT.
        #
        # A custom thumbnail on a Short has been YPP-only since 2026-07-25 and
        # has no API surface at all, so the first frame IS the thumbnail.
        # Holding it briefly means YouTube samples the clean composed still
        # rather than a frame caught partway through the Ken Burns move.
        hold_open = (0.0 if str(request.video_format or "").upper() == "LONGFORM"
                     else float(self.cfg.get("video.short_hold_first_seconds",
                                             0.4)))
        clips = self._retry("render", lambda: self.composer.render_scene_clips(
            timings, job_dir / "clips", w, h,
            hold_first_seconds=hold_open), job)
        result = self._retry("render", lambda: self.composer.finalize(
            clips, durations, master, ass_path, job_dir / "video.mp4", w, h), job)
        cleanup_clips(clips)

        job.video_path = str(result.video)
        target = float(request.duration_seconds)
        safe_write_json(job_dir / "render_report.json", {
            "duration": result.duration, "resolution": f"{w}x{h}",
            "fps": result.fps, "caption_groups": groups,
            "music_source": music_src, "sfx": bool(sfx_path),
            "audio_lufs": audio_stats.get("input_i"),
            "audio_true_peak": audio_stats.get("input_tp"),
            "scene_count": len(timings),
            "motions": [t.motion for t in timings],
            # Recorded so the artifact is self-describing: "42.8s" means
            # nothing without knowing what was asked for.
            "video_format": request.video_format,
            "target_duration": target,
            "duration_error_pct": (round((result.duration - target) / target * 100, 1)
                                   if target > 0 else None),
            "batched_render": len(timings) > self.composer.segment_max,
            "segments": (-(-len(timings) // self.composer.segment_max)
                         if len(timings) > self.composer.segment_max else 1),
        })
        self.db.save_job(job)
        return result.video

    def stage_finalize(self, job: VideoJob, request: AutomationRequest, profile,
                       idea: ContentIdea, script: Script, video: Path,
                       videos: list[ResearchVideo],
                       assets: list) -> tuple[VideoMetadata, QualityReport]:
        self._advance(job, JobStatus.QUALITY_CHECK, "metadata + checks")
        job_dir = Path(job.dir)

        # ---- metadata ---------------------------------------------------
        meta = self._retry("metadata", lambda: self.metadata_engine.build(
            script, idea, profile, video_format=request.video_format,
            language=request.language,
            made_for_kids=profile.made_for_kids or request.made_for_kids,
            synthetic_disclosure=bool(
                self.cfg.get("youtube.synthetic_disclosure", True)),
            # Real titles from this niche, as PATTERN input. The title prompt
            # previously saw no research at all, which is why titles came out
            # generic - it had nothing to be attractive against.
            research=videos), job)

        # ---- thumbnail --------------------------------------------------
        #
        # Not built for SHORTS, deliberately. A custom thumbnail on a Short
        # has been restricted to YouTube Partner Programme channels since
        # 2026-07-25 and there is no API surface for it at all, so building
        # one costs render time to produce a file nothing can upload. For a
        # Short the first frame IS the thumbnail, which is a composition
        # problem rather than an upload one.
        thumbnail: Path | None = None
        shorts = str(request.video_format or "").upper() != "LONGFORM"
        if shorts:
            log_event("THUMBNAIL", "skipped for a Short - custom thumbnails "
                      "are YPP-only with no API surface; the first frame is "
                      "the thumbnail")
        try:
            if shorts:
                raise _ThumbnailNotApplicable
            thumbnail, variants = self.thumbnail_engine.generate(
                title=meta.title, out_dir=job_dir / "thumbnails", video=video,
                # The scene assets, preferred over the rendered video: the
                # render has the captions burnt into it, and a thumbnail cut
                # from it carries the video's subtitle AND its own headline.
                sources=self._thumbnail_sources(script),
                video_format=request.video_format,
                made_for_kids=profile.made_for_kids,
                language=request.language)
            job.thumbnail_path = str(thumbnail)
            safe_write_json(job_dir / "thumbnail_report.json",
                            {"selected": thumbnail.name,
                             "variants": [{"style": v.style, "score": v.score,
                                           "text": v.text, "metrics": v.metrics}
                                          for v in variants]})
        except _ThumbnailNotApplicable:
            pass                                # already logged, not a failure
        except Exception as exc:
            log_event("THUMBNAIL", "generation failed", error=str(exc)[:180])

        # ---- originality + fact check -----------------------------------
        orig = self.originality.check(
            script, idea, videos, assets,
            voice_provider=self.voice_engine.providers[0].name
            if self.voice_engine.providers else "")
        safe_write_json(job_dir / "originality_report.json", orig.to_dict())

        fact = self.factchecker.check(script, profile)
        safe_write_json(job_dir / "factcheck_report.json", fact.to_dict())

        # ---- quality gate -----------------------------------------------
        # A per-run minimum, when one was asked for.
        #
        # The Settings slider displayed a threshold and explained that the
        # backend refuses to upload below it, while the number went only to
        # device preferences and was read by nothing at all. Applied here so
        # the sentence is true. Restored afterwards because the gate is shared
        # across runs on this Pipeline instance.
        requested_minimum = int(getattr(request, "min_quality_score", 0) or 0)
        previous_minimum = self.quality_gate.minimum
        if requested_minimum:
            self.quality_gate.minimum = float(requested_minimum)
        try:
            quality = self.quality_gate.evaluate(
                video=video, metadata=meta, script=script, profile=profile,
                subtitle=(Path(job.subtitle_path) if job.subtitle_path
                          else None),
                thumbnail=thumbnail,
                target_duration=float(request.duration_seconds),
                video_format=request.video_format, originality=orig,
                factcheck=fact)
        finally:
            self.quality_gate.minimum = previous_minimum

        safe_write_json(job_dir / "metadata.json", meta.to_dict())
        safe_write_json(job_dir / "quality_report.json", quality.to_dict())
        job.metadata = meta.to_dict()
        job.quality = quality.to_dict()
        self.db.save_job(job)
        return meta, quality

    def stage_publish(self, job: VideoJob, request: AutomationRequest,
                      meta: VideoMetadata, quality: QualityReport,
                      fact_requires_approval: bool = False) -> dict[str, Any] | None:
        if not quality.passed:
            self._advance(job, JobStatus.REJECTED,
                          f"quality {quality.score:.0f}/100 "
                          f"({len(quality.blockers)} blockers)")
            log_event("PUBLISH", "upload refused by quality gate",
                      score=f"{quality.score:.0f}",
                      minimum=self.quality_gate.minimum,
                      blockers="; ".join(quality.blockers[:3]))
            return None

        # "Publish without asking" has to actually mean it.
        #
        # This was an unconditional OR over the CONFIG default, so
        # `automation.approval_required: true` was a floor rather than a
        # default and no request could ever escape it. Choosing "Auto -
        # publish without asking" in the app did nothing, which is worse than
        # either behaviour because the UI promised something it could not do.
        #
        # Safe to honour because `youtube.force_private` pins every upload to
        # private visibility, so "auto" means "uploaded, private, waiting for
        # you in Studio" - not "live to subscribers unreviewed".
        # Made for Kids needs an explicit human confirmation ONCE PER
        # AUTOMATION, not once per video - see needs_approval().
        kids_confirmed = self.db.automation_has_approved_run(job.automation_id)
        approval_required, reason = needs_approval(
            mode=request.mode,
            config_default_requires_approval=bool(
                self.cfg.get("automation.approval_required", True)),
            made_for_kids=bool(meta.made_for_kids),
            kids_already_confirmed=kids_confirmed,
            fact_requires_approval=bool(fact_requires_approval))

        if approval_required:
            self._advance(job, JobStatus.AWAITING_APPROVAL, reason)
            log_event("PUBLISH", "waiting for human approval", reason=reason,
                      job=job.job_id)
            return None

        return self.publish_now(job, request, meta)

    def publish_now(self, job: VideoJob, request: AutomationRequest,
                    meta: VideoMetadata) -> dict[str, Any]:
        """Actually upload (or schedule). Also used by the approve flow."""
        job_dir = Path(job.dir)
        schedule = (request.publish_mode != "immediate"
                    and (request.frequency != "once" or bool(request.upload_time)))
        # force_private also suppresses SCHEDULING, not just the privacy field.
        # A scheduled insert carries publishAt, and YouTube publishes on that
        # timestamp by itself - so leaving the schedule in place would make the
        # video public regardless of what privacyStatus said at upload.
        if self.uploader.force_private:
            schedule = False
        if schedule and request.upload_time:
            meta.publish_at = self.uploader.resolve_publish_at(
                upload_time=request.upload_time, timezone=request.timezone,
                days=request.days or None)

        # Resolve the channel: an explicit choice, else the niche mapping, else
        # the default. Done here so the log and the job record say which
        # channel a video went to.
        channel_id = (getattr(request, "channel_id", "") or "").strip()
        if not channel_id:
            mapped = self.auth.channels_store.for_niche(
                request.niche, getattr(request, "niche_group", ""))
            channel_id = mapped.channel_id if mapped else ""
        if channel_id:
            log_event("YOUTUBE", "publishing to a specific channel",
                      channel=channel_id, niche=request.niche)

        result = self.uploader.upload(
            video=Path(job.video_path), meta=meta,
            thumbnail=Path(job.thumbnail_path) if job.thumbnail_path else None,
            subtitle=Path(job.subtitle_path) if job.subtitle_path else None,
            schedule=bool(meta.publish_at), channel_id=channel_id)

        safe_write_json(job_dir / "upload_result.json", result.to_dict())
        job.metadata = meta.to_dict()

        if result.dry_run or not result.video_id:
            self._advance(job, JobStatus.READY,
                          "artifacts complete; upload not performed")
        elif meta.publish_at:
            job.youtube_video_id = result.video_id
            job.scheduled_for = meta.publish_at
            self._advance(job, JobStatus.SCHEDULED, meta.publish_at)
            self.db.save_published(job)
            # The file is on YouTube now; the local copy is 19 MB of nothing.
            self.reclaim(job)
        else:
            job.youtube_video_id = result.video_id
            job.published_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._advance(job, JobStatus.PUBLISHED, result.url)
            self.db.save_published(job)
            self.reclaim(job)
        return result.to_dict()

    # ==================================================================
    # Full run
    # ==================================================================
    def apply_style_template(self, request: AutomationRequest, profile):
        """Fold the chosen StyleTemplate into the profile and engine settings.

        A NicheProfile says what the content IS; a StyleTemplate says how the
        video looks and moves (spec section 45). Applying it in one place means
        every downstream stage - script pacing, captions, transitions, motion,
        colour grade, music - reads a single consistent set of numbers.

        Returns (profile, template).
        """
        template = select_template(
            request.niche, request.style,
            made_for_kids=profile.made_for_kids,
            # A half-hour story and a 45-second one want different pacing from
            # the same subject, so the format is part of the choice.
            long_form=str(getattr(request, "video_format", "")).upper()
            == "LONGFORM",
            forced=str(self.cfg.get("video.style_template", "")))
        profile = apply_to_profile(profile, template)

        base_font = int(self.cfg.get("captions.font_size", 112))
        for key, value in caption_overrides(template, base_font).items():
            self.cfg.set(key, value)
        for key, value in video_overrides(template).items():
            self.cfg.set(key, value)
        # Must be applied BEFORE the visual engine is rebuilt below: the
        # provider chain is decided at construction time.
        for key, value in visual_overrides(template).items():
            self.cfg.set(key, value)

        # These cached the previous settings at construction time.
        self.caption_engine = CaptionEngine(self.cfg)
        self.composer = VideoComposer(self.cfg)
        # The visual engine caches its PROVIDER CHAIN, so a template asking
        # for illustration would otherwise be ignored for the whole process.
        self.visual_engine = VisualEngine(self.cfg)
        self._motion_cycle = list(template.motion_cycle)

        log_event("PIPELINE", "style template selected", template=template.name,
                  scene_seconds=template.scene_seconds,
                  captions=template.caption_style,
                  transition=f"{template.transition}/{template.transition_duration}s")
        return profile, template

    def run(self, request: AutomationRequest, *,
            skip_preflight: bool = False) -> PipelineResult:
        # A COPY, because this method WRITES to the request: the banked
        # entry's own duration replaces the requested one, and a
        # child-directed entry tightens made_for_kids. The worker reuses a
        # single request object for every run in `count`, so those writes
        # leaked forward. Measured: run 1 claimed a 37-second Short and run 2
        # - a live fallback once the bank ran dry under bank_first - was
        # written, paced and rendered for 37 seconds instead of the 45 the
        # operator asked for. Copying ends each run's mutations with the run,
        # which is also what keeps the live path unaffected by a bank run
        # beside it.
        request = AutomationRequest.from_dict(request.to_dict())
        job = VideoJob(automation_id=request.id, request=request.to_dict())
        job_dir = self._job_dir(job, request)
        self.db.save_job(job)

        if not skip_preflight:
            problems = self.preflight(request)
            blocking = [p for p in problems if "ffmpeg" in p or "limit" in p
                        or "YOUTUBE_API_KEY" in p]
            if blocking:
                job.error = " | ".join(blocking)
                self._advance(job, JobStatus.FAILED, job.error)
                raise PipelineError("preflight", job.error)

        log_event("PIPELINE", "started", job=job.job_id, niche=request.niche,
                  duration=request.duration_seconds, format=request.video_format,
                  mode=request.mode, dry_run=self.cfg.dry_run)
        started = time.time()

        # INSIDE the try, even though nothing before it can have claimed
        # anything. A bank failure is the one stage failure the operator is
        # most likely to cause - asking for a slot the bank cannot fill - and
        # it raised from outside the handler, so the job row was never
        # advanced. Measured: script_source=bank against an empty bank left
        # status=IDEA and error='' while the raised message said exactly what
        # was missing. The API returns 202 and the app polls a job that will
        # never move or explain itself.
        claim = None
        try:
            # Claimed before research so the entry's own length can replace
            # the requested duration before the niche profile's pacing is
            # used.
            claim = self.stage_bank(job, request)

            # BUILT AFTER THE CLAIM, because the claim can change the request.
            #
            # `stage_bank` tightens `made_for_kids` when the entry it claimed
            # is child-directed, and it replaces the duration with the
            # entry's own. The profile used to be built before all of that,
            # so a kids script claimed by a custom topic was rendered from a
            # general-audience profile: no KIDS_RESTRICTIONS on the image
            # prompts, karaoke captions instead of blocks, and the wrong
            # pacing - while the request said made_for_kids=True and the
            # upload was correctly classified. The video was flagged for
            # children and did not look like it.
            #
            # Preflight still runs first, so a blocked run cannot leave a
            # claimed entry behind.
            profile = build_profile(
                request.niche, audience=request.audience,
                style=request.style, made_for_kids=request.made_for_kids,
                language=request.language,
                duration_seconds=request.duration_seconds)
            profile, template = self.apply_style_template(request, profile)
            safe_write_json(job_dir / "niche_profile.json", {
                **profile.to_dict(),
                "style_template": template.to_dict(),
            })

            videos = self.stage_research(job, request, profile)
            idea, context = self.stage_idea(job, request, profile, videos,
                                            claim)
            script = self.stage_script(job, request, profile, idea, context,
                                       claim)
            voice, total, offsets = self.stage_voice(job, request, profile, script)
            assets = self.stage_visuals(job, request, profile, script, claim)
            video = self.stage_render(job, request, profile, script, voice,
                                      total, offsets)
            meta, quality = self.stage_finalize(job, request, profile, idea,
                                                script, video, videos, assets)
            fact = read_json(job_dir / "factcheck_report.json", {}) or {}
            uploaded = self.stage_publish(
                job, request, meta, quality,
                fact_requires_approval=bool(fact.get("requires_approval")))
        except JobCancelled as exc:
            # `_raise_if_cancelled` has already written CANCELLED, so there is
            # no status to advance - but the claim still has to go back, or
            # cancelling a render silently consumes a curated script.
            self._release_bank(claim, exc, job)
            raise
        except PipelineError as exc:
            # The script was fine; something after it was not. Put the entry
            # back rather than burning a curated script on a transient TTS or
            # ffmpeg failure - unlike a generated script it cannot be
            # reproduced on demand.
            self._release_bank(claim, exc, job)
            # A stage that raises without stamping job.error used to leave the
            # row blank. The exception text is the only description of the
            # failure that exists, so it becomes the error when nothing more
            # specific was recorded.
            job.error = job.error or str(exc)[:500]
            self._advance(job, JobStatus.FAILED, job.error)
            raise
        except Exception as exc:
            self._release_bank(claim, exc, job)
            job.error = str(exc)[:500]
            self._advance(job, JobStatus.FAILED, job.error)
            raise PipelineError("pipeline", str(exc)) from exc

        elapsed = time.time() - started
        artifacts = {p.name: str(p) for p in sorted(job_dir.iterdir())
                     if p.is_file()}
        safe_write_json(job_dir / "job.json", job.to_dict())
        log_event("PIPELINE", "finished", job=job.job_id, status=job.status,
                  quality=f"{quality.score:.0f}/100",
                  seconds=f"{elapsed:.0f}")
        return PipelineResult(job=job, artifacts=artifacts, quality=quality,
                              uploaded=uploaded)

    # ==================================================================
    # Approval / analytics entry points
    # ==================================================================
    def approve(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if job is None:
            raise PipelineError("approve", f"unknown job {job_id}")
        if job.status != JobStatus.AWAITING_APPROVAL.value:
            raise PipelineError(
                "approve", f"job {job_id} is {job.status}, not awaiting approval")
        request = AutomationRequest.from_dict(job.request or {})
        meta = VideoMetadata.from_dict(job.metadata or {})
        log_event("APPROVAL", "approved by user", job=job_id)
        return self.publish_now(job, request, meta)

    def reject(self, job_id: str, reason: str = "") -> VideoJob:
        job = self.db.get_job(job_id)
        if job is None:
            raise PipelineError("reject", f"unknown job {job_id}")
        self._advance(job, JobStatus.REJECTED, reason or "rejected by user")
        self.reclaim(job)
        return job

    # ==================================================================
    # Disk
    # ==================================================================
    def reclaim(self, job: VideoJob) -> dict[str, Any]:
        """Delete a finished job's regenerable media.

        Called the moment a job reaches a state where the video is either on
        YouTube or will never be published. Roughly 52 MB of the 57 MB a
        45-second Short occupies is regenerable - the intermediate audio stems
        alone are 26 MB - and nothing used to delete any of it.

        Reports are always kept: they are kilobytes and they are the record of
        what was made and why.
        """
        if not bool(self.cfg.get("storage.reclaim_after_finish", True)):
            return {}
        if not job.dir:
            return {}
        from .core.storage import may_reclaim, reclaim_job
        if not may_reclaim(job.status):
            return {}
        return reclaim_job(Path(job.dir), job.job_id).to_dict()

    def sweep_storage(self, *, after_days: float | None = None) -> list[dict]:
        """Age-based reclaim across every job. Safe to call repeatedly."""
        from .core.storage import sweep
        days = (float(self.cfg.get("storage.reclaim_after_days", 7.0))
                if after_days is None else float(after_days))
        results = sweep(self.workspace, self.db.list_jobs(limit=1000),
                        after_days=days, dry_run=bool(self.cfg.dry_run))
        return [r.to_dict() for r in results]

    def collect_analytics(self, *, days: int = 28) -> dict[str, Any]:
        collector = AnalyticsCollector(self.cfg, self.auth, self.db)
        stats = collector.collect_all(days=days)
        for job in self.db.list_jobs(JobStatus.SCHEDULED.value, limit=200):
            # Promote scheduled jobs whose publish time has passed.
            if job.scheduled_for and job.scheduled_for <= time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()):
                self._advance(job, JobStatus.PUBLISHED, "scheduled time reached")
        insights = self.learner.learn()
        return {"collected": [s.to_dict() for s in stats],
                "insights": [i.to_dict() for i in insights],
                "hints": self.learner.hints()}

    def resume_pending(self) -> list[str]:
        """Recover jobs interrupted by a crash/reboot (spec section 22)."""
        pending = self.db.pending_jobs()
        recovered: list[str] = []
        for job in pending:
            log_event("RECOVERY", "found interrupted job", job=job.job_id,
                      status=job.status, retries=job.retry_count)
            if job.retry_count >= int(self.cfg.get("automation.max_retries", 3)) * 3:
                self._advance(job, JobStatus.FAILED, "retry budget exhausted")
                continue
            recovered.append(job.job_id)
        return recovered

    def close(self) -> None:
        self.db.close()


def _parse_rate(rate: str) -> float:
    """'+8%' -> 8.0"""
    try:
        return float(str(rate).replace("%", "").strip())
    except ValueError:
        return 0.0
