"""Backend HTTP API - the surface the Android app talks to (spec section 28).

Security (spec section 31):
  * every mutating endpoint requires the X-API-Key header (AUTOTUBE_API_TOKEN)
  * a simple per-IP token bucket rate limiter
  * no secret ever appears in a response or a log line
  * requests are validated by pydantic models, not hand-parsed

Heavy work never runs inside a request: jobs are queued to a background worker
thread, and the phone polls job status. That is what lets the Android app stay
responsive and survive being backgrounded by Android.
"""
from __future__ import annotations

import json
import queue
import secrets
import shutil
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Literal

from fastapi import (BackgroundTasks, Depends, FastAPI, Header, HTTPException,
                     Query, Request)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.core.config import load_config                        # noqa: E402
from engine.core.logging import log_event, setup_logging          # noqa: E402
from engine.core.models import AutomationRequest, JobStatus       # noqa: E402
from engine.core.niche import build_profile, is_kids_niche        # noqa: E402
from engine.core.util import have_ffmpeg                          # noqa: E402
from engine.pipeline import JobCancelled                          # noqa: E402

CFG = load_config()
setup_logging(jsonl=CFG.workspace / "logs" / "api.jsonl")

app = FastAPI(
    title="Jaadugar backend",
    version="0.1.0",
    description="Research, produce and publish original YouTube videos.",
)

# The Android app talks over HTTPS to a host the user controls; CORS is only
# relevant for a browser dashboard, so keep it explicit and narrow.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in str(CFG.get("api.cors_origins", "")).split(",") if o],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)


# ==========================================================================
# Auth + rate limiting
# ==========================================================================
def _expected_token() -> str:
    return CFG.secret("AUTOTUBE_API_TOKEN")


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = _expected_token()
    if not expected:
        # Fail closed: an unauthenticated backend that can upload to someone's
        # YouTube channel is not an acceptable default.
        raise HTTPException(
            status_code=503,
            detail="AUTOTUBE_API_TOKEN is not set on the backend. "
                   "Set it in .env and restart (see docs/SECURITY.md).")
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


_BUCKETS: dict[str, deque] = defaultdict(deque)
_RATE_LOCK = threading.Lock()
RATE_LIMIT = int(CFG.get("api.rate_limit_per_minute", 60))


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    client = request.client.host if request.client else "unknown"
    now = time.time()
    with _RATE_LOCK:
        bucket = _BUCKETS[client]
        while bucket and now - bucket[0] > 60.0:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT:
            return JSONResponse(
                status_code=429,
                content={"detail": f"rate limit: {RATE_LIMIT} requests/minute"})
        bucket.append(now)
    return await call_next(request)


# ==========================================================================
# Request models
# ==========================================================================
class AutomationBody(BaseModel):
    niche: str = Field(min_length=2, max_length=120)
    audience: str = Field(default="18-35", max_length=60)
    language: str = Field(default="en", max_length=12)
    video_format: Literal["SHORT", "LONGFORM"] = "SHORT"
    duration_seconds: int = Field(default=45, ge=8, le=3600)
    style: str = Field(default="fast-paced, curiosity-driven", max_length=200)
    voice_gender: Literal["female", "male", "child"] = "female"
    caption_language: str = Field(default="", max_length=12)
    caption_style: Literal["", "karaoke", "block", "none"] = ""
    channel_id: str = Field(default="", max_length=64)
    # 0 = use the backend's configured minimum. The Settings slider showed a
    # number and explained that the backend refuses to upload below it, while
    # the value was written only to device preferences and read by nothing.
    min_quality_score: int = Field(default=0, ge=0, le=100)
    count: int = Field(default=1, ge=1, le=10)
    mode: Literal["AUTO", "APPROVAL"] = "APPROVAL"
    frequency: Literal["once", "daily", "weekly", "days"] = "once"
    days: list[int] = Field(default_factory=list)
    upload_time: str = Field(default="", max_length=5)
    timezone: str = Field(default="Asia/Kolkata", max_length=64)
    made_for_kids: bool = False
    keywords: list[str] = Field(default_factory=list, max_length=10)
    # "immediate" uploads on approval; "scheduled" hands YouTube a publishAt.
    publish_mode: Literal["scheduled", "immediate"] = "scheduled"

    @field_validator("upload_time")
    @classmethod
    def _check_time(cls, v: str) -> str:
        if not v:
            return v
        parts = v.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise ValueError("upload_time must be HH:MM")
        if not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
            raise ValueError("upload_time out of range")
        return v

    @field_validator("days")
    @classmethod
    def _check_days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days must be 0 (Mon) to 6 (Sun)")
        return v

    @field_validator("timezone")
    @classmethod
    def _check_tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(v)
        except Exception as exc:
            raise ValueError(f"unknown timezone: {v}") from exc
        return v

    def to_request(self) -> AutomationRequest:
        return AutomationRequest(**self.model_dump())


class TokenBody(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=4096)
    # The Android OAuth client that minted the token. Optional for backwards
    # compatibility, but without it the backend has to guess, and refreshing an
    # Android-issued token with the desktop client's credentials fails.
    client_id: str = Field(default="", max_length=256)


