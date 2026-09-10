"""SQLite persistence.

The Android app owns its own Room/SQLite database; this is the backend mirror.
Both use the same table names and column semantics so the two stay in sync.

Credentials are deliberately NOT stored here (spec section 27) - OAuth tokens
live in a separate 0600 token store, see engine/youtube/auth.py.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from .logging import log_event
from .models import JobStatus, VideoJob

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS niche_profiles (
    name        TEXT PRIMARY KEY,
    profile     TEXT NOT NULL,          -- JSON
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS research_videos (
    video_id        TEXT PRIMARY KEY,
    niche           TEXT NOT NULL,
    title           TEXT NOT NULL,
    channel_id      TEXT,
    channel_title   TEXT,
    published_at    TEXT,
    views           INTEGER DEFAULT 0,
    likes           INTEGER DEFAULT 0,
    comments        INTEGER DEFAULT 0,
    duration_seconds INTEGER DEFAULT 0,
    is_short        INTEGER DEFAULT 0,
    view_velocity   REAL DEFAULT 0,
    engagement_rate REAL DEFAULT 0,
    performance_ratio REAL DEFAULT 0,
    is_breakout     INTEGER DEFAULT 0,
    viral_score     REAL DEFAULT 0,
    payload         TEXT NOT NULL,      -- full JSON
    fetched_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_research_niche ON research_videos(niche, fetched_at);

CREATE TABLE IF NOT EXISTS content_ideas (
    idea_id     TEXT PRIMARY KEY,
    niche       TEXT NOT NULL,
    topic       TEXT,
    working_title TEXT,
    hook_type   TEXT,
    opportunity_score REAL DEFAULT 0,
    used        INTEGER DEFAULT 0,
    payload     TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS scripts (
    script_id   TEXT PRIMARY KEY,
    idea_id     TEXT,
    language    TEXT,
    provider    TEXT,
    retention_score REAL DEFAULT 0,
    text_hash   TEXT,
    payload     TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_scripts_hash ON scripts(text_hash);

CREATE TABLE IF NOT EXISTS assets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    asset       TEXT NOT NULL,
    source      TEXT NOT NULL,
    license     TEXT NOT NULL,
    prompt      TEXT,
    attribution TEXT,
    url         TEXT,
    scene_index INTEGER DEFAULT -1,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assets_job ON assets(job_id);

CREATE TABLE IF NOT EXISTS video_jobs (
    job_id      TEXT PRIMARY KEY,
    automation_id TEXT,
    status      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    error       TEXT DEFAULT '',
    retry_count INTEGER DEFAULT 0,
    scheduled_for TEXT DEFAULT '',
    youtube_video_id TEXT DEFAULT '',
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON video_jobs(status, updated_at);

CREATE TABLE IF NOT EXISTS published_videos (
    youtube_video_id TEXT PRIMARY KEY,
    job_id      TEXT,
    title       TEXT,
    niche       TEXT,
    topic       TEXT,
    hook_type   TEXT,
    title_type  TEXT,
    duration    REAL,
    visual_style TEXT,
    published_time TEXT,
    payload     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_video_id TEXT NOT NULL,
    collected_at REAL NOT NULL,
    views       INTEGER DEFAULT 0,
    watch_time_minutes REAL DEFAULT 0,
    avg_view_duration REAL DEFAULT 0,
    avg_view_percentage REAL DEFAULT 0,
    likes       INTEGER DEFAULT 0,
    comments    INTEGER DEFAULT 0,
    subscribers_gained INTEGER DEFAULT 0,
    impressions INTEGER DEFAULT 0,
    ctr         REAL DEFAULT 0,          -- own channel only
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_analytics_video ON analytics(youtube_video_id, collected_at);

CREATE TABLE IF NOT EXISTS schedules (
    id          TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL,
    job_id      TEXT DEFAULT '',
    publish_at  TEXT NOT NULL,          -- RFC3339 UTC
    local_time  TEXT NOT NULL,
    timezone    TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'PENDING',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_schedules_state ON schedules(state, publish_at);

-- Recurring automations.
--
-- These used to exist only in Room on the phone, with the server keeping the
-- request in an in-memory queue.Two consequences, both of which the user hit:
-- cancelling an automation was forgotten on the next service restart, so it
-- resumed; and there was nowhere to LIST what had been scheduled, because the
-- only server-side trace was the automation_id column on jobs already made.
CREATE TABLE IF NOT EXISTS automations (
    id           TEXT PRIMARY KEY,
    niche        TEXT NOT NULL,
    frequency    TEXT NOT NULL DEFAULT 'once',
    days         TEXT DEFAULT '',
    upload_time  TEXT DEFAULT '',
    timezone     TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    enabled      INTEGER DEFAULT 1,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    cancelled_at REAL DEFAULT 0,
    payload      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_automations_enabled ON automations(enabled, created_at);

-- The script bank: pre-written, human-reviewed scripts.
--
-- The JSONL file is the DELIVERY format; this table is the runtime. Keeping
-- state (used_at) in the file would mean rewriting a 300-line file on every
-- render and losing the state on any re-import, so the file carries content
-- and the table carries content plus state.
--
-- `used_at` rather than a "where we got to" pointer: a pointer breaks the
-- moment an entry is deleted or the file is reordered, and a timestamp also
-- answers "which script became which video, and when".
CREATE TABLE IF NOT EXISTS bank_entries (
    entry_id     TEXT PRIMARY KEY,
    grp          TEXT NOT NULL,
    topic        TEXT NOT NULL DEFAULT '',
    shape        TEXT NOT NULL DEFAULT 'narrative',
    language     TEXT NOT NULL DEFAULT 'en',
    video_format TEXT NOT NULL DEFAULT 'SHORT',
    made_for_kids INTEGER DEFAULT 0,
    title        TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    est_seconds  REAL DEFAULT 0,
    imported_at  REAL NOT NULL,
    used_at      REAL DEFAULT 0,
    used_job_id  TEXT DEFAULT '',
    payload      TEXT NOT NULL
);
-- The selection query is "next unused entry for this group+language+format,
-- oldest first", so that is the index.
CREATE INDEX IF NOT EXISTS ix_bank_pick
    ON bank_entries(grp, language, video_format, used_at, imported_at);
CREATE INDEX IF NOT EXISTS ix_bank_hash ON bank_entries(content_hash);

CREATE TABLE IF NOT EXISTS service_configs (
    name        TEXT PRIMARY KEY,
    enabled     INTEGER DEFAULT 1,
    settings    TEXT NOT NULL,          -- JSON, never secrets
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS quota_usage (
    day         TEXT PRIMARY KEY,       -- YYYY-MM-DD (Pacific, per Google reset)
    units       INTEGER NOT NULL DEFAULT 0,
    detail      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS strategy_weights (
    dimension   TEXT NOT NULL,          -- hook_type | title_type | duration_bucket ...
    value       TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    samples     INTEGER NOT NULL DEFAULT 0,
    score       REAL NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (dimension, value)
);
"""


