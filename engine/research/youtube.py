"""YouTube research via the official Data API v3 (spec section 4).

No scraping.  Only `search.list`, `videos.list` and `channels.list`, all of
which are documented public endpoints.

QUOTA IS THE REAL CONSTRAINT, not money.  A new Google Cloud project gets
10,000 units/day:
    search.list      = 100 units   <- the expensive one
    videos.list      =   1 unit    (up to 50 ids per call)
    channels.list    =   1 unit    (up to 50 ids per call)
    videos.insert    = 1600 units  <- an upload
So a research run of 3 searches costs ~302 units and an upload costs 1600.
`QuotaGuard` tracks spend against the Pacific-midnight reset and refuses calls
that would exceed the budget, reserving room for the day's uploads.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

import httpx

from ..core.config import Config
from ..core.db import Database
from ..core.logging import log_event
from ..core.models import ResearchVideo
from ..core.niche import NicheProfile
from ..core.util import pacific_day, retry, utc_now
from .scoring import score_all

API_BASE = "https://www.googleapis.com/youtube/v3"


class QuotaExceeded(RuntimeError):
    pass


class QuotaGuard:
    """Tracks YouTube API unit spend per Pacific day."""

    def __init__(self, cfg: Config, db: Database | None = None):
        self.cfg = cfg
        self.db = db
        self.limit = int(cfg.get("youtube.daily_quota_units", 10000))
        self.costs: dict[str, int] = dict(cfg.get("youtube.quota_costs", {}) or {})
        # Keep room for the day's uploads so research cannot starve publishing.
        per_upload = self.costs.get("video_insert", 1600)
        self.reserve = per_upload * int(cfg.get("automation.daily_video_limit", 3))
        self._local = 0

    def cost(self, op: str) -> int:
        return int(self.costs.get(op, 1))

    def used(self) -> int:
        if self.db is not None:
            return self.db.quota_used(pacific_day())
        return self._local

    def remaining(self, *, respect_reserve: bool = True) -> int:
        cap = self.limit - (self.reserve if respect_reserve else 0)
        return max(cap - self.used(), 0)

    def check(self, op: str, *, respect_reserve: bool = True) -> None:
        need = self.cost(op)
        if need > self.remaining(respect_reserve=respect_reserve):
            raise QuotaExceeded(
                f"YouTube quota: {op} needs {need} units, "
                f"{self.remaining(respect_reserve=respect_reserve)} available "
                f"(used {self.used()}/{self.limit} today"
                + (f", {self.reserve} reserved for uploads)" if respect_reserve else ")"))

    def spend(self, op: str) -> int:
        units = self.cost(op)
        if self.db is not None:
            return self.db.add_quota(pacific_day(), units, op)
        self._local += units
        return self._local


def _keywords(text: str) -> list[str]:
    """Content words from a niche name, for relevance checks."""
    stop = {"and", "the", "for", "with", "of", "to", "in", "on", "a", "an"}
    return [w for w in re.split(r"[^\w]+", (text or "").lower())
            if w and w not in stop]


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def parse_iso8601_duration(value: str) -> int:
    """PT1M30S -> 90.  Handles hours/minutes/seconds and bare days."""
    import re
    if not value:
        return 0
    m = re.fullmatch(
        r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", value.strip())
    if not m:
        return 0
    days, hours, minutes, seconds = (float(x) if x else 0.0 for x in m.groups())
    return int(days * 86400 + hours * 3600 + minutes * 60 + seconds)


class YouTubeResearch:
    def __init__(self, cfg: Config, db: Database | None = None,
                 quota: QuotaGuard | None = None):
        self.cfg = cfg
        self.db = db
        self.api_key = cfg.secret("YOUTUBE_API_KEY")
        self.quota = quota or QuotaGuard(cfg, db)
        self.timeout = 45

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    # ------------------------------------------------------------------
    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        params = {**params, "key": self.api_key}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(f"{API_BASE}/{path}", params=params)
        if resp.status_code == 403:
            body = resp.text[:400]
            if "quotaExceeded" in body:
                raise QuotaExceeded("YouTube API reports quotaExceeded for today")
            raise RuntimeError(f"YouTube API 403 (check key restrictions): {body}")
        if resp.status_code >= 400:
            raise RuntimeError(f"YouTube API {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    # ------------------------------------------------------------------
    def build_queries(self, niche: str, profile: NicheProfile,
                      extra_keywords: list[str] | None = None,
                      limit: int = 3) -> list[str]:
        base = niche.strip()
        queries = [base]
        for mod in profile.search_modifiers:
            queries.append(f"{base} {mod}")
        for kw in (extra_keywords or []):
            queries.append(f"{base} {kw}")
        # De-duplicate, preserve order, respect the quota-driven limit.
        seen: set[str] = set()
        out: list[str] = []
        for q in queries:
            key = q.lower().strip()
            if key and key not in seen:
                seen.add(key)
                out.append(q.strip())
        return out[:max(1, limit)]

    def search_ids(self, query: str, *, max_results: int = 25,
                   published_within_days: int = 90,
                   video_duration: str = "any",
                   region_code: str = "IN",
                   relevance_language: str = "en",
                   order: str = "viewCount") -> list[str]:
        from datetime import timedelta
        self.quota.check("search_list")
        published_after = (utc_now() - timedelta(days=published_within_days))
        params = {
            "part": "id",
            "q": query,
            "type": "video",
            "order": order,
            "maxResults": min(max(max_results, 1), 50),
            "publishedAfter": published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "regionCode": region_code,
            "relevanceLanguage": relevance_language,
            "safeSearch": "moderate",
        }
        if video_duration in {"short", "medium", "long"}:
            params["videoDuration"] = video_duration
        data = retry(lambda: self._get("search", params), attempts=3, backoff=3,
                     tag="RESEARCH", what=f"search '{query}'")
        self.quota.spend("search_list")
        ids = [item["id"]["videoId"] for item in data.get("items", [])
               if item.get("id", {}).get("videoId")]
        log_event("RESEARCH", "search complete", query=query, results=len(ids),
                  quota_used=self.quota.used())
        return ids

    def hydrate(self, video_ids: list[str]) -> list[ResearchVideo]:
        """videos.list + channels.list for the full public signal set."""
        if not video_ids:
            return []
        videos: list[ResearchVideo] = []
        channel_ids: set[str] = set()

        for batch in _chunks(list(dict.fromkeys(video_ids)), 50):
            self.quota.check("videos_list")
            data = retry(lambda b=batch: self._get("videos", {
                "part": "snippet,statistics,contentDetails,status",
                "id": ",".join(b), "maxResults": 50}),
                attempts=3, backoff=3, tag="RESEARCH", what="videos.list")
            self.quota.spend("videos_list")
            for item in data.get("items", []):
                snippet = item.get("snippet", {}) or {}
                stats = item.get("statistics", {}) or {}
                details = item.get("contentDetails", {}) or {}
                duration = parse_iso8601_duration(details.get("duration", ""))
                thumbs = snippet.get("thumbnails", {}) or {}
                best_thumb = (thumbs.get("maxres") or thumbs.get("standard")
                              or thumbs.get("high") or thumbs.get("medium") or {})
                video = ResearchVideo(
                    video_id=item.get("id", ""),
                    title=snippet.get("title", ""),
                    channel_id=snippet.get("channelId", ""),
                    channel_title=snippet.get("channelTitle", ""),
                    published_at=snippet.get("publishedAt", ""),
                    duration_seconds=duration,
                    views=int(stats.get("viewCount", 0) or 0),
                    likes=int(stats.get("likeCount", 0) or 0),
                    comments=int(stats.get("commentCount", 0) or 0),
                    description=(snippet.get("description", "") or "")[:1500],
                    tags=list(snippet.get("tags", []) or [])[:25],
                    category_id=str(snippet.get("categoryId", "")),
                    thumbnail_url=best_thumb.get("url", ""),
                    # YouTube does not expose a "is Short" flag; duration <= 180s
                    # plus a vertical-friendly length is the practical proxy.
                    is_short=duration > 0 and duration <= 180,
                )
                if video.video_id:
                    videos.append(video)
                    if video.channel_id:
                        channel_ids.add(video.channel_id)

        stats_by_channel = self._channel_stats(sorted(channel_ids))
        for v in videos:
            cs = stats_by_channel.get(v.channel_id, {})
            v.channel_subscribers = cs.get("subscribers", 0)
            v.channel_video_count = cs.get("videos", 0)
            v.channel_total_views = cs.get("views", 0)
        return videos

    def _channel_stats(self, channel_ids: list[str]) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for batch in _chunks(channel_ids, 50):
            try:
                self.quota.check("channels_list")
            except QuotaExceeded:
                log_event("RESEARCH", "skipping channel stats - quota guard")
                break
            data = retry(lambda b=batch: self._get("channels", {
                "part": "statistics", "id": ",".join(b), "maxResults": 50}),
                attempts=3, backoff=3, tag="RESEARCH", what="channels.list")
            self.quota.spend("channels_list")
            for item in data.get("items", []):
                s = item.get("statistics", {}) or {}
                out[item.get("id", "")] = {
                    "subscribers": int(s.get("subscriberCount", 0) or 0),
                    "videos": int(s.get("videoCount", 0) or 0),
                    "views": int(s.get("viewCount", 0) or 0),
                }
        return out

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Seeded-channel research: watch a fixed set of channels cheaply
    # ------------------------------------------------------------------
    def _seed_path(self) -> Path:
        return Path(str(self.cfg.get("paths.workspace", "workspace"))) / \
            "seed_channels.json"

    def _load_seeds(self) -> dict[str, list[str]]:
        path = self._seed_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {k: list(v) for k, v in data.items() if isinstance(v, list)}
        except (ValueError, OSError):
            return {}

    def _save_seeds(self, seeds: dict[str, list[str]]) -> None:
        path = self._seed_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(seeds, indent=1, ensure_ascii=False),
                            encoding="utf-8")
        except OSError as exc:
            log_event("RESEARCH", "could not cache seed channels",
                      error=str(exc)[:120])

    def seed_channels(self, niche: str, profile: NicheProfile, *,
                      limit: int = 4) -> list[str]:
        """Channel ids worth watching for this niche. Discovered once, cached.

        This is the ONLY place a `search.list` is spent in the cheap path, and
        it happens once per niche for the lifetime of the install. Everything
        afterwards is 1-unit calls against these ids.
        """
        key = (niche or "").strip().lower()
        seeds = self._load_seeds()
        if seeds.get(key):
            return seeds[key][:limit]
        if not self.configured:
            return []
        try:
            self.quota.check("search_list")
        except QuotaExceeded as exc:
            log_event("RESEARCH", "cannot discover seed channels today",
                      reason=str(exc)[:120])
            return []
        # The NICHE only. Including profile.audience put "5-7" in the query
        # and "kids bedtime stories 5-7" returned a comedy channel whose top
        # video was "Indian Schools, Gully Cricket & Summer Holidays" at 39M
        # views - relevant to nothing, and it would have seeded every future
        # research run for that niche because the result is cached.
        query = (niche or "").strip()
        try:
            data = self._get("search", {
                "part": "snippet", "type": "channel", "q": query,
                # Over-fetch: the relevance filter below discards channels
                # whose uploads do not actually match the niche, so the
                # candidate pool has to be bigger than the target.
                "maxResults": min(25, max(limit * 4, 10)),
                "regionCode": str(self.cfg.get("research.region_code", "IN")),
                "relevanceLanguage": str(
                    self.cfg.get("research.relevance_language", "en")),
            })
            self.quota.spend("search_list")
        except Exception as exc:                # noqa: BLE001
            log_event("RESEARCH", "seed channel discovery failed",
                      error=str(exc)[:140])
            return []
        found: list[str] = []
        for item in data.get("items", []):
            cid = (((item.get("snippet") or {}).get("channelId"))
                   or ((item.get("id") or {}).get("channelId")) or "")
            if cid and cid not in found:
                found.append(cid)
        found = self._keep_relevant(found, niche, limit=limit)
        if found:
            seeds[key] = found
            self._save_seeds(seeds)
            log_event("RESEARCH", "seed channels discovered and cached",
                      niche=niche, channels=len(found), units_spent=100)
        else:
            log_event("RESEARCH", "no channel matched this niche closely "
                      "enough to seed; falling back to keyword search",
                      niche=niche)
        return found

    def _keep_relevant(self, channel_ids: list[str], niche: str, *,
                       limit: int) -> list[str]:
        """Drop channels whose recent uploads have nothing to do with the niche.

        Channel search relevance is weak and the result is CACHED FOREVER, so
        a bad seed poisons every future run for that niche. Validating costs
        one unit per channel and is checked against the titles the channel
        actually publishes rather than against its own description, which is
        marketing.
        """
        wanted = {w for w in _keywords(niche) if len(w) > 3}
        if not wanted or not channel_ids:
            return channel_ids[:limit]
        playlists = self._uploads_playlists(channel_ids)
        scored: list[tuple[float, str]] = []
        for cid, playlist in playlists.items():
            ids = self._playlist_video_ids(playlist, limit=10)
            if not ids:
                continue
            titles = " ".join(v.title.lower() for v in self.hydrate(ids))
            hits = sum(1 for w in wanted if w in titles)
            share = hits / len(wanted)
            scored.append((share, cid))
            log_event("RESEARCH", "seed candidate checked", channel=cid,
                      niche_overlap=f"{share:.0%}")
        # A third of the niche's own words appearing in ten recent titles is a
        # low bar deliberately: it rejects the comedy channel that matched
        # "kids bedtime stories" while keeping a channel that calls them
        # "moral stories for children".
        keep = [cid for share, cid in sorted(scored, reverse=True)
                if share >= 0.34]
        return keep[:limit]

    def _uploads_playlists(self, channel_ids: list[str]) -> dict[str, str]:
        """channel id -> uploads playlist id. One unit for up to 50 channels."""
        out: dict[str, str] = {}
        for batch in _chunks(channel_ids, 50):
            try:
                self.quota.check("channels_list")
                data = self._get("channels", {
                    "part": "contentDetails", "id": ",".join(batch),
                    "maxResults": 50})
                self.quota.spend("channels_list")
            except Exception as exc:            # noqa: BLE001
                log_event("RESEARCH", "uploads playlist lookup failed",
                          error=str(exc)[:140])
                continue
            for item in data.get("items", []):
                playlist = (((item.get("contentDetails") or {})
                             .get("relatedPlaylists") or {}).get("uploads"))
                if playlist:
                    out[str(item.get("id", ""))] = str(playlist)
        return out

    def _playlist_video_ids(self, playlist_id: str, *,
                            limit: int = 50) -> list[str]:
        """Recent uploads from one playlist. One unit per 50."""
        try:
            self.quota.check("playlist_items")
            data = self._get("playlistItems", {
                "part": "contentDetails", "playlistId": playlist_id,
                "maxResults": min(50, max(1, limit))})
            self.quota.spend("playlist_items")
        except Exception as exc:                # noqa: BLE001
            log_event("RESEARCH", "playlist read failed",
                      playlist=playlist_id, error=str(exc)[:140])
            return []
        out: list[str] = []
        for item in data.get("items", []):
            vid = ((item.get("contentDetails") or {}).get("videoId") or "")
            if vid:
                out.append(str(vid))
        return out

    def research_channels(self, niche: str, profile: NicheProfile, *,
                          per_channel: int = 50) -> list[ResearchVideo]:
        """Everything the seeded channels published recently, scored.

        Returns [] rather than raising when there are no seeds, so the caller
        can fall back to keyword search.
        """
        channels = self.seed_channels(niche, profile)
        if not channels:
            return []
        playlists = self._uploads_playlists(channels)
        if not playlists:
            return []
        ids: list[str] = []
        for playlist in playlists.values():
            ids += self._playlist_video_ids(playlist, limit=per_channel)
        if not ids:
            return []
        videos = self.hydrate(ids)
        spent = 1 + 2 * len(playlists)
        log_event("RESEARCH", "seeded-channel corpus", channels=len(playlists),
                  videos=len(videos), approx_units=spent,
                  versus_keyword_search=100 * int(
                      self.cfg.get("research.max_queries", 3)) + 2)
        min_views = int(self.cfg.get("research.min_views", 5000))
        videos = [v for v in videos if v.views >= min_views]
        return score_all(
            videos, self.cfg.get("scoring.weights"),
            float(self.cfg.get("scoring.breakout_ratio_threshold", 2.5)))

    def research(self, niche: str, profile: NicheProfile, *,
                 video_format: str = "SHORT",
                 extra_keywords: list[str] | None = None,
                 use_cache: bool = True) -> list[ResearchVideo]:
        """Full research run: cache -> search -> hydrate -> score."""
        cache_ttl = float(self.cfg.get("research.cache_ttl_hours", 12))
        if use_cache and self.db is not None:
            cached = self.db.recent_research(niche, cache_ttl)
            min_needed = int(self.cfg.get("research.max_results_per_query", 25))
            if len(cached) >= min_needed:
                log_event("RESEARCH", "using cached corpus", videos=len(cached),
                          niche=niche, ttl_hours=cache_ttl)
                return score_all(
                    [ResearchVideo.from_dict(c) for c in cached],
                    self.cfg.get("scoring.weights"),
                    float(self.cfg.get("scoring.breakout_ratio_threshold", 2.5)))

        if not self.configured:
            raise RuntimeError(
                "YOUTUBE_API_KEY is not set - research cannot run. "
                "See docs/YOUTUBE_SETUP.md (free, no credit card).")

        log_event("RESEARCH", "started", niche=niche, format=video_format)
        max_queries = int(self.cfg.get("research.max_queries", 3))
        # Never spend more than half the remaining budget on one research run.
        affordable = max(1, self.quota.remaining() // (2 * self.quota.cost("search_list")))
        queries = self.build_queries(niche, profile, extra_keywords,
                                     limit=min(max_queries, affordable))

        duration_filter = "short" if video_format != "LONGFORM" else "medium"
        all_ids: list[str] = []
        for q in queries:
            try:
                all_ids += self.search_ids(
                    q,
                    max_results=int(self.cfg.get("research.max_results_per_query", 25)),
                    published_within_days=int(self.cfg.get("research.published_within_days", 90)),
                    video_duration=duration_filter,
                    region_code=str(self.cfg.get("research.region_code", "IN")),
                    relevance_language=str(self.cfg.get("research.relevance_language", "en")),
                )
            except QuotaExceeded as exc:
                log_event("RESEARCH", "stopping searches", reason=str(exc)[:160])
                break

        videos = self.hydrate(all_ids)
        min_views = int(self.cfg.get("research.min_views", 5000))
        videos = [v for v in videos if v.views >= min_views]
        videos = score_all(
            videos, self.cfg.get("scoring.weights"),
            float(self.cfg.get("scoring.breakout_ratio_threshold", 2.5)))

        if self.db is not None and videos:
            self.db.save_research(niche, videos)
        breakout_count = sum(1 for v in videos if v.is_breakout)
        log_event("RESEARCH", f"{len(videos)} videos found", niche=niche,
                  quota_used=self.quota.used())
        log_event("RESEARCH", f"{breakout_count} breakout videos")
        if not videos:
            raise RuntimeError(
                f"research returned no videos for '{niche}' above "
                f"{min_views} views in the last "
                f"{self.cfg.get('research.published_within_days')} days")
        return videos