class RejectBody(BaseModel):
    reason: str = Field(default="", max_length=400)


# ==========================================================================
# Job worker (spec sections 21, 22)
# ==========================================================================
class Worker:
    """Single background thread that drains the job queue.

    One at a time on purpose: rendering is CPU-bound, and the target user is
    running this on a laptop or a small free-tier box, not a render farm.
    """

    def __init__(self) -> None:
        self.queue: queue.Queue[AutomationRequest] = queue.Queue()
        self.thread: threading.Thread | None = None
        self.janitor: threading.Thread | None = None
        self.current: str | None = None
        self.current_automation: str | None = None
        self.pipeline = None
        self._lock = threading.Lock()
        self.history: deque[dict[str, Any]] = deque(maxlen=50)
        # Cancellation is cooperative: the pipeline checks these at every stage
        # boundary. There is no safe way to interrupt an ffmpeg encode
        # mid-frame, so "stop" means "stop at the next seam".
        self.cancelled_jobs: set[str] = set()
        self.cancelled_automations: set[str] = set()

    def _is_cancelled(self, job) -> bool:
        if job.job_id in self.cancelled_jobs:
            return True
        if job.automation_id in self.cancelled_automations:
            return True
        # Also ask the DATABASE, because the sets above are in-memory and a
        # service restart emptied them - which quietly resurrected automations
        # the user had stopped. Checked last so the common case stays free of
        # a query per stage boundary.
        try:
            return not self._automation_enabled(job.automation_id)
        except Exception:
            return False

    def _automation_enabled(self, automation_id: str) -> bool:
        """True when this automation is unknown or still enabled.

        Unknown counts as enabled: a one-off run submitted before automations
        were persisted has no row, and refusing to run it would be a
        regression rather than a cancellation.
        """
        if not automation_id:
            return True
        row = _db().get_automation(automation_id)
        if row is None:
            return True
        return bool(row.get("enabled", 1))

    def cancel_job(self, job_id: str) -> None:
        self.cancelled_jobs.add(job_id)

    def cancel_automation(self, automation_id: str) -> int:  # noqa: D401
        """Stop a recurring automation: its queued runs and any running job.

        Returns how many queued runs were dropped. Draining is done by
        rebuilding the queue rather than by peeking, because queue.Queue has no
        remove() and reaching into its internals would race the worker thread.
        """
        self.cancelled_automations.add(automation_id)
        # Persisted as well as remembered: the in-memory set does not survive
        # a restart, and a "stopped" daily automation that comes back tomorrow
        # is the exact failure this endpoint exists to prevent.
        try:
            _db().cancel_automation(automation_id)
        except Exception as exc:
            log_event("WORKER", "could not persist the cancellation",
                      automation=automation_id, error=str(exc)[:160])
        dropped = 0
        kept: list[AutomationRequest] = []
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            if item.id == automation_id:
                dropped += 1
            else:
                kept.append(item)
            self.queue.task_done()
        for item in kept:
            self.queue.put(item)
        return dropped

    def _ensure_pipeline(self):
        if self.pipeline is None:
            from engine.pipeline import Pipeline
            self.pipeline = Pipeline(CFG)
        return self.pipeline

    def start(self) -> None:
        with self._lock:
            if self.thread and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self._loop, daemon=True,
                                           name="autotube-worker")
            self.thread.start()

    def submit(self, request: AutomationRequest) -> None:
        # Recorded before queueing so it can be listed and cancelled even if
        # the process dies before the run starts.
        try:
            _db().save_automation(request)
        except Exception as exc:
            log_event("WORKER", "could not persist the automation",
                      error=str(exc)[:160])
        self.queue.put(request)
        self.start()

    def _loop(self) -> None:
        while True:
            try:
                request = self.queue.get(timeout=2.0)
            except queue.Empty:
                continue
            pipeline = self._ensure_pipeline()
            pipeline.cancel_check = self._is_cancelled
            # Recorded BEFORE the run, not after. It used to be assigned from
            # the result, so `current` only ever named a job that had already
            # finished - useless for reporting what is running and for
            # cancelling it.
            self.current_automation = request.id
            for _ in range(max(1, request.count)):
                if request.id in self.cancelled_automations:
                    log_event("WORKER", "remaining runs cancelled",
                              automation=request.id)
                    break
                try:
                    result = pipeline.run(request)
                    self.current = result.job.job_id
                    self.history.append({
                        "job_id": result.job.job_id,
                        "status": result.job.status,
                        "quality": (result.quality.score if result.quality else 0),
                        "at": time.time()})
                except JobCancelled as exc:
                    log_event("WORKER", "job cancelled", job=str(exc)[:60])
                    self.history.append({"job_id": str(exc), "status": "CANCELLED",
                                         "at": time.time()})
                except Exception as exc:
                    log_event("WORKER", "job failed", error=str(exc)[:300])
                    self.history.append({"job_id": None, "status": "FAILED",
                                         "error": str(exc)[:300], "at": time.time()})
            self.current = None
            self.current_automation = None
            self.queue.task_done()

    # ------------------------------------------------------------------
    # Janitor: the age-based sweep, on a schedule
    # ------------------------------------------------------------------
    def start_janitor(self) -> None:
        """Start the periodic storage sweep, unless it is switched off."""
        if not bool(CFG.get("storage.auto_sweep", True)):
            log_event("JANITOR", "automatic sweep disabled by config",
                      hint="storage.auto_sweep")
            return
        with self._lock:
            if self.janitor and self.janitor.is_alive():
                return
            self.janitor = threading.Thread(target=self._janitor_loop,
                                            daemon=True,
                                            name="autotube-janitor")
            self.janitor.start()
            # Say that it started, and on what schedule.
            #
            # It only logged when DISABLED, which meant a running janitor was
            # indistinguishable from a missing one - and Linux does not expose
            # Python thread names, so there was no way to check from outside
            # either. For something that runs unattended for weeks, "is it
            # actually on?" has to be answerable from the log.
            log_event("JANITOR", "started",
                      every_hours=CFG.get("storage.sweep_interval_hours", 6.0),
                      reclaim_after_days=CFG.get("storage.reclaim_after_days",
                                                 7.0),
                      min_free_gb=CFG.get("storage.min_free_gb", 5.0),
                      free_gb=f"{self._free_gb():.1f}")

    def _free_gb(self) -> float:
        try:
            usage = shutil.disk_usage(str(CFG.workspace))
            return usage.free / (1024 ** 3)
        except OSError:
            return float("inf")

    def _sweep_once(self, *, after_days: float, reason: str) -> float:
        """One sweep. Returns megabytes freed. Never raises."""
        try:
            results = self._ensure_pipeline().sweep_storage(
                after_days=after_days)
        except Exception as exc:                # noqa: BLE001 - see docstring
            # A janitor that can kill the process is worse than a full disk.
            log_event("JANITOR", "sweep failed", reason=reason,
                      error=str(exc)[:200])
            return 0.0
        freed = sum(float(r.get("freed_mb", 0.0)) for r in results)
        # BOTH outcomes are logged, including "nothing to do".
        #
        # Logging only on a hit made a healthy quiet janitor look identical to
        # a dead one. A line every six hours is cheap and it is the only
        # evidence the schedule is alive.
        if results:
            log_event("JANITOR", "swept old job media", reason=reason,
                      jobs=len(results), freed=f"{freed:.1f}MB",
                      after_days=after_days,
                      free_gb=f"{self._free_gb():.1f}")
        else:
            log_event("JANITOR", "nothing old enough to sweep", reason=reason,
                      after_days=after_days,
                      free_gb=f"{self._free_gb():.1f}")
        return freed

    def _janitor_loop(self) -> None:
        interval = max(0.25, float(
            CFG.get("storage.sweep_interval_hours", 6.0))) * 3600.0
        normal_days = float(CFG.get("storage.reclaim_after_days", 7.0))
        urgent_days = max(0.5, float(
            CFG.get("storage.urgent_reclaim_after_days", 1.0)))
        floor_gb = float(CFG.get("storage.min_free_gb", 5.0))

        # A short delay before the first sweep. Startup already runs
        # resume_pending(), and racing it to walk the same directories on two
        # cores is pointless.
        first = min(interval, 120.0)
        time.sleep(first)

        while True:
            self._sweep_once(after_days=normal_days, reason="scheduled")

            free = self._free_gb()
            if free < floor_gb:
                # Escalate. AWAITING_APPROVAL and READY are still exempt, so
                # this cannot take a video somebody is waiting to review or
                # upload - it takes the older debris first.
                log_event("JANITOR", "low disk, sweeping more aggressively",
                          free_gb=f"{free:.1f}", floor_gb=floor_gb,
                          after_days=urgent_days)
                self._sweep_once(after_days=urgent_days, reason="low disk")
                still = self._free_gb()
                if still < floor_gb:
                    # Say so loudly rather than silently continuing to a
                    # render that will die mid-encode.
                    log_event("JANITOR", "STILL low on disk after sweeping - "
                              "renders may fail; approve or reject the "
                              "pending jobs, or run `autotube prune`",
                              free_gb=f"{still:.1f}", floor_gb=floor_gb)
            time.sleep(interval)

    @property
    def depth(self) -> int:
        return self.queue.qsize()