class Database:
    """Thread-safe thin wrapper over sqlite3 (WAL, one shared connection)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._add_missing_columns()

    # ---- migration ---------------------------------------------------
    #
    # CREATE TABLE IF NOT EXISTS does nothing to a table that already exists,
    # so a column added to SCHEMA never reaches a database created before it.
    # The failure is a runtime "no such column" on a machine that has been
    # running for a while and never on a fresh one, which is the worst
    # possible place for it to surface.
    ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
        ("bank_entries", "topic", "TEXT NOT NULL DEFAULT ''"),
    )

    def _add_missing_columns(self) -> None:
        for table, column, spec in self.ADDED_COLUMNS:
            with self._lock:
                have = {r["name"] for r in
                        self._conn.execute(f"PRAGMA table_info({table})")}
                if not have or column in have:
                    continue        # table absent (fresh DB) or already there
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
                self._conn.commit()

    # ---- low level ---------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- settings ----------------------------------------------------
    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO user_settings(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), time.time()),
        )

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM user_settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    # ---- jobs --------------------------------------------------------
    def save_job(self, job: VideoJob) -> None:
        job.updated_at = time.time()
        self.execute(
            "INSERT INTO video_jobs(job_id,automation_id,status,created_at,updated_at,"
            "error,retry_count,scheduled_for,youtube_video_id,payload) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
            "status=excluded.status, updated_at=excluded.updated_at, error=excluded.error,"
            "retry_count=excluded.retry_count, scheduled_for=excluded.scheduled_for,"
            "youtube_video_id=excluded.youtube_video_id, payload=excluded.payload",
            (job.job_id, job.automation_id, job.status, job.created_at, job.updated_at,
             job.error, job.retry_count, job.scheduled_for, job.youtube_video_id,
             json.dumps(job.to_dict(), ensure_ascii=False)),
        )

    def get_job(self, job_id: str) -> VideoJob | None:
        row = self.query_one("SELECT payload FROM video_jobs WHERE job_id=?", (job_id,))
        return VideoJob.from_dict(json.loads(row["payload"])) if row else None

    # ---- automations -------------------------------------------------
    def save_automation(self, request: Any) -> None:
        """Persist a recurring automation so cancelling it survives a restart."""
        now = time.time()
        existing = self.get_automation(request.id)
        created = existing.get("created_at", now) if existing else now
        self.execute(
            "INSERT INTO automations(id,niche,frequency,days,upload_time,"
            "timezone,enabled,created_at,updated_at,cancelled_at,payload) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "niche=excluded.niche, frequency=excluded.frequency, "
            "days=excluded.days, upload_time=excluded.upload_time, "
            "timezone=excluded.timezone, updated_at=excluded.updated_at, "
            "payload=excluded.payload",
            (request.id, request.niche, request.frequency,
             ",".join(str(d) for d in (request.days or [])),
             request.upload_time or "", request.timezone or "", 1,
             created, now, 0,
             json.dumps(request.to_dict(), ensure_ascii=False)),
        )

    def get_automation(self, automation_id: str) -> dict[str, Any] | None:
        row = self.query_one(
            "SELECT * FROM automations WHERE id=?", (automation_id,))
        return dict(row) if row else None

    def list_automations(self, *, include_cancelled: bool = False,
                         limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM automations"
        if not include_cancelled:
            sql += " WHERE enabled=1"
        sql += " ORDER BY created_at DESC LIMIT ?"
        return [dict(r) for r in self.query(sql, (limit,))]

    def cancel_automation(self, automation_id: str) -> bool:
        """Mark it cancelled. Returns False if it was not known.

        Persisted deliberately: the worker's in-memory cancelled set was lost
        on restart, which quietly resurrected automations the user had stopped.
        """
        if self.get_automation(automation_id) is None:
            return False
        self.execute(
            "UPDATE automations SET enabled=0, cancelled_at=?, updated_at=? "
            "WHERE id=?", (time.time(), time.time(), automation_id))
        return True

    def cancelled_automation_ids(self) -> set[str]:
        return {r["id"] for r in
                self.query("SELECT id FROM automations WHERE enabled=0")}

    def count_jobs_for_automation(self, automation_id: str) -> int:
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM video_jobs WHERE automation_id=?",
            (automation_id,))
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------
    # Script bank
    # ------------------------------------------------------------------
    def save_bank_entry(self, entry) -> None:
        """Insert or replace one entry, PRESERVING its used state and review.

        Re-importing a corrected file must not un-use scripts that have
        already become videos, or the same story publishes twice.

        The REVIEW is preserved on the same principle, and only when the
        narration is untouched. A bank file carries no `human` block - that
        is recorded by `stories review`, into the payload - so re-importing a
        file to correct anything else silently discarded every verdict, and
        "ready" went from five to nought with the import reporting success.
        Measured while fixing three character descriptions.

        A CHANGED narration drops the review deliberately: a verdict on
        different words is not a verdict on these.
        """
        existing = self.query_one(
            "SELECT used_at, used_job_id, content_hash, payload "
            "FROM bank_entries WHERE entry_id=?", (entry.entry_id,))
        used_at = float(existing["used_at"]) if existing else 0.0
        used_job = str(existing["used_job_id"]) if existing else ""
        if existing and not (getattr(entry, "human", None) or {}).get(
                "reviewer"):
            same_text = str(existing["content_hash"] or "") == entry.content_hash
            try:
                held = (json.loads(existing["payload"]) or {}).get("human") or {}
            except (ValueError, TypeError):
                held = {}
            if same_text and held.get("reviewer"):
                entry.human = dict(held)
                log_event("BANK", "kept the existing review across a "
                                  "re-import",
                          entry=entry.entry_id, reviewer=held.get("reviewer"),
                          kind=held.get("kind", "human"))
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO bank_entries (entry_id, grp, topic, "
                "shape, language, video_format, made_for_kids, title, "
                "content_hash, est_seconds, imported_at, used_at, "
                "used_job_id, payload) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (entry.entry_id, entry.group, entry.topic, entry.shape,
                 entry.language, entry.video_format,
                 int(bool(entry.made_for_kids)),
                 entry.title, entry.content_hash, float(entry.estimated_seconds),
                 time.time(), used_at, used_job,
                 json.dumps(entry.to_dict(), ensure_ascii=False)))
            self._conn.commit()

    def bank_entries(self, *, group: str = "", topic: str = "",
                     language: str = "", video_format: str = "",
                     unused_only: bool = False,
                     limit: int = 5000) -> list[dict]:
        """Raw bank rows, newest-imported last."""
        clauses, params = [], []
        if group:
            clauses.append("grp=?")
            params.append(group.lower())
        if topic:
            # Same semantics as claim_bank_entry: a topic-less entry is
            # group-wide and counts towards every topic. Listing and claiming
            # disagreeing about what is available would make the status
            # report a lie.
            clauses.append("(topic='' OR topic=?)")
            params.append(topic.lower())
        if language:
            clauses.append("language=?")
            params.append(language.lower())
        if video_format:
            clauses.append("video_format=?")
            params.append(video_format.upper())
        if unused_only:
            clauses.append("used_at<=0")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.query(
            f"SELECT * FROM bank_entries{where} ORDER BY imported_at LIMIT ?",
            (*params, limit))
        return [dict(r) for r in rows]

    def claim_bank_entry(self, *, group: str, language: str,
                         video_format: str, job_id: str,
                         topics: Sequence[str] = (),
                         near_seconds: float = 0.0,
                         tolerance: float = 0.25) -> dict | None:
        """Take the next unused entry and mark it used, atomically.

        Atomic because two automations firing at the same minute would
        otherwise both take entry #1 and publish the same story twice. The
        UPDATE carries `used_at<=0` in its WHERE, so exactly one of them wins.

        `near_seconds` makes the Create screen's duration field a FILTER
        rather than a target: ask for 45s and you get a script whose own
        length is within tolerance of that, instead of a script stretched to
        fit by the speaking-rate re-fit.
        """
        clauses = ["grp=?", "language=?", "video_format=?", "used_at<=0"]
        params: list = [group.lower(), language.lower(), video_format.upper()]
        wanted = [t.strip().lower() for t in topics if t and t.strip()]
        if wanted:
            # An entry with no topic is group-wide and matches any topic
            # selection, so a bank imported before topics existed keeps
            # working instead of becoming unreachable.
            marks = ",".join("?" for _ in wanted)
            clauses.append(f"(topic='' OR topic IN ({marks}))")
            params += wanted
        if near_seconds > 0:
            low = near_seconds * (1.0 - tolerance)
            high = near_seconds * (1.0 + tolerance)
            clauses.append("est_seconds BETWEEN ? AND ?")
            params += [low, high]
        where = " AND ".join(clauses)
        with self._lock:
            row = self._conn.execute(
                f"SELECT * FROM bank_entries WHERE {where} "
                f"ORDER BY imported_at LIMIT 1", tuple(params)).fetchone()
            if row is None:
                return None
            updated = self._conn.execute(
                "UPDATE bank_entries SET used_at=?, used_job_id=? "
                "WHERE entry_id=? AND used_at<=0",
                (time.time(), job_id, row["entry_id"]))
            self._conn.commit()
            if updated.rowcount != 1:
                return None                 # somebody else claimed it first
        return dict(row)

    def claim_specific_bank_entry(self, entry_id: str,
                                  job_id: str) -> dict | None:
        """Claim ONE known entry, atomically. None if somebody got there first.

        Exists so a caller can decide whether it WANTS an entry before
        marking it used. `claim_bank_entry` marks a row used and then hands
        it over, which is fine when any row will do and catastrophic when the
        caller might reject it: the previous consumer looped, and every entry
        it declined - unapproved, unparseable, machine-approved under a
        human-only policy - stayed claimed. One render walked the whole pool
        and consumed it, and since `save_bank_entry` preserves used state,
        reviewing an entry afterwards could not bring it back.

        The atomicity is the same trick: `used_at<=0` in the UPDATE's WHERE,
        so exactly one of two racing callers can win.
        """
        with self._lock:
            updated = self._conn.execute(
                "UPDATE bank_entries SET used_at=?, used_job_id=? "
                "WHERE entry_id=? AND used_at<=0",
                (time.time(), job_id, entry_id))
            self._conn.commit()
            if updated.rowcount != 1:
                return None
            row = self._conn.execute(
                "SELECT * FROM bank_entries WHERE entry_id=?",
                (entry_id,)).fetchone()
        return dict(row) if row is not None else None

    def bank_candidates(self, *, group: str, language: str,
                        video_format: str, topics: Sequence[str] = (),
                        near_seconds: float = 0.0,
                        tolerance: float = 0.25,
                        limit: int = 200) -> list[dict]:
        """Unused entries that MIGHT be claimable, oldest first. No mutation.

        The read half of what `claim_bank_entry` does in one step. The caller
        filters these on whatever it cannot express in SQL - approval state,
        reviewer kind, whether the payload parses - and then claims the one
        it picked with `claim_specific_bank_entry`.
        """
        # Language is matched on the COMPATIBLE set, not the exact string.
        # A request for "en-IN" found none of fifteen banked "en" entries
        # before this - see languages.claimable for why base language alone
        # is not the right rule either.
        from .languages import claimable

        langs = [c.lower() for c in claimable(language)] or [language.lower()]
        marks = ",".join("?" for _ in langs)
        clauses = ["grp=?", f"language IN ({marks})", "video_format=?",
                   "used_at<=0"]
        params: list = [group.lower(), *langs, video_format.upper()]
        wanted = [t.strip().lower() for t in topics if t and t.strip()]
        if wanted:
            topic_marks = ",".join("?" for _ in wanted)
            clauses.append(f"(topic='' OR topic IN ({topic_marks}))")
            params += wanted
        if near_seconds > 0:
            clauses.append("est_seconds BETWEEN ? AND ?")
            params += [near_seconds * (1.0 - tolerance),
                       near_seconds * (1.0 + tolerance)]
        rows = self.query(
            f"SELECT * FROM bank_entries WHERE {' AND '.join(clauses)} "
            f"ORDER BY imported_at LIMIT ?", (*params, limit))
        return [dict(r) for r in rows]

    def release_bank_entry(self, entry_id: str) -> None:
        """Put an entry back in the pool.

        Called when a claimed script fails to render: the script is fine, the
        render was not, and burning a curated entry on a transient ffmpeg
        failure is pure loss.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE bank_entries SET used_at=0, used_job_id='' "
                "WHERE entry_id=?", (entry_id,))
            self._conn.commit()

    def delete_bank_entry(self, entry_id: str) -> bool:
        """Remove an entry. Returns whether there was one to remove.

        Needed because an entry cannot otherwise be CORRECTED. The entry_id is
        derived from a hash of the narration, so fixing a line produces a new
        id - and the variety gate then compares the fix against the original
        still sitting in the table and rejects it as a near-duplicate at 62%
        4-gram overlap. Measured, while removing a policy violation from a
        story. Retire the old entry, import the fix.
        """
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM bank_entries WHERE entry_id=?", (entry_id,))
            self._conn.commit()
        return cur.rowcount > 0

    # A job is presumed dead after this long with no status change. Generous:
    # a fifteen-minute long-form video spends over an hour in ffmpeg on two
    # ARM cores, and reclaiming an entry from a render that is merely slow is
    # how the same script publishes twice.
    STALE_JOB_HOURS = 3.0

    def orphaned_bank_entries(self, *,
                              stale_hours: float | None = None) -> list[dict]:
        """Entries claimed by a job that will never finish.

        A claim is handed back by run()'s exception handlers, which only run
        if the process survives to reach them. A kill does not: this project's
        own long-form render was OOM-killed mid-encode (exit 137) and left its
        entry marked used, its job frozen at RENDERING, and no way back.
        Ctrl-C, a service restart and a power cut all do the same.

        Three ways to be orphaned, and the third is the one that matters:
          * the job is absent from video_jobs entirely;
          * the job reached a terminal failure;
          * the job is mid-stage but has not moved for `stale_hours`. Status
            alone cannot tell a dead process from a busy one - the OOM-killed
            render sat at RENDERING looking exactly like work in progress - so
            the only available signal is that nothing has changed.
        """
        cutoff = float(self.STALE_JOB_HOURS if stale_hours is None
                       else stale_hours) * 3600.0
        rows = self.query(
            "SELECT b.entry_id, b.title, b.used_job_id, b.used_at, "
            "       j.status AS job_status, j.updated_at AS job_updated "
            "FROM bank_entries b "
            "LEFT JOIN video_jobs j ON j.job_id = b.used_job_id "
            "WHERE b.used_at > 0")
        # Never reclaim from a job that is WAITING for the user - it has
        # finished its work and the entry is legitimately spent.
        finished = {JobStatus.FAILED.value, JobStatus.REJECTED.value}
        settled = {JobStatus.PUBLISHED.value, JobStatus.SCHEDULED.value,
                   JobStatus.AWAITING_APPROVAL.value, JobStatus.READY.value}
        now = time.time()
        out = []
        for row in rows:
            record = dict(row)
            status = record.get("job_status")
            if status is None:
                record["reason"] = "job never recorded"
            elif status in finished:
                record["reason"] = f"job {status}"
            elif status in settled:
                continue                    # spent on purpose
            else:
                idle = now - float(record.get("job_updated") or 0.0)
                if idle < cutoff:
                    continue                # still plausibly working
                record["reason"] = (f"stuck at {status} for "
                                    f"{idle / 3600:.1f}h")
            out.append(record)
        return out

    def bank_counts(self) -> list[dict]:
        """Per group/language/format: how many entries, how many left."""
        rows = self.query(
            "SELECT grp, language, video_format, COUNT(*) AS total, "
            "SUM(CASE WHEN used_at<=0 THEN 1 ELSE 0 END) AS unused "
            "FROM bank_entries GROUP BY grp, language, video_format "
            "ORDER BY grp, language, video_format")
        return [dict(r) for r in rows]

    def automation_finished_runs(self, automation_id: str) -> int:
        """How many of an automation's videos have reached a final state.

        PUBLISHED or SCHEDULED only. Deliberately NOT counting
        AWAITING_APPROVAL or READY: those still need the user, so a "just
        once" automation holding one of them is not finished and must stay
        visible in the Schedule tab.
        """
        if not automation_id:
            return 0
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM video_jobs "
            "WHERE automation_id=? AND status IN (?, ?)",
            (automation_id, JobStatus.PUBLISHED.value,
             JobStatus.SCHEDULED.value))
        return int(row["n"]) if row else 0

    def automation_has_approved_run(self, automation_id: str) -> bool:
        """True when a human has already approved a video from this automation.

        Used by the publish gate: the Made-for-Kids classification needs an
        explicit human confirmation, but it needs it ONCE per automation, not
        once per video. Without this, a kids automation set to "publish
        without asking" waited for approval on every single run - which made
        the setting a lie.
        """
        if not automation_id:
            return False
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM video_jobs "
            "WHERE automation_id=? AND status IN (?, ?, ?)",
            (automation_id, JobStatus.PUBLISHED.value,
             JobStatus.SCHEDULED.value, JobStatus.READY.value))
        return bool(row and int(row["n"]) > 0)

    def delete_jobs(self, *, job_ids: list[str] | None = None,
                    keep_active: bool = True,
                    older_than: float | None = None) -> list[VideoJob]:
        """Remove job ROWS and return the jobs that were removed.

        Returns them so the caller can delete the matching directories - the
        database row and the 57 MB on disk are two halves of one job, and
        clearing the list without freeing the disk would be the worst of both.

        `keep_active` protects anything still in flight or waiting for the
        user: clearing the dashboard should tidy up history, not silently
        abandon a render that is halfway through.
        """
        # READY and SCHEDULED are not history, and leaving them out of this
        # tuple was data loss. READY means approved and rendered with the
        # upload still to happen, so clearing it threw away the video the
        # upload was about to send. SCHEDULED means already uploaded with a
        # publishAt, so the media is spare but the ROW is the only record that
        # something is going live - clearing it makes the Schedule tab forget
        # a video that will still publish.
        active = (JobStatus.IDEA.value, JobStatus.RESEARCH.value,
                  JobStatus.SCRIPT.value, JobStatus.VOICE.value,
                  JobStatus.VISUALS.value, JobStatus.RENDERING.value,
                  JobStatus.QUALITY_CHECK.value,
                  JobStatus.AWAITING_APPROVAL.value,
                  JobStatus.READY.value,
                  JobStatus.SCHEDULED.value)
        doomed: list[VideoJob] = []
        for job in self.list_jobs(limit=5000):
            if job_ids is not None and job.job_id not in job_ids:
                continue
            if keep_active and job.status in active:
                continue
            if older_than is not None and (job.updated_at or 0) > older_than:
                continue
            doomed.append(job)
        if not doomed:
            return []
        with self._lock:
            self._conn.executemany("DELETE FROM video_jobs WHERE job_id = ?",
                                   [(j.job_id,) for j in doomed])
            self._conn.commit()
        return doomed

    def list_jobs(self, status: str | None = None, limit: int = 100) -> list[VideoJob]:
        if status:
            rows = self.query(
                "SELECT payload FROM video_jobs WHERE status=? "
                "ORDER BY updated_at DESC LIMIT ?", (status, limit))
        else:
            rows = self.query(
                "SELECT payload FROM video_jobs ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [VideoJob.from_dict(json.loads(r["payload"])) for r in rows]

    def pending_jobs(self, limit: int = 50) -> list[VideoJob]:
        terminal = (JobStatus.PUBLISHED.value, JobStatus.FAILED.value,
                    JobStatus.REJECTED.value, JobStatus.AWAITING_APPROVAL.value,
                    JobStatus.SCHEDULED.value, JobStatus.READY.value)
        placeholders = ",".join("?" * len(terminal))
        rows = self.query(
            f"SELECT payload FROM video_jobs WHERE status NOT IN ({placeholders}) "
            "ORDER BY created_at ASC LIMIT ?", (*terminal, limit))
        return [VideoJob.from_dict(json.loads(r["payload"])) for r in rows]

    def count_jobs_since(self, since_ts: float, statuses: tuple[str, ...]) -> int:
        placeholders = ",".join("?" * len(statuses))
        row = self.query_one(
            f"SELECT COUNT(*) c FROM video_jobs WHERE updated_at >= ? "
            f"AND status IN ({placeholders})", (since_ts, *statuses))
        return int(row["c"]) if row else 0

    # ---- research ----------------------------------------------------
    def save_research(self, niche: str, videos: list[Any]) -> None:
        ts = time.time()
        for v in videos:
            d = v.to_dict() if hasattr(v, "to_dict") else dict(v)
            self.execute(
                "INSERT INTO research_videos(video_id,niche,title,channel_id,channel_title,"
                "published_at,views,likes,comments,duration_seconds,is_short,view_velocity,"
                "engagement_rate,performance_ratio,is_breakout,viral_score,payload,fetched_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(video_id) DO UPDATE SET views=excluded.views,"
                "likes=excluded.likes, comments=excluded.comments,"
                "view_velocity=excluded.view_velocity, viral_score=excluded.viral_score,"
                "payload=excluded.payload, fetched_at=excluded.fetched_at",
                (d["video_id"], niche, d["title"], d.get("channel_id"), d.get("channel_title"),
                 d.get("published_at"), d.get("views", 0), d.get("likes", 0),
                 d.get("comments", 0), d.get("duration_seconds", 0),
                 int(bool(d.get("is_short"))), d.get("view_velocity", 0.0),
                 d.get("engagement_rate", 0.0), d.get("performance_ratio", 0.0),
                 int(bool(d.get("is_breakout"))), d.get("viral_score", 0.0),
                 json.dumps(d, ensure_ascii=False), ts),
            )

    def recent_research(self, niche: str, max_age_hours: float) -> list[dict[str, Any]]:
        cutoff = time.time() - max_age_hours * 3600
        rows = self.query(
            "SELECT payload FROM research_videos WHERE niche=? AND fetched_at>=? "
            "ORDER BY viral_score DESC", (niche, cutoff))
        return [json.loads(r["payload"]) for r in rows]

    # ---- ideas / scripts --------------------------------------------
    def save_idea(self, niche: str, idea: Any) -> None:
        d = idea.to_dict() if hasattr(idea, "to_dict") else dict(idea)
        self.execute(
            "INSERT INTO content_ideas(idea_id,niche,topic,working_title,hook_type,"
            "opportunity_score,used,payload,created_at) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(idea_id) DO UPDATE SET payload=excluded.payload",
            (d["idea_id"], niche, d.get("topic"), d.get("working_title"),
             d.get("hook_type"), d.get("opportunity_score", 0.0), 0,
             json.dumps(d, ensure_ascii=False), time.time()),
        )

    def mark_idea_used(self, idea_id: str) -> None:
        self.execute("UPDATE content_ideas SET used=1 WHERE idea_id=?", (idea_id,))

    def used_topics(self, niche: str, limit: int = 200) -> list[str]:
        rows = self.query(
            "SELECT topic FROM content_ideas WHERE niche=? AND used=1 "
            "ORDER BY created_at DESC LIMIT ?", (niche, limit))
        return [r["topic"] or "" for r in rows]

    def save_script(self, script: Any, text_hash: str) -> None:
        d = script.to_dict() if hasattr(script, "to_dict") else dict(script)
        self.execute(
            "INSERT INTO scripts(script_id,idea_id,language,provider,retention_score,"
            "text_hash,payload,created_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(script_id) DO UPDATE SET payload=excluded.payload",
            (d["script_id"], d.get("idea_id"), d.get("language"), d.get("provider"),
             d.get("retention_score", 0.0), text_hash,
             json.dumps(d, ensure_ascii=False), time.time()),
        )

    def recent_script_texts(
            self, limit: int = 50) -> list[tuple[str, str, str]]:
        """(script_id, text, provider), newest first.

        The PROVIDER is returned because the self-similarity check needs it.
        A banked script's provider is "bank:<entry_id>", and two renders of
        one entry are the same text by design - so without it, retrying a
        banked video after a failed render reads as a 100% duplicate of its
        own failed attempt and can never publish.
        """
        rows = self.query(
            "SELECT script_id, provider, payload FROM scripts "
            "ORDER BY created_at DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            try:
                payload = json.loads(r["payload"])
            except (ValueError, TypeError):
                continue
            out.append((r["script_id"],
                        payload.get("script", ""),
                        r["provider"] or payload.get("provider", "")))
        return out

    # ---- assets ------------------------------------------------------
    def save_assets(self, job_id: str, assets: list[Any]) -> None:
        for a in assets:
            d = a.to_dict() if hasattr(a, "to_dict") else dict(a)
            self.execute(
                "INSERT INTO assets(job_id,asset,source,license,prompt,attribution,url,"
                "scene_index,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, d["asset"], d["source"], d["license"], d.get("prompt", ""),
                 d.get("attribution", ""), d.get("url", ""), d.get("scene_index", -1),
                 time.time()),
            )

    # ---- published + analytics --------------------------------------
    def save_published(self, job: VideoJob) -> None:
        script = job.script or {}
        idea = job.idea or {}
        meta = job.metadata or {}
        self.execute(
            "INSERT INTO published_videos(youtube_video_id,job_id,title,niche,topic,"
            "hook_type,title_type,duration,visual_style,published_time,payload) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(youtube_video_id) DO UPDATE SET "
            "payload=excluded.payload",
            (job.youtube_video_id, job.job_id, meta.get("title", ""),
             (job.request or {}).get("niche", ""), idea.get("topic", ""),
             idea.get("hook_type", ""), _title_type(meta.get("title", "")),
             script.get("estimated_duration", 0.0),
             (job.request or {}).get("style", ""),
             job.published_at or job.scheduled_for,
             json.dumps(job.to_dict(), ensure_ascii=False)),
        )

    def save_analytics(self, video_id: str, row: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO analytics(youtube_video_id,collected_at,views,watch_time_minutes,"
            "avg_view_duration,avg_view_percentage,likes,comments,subscribers_gained,"
            "impressions,ctr,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (video_id, time.time(), row.get("views", 0),
             row.get("watch_time_minutes", 0.0), row.get("avg_view_duration", 0.0),
             row.get("avg_view_percentage", 0.0), row.get("likes", 0),
             row.get("comments", 0), row.get("subscribers_gained", 0),
             row.get("impressions", 0), row.get("ctr", 0.0),
             json.dumps(row, ensure_ascii=False)),
        )

    # ---- quota -------------------------------------------------------
    def add_quota(self, day: str, units: int, op: str) -> int:
        row = self.query_one("SELECT units, detail FROM quota_usage WHERE day=?", (day,))
        detail = json.loads(row["detail"]) if row else {}
        detail[op] = detail.get(op, 0) + 1
        total = (int(row["units"]) if row else 0) + units
        self.execute(
            "INSERT INTO quota_usage(day,units,detail) VALUES(?,?,?) "
            "ON CONFLICT(day) DO UPDATE SET units=excluded.units, detail=excluded.detail",
            (day, total, json.dumps(detail)))
        return total

    def quota_used(self, day: str) -> int:
        row = self.query_one("SELECT units FROM quota_usage WHERE day=?", (day,))
        return int(row["units"]) if row else 0

    # ---- strategy ----------------------------------------------------
    def upsert_strategy(self, dimension: str, value: str, weight: float,
                        samples: int, score: float) -> None:
        self.execute(
            "INSERT INTO strategy_weights(dimension,value,weight,samples,score,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(dimension,value) DO UPDATE SET "
            "weight=excluded.weight, samples=excluded.samples, score=excluded.score,"
            "updated_at=excluded.updated_at",
            (dimension, value, weight, samples, score, time.time()))

    def strategy(self, dimension: str) -> dict[str, float]:
        rows = self.query(
            "SELECT value, weight FROM strategy_weights WHERE dimension=?", (dimension,))
        return {r["value"]: float(r["weight"]) for r in rows}


def _title_type(title: str) -> str:
    t = (title or "").lower()
    if t.startswith(("why", "how", "what", "who", "when", "can ", "is ", "do ")) or "?" in t:
        return "question"
    if any(t.startswith(f"{n} ") or f" {n} " in t[:14] for n in
           ("3", "5", "7", "10", "top")):
        return "listicle"
    if any(w in t for w in ("found", "discovered", "revealed", "hidden", "secret")):
        return "discovery"
    if any(w in t for w in ("never", "stop", "don't", "mistake", "wrong")):
        return "warning"
    return "statement"
