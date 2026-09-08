"""Reclaiming disk from finished jobs.

A job directory is the pipeline's output AND its scratch space, and nothing
ever deleted it: eight jobs held 996 MB on a server with 96 GB, which is fine
until it is not. Measured on one 45-second Short:

    video.mp4    19 MB      master.wav   8.5 MB
    music.wav   8.9 MB      sfx.wav      8.5 MB
    voice.wav   4.3 MB      voice_scenes 4.2 MB
    assets      3.4 MB      thumbnails   504 KB
    ------------------------------------------------
    total       57 MB, of which ~52 MB is regenerable media

The intermediate audio stems are the clearest waste: master, music and sfx are
mixes that only exist to be combined into the video, and once it is rendered
they can never be needed again. The JSON reports are kept in every case - they
are kilobytes, they are the record of what was made and why, and the quality
and originality reports are the evidence behind a publishing decision.

WHAT IS SAFE TO DELETE, AND WHEN, is the whole design here:

  * PUBLISHED / SCHEDULED - the video is on YouTube. Nothing local is needed.
  * REJECTED / CANCELLED / FAILED - the video will never be published.
  * AWAITING_APPROVAL - NEVER. The user is about to watch this video to decide
    on it; deleting it turns the approval screen into a broken player.
  * READY - only on the age sweep. With uploads enabled READY is transient,
    but in a dry run it is the FINAL state and video.mp4 is the only copy
    that exists, so an immediate reclaim would throw away the whole point of
    the run.
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .logging import log_event
from .models import JobStatus

# Regenerable media, in the order it is reported. Everything else in a job
# directory is kept.
HEAVY_FILES = ("video.mp4", "master.wav", "music.wav", "sfx.wav", "voice.wav")
HEAVY_DIRS = ("assets", "voice_scenes", "clips")

# Reclaiming these the moment a job reaches them costs nothing.
RECLAIM_ON_SIGHT = frozenset({
    JobStatus.PUBLISHED.value, JobStatus.SCHEDULED.value,
    JobStatus.REJECTED.value, JobStatus.CANCELLED.value,
    JobStatus.FAILED.value,
})

# States whose media must never be deleted automatically.
#
# READY was missing, and that was data loss with a clear reproduction: approve
# a video, and it moves to READY - rendered, approved, upload not yet done.
# READY is not in RECLAIM_ON_SIGHT so nothing freed it immediately, but it was
# not protected either, so both the age sweep and the dashboard's Clear button
# would delete its video.mp4. The upload then had nothing to send, which
# presents as "publish does nothing" and as a published video with no
# thumbnail. Found in the server log: ten reclaims freeing 987 MB immediately
# before one "jobs cleared count=10".
#
# SCHEDULED is deliberately NOT here: it has already been uploaded, so its
# local media is genuinely spare (see RECLAIM_ON_SIGHT). Its database ROW
# still has to survive, which is a separate guard in db.delete_jobs.
NEVER_RECLAIM = frozenset({JobStatus.AWAITING_APPROVAL.value,
                           JobStatus.READY.value})


@dataclass
class Reclaimed:
    job_id: str
    freed_bytes: int
    removed: list[str]

    @property
    def freed_mb(self) -> float:
        return round(self.freed_bytes / (1024 * 1024), 1)

    def to_dict(self) -> dict:
        return {"job_id": self.job_id, "freed_bytes": self.freed_bytes,
                "freed_mb": self.freed_mb, "removed": self.removed}


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def reclaim_job(job_dir: Path, job_id: str = "") -> Reclaimed:
    """Delete the regenerable media in one job directory.

    Idempotent: reclaiming an already-reclaimed job frees nothing and reports
    nothing removed, rather than failing.
    """
    freed = 0
    removed: list[str] = []
    if not job_dir.exists():
        return Reclaimed(job_id=job_id, freed_bytes=0, removed=[])

    for name in HEAVY_FILES:
        target = job_dir / name
        if target.is_file():
            size = target.stat().st_size
            try:
                target.unlink()
            except OSError as exc:
                log_event("STORAGE", "could not delete file", file=name,
                          error=str(exc)[:120])
                continue
            freed += size
            removed.append(name)

    for name in HEAVY_DIRS:
        target = job_dir / name
        if target.is_dir():
            size = _size(target)
            try:
                shutil.rmtree(target)
            except OSError as exc:
                log_event("STORAGE", "could not delete directory", dir=name,
                          error=str(exc)[:120])
                continue
            freed += size
            removed.append(name + "/")

    if removed:
        log_event("STORAGE", "reclaimed job media", job=job_id or job_dir.name,
                  freed=f"{freed / (1024 * 1024):.1f}MB",
                  removed=",".join(removed))
    return Reclaimed(job_id=job_id or job_dir.name, freed_bytes=freed,
                     removed=removed)


def may_reclaim(status: str, *, age_days: float = 0.0,
                after_days: float = 0.0) -> bool:
    """Whether this job's media can go now.

    Two independent reasons: it reached a state where the media is no longer
    needed, or it is simply old. Approval is exempt from both - an unwatched
    video with no file is worse than a full disk.
    """
    if status in NEVER_RECLAIM:
        return False
    if status in RECLAIM_ON_SIGHT:
        return True
    return bool(after_days) and age_days >= after_days


def sweep(workspace: Path, jobs, *, after_days: float = 7.0,
          keep_last: int = 5, now: float | None = None) -> list[Reclaimed]:
    """Reclaim every eligible job. `jobs` is an iterable of VideoJob.

    `keep_last` always spares the newest N jobs whatever their age or state.
    Borrowed from the existing `autotube prune` command, and worth keeping: a
    sweep that can empty the workspace completely leaves nothing to inspect
    when something goes wrong, and one slow-running test asserts against the
    most recent real render.

    `now` is injectable so the age arithmetic can be tested without waiting a
    week.
    """
    stamp = time.time() if now is None else now
    ordered = sorted(jobs, key=lambda j: (j.updated_at or 0), reverse=True)
    spared = {j.job_id for j in ordered[:max(0, keep_last)]}
    out: list[Reclaimed] = []
    for job in ordered:
        if job.job_id in spared:
            continue
        directory = Path(job.dir) if job.dir else None
        if directory is None or not directory.exists():
            continue
        age_days = max(0.0, (stamp - (job.updated_at or stamp)) / 86400.0)
        if not may_reclaim(job.status, age_days=age_days,
                           after_days=after_days):
            continue
        result = reclaim_job(directory, job.job_id)
        if result.removed:
            out.append(result)
    if out:
        total = sum(r.freed_bytes for r in out)
        log_event("STORAGE", "sweep complete", jobs=len(out),
                  freed=f"{total / (1024 * 1024):.1f}MB",
                  after_days=after_days)
    return out