WORKER = Worker()


def _db():
    from engine.core.db import Database
    return Database(CFG.workspace / "autotube.db")


# ==========================================================================
# Public endpoints
# ==========================================================================
@app.get("/health")
def health() -> dict[str, Any]:
    """Unauthenticated liveness + capability probe."""
    from engine.content.llm import LLMRouter
    from engine.tts.providers import build_providers
    router = LLMRouter(list(CFG.get("content.llm_provider_order", [])), CFG)
    return {
        "ok": True,
        "version": app.version,
        "ffmpeg": have_ffmpeg(),
        "dry_run": CFG.dry_run,
        "upload_enabled": bool(CFG.get("youtube.upload_enabled")),
        # Reported so the app can say WHY a scheduled publish will not happen,
        # instead of silently uploading everything as private.
        "force_private": bool(CFG.get("youtube.force_private")),
        "approval_required": bool(CFG.get("automation.approval_required")),
        "llm_providers": [p.name for p in router.usable],
        "tts_providers": [p.name for p in build_providers(
            list(CFG.get("tts.provider_order", []))) if p.available()],
        "research_configured": CFG.has_secret("YOUTUBE_API_KEY"),
        "auth_required": bool(_expected_token()),
        "queue_depth": WORKER.depth,
    }


@app.get("/config", dependencies=[Depends(require_api_key)])
def get_config() -> dict[str, Any]:
    """Non-secret configuration, for the Settings screen."""
    return {
        "quality": CFG.get("quality", {}),
        "video": CFG.get("video", {}),
        "captions": CFG.get("captions", {}),
        "automation": CFG.get("automation", {}),
        "timezone": CFG.get("timezone", {}),
        "youtube": {k: v for k, v in (CFG.get("youtube", {}) or {}).items()
                    if k != "quota_costs"},
        "tts": {"provider_order": CFG.get("tts.provider_order"),
                "voice_gender": CFG.get("tts.voice_gender")},
        "dry_run": CFG.dry_run,
    }


