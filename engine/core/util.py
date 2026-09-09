"""Shared helpers: retry, subprocess, text, time/timezone, similarity, paths."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence, TypeVar
from zoneinfo import ZoneInfo

from .logging import log_event

T = TypeVar("T")


# --------------------------------------------------------------------------
# Retry (spec section 22)
# --------------------------------------------------------------------------
class RetryError(RuntimeError):
    def __init__(self, message: str, attempts: int, last: BaseException | None = None):
        super().__init__(message)
        self.attempts = attempts
        self.last = last


def retry(fn: Callable[[], T], *, attempts: int = 3, backoff: float = 2.0,
          tag: str = "RETRY", what: str = "operation",
          exceptions: tuple[type[BaseException], ...] = (Exception,)) -> T:
    """Call `fn` with exponential backoff. Raises RetryError after `attempts`."""
    last: BaseException | None = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except exceptions as exc:            # noqa: PERF203 - intentional
            last = exc
            if i >= attempts:
                break
            wait = backoff * (2 ** (i - 1))
            log_event(tag, f"{what} failed, retrying in {wait:.0f}s",
                      attempt=f"{i}/{attempts}", error=str(exc)[:180])
            time.sleep(wait)
    raise RetryError(f"{what} failed after {attempts} attempts", attempts, last)


def first_working(providers: Sequence[Any], call: Callable[[Any], T], *,
                  tag: str = "FALLBACK", what: str = "provider") -> T:
    """Try each provider in order; return the first success (fallback chain)."""
    errors: list[str] = []
    for p in providers:
        name = getattr(p, "name", p.__class__.__name__)
        try:
            if hasattr(p, "available") and not p.available():
                errors.append(f"{name}: unavailable")
                log_event(tag, f"skipping {what}", provider=name, reason="unavailable")
                continue
            result = call(p)
            log_event(tag, f"{what} succeeded", provider=name)
            return result
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            log_event(tag, f"{what} failed, trying next", provider=name,
                      error=str(exc)[:200])
    raise RuntimeError(f"all {what}s failed -> " + " | ".join(errors))


# --------------------------------------------------------------------------
# Subprocess / ffmpeg
# --------------------------------------------------------------------------
class CommandError(RuntimeError):
    def __init__(self, cmd: Sequence[str], code: int, stderr: str):
        self.cmd, self.code, self.stderr = list(cmd), code, stderr
        tail = "\n".join(stderr.strip().splitlines()[-14:])
        super().__init__(f"command failed ({code}): {' '.join(cmd[:4])} ...\n{tail}")


def run(cmd: Sequence[str], *, timeout: int = 1800, check: bool = True,
        cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        list(cmd), capture_output=True, text=True, timeout=timeout,
        cwd=str(cwd) if cwd else None, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise CommandError(cmd, proc.returncode, proc.stderr or "")
    return proc


def which(name: str) -> str | None:
    return shutil.which(name)


def ffmpeg_filter_path(path) -> str:
    """Escape a path for use INSIDE an ffmpeg filter argument.

    Filters parse ':' and '\\' themselves, so C:\\a\\b.ass has to become
    C\\:/a/b.ass or ffmpeg reads the drive letter as an option separator.

    ONLY the drive letter's colon is escaped. A second, hand-rolled copy of
    this escaped every colon in the path, ffmpeg rejected the filter, and the
    caller turned that into a thumbnail with no text on it at all - which is
    why there is now one implementation here rather than two.
    """
    text = str(path).replace("\\", "/")
    if len(text) > 1 and text[1] == ":":
        return text.replace(":", "\\:", 1)
    return text


def ffmpeg_bin() -> str:
    return which("ffmpeg") or "ffmpeg"


def ffprobe_bin() -> str:
    return which("ffprobe") or "ffprobe"


def have_ffmpeg() -> bool:
    return which("ffmpeg") is not None and which("ffprobe") is not None


def probe_duration(path: str | Path) -> float:
    out = run([ffprobe_bin(), "-v", "error", "-show_entries",
               "format=duration", "-of", "default=nw=1:nk=1", str(path)], timeout=120)
    try:
        return float((out.stdout or "0").strip())
    except ValueError:
        return 0.0


def probe_json(path: str | Path) -> dict[str, Any]:
    import json
    out = run([ffprobe_bin(), "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(path)], timeout=120)
    try:
        return json.loads(out.stdout or "{}")
    except ValueError:
        return {}


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------
# Word tokenising has to work in every language the app offers, and \w is not
# enough on its own.
#
# The original ASCII class [A-Za-z0-9'] returned ZERO words for every Indic
# script, which silently destroyed non-English video: the model writes a
# correct Hindi script -> the word-floor check counts 0 -> "script under the
# word floor, re-asking" -> the retry counts 0 too -> the pipeline falls back
# to the structural TEMPLATE, which only speaks English. So asking for Hindi
# produced an English video built from boilerplate, and nothing in the logs
# named language as the cause.
#
# Switching to [\w']+ fixed the zero but introduced the opposite error: Python's
# \w does not match Unicode combining marks, so Devanagari and Tamil words
# split at every vowel sign and the count came out roughly three times too
# high - which would have skewed the duration budget just as badly in the other
# direction.
#
# So: split on whitespace, then strip punctuation from the ends. One token per
# spoken word, in any script, which is what the word budget and the measured
# speech rate both assume. The danda and double danda are included because they
# end sentences in Devanagari the way a full stop does in English.
_PUNCT = (
    "".join(chr(i) for i in range(0x20, 0x7F)
            if not chr(i).isalnum() and chr(i) != "'")
    + "‘’“”–—…"   # curly quotes, dashes
    + "।॥"                                     # danda, double danda
)
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "it", "its", "this", "that", "these",
    "those", "as", "at", "by", "from", "you", "your", "we", "our", "they", "their",
    "i", "me", "my", "he", "she", "his", "her", "them", "what", "how", "why",
    "when", "who", "which", "can", "will", "would", "could", "should", "do",
    "does", "did", "not", "no", "yes", "if", "then", "than", "so", "just", "about",
    "into", "out", "up", "down", "more", "most", "some", "all", "any", "every",
    "video", "shorts", "youtube", "subscribe", "like", "watch", "new", "best",
    "top", "vs", "part", "full", "official", "episode",
}


def words(text: str) -> list[str]:
    return [token for token in
            (raw.strip(_PUNCT) for raw in (text or "").lower().split())
            if token]


def keywords(text: str, limit: int = 12, min_len: int = 3) -> list[str]:
    counts: dict[str, int] = {}
    for w in words(text):
        if len(w) < min_len or w in STOPWORDS or w.isdigit():
            continue
        counts[w] = counts.get(w, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def slugify(text: str, max_len: int = 60) -> str:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return (t[:max_len] or "untitled").strip("-")


def sha1(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8", "ignore")).hexdigest()


def shingles(text: str, n: int = 4) -> set[str]:
    ws = [w for w in words(text) if w not in STOPWORDS]
    if len(ws) < n:
        return {" ".join(ws)} if ws else set()
    return {" ".join(ws[i:i + n]) for i in range(len(ws) - n + 1)}


def jaccard(a: str, b: str, n: int = 4) -> float:
    sa, sb = shingles(a, n), shingles(b, n)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def token_overlap(a: str, b: str) -> float:
    """Cosine-ish overlap of content words - used for topic duplicate detection."""
    sa = {w for w in words(a) if w not in STOPWORDS and len(w) > 2}
    sb = {w for w in words(b) if w not in STOPWORDS and len(w) > 2}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def count_words(text: str) -> int:
    return len(words(text))


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut[int(limit * 0.6):]:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,.;:-") + "..."


# --------------------------------------------------------------------------
# Time / timezone (spec section 19-20)
# --------------------------------------------------------------------------
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_rfc3339(value: str) -> datetime:
    v = (value or "").strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def age_days(published_at: str, ref: datetime | None = None) -> float:
    try:
        pub = parse_rfc3339(published_at)
    except (ValueError, TypeError):
        return 0.0
    delta = (ref or utc_now()) - pub
    return max(delta.total_seconds() / 86400.0, 0.0)


def local_slot_to_utc(time_hhmm: str, tz_name: str, *, days: Iterable[int] | None = None,
                      after: datetime | None = None) -> datetime:
    """Next occurrence of HH:MM in `tz_name` (optionally restricted to weekdays)."""
    tz = ZoneInfo(tz_name)
    ref = (after or utc_now()).astimezone(tz)
    hh, mm = (int(x) for x in time_hhmm.split(":")[:2])
    allowed = sorted(set(days)) if days else list(range(7))
    for offset in range(0, 15):
        cand = (ref + timedelta(days=offset)).replace(
            hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= ref or cand.weekday() not in allowed:
            continue
        return cand.astimezone(timezone.utc)
    raise ValueError(f"no slot found for {time_hhmm} in {tz_name}")


def pacific_day() -> str:
    """Google resets YouTube API quota at midnight Pacific Time."""
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------
def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def normalize(value: float, ceiling: float) -> float:
    """Map [0, ceiling] -> [0, 1] with a log curve (heavy-tailed metrics)."""
    import math
    if ceiling <= 0 or value <= 0:
        return 0.0
    return clamp(math.log1p(value) / math.log1p(ceiling))


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_write_json(path: str | Path, data: Any) -> Path:
    import json
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return p


def read_json(path: str | Path, default: Any = None) -> Any:
    import json
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return default


# --------------------------------------------------------------------------
# Grammar helpers for generated copy
# --------------------------------------------------------------------------
# Nouns that look plural but take a singular verb, and mass nouns.
_SINGULAR_LOOKS_PLURAL = {
    "physics", "mathematics", "economics", "politics", "news", "ethics",
    "statistics", "genetics", "robotics", "electronics", "athletics",
    "series", "species", "means", "gas", "glass", "class", "process",
    "business", "access", "success", "analysis", "basis", "crisis",
}


def subject_is_plural(text: str) -> bool:
    """Best-effort: does this topic label take a plural verb?

    Generated copy has to work for both "neutron star" and "black holes", and
    guessing wrong produces "Animals is usually explained" - which is exactly
    the bug this exists to prevent. Only the HEAD noun matters, which for an
    English noun phrase is the last word.
    """
    tokens = [t for t in words(text) if t]
    if not tokens:
        return False
    head = tokens[-1]
    if head in _SINGULAR_LOOKS_PLURAL:
        return False
    if head.endswith("ss") or head.endswith("us") or head.endswith("is"):
        return False
    # Irregular plurals worth knowing about.
    if head in {"people", "children", "men", "women", "teeth", "feet", "mice",
                "geese", "data", "media", "phenomena", "criteria"}:
        return True
    return head.endswith("s")


def agree(text: str, singular: str, plural: str) -> str:
    """Pick the verb form that agrees with `text`.

        agree("animals", "is", "are")      -> "are"
        agree("neutron star", "is", "are") -> "is"
    """
    return plural if subject_is_plural(text) else singular