@app.get("/niche/preview", dependencies=[Depends(require_api_key)])
def niche_preview(niche: str = Query(min_length=2, max_length=120),
                  audience: str = "18-35", style: str = "",
                  duration: int = Query(45, ge=8, le=3600)) -> dict[str, Any]:
    """Show how a niche will be interpreted before starting an automation."""
    profile = build_profile(niche, audience=audience, style=style,
                            duration_seconds=duration)
    return {"profile": profile.to_dict(),
            "kids_niche_detected": is_kids_niche(niche),
            "requires_kids_confirmation": is_kids_niche(niche)}


class ClearBody(BaseModel):
    """What to clear from the dashboard."""
    job_ids: list[str] = Field(default_factory=list, max_length=500)
    # 0 = clear everything eligible now. 7 = only what is older than a week.
    older_than_days: float = Field(default=0.0, ge=0.0, le=3650.0)
    # Also delete the media on disk. On by default: clearing the list while
    # leaving 57 MB per job on the server is the worst of both.
    free_disk: bool = True


@app.post("/jobs/clear", dependencies=[Depends(require_api_key)])
def clear_jobs(body: ClearBody) -> dict[str, Any]:
    """Remove finished jobs from the list, and their media from disk.

    "Finished" means PUBLISHED, FAILED, REJECTED or CANCELLED. Anything still
    in flight, waiting for approval, approved-but-not-yet-uploaded (READY) or
    uploaded-and-waiting-to-go-live (SCHEDULED) is never cleared, whatever is
    asked - tidying up history must not throw away a video that has not been
    sent yet, or forget one that is about to publish.
    """
    db = _db()
    cutoff = (time.time() - body.older_than_days * 86400.0
              if body.older_than_days else None)
    removed = db.delete_jobs(job_ids=body.job_ids or None,
                             keep_active=True, older_than=cutoff)
    freed = 0
    if body.free_disk:
        from engine.core.storage import reclaim_job
        for job in removed:
            if job.dir:
                freed += reclaim_job(Path(job.dir), job.job_id).freed_bytes
    log_event("API", "jobs cleared", count=len(removed),
              freed=f"{freed / (1024 * 1024):.1f}MB",
              older_than_days=body.older_than_days)
    return {"cleared": len(removed),
            "freed_mb": round(freed / (1024 * 1024), 1),
            "job_ids": [j.job_id for j in removed]}


@app.post("/jobs/{job_id}/reclaim", dependencies=[Depends(require_api_key)])
def reclaim_one(job_id: str) -> dict[str, Any]:
    """Free one job's media without removing it from the list."""
    db = _db()
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"error": "job_not_found"})
    from engine.core.storage import NEVER_RECLAIM, reclaim_job
    if job.status in NEVER_RECLAIM:
        raise HTTPException(status_code=409, detail={
            "error": "awaiting_approval",
            "message": ("This video is waiting for your approval - deleting it "
                        "would leave nothing to review. Approve or reject it "
                        "first.")})
    if not job.dir:
        return {"freed_mb": 0.0, "removed": []}
    return reclaim_job(Path(job.dir), job_id).to_dict()


@app.post("/maintenance/reclaim", dependencies=[Depends(require_api_key)])
def reclaim_sweep(older_than_days: float = Query(7.0, ge=0.0, le=3650.0)
                  ) -> dict[str, Any]:
    """Age-based sweep across every job. Idempotent."""
    results = WORKER._ensure_pipeline().sweep_storage(after_days=older_than_days)
    freed = sum(r.get("freed_bytes", 0) for r in results)
    return {"jobs": len(results),
            "freed_mb": round(freed / (1024 * 1024), 1),
            "older_than_days": older_than_days,
            "detail": results}


@app.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_api_key)])
def cancel_job(job_id: str) -> dict[str, Any]:
    """Stop a job that is queued or rendering.

    Cancellation is cooperative and takes effect at the next stage boundary -
    an ffmpeg encode cannot be interrupted mid-frame safely, so a job that is
    three seconds into a render will finish that render and stop before the
    next stage. Marked CANCELLED rather than FAILED so it is not retried and
    does not look like a fault.
    """
    db = _db()
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"error": "job_not_found"})
    if JobStatus(job.status).terminal:
        return {"cancelled": False, "status": job.status,
                "note": "job already finished"}
    WORKER.cancel_job(job_id)
    log_event("API", "cancel requested", job=job_id, status=job.status)
    return {"cancelled": True, "status": job.status,
            "note": "stops at the next stage boundary"}


@app.get("/automations", dependencies=[Depends(require_api_key)])
def list_automations(include_cancelled: bool = False,
                     limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Every recurring automation, so the app can show what is scheduled.

    Until now the only record of an automation was a row in the phone's own
    database, which meant there was nowhere to see what had been scheduled and
    no way to cancel it authoritatively.
    """
    db = _db()
    # Resolve channel titles once rather than per row. Best-effort: a missing
    # or broken token store must not stop the schedule from being listed.
    titles: dict[str, str] = {}
    try:
        from engine.youtube.auth import YouTubeAuth
        store = YouTubeAuth(CFG).channels_store
        titles = {c.channel_id: (c.title or c.channel_id) for c in store.all()}
        default_channel = store.default_id()
    except Exception:                           # noqa: BLE001
        default_channel = ""
    from engine.core.groups import group_for_topic

    out = []
    for row in db.list_automations(include_cancelled=include_cancelled,
                                  limit=limit):
        payload = {}
        try:
            payload = json.loads(row.get("payload") or "{}")
        except ValueError:
            payload = {}
        automation_id = row["id"]
        out.append({
            "id": automation_id,
            "niche": row.get("niche", ""),
            "frequency": row.get("frequency", "once"),
            "upload_time": row.get("upload_time", ""),
            "days": [int(d) for d in str(row.get("days") or "").split(",") if d],
            "timezone": row.get("timezone", ""),
            "enabled": bool(row.get("enabled", 1)),
            "created_at": row.get("created_at", 0.0),
            "cancelled_at": row.get("cancelled_at", 0.0),
            "video_format": payload.get("video_format", ""),
            "language": payload.get("language", ""),
            "made_for_kids": bool(payload.get("made_for_kids", False)),
            "videos_made": db.count_jobs_for_automation(automation_id),
            "running": automation_id == WORKER.current_automation,
            # WHICH CHANNEL this posts to, resolved the same way the pipeline
            # resolves it: an explicit id, else the topic's group mapping,
            # else the default. Without this the Schedule tab lists two daily
            # automations with no way to tell which is the kids one and which
            # is the finance one - which is the whole point of having several.
            **_automation_target(payload, row, titles, default_channel,
                                 group_for_topic),
        })
    return {"automations": out, "queue_depth": WORKER.depth,
            "running": WORKER.current_automation or ""}


def _automation_target(payload: dict[str, Any], row: dict[str, Any],
                       titles: dict[str, str], default_channel: str,
                       group_for_topic) -> dict[str, Any]:
    """Which channel and group an automation publishes to, for display.

    Mirrors Pipeline's resolution order deliberately - explicit channel id,
    then the topic's group, then the default. If this disagreed with the
    pipeline the app would confidently show the wrong destination.
    """
    niche = str(row.get("niche") or "")
    group = group_for_topic(niche)
    explicit = str(payload.get("channel_id") or "")
    channel_id = explicit
    if not channel_id:
        try:
            from engine.youtube.auth import YouTubeAuth
            mapped = YouTubeAuth(CFG).channels_store.for_niche(niche)
            channel_id = mapped.channel_id if mapped else ""
        except Exception:                       # noqa: BLE001
            channel_id = ""
    resolved = channel_id or default_channel
    return {
        "channel_id": resolved,
        "channel_title": titles.get(resolved, ""),
        "channel_is_default": bool(resolved and resolved == default_channel
                                   and not explicit),
        "group": group.key if group else "",
        "group_label": group.label if group else "",
    }


@app.delete("/automations/{automation_id}",
            dependencies=[Depends(require_api_key)])
def cancel_automation(automation_id: str) -> dict[str, Any]:
    """Stop a recurring automation: queued runs and the one in progress."""
    dropped = WORKER.cancel_automation(automation_id)
    log_event("API", "automation cancelled", automation=automation_id,
              dropped=dropped)
    return {"cancelled": True, "dropped_from_queue": dropped,
            "note": "a run already in progress stops at the next stage"}


@app.post("/automations", dependencies=[Depends(require_api_key)],
          status_code=202)
def create_automation(body: AutomationBody) -> dict[str, Any]:
    """Queue an automation run. Returns immediately; poll /jobs for progress."""
    request = body.to_request()

    # Kids content must be explicitly confirmed (spec section 9).
    if is_kids_niche(request.niche) and not request.made_for_kids:
        raise HTTPException(
            status_code=409,
            detail={"error": "kids_confirmation_required",
                    "message": ("This niche looks child-directed. Confirm the "
                                "'Made for Kids' classification before "
                                "publishing."),
                    "niche": request.niche})

    pipeline_problems: list[str] = []
    if not have_ffmpeg():
        pipeline_problems.append("ffmpeg not installed on the backend")
    if not CFG.has_secret("YOUTUBE_API_KEY"):
        pipeline_problems.append("YOUTUBE_API_KEY not configured on the backend")
    if pipeline_problems:
        raise HTTPException(status_code=503,
                           detail={"error": "backend_not_ready",
                                   "problems": pipeline_problems})

    WORKER.submit(request)
    log_event("API", "automation queued", niche=request.niche,
              count=request.count, mode=request.mode)
    return {"accepted": True, "automation_id": request.id,
            "queued": WORKER.depth,
            "note": "poll GET /jobs for progress"}


@app.get("/jobs", dependencies=[Depends(require_api_key)])
def list_jobs(status: str = "", limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    db = _db()
    try:
        jobs = db.list_jobs(status.upper() or None, limit=limit)
        return {
            "queue_depth": WORKER.depth,
            "recent_worker_results": list(WORKER.history)[-10:],
            "jobs": [_job_summary(j) for j in jobs],
        }
    finally:
        db.close()


@app.get("/jobs/{job_id}", dependencies=[Depends(require_api_key)])
def get_job(job_id: str) -> dict[str, Any]:
    db = _db()
    try:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        data = job.to_dict()
        # Point the app at the download endpoints rather than raw disk paths.
        data["media"] = {
            "video": f"/jobs/{job_id}/file/video" if job.video_path else None,
            "thumbnail": f"/jobs/{job_id}/file/thumbnail" if job.thumbnail_path else None,
            "subtitle": f"/jobs/{job_id}/file/subtitle" if job.subtitle_path else None,
            "voice": f"/jobs/{job_id}/file/voice" if job.voice_path else None,
        }
        return data
    finally:
        db.close()


@app.get("/jobs/{job_id}/file/{kind}", dependencies=[Depends(require_api_key)])
def get_job_file(job_id: str, kind: str):
    """Stream a produced artifact (video preview, thumbnail, captions)."""
    db = _db()
    try:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        mapping = {
            "video": (job.video_path, "video/mp4"),
            "thumbnail": (job.thumbnail_path, "image/jpeg"),
            "subtitle": (job.subtitle_path, "text/plain"),
            "voice": (job.voice_path, "audio/wav"),
        }
        if kind not in mapping:
            raise HTTPException(status_code=400, detail="unknown file kind")
        raw, media_type = mapping[kind]
        if not raw:
            raise HTTPException(status_code=404, detail=f"{kind} not produced")
        path = Path(raw).resolve()
        # Path containment check: never serve outside the workspace.
        workspace = CFG.workspace.resolve()
        if workspace not in path.parents and path != workspace:
            raise HTTPException(status_code=403, detail="path outside workspace")
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"{kind} file missing")
        return FileResponse(str(path), media_type=media_type,
                            filename=path.name)
    finally:
        db.close()


@app.post("/jobs/{job_id}/approve", dependencies=[Depends(require_api_key)])
def approve_job(job_id: str, background: BackgroundTasks) -> dict[str, Any]:
    """Approve a job awaiting review; upload/schedule runs in the background."""
    from engine.pipeline import Pipeline

    def do_approve() -> None:
        """Approve and upload, recording any failure ON THE JOB.

        The endpoint has already answered {"accepted": true} by the time this
        runs, so a failure here used to go only to the server log: job.error
        was never set and the status never advanced, which left the card
        sitting in "Waiting for your approval" for ever with nothing on the
        phone to explain it. Approving again produced the same silence.

        Catches bare Exception on purpose. A background task that dies with an
        unexpected error is exactly the case that needs to reach the user -
        narrowing this to PipelineError is how an ffmpeg or Google API
        exception disappeared.
        """
        pipe = Pipeline(CFG)
        try:
            pipe.approve(job_id)
        except Exception as exc:                      # noqa: BLE001
            log_event("API", "approval failed", job=job_id,
                      error=str(exc)[:200])
            try:
                failed = pipe.db.get_job(job_id)
                if failed is not None:
                    failed.error = f"approval/upload failed: {str(exc)[:400]}"
                    pipe._advance(failed, JobStatus.FAILED, failed.error)
            except Exception as inner:                # noqa: BLE001
                log_event("API", "could not record the approval failure",
                          job=job_id, error=str(inner)[:160])
        finally:
            pipe.close()

    db = _db()
    try:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        if job.status != JobStatus.AWAITING_APPROVAL.value:
            raise HTTPException(
                status_code=409,
                detail=f"job is {job.status}, not awaiting approval")
    finally:
        db.close()

    background.add_task(do_approve)
    return {"accepted": True, "job_id": job_id}


@app.post("/jobs/{job_id}/reject", dependencies=[Depends(require_api_key)])
def reject_job(job_id: str, body: RejectBody) -> dict[str, Any]:
    from engine.pipeline import Pipeline
    pipe = Pipeline(CFG)
    try:
        job = pipe.reject(job_id, body.reason)
        return {"job_id": job.job_id, "status": job.status}
    finally:
        pipe.close()


# ==========================================================================
@app.get("/research", dependencies=[Depends(require_api_key)])
def research(niche: str = Query(min_length=2, max_length=120),
             video_format: Literal["SHORT", "LONGFORM"] = "SHORT",
             limit: int = Query(20, ge=1, le=50)) -> dict[str, Any]:
    """Run (or serve cached) research for the Research screen."""
    from engine.pipeline import Pipeline
    from engine.research.gaps import cluster_videos, find_gaps
    pipe = Pipeline(CFG)
    try:
        profile = build_profile(niche)
        videos = pipe.research_engine.research(niche, profile,
                                              video_format=video_format)
        clusters = cluster_videos(videos)
        gaps = find_gaps(clusters, videos)
        return {
            "niche": niche,
            "quota_used_today": pipe.quota.used(),
            "quota_limit": pipe.quota.limit,
            "videos": [v.to_dict() for v in videos[:limit]],
            "breakouts": [v.video_id for v in videos if v.is_breakout],
            "clusters": [c.to_dict() for c in clusters],
            "gaps": [g.to_dict() for g in gaps],
            "disclaimer": ("ctr_potential_score is a heuristic over public "
                           "signals. YouTube does not expose other channels' "
                           "real CTR."),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:400]) from exc
    finally:
        pipe.close()


@app.get("/analytics", dependencies=[Depends(require_api_key)])
def analytics(days: int = Query(28, ge=1, le=365),
              collect: bool = False) -> dict[str, Any]:
    """Own-channel analytics + the learned strategy."""
    from engine.pipeline import Pipeline
    pipe = Pipeline(CFG)
    try:
        if collect:
            return pipe.collect_analytics(days=days)
        rows = pipe.db.query(
            "SELECT youtube_video_id, MAX(collected_at) AS at, views, "
            "avg_view_percentage, ctr, subscribers_gained, likes, comments "
            "FROM analytics GROUP BY youtube_video_id "
            "ORDER BY at DESC LIMIT 100")
        return {
            "videos": [dict(r) for r in rows],
            "strategy": pipe.learner.report(),
            "note": "CTR is available only for your own authenticated channel.",
        }
    finally:
        pipe.close()


@app.get("/quota", dependencies=[Depends(require_api_key)])
def quota() -> dict[str, Any]:
    from engine.pipeline import Pipeline
    pipe = Pipeline(CFG)
    try:
        return {
            "used_today": pipe.quota.used(),
            "limit": pipe.quota.limit,
            "reserved_for_uploads": pipe.quota.reserve,
            "available_for_research": pipe.quota.remaining(),
            "costs": pipe.quota.costs,
            "max_uploads_per_day": pipe.quota.limit // pipe.quota.cost("video_insert"),
            "resets": "midnight US Pacific",
        }
    finally:
        pipe.close()


# ==========================================================================
@app.get("/youtube/status", dependencies=[Depends(require_api_key)])
def youtube_status() -> dict[str, Any]:
    from engine.youtube.auth import YouTubeAuth
    auth = YouTubeAuth(CFG)

    # Where the OAuth client came from, rather than a bare "configured" flag.
    #
    # `configured` only ever meant "YOUTUBE_CLIENT_ID/SECRET are set in .env",
    # so connecting from the phone - which is the supported path and needs no
    # desktop client at all - showed up in the app as "Backend OAuth client:
    # missing on backend" in red. That reads as a fault when it is the normal,
    # correct state.
    stored = auth.store.read() if auth.store.exists() else {}
    if stored.get("client_id"):
        source = "device"
    elif auth.configured:
        source = "env"
    else:
        source = "none"

    out: dict[str, Any] = {
        "configured": auth.configured,
        "authorized": auth.authorized,
        "client_source": source,
        "channels": [],
    }
    if auth.authorized:
        try:
            out["channels"] = auth.channels()
        except Exception as exc:
            out["error"] = str(exc)[:240]
            # `authorized` is true whenever a token exists, but a token that
            # cannot be refreshed is not a working connection. Say so, instead
            # of showing "Authorised: yes" beside a refresh error.
            out["authorized"] = False
    return out


class NicheMapBody(BaseModel):
    niches: list[str] = Field(default_factory=list, max_length=40)


@app.get("/youtube/accounts", dependencies=[Depends(require_api_key)])
def list_accounts() -> dict[str, Any]:
    """Every brand channel this backend can publish to.

    One entry per authorisation. A YouTube token is bound to a single channel -
    the channel is chosen in Google's chooser during consent - so posting to
    several brand channels under one Google account means several
    authorisations, not one token with a channel parameter.
    """
    from engine.youtube.auth import YouTubeAuth
    auth = YouTubeAuth(CFG)
    store = auth.channels_store
    default = store.default_id()
    return {
        "accounts": [
            {**channel.public(), "is_default": channel.channel_id == default}
            for channel in store.all()
        ],
        "default": default,
    }


@app.post("/youtube/accounts/{channel_id}/default",
          dependencies=[Depends(require_api_key)])
def set_default_account(channel_id: str) -> dict[str, Any]:
    from engine.youtube.auth import YouTubeAuth
    if not YouTubeAuth(CFG).channels_store.set_default(channel_id):
        raise HTTPException(status_code=404,
                            detail={"error": "channel_not_found"})
    return {"default": channel_id}


@app.post("/youtube/accounts/{channel_id}/niches",
          dependencies=[Depends(require_api_key)])
def set_account_niches(channel_id: str, body: NicheMapBody) -> dict[str, Any]:
    """Map niches to a channel, so a kids video posts to the kids channel.

    A niche belongs to exactly one channel - otherwise "which channel does
    this go to" has two answers - so assigning it here removes it from any
    other channel.
    """
    from engine.youtube.auth import YouTubeAuth
    store = YouTubeAuth(CFG).channels_store
    if not store.set_niches(channel_id, body.niches):
        raise HTTPException(status_code=404,
                            detail={"error": "channel_not_found"})
    log_event("API", "channel niches set", channel=channel_id,
              niches=",".join(body.niches) or "-")
    return {"channel_id": channel_id, "niches": body.niches}


@app.get("/niche-groups", dependencies=[Depends(require_api_key)])
def list_niche_groups() -> dict[str, Any]:
    """The channel groups and their topics.

    Served rather than hard-coded in the app so there is ONE list. Two
    hand-kept copies drift silently: a topic missing from the app cannot be
    selected, and a topic missing from the backend maps to no channel.
    """
    from engine.core.groups import dump
    return {"groups": dump()}


@app.delete("/youtube/accounts/{channel_id}",
            dependencies=[Depends(require_api_key)])
def remove_account(channel_id: str) -> dict[str, Any]:
    """Forget a channel's authorisation. Videos already published stay up."""
    from engine.youtube.auth import YouTubeAuth
    if not YouTubeAuth(CFG).channels_store.remove(channel_id):
        raise HTTPException(status_code=404,
                            detail={"error": "channel_not_found"})
    log_event("API", "channel authorisation removed", channel=channel_id)
    return {"removed": channel_id}


@app.post("/youtube/token", dependencies=[Depends(require_api_key)])
def import_token(body: TokenBody) -> dict[str, Any]:
    """Receive the refresh token the Android app obtained via AppAuth.

    The token is written to the 0600 token store; it is never logged and never
    returned by any endpoint. The client id is stored with it because an
    Android client is a public PKCE client and only that same client can
    refresh the token.
    """
    from engine.youtube.auth import YouTubeAuth
    auth = YouTubeAuth(CFG)
    auth.import_refresh_token(body.refresh_token, body.client_id)
    return {"stored": True, "authorized": auth.authorized,
            "refreshable": bool(body.client_id or auth.configured)}


def _job_summary(job) -> dict[str, Any]:
    meta = job.metadata or {}
    quality = job.quality or {}
    request = job.request or {}
    return {
        "job_id": job.job_id,
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "niche": request.get("niche", ""),
        "title": meta.get("title", ""),
        "quality_score": quality.get("score", 0),
        "quality_passed": quality.get("passed", False),
        "blockers": quality.get("blockers", []),
        "retention_score": (job.script or {}).get("retention_score", 0),
        "duration": (job.script or {}).get("estimated_duration", 0),
        "youtube_video_id": job.youtube_video_id,
        "scheduled_for": job.scheduled_for,
        "error": job.error,
        "retry_count": job.retry_count,
        "has_video": bool(job.video_path),
        "has_thumbnail": bool(job.thumbnail_path),
    }


@app.on_event("startup")
def on_startup() -> None:
    log_event("API", "backend started", dry_run=CFG.dry_run,
              upload_enabled=bool(CFG.get("youtube.upload_enabled")),
              auth_configured=bool(_expected_token()))
    # The age-based sweep, on a schedule.
    #
    # `reclaim_after_days` has been in config since the storage work landed and
    # `sweep_storage()` has been implemented all along, but the only caller was
    # the /maintenance/reclaim endpoint and NOTHING invoked it - so the
    # immediate reclaim on publish worked and everything else accumulated
    # forever. Started here so it runs whether or not the phone is open.
    WORKER.start_janitor()

    # Recover anything interrupted by the last shutdown (spec section 22).
    try:
        from engine.pipeline import Pipeline
        pipe = Pipeline(CFG)
        recovered = pipe.resume_pending()
        pipe.close()
        if recovered:
            log_event("API", "interrupted jobs found", count=len(recovered))
    except Exception as exc:
        log_event("API", "recovery scan failed", error=str(exc)[:200])
