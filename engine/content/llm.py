"""Pluggable LLM providers, free-first (spec section 29).

  groq     - free tier, no credit card. Llama 3.3 70B. Fast, high quality.
  gemini   - Google AI Studio free tier, no credit card.
  ollama   - fully local, no key, no quota. Needs the ollama daemon running.
  template - deterministic, no network. A DEGRADED FALLBACK, not a mock: it
             produces a real, publishable-shape script from the research data,
             but without an LLM the prose is formulaic. Every script records
             which provider produced it and the quality gate penalises
             `template`, so this can never silently masquerade as AI output.

All providers implement the same `complete()` contract and JSON extraction is
shared, because every one of them occasionally wraps JSON in prose or fences.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from ..core.logging import log_event


class RateLimited(RuntimeError):
    """Throttled, with the API's own reported wait attached.

    Separate from LLMError so the retry layer can sleep for `retry_after`
    instead of guessing. Groq reports x-ratelimit-reset-tokens in values as
    small as 157ms, against a flat 25-second backoff.
    """

    def __init__(self, message: str, retry_after: float = 0.0,
                 model: str = ""):
        super().__init__(message)
        self.retry_after = max(0.0, float(retry_after))
        self.model = model


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str = ""


class LLMProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def complete(self, prompt: str, *, system: str = "", json_mode: bool = False,
                 temperature: float = 0.8, max_tokens: int = 4096) -> LLMResult: ...


# --------------------------------------------------------------------------
# JSON extraction - shared, because every provider does this differently
# --------------------------------------------------------------------------
def extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of an LLM response.

    Handles: bare JSON, ```json fences, prose-wrapped JSON, and trailing
    commas.  Raises LLMError if nothing parseable is present.
    """
    if not text:
        raise LLMError("empty LLM response")
    raw = text.strip()

    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if fence:
        raw = fence.group(1).strip()

    start = raw.find("{")
    if start == -1:
        raise LLMError(f"no JSON object in response: {raw[:200]}")

    # Walk to the matching brace, respecting strings and escapes.
    depth, in_str, esc, end = 0, False, False, -1
    for i, ch in enumerate(raw[start:], start=start):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        raise LLMError("unterminated JSON object in response")

    candidate = raw[start:end + 1]
    try:
        return json.loads(candidate)
    except ValueError:
        cleaned = re.sub(r",(\s*[}\]])", r"\1", candidate)      # trailing commas
        cleaned = cleaned.replace("\t", " ")
        try:
            return json.loads(cleaned)
        except ValueError as exc:
            raise LLMError(f"invalid JSON from LLM: {exc}") from exc


# --------------------------------------------------------------------------
# Model retirement
# --------------------------------------------------------------------------
# Hosted providers retire model IDs on their own schedule. This project shipped
# with `gemini-2.0-flash`, which Google shut down on 2026-06-01; every script
# request then failed with a 404 that looked like a broken API key. Treat
# "this model is gone" as a recoverable condition and fall forward to the next
# candidate, but never swallow a rate limit or an auth failure that way.
_MODEL_RETIRED = re.compile(
    r"(model[_ ]not[_ ]found|does not exist|is not found|not_found|"
    r"decommissioned|discontinued|no longer (available|supported)|"
    r"has been (shut down|retired|removed)|unsupported model|"
    r"invalid model|unknown model)", re.I)


# Conditions that clear on their own: rate limits (which are usually per
# minute), and upstream overload. Retrying these beats downgrading to a weaker
# provider. Auth failures and retired models are NOT here - no amount of
# waiting fixes a bad key.
#
# The status codes are word-anchored on purpose. A bare `50[234]` also matches
# inside "15034 tokens" and a bare `429` inside "14290", so an ordinary error
# mentioning a token count would be misread as an upstream outage.
_TRANSIENT_LLM = re.compile(
    r"(\b429\b|rate limit|too many requests|\b50[234]\b|overloaded|"
    r"unavailable|timed? ?out|timeout|temporarily|try again)", re.I)


_TIMEOUT = re.compile(r"(timed? ?out|timeout|deadline exceeded)", re.I)


def _is_transient_llm(message: str, *, retry_timeouts: bool = True) -> bool:
    """True if waiting could plausibly fix it.

    `retry_timeouts` exists because a timeout means two different things. From
    a hosted provider it is usually a network blip worth one more attempt. From
    a LOCAL model it means the request did not fit the time budget we chose,
    and the identical request will not fit next time either.

    Observed with both hosted providers rate-limited: the chain fell through to
    CPU-only ollama, timed out after 300s, and was then retried on the same
    300s budget. Ten minutes burned to arrive in the same place.
    """
    if re.search(r"\b(401|403)\b|invalid api key|permission denied", message, re.I):
        return False
    if _is_model_retired(message):
        return False
    if not retry_timeouts and _TIMEOUT.search(message):
        # A 504 is the far end giving up, not our budget being too small.
        return bool(re.search(r"\b504\b", message))
    return bool(_TRANSIENT_LLM.search(message))


def _cannot_do_json(message: str) -> bool:
    """True if this model cannot satisfy JSON mode for this prompt.

    A DIFFERENT MODEL is the fix, which is why it belongs beside the
    retirement check rather than in the transient one - waiting cannot help.

    CORRECTED 2026-09-10. The observation below was right and the conclusion
    was wrong, and it cost this project its best model for three days.

    What was measured: gpt-oss-120b and gpt-oss-20b answered HTTP 400
    json_validate_failed on the kids-story prompt with an EMPTY
    `failed_generation`; with JSON mode off, gpt-oss-120b returned 200 and
    zero characters. That was read as "this model cannot satisfy JSON mode",
    so it was demoted permanently and qwen wrote every story instead.

    The real cause was an unbounded reasoning budget plus the legacy
    `max_tokens` alias. Re-measured on the identical prompt and key:
        json_object + max_tokens=4096                -> 400, empty
        no JSON mode, max_completion_tokens=1500     -> 200, 0 chars,
                                                        completion_tokens=1500
        + reasoning_effort=low + max_completion      -> 200, 2,761 chars
    The reasoning channel was eating the entire budget. `_call` now always
    sends `reasoning_effort` for these models, so this path should no longer
    fire for that reason.

    It is KEPT because a genuine per-model JSON incapacity is still possible
    and a different model is still the only fix - waiting cannot help, which
    is why it lives beside the retirement check rather than the transient one.

    Before this, a 400 failed the whole PROVIDER, so the fallback list was
    never consulted and kids-story generation fell through to the template -
    the exact boilerplate the story work was undertaken to remove.
    """
    return "json_validate_failed" in message.lower()


def parse_reset(value: str | None) -> float:
    """Groq's reset headers, in seconds. "1m26.4s", "615ms", "2.5s".

    Returns 0.0 for anything unparseable, which means "retry now" - the
    caller already has its own ceiling, and an unreadable header should not
    become a long sleep.
    """
    text = (value or "").strip().lower()
    if not text:
        return 0.0
    total, number = 0.0, ""
    index = 0
    while index < len(text):
        ch = text[index]
        if ch.isdigit() or ch == ".":
            number += ch
            index += 1
            continue
        if text.startswith("ms", index):
            total += float(number or 0) / 1000.0
            number = ""
            index += 2
            continue
        if ch == "m":
            total += float(number or 0) * 60.0
            number = ""
            index += 1
            continue
        if ch == "s":
            total += float(number or 0)
            number = ""
            index += 1
            continue
        index += 1
    if number:
        total += float(number)
    return total


def _is_model_retired(message: str) -> bool:
    """True if the error means "that model ID is gone", not "you are throttled"."""
    if re.search(r"\b(429|rate limit|quota|401|403|invalid api key|"
                 r"permission denied)\b", message, re.I):
        return False
    return bool(_MODEL_RETIRED.search(message)) or " 404" in message


# --------------------------------------------------------------------------
# Groq
# --------------------------------------------------------------------------
class GroqProvider:
    name = "groq"
    ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
    # Tried in order. Hosted model IDs get decommissioned with little notice,
    # so a single hard-coded ID is a time bomb - see MODEL_RETIREMENT below.
    # Verified against GET /openai/v1/models on the live key (2026-09-09):
    # it serves 14 models and NEITHER llama-3.3-70b-versatile NOR
    # llama-3.1-8b-instant is among them. Both were in this list, and
    # llama-3.3-70b was FIRST - so every single Groq call spent a round trip
    # collecting a 404 before reaching a model that exists. Two of the four
    # entries were dead, which also meant the "fallback" list had one real
    # member.
    #
    # The qwen pair replaces them because they are actually on the key. Query
    # the endpoint before editing this list; hosted IDs get decommissioned
    # with little notice, which is the whole reason MODEL_RETIREMENT exists
    # below.
    FALLBACK_MODELS = [
        "openai/gpt-oss-120b",       # 200k tokens/day on the free tier
        "qwen/qwen3.8-27b",          # real fallback, verified present
        "qwen/qwen3.6-27b",
        "openai/gpt-oss-20b",        # smallest, last resort
    ]

    def __init__(self, api_key: str = "", model: str = "", timeout: int = 120):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self.model = model or os.environ.get("GROQ_MODEL", "") or self.FALLBACK_MODELS[0]
        self.timeout = timeout
        # model -> (tokens remaining, epoch when the window resets).
        #
        # Populated from EVERY response, including failures: the header comes
        # back on a 429 too, and it is the only way to know which model still
        # has headroom.
        self._budget: dict[str, tuple[int, float]] = {}

    def available(self) -> bool:
        return bool(self.api_key)

    # ------------------------------------------------------------------
    # Per-model token budget, read from the response headers
    # ------------------------------------------------------------------
    def _note_budget(self, model: str, headers) -> None:
        """Remember how much this model has left, and when it resets."""
        remaining = headers.get("x-ratelimit-remaining-tokens")
        if remaining is None:
            return
        try:
            left = int(float(remaining))
        except (TypeError, ValueError):
            return
        reset = parse_reset(headers.get("x-ratelimit-reset-tokens"))
        self._budget[model] = (left, time.time() + reset)

    @staticmethod
    def _reported_wait(headers) -> float:
        """How long the API says to wait, capped so a bad header cannot hang.

        `retry-after` first because it is the explicit instruction; the
        token-reset header is the fallback. Observed values go as low as
        157ms, against the flat 25-second backoff this replaces.
        """
        for name in ("retry-after", "x-ratelimit-reset-tokens",
                     "x-ratelimit-reset-requests"):
            wait = parse_reset(headers.get(name))
            if wait > 0:
                return min(wait, 90.0)
        return 5.0

    def _candidates(self) -> list[str]:
        """Models to try, the one with the most headroom first.

        Ordering by remembered budget rather than by a fixed list means a
        model throttled a moment ago goes to the back instead of being tried
        first again. A model with no record sorts ahead of a known-empty one,
        because an unknown budget is more promising than a spent one.
        """
        models = [self.model] + [m for m in self.FALLBACK_MODELS
                                 if m != self.model]
        now = time.time()

        def headroom(name: str) -> float:
            record = self._budget.get(name)
            if record is None:
                return float("inf")         # never used; assume full
            left, resets_at = record
            if now >= resets_at:
                return float("inf")         # the window has rolled over
            return float(left)

        # Stable sort, so the configured order breaks ties.
        return sorted(models, key=lambda m: -headroom(m))

    def complete(self, prompt: str, *, system: str = "", json_mode: bool = False,
                 temperature: float = 0.8, max_tokens: int = 4096) -> LLMResult:
        last: Exception | None = None
        limited: list[RateLimited] = []
        candidates = self._candidates()
        for model in candidates:
            try:
                result = self._call(model, prompt, system, json_mode,
                                    temperature, max_tokens)
            except RateLimited as exc:
                # THE FIX. Groq's limits are PER MODEL - four models each
                # reported their own remaining-token count, 8,000 tokens a
                # minute each - so a 429 here says nothing about the next
                # model. Sleeping on this one while three others sit idle with
                # full budgets is what turned 45 long-form section calls into
                # 26 of the run's 81 minutes.
                log_event("LLM", "model rate-limited, rotating to the next",
                          model=model, retry_after=f"{exc.retry_after:.1f}s")
                limited.append(exc)
                last = exc
                continue
            except LLMError as exc:
                text = str(exc)
                if _cannot_do_json(text):
                    log_event("LLM", "model cannot satisfy JSON mode, trying "
                              "the next one", model=model,
                              error=text[:120])
                    last = exc
                    continue
                if not _is_model_retired(text):
                    raise
                log_event("LLM", "groq model unavailable, trying next",
                          model=model, error=text[:140])
                last = exc
                continue
            self.model = model          # stick with what worked
            return result

        if limited and len(limited) == len(candidates):
            # EVERY model is throttled. Raise with the SHORTEST reported wait
            # so the retry layer sleeps exactly that long instead of guessing.
            soonest = min(limited, key=lambda e: e.retry_after)
            raise RateLimited(
                f"every groq model is rate-limited; the soonest resets in "
                f"{soonest.retry_after:.1f}s",
                retry_after=soonest.retry_after)
        raise last or LLMError("no usable groq model")

    def _call(self, model: str, prompt: str, system: str, json_mode: bool,
              temperature: float, max_tokens: int) -> LLMResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [{"role": "user", "content": prompt}],
            "temperature": temperature,
            # `max_completion_tokens`, NOT `max_tokens`.
            #
            # These are not the same thing on a reasoning model. `max_tokens`
            # is the legacy alias and does not separate the hidden reasoning
            # channel from the content, so an unbounded reasoning pass can
            # consume the entire budget and return a valid 200 with ZERO
            # characters of content. Measured: gpt-oss-120b with
            # max_completion_tokens=1500 and no JSON mode returned
            # completion_tokens=1500 and content of length 0.
            "max_completion_tokens": max_tokens,
        }
        # THE PARAMETER THAT WAS MISSING, and it cost this project its best
        # model for three days.
        #
        # Without it, gpt-oss burns its whole completion budget on reasoning
        # and then fails JSON validation with an EMPTY `failed_generation`,
        # which read exactly like "this model cannot do JSON mode". It can.
        # Measured on the identical kids-story prompt and the same key:
        #   json_object + max_tokens=4096            -> 400 json_validate_failed
        #   + reasoning_effort=low + max_completion  -> 200, 2,761 chars
        # The wrong conclusion demoted gpt-oss-120b on the first call of every
        # job, which is why qwen wrote every story instead.
        if "gpt-oss" in model or model.startswith("openai/"):
            payload["reasoning_effort"] = "low"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self.ENDPOINT, json=payload, headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"})
            # Record what this model has left BEFORE anything can raise. The
            # header comes back on failures too, and it is what tells the
            # rotation below which model to try next.
            self._note_budget(model, resp.headers)
            if resp.status_code == 429:
                wait = self._reported_wait(resp.headers)
                raise RateLimited(
                    f"groq rate limit on {model} (retry in {wait:.1f}s)",
                    retry_after=wait, model=model)
            if resp.status_code >= 400:
                raise LLMError(f"groq {resp.status_code}: {resp.text[:220]}")
            data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"unexpected groq response shape: {exc}") from exc
        return LLMResult(text=text, provider=self.name, model=model)


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------
class GeminiProvider:
    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"
    # Google shut down gemini-2.0-flash on 2026-06-01. It was this project's
    # original default, and the pipeline died with a 404 until the list below
    # was introduced: never pin a single hosted model ID.
    FALLBACK_MODELS = [
        "gemini-3.7-flash",        # current stable Flash
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",   # cheapest/highest-volume
        "gemini-2.5-flash",
    ]

    def __init__(self, api_key: str = "", model: str = "", timeout: int = 120):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model or os.environ.get("GEMINI_MODEL", "") or self.FALLBACK_MODELS[0]
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def _candidates(self) -> list[str]:
        return [self.model] + [m for m in self.FALLBACK_MODELS if m != self.model]

    def complete(self, prompt: str, *, system: str = "", json_mode: bool = False,
                 temperature: float = 0.8, max_tokens: int = 4096) -> LLMResult:
        last: LLMError | None = None
        for model in self._candidates():
            try:
                result = self._call(model, prompt, system, json_mode,
                                    temperature, max_tokens)
            except LLMError as exc:
                if not _is_model_retired(str(exc)):
                    raise
                log_event("LLM", "gemini model unavailable, trying next",
                          model=model, error=str(exc)[:140])
                last = exc
                continue
            self.model = model          # stick with what worked
            return result
        raise last or LLMError("no usable gemini model")

    def _call(self, model: str, prompt: str, system: str, json_mode: bool,
              temperature: float, max_tokens: int) -> LLMResult:
        # Gemini 2.5+ and the 3.x family spend output tokens on internal
        # reasoning BEFORE emitting any text, and that spend counts against
        # maxOutputTokens. Asking for 4096 on a script call produced exactly
        # what you would expect once you know that: 2,169 characters of
        # truncated JSON on one attempt and a candidate with zero parts on the
        # next, after which the pipeline silently fell back to the template
        # builder. The caller's budget is therefore treated as "text I want",
        # not "total allowance", and thinking is capped so it cannot eat the lot.
        budget = max(int(max_tokens) * 3, 8192)
        gen: dict[str, Any] = {"temperature": temperature,
                               "maxOutputTokens": budget}
        if json_mode:
            gen["responseMimeType"] = "application/json"
        gen["thinkingConfig"] = {"thinkingBudget": min(2048, budget // 4)}
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen,
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"{self.BASE}/{model}:generateContent"

        data = self._post(url, payload)
        if data is None:
            # Some models reject thinkingConfig outright. Retry without it
            # rather than losing the provider over an optional field.
            gen.pop("thinkingConfig", None)
            log_event("LLM", "gemini rejected thinkingConfig, retrying without",
                      model=model)
            data = self._post(url, payload, allow_retry=False)

        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback") or {}
            raise LLMError(f"gemini returned no candidates "
                           f"(promptFeedback={str(feedback)[:160]})")
        candidate = candidates[0]
        parts = ((candidate.get("content") or {}).get("parts")) or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text:
            # Surface finishReason and the token split. Without this the
            # failure reads as "empty LLM response" and tells you nothing
            # about which budget you actually exhausted.
            usage = data.get("usageMetadata") or {}
            raise LLMError(
                f"gemini produced no text (finishReason="
                f"{candidate.get('finishReason')}, "
                f"thoughts={usage.get('thoughtsTokenCount')}, "
                f"output={usage.get('candidatesTokenCount')}, "
                f"budget={budget}) - raise max_tokens or lower thinkingBudget")
        return LLMResult(text=text, provider=self.name, model=model)

    def _post(self, url: str, payload: dict[str, Any],
              allow_retry: bool = True) -> dict[str, Any] | None:
        """POST once. Returns None if `thinkingConfig` was the problem."""
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload,
                               headers={"x-goog-api-key": self.api_key,
                                        "Content-Type": "application/json"})
            if resp.status_code == 429:
                raise LLMError("gemini rate limit reached (free tier)")
            if resp.status_code >= 400:
                body = resp.text[:400]
                if (allow_retry and resp.status_code == 400
                        and "thinking" in body.lower()):
                    return None
                raise LLMError(f"gemini {resp.status_code}: {body[:220]}")
            return resp.json()


# --------------------------------------------------------------------------
# Ollama (local)
# --------------------------------------------------------------------------
class OllamaProvider:
    """Local inference. Free and unlimited, but CPU-only hosts are SLOW:
    expect minutes per call for a 7B model without a GPU. Prefer groq/gemini
    for interactive use and keep ollama as the offline fallback.
    """

    name = "ollama"
    # A timeout here is our own budget expiring, not a blip. Retrying it costs
    # another full timeout and lands in exactly the same place.
    retry_timeouts = False

    # 900s was the original default and it made the pipeline indistinguishable
    # from a hang: a CPU-only 7B model can sit there for a quarter of an hour
    # with nothing on stdout. 300s still allows a slow-but-working local model
    # while failing over to the template builder in a bearable time. Raise it
    # with OLLAMA_TIMEOUT if you have a GPU and want the headroom.
    DEFAULT_TIMEOUT = 300

    def __init__(self, host: str = "", model: str = "", timeout: int = 0):
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
                     ).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
        self.timeout = timeout or int(
            os.environ.get("OLLAMA_TIMEOUT", self.DEFAULT_TIMEOUT))
        self._probe: tuple[float, bool] | None = None

    def available(self) -> bool:
        # A daemon busy generating a previous request answers /api/tags slowly.
        # A short timeout therefore reports a working provider as unavailable,
        # which silently downgrades the pipeline to the template builder - so
        # allow real time here and cache the answer briefly.
        import time as _time
        if self._probe and _time.time() - self._probe[0] < 30.0:
            return self._probe[1]
        ok = False
        try:
            with httpx.Client(timeout=12.0) as client:
                resp = client.get(f"{self.host}/api/tags")
                ok = resp.status_code == 200
        except Exception:
            ok = False
        self._probe = (_time.time(), ok)
        return ok

    def complete(self, prompt: str, *, system: str = "", json_mode: bool = False,
                 temperature: float = 0.8, max_tokens: int = 4096) -> LLMResult:
        payload: dict[str, Any] = {
            "model": self.model, "prompt": prompt, "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if system:
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"
        # Say so before blocking. Local inference on CPU can take minutes, and
        # a silent wait is the single most common "it hung" report.
        log_event("LLM", "waiting on local ollama (can take minutes on CPU)",
                  model=self.model, timeout=f"{self.timeout}s")
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{self.host}/api/generate", json=payload)
            if resp.status_code >= 400:
                raise LLMError(f"ollama {resp.status_code}: {resp.text[:220]}")
            data = resp.json()
        text = data.get("response", "")
        if not text:
            raise LLMError("ollama returned no text")
        return LLMResult(text=text, provider=self.name, model=self.model)


# --------------------------------------------------------------------------
# Deterministic fallback
# --------------------------------------------------------------------------
class TemplateProvider:
    """No-network fallback.  Signals its own limitation instead of pretending.

    It does not attempt to answer arbitrary prompts: the callers that need a
    guaranteed result (idea generation, script generation) have dedicated
    deterministic builders.  For anything else it raises, so a caller never
    receives plausible-looking nonsense.
    """

    name = "template"
    DEGRADED = True

    def available(self) -> bool:
        return True

    def complete(self, prompt: str, *, system: str = "", json_mode: bool = False,
                 temperature: float = 0.8, max_tokens: int = 4096) -> LLMResult:
        raise LLMError(
            "template provider cannot answer free-form prompts; "
            "the caller must use its deterministic builder instead")


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------
class LLMRouter:
    """Tries providers in configured order; reports which one answered."""

    def __init__(self, order: list[str], cfg=None):
        self.cfg = cfg
        self.providers: list[LLMProvider] = []
        registry = {
            "groq": lambda: GroqProvider(
                (cfg.secret("GROQ_API_KEY") if cfg else ""),
                (cfg.secret("GROQ_MODEL") if cfg else "")),
            "gemini": lambda: GeminiProvider(
                (cfg.secret("GEMINI_API_KEY") if cfg else ""),
                (cfg.secret("GEMINI_MODEL") if cfg else "")),
            "ollama": lambda: OllamaProvider(
                (cfg.secret("OLLAMA_HOST") if cfg else ""),
                (cfg.secret("OLLAMA_MODEL") if cfg else "")),
            "template": TemplateProvider,
        }
        for name in order:
            factory = registry.get(name)
            if factory:
                self.providers.append(factory())
        # Free-tier limits reset per minute, so the default waits are long
        # enough to actually clear one.
        self.transient_retries = int(
            cfg.get("content.transient_retries", 3) if cfg else 3)
        self.retry_backoff = float(
            cfg.get("content.retry_backoff_seconds", 25) if cfg else 25)

    @property
    def usable(self) -> list[LLMProvider]:
        return [p for p in self.providers
                if not isinstance(p, TemplateProvider) and p.available()]

    def has_real_llm(self) -> bool:
        return bool(self.usable)

    # Providers that refuse a whole CATEGORY of request, whatever the wording.
    #
    # Measured against the live key: Gemini blocks children's-story generation
    # outright. Five prompt shapes were tried, including the bare baseline
    # "Write a two-sentence bedtime story for a young audience" with no
    # constraints attached at all, and every one came back with no candidates
    # and blockReason PROHIBITED_CONTENT. It is provider policy about content
    # for minors, not something a rephrase gets around - positive framings of
    # the agency and calm-mood instructions were tried and blocked too.
    #
    # Skipping it for those requests is worth doing rather than letting it
    # fail: each attempt costs a round trip plus the transient-retry backoff
    # on a guaranteed refusal, and it preserves Gemini's daily quota for the
    # niches where it does work.
    CATEGORY_REFUSALS: dict[str, tuple[str, ...]] = {
        "gemini": ("kids_story",),
    }

    def complete(self, prompt: str, *, system: str = "", json_mode: bool = False,
                 temperature: float = 0.8, max_tokens: int = 4096,
                 category: str = "") -> LLMResult:
        errors: list[str] = []
        for provider in self.providers:
            if isinstance(provider, TemplateProvider):
                continue
            if not provider.available():
                errors.append(f"{provider.name}: not configured")
                continue
            if category and category in self.CATEGORY_REFUSALS.get(
                    provider.name, ()):
                errors.append(f"{provider.name}: refuses {category} content")
                log_event("LLM", "skipping a provider that refuses this "
                          "content category", provider=provider.name,
                          category=category)
                continue
            try:
                result = self._with_backoff(
                    provider, prompt, system, json_mode, temperature, max_tokens)
            except Exception as exc:
                errors.append(f"{provider.name}: {str(exc)[:180]}")
                log_event("LLM", "provider failed, falling back",
                          provider=provider.name, error=str(exc)[:180])
                continue
            log_event("LLM", "completion ok", provider=provider.name,
                      model=result.model, chars=len(result.text))
            return result
        raise LLMError("no LLM provider succeeded -> " + " | ".join(errors))

    def _with_backoff(self, provider, prompt: str, system: str, json_mode: bool,
                      temperature: float, max_tokens: int) -> LLMResult:
        """Retry ONE provider through transient conditions before giving up.

        Free-tier limits are mostly per MINUTE, so falling straight through to
        the next provider is the wrong move: waiting 30 seconds gets you the
        good model, while moving on gets you a weaker one - or, at the end of
        the chain, CPU-only ollama at minutes per call.

        Seen against real keys in one run: Groq's gpt-oss-120b allows 8,000
        tokens/minute and a single ideas call spent it, then Gemini answered
        503 (transient overload). Both were treated as permanent failures and
        the pipeline degraded all the way to local inference. Neither had to.

        Only transient conditions are retried. A bad key or a retired model
        fails immediately, because waiting cannot fix either.
        """
        attempts = max(1, self.transient_retries)
        delay = self.retry_backoff
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return provider.complete(
                    prompt, system=system, json_mode=json_mode,
                    temperature=temperature, max_tokens=max_tokens)
            except Exception as exc:
                last = exc
                retry_timeouts = getattr(provider, "retry_timeouts", True)
                if attempt >= attempts or not _is_transient_llm(
                        str(exc), retry_timeouts=retry_timeouts):
                    raise
                # Sleep for as long as the API ASKED, when it said.
                #
                # This was a flat 25s doubling to 50s. Groq reports
                # x-ratelimit-reset-tokens in values as small as 157ms, so the
                # fixed backoff was up to 40x longer than necessary - and on a
                # 45-call long-form script that is most of the wall clock. A
                # small floor stops a near-zero reset becoming a hot loop.
                reported = getattr(exc, "retry_after", 0.0) or 0.0
                wait = max(0.5, min(reported, delay)) if reported else delay
                log_event("LLM", "transient, waiting rather than downgrading",
                          provider=provider.name, attempt=f"{attempt}/{attempts}",
                          wait=f"{wait:.1f}s",
                          source="api" if reported else "backoff",
                          error=str(exc)[:120])
                time.sleep(wait)
                delay *= 2
        raise last or LLMError(f"{provider.name} failed")

    def complete_json(self, prompt: str, *, system: str = "",
                      temperature: float = 0.8, max_tokens: int = 4096,
                      attempts: int = 2, category: str = ""
                      ) -> tuple[dict[str, Any], str]:
        """Completion that must yield JSON. Retries with a stricter nudge.

        `category` lets the caller name what KIND of request this is, so a
        provider known to refuse that category is skipped rather than tried
        and retried against a certain refusal.
        """
        last: Exception | None = None
        for i in range(attempts):
            nudge = ("" if i == 0 else
                     "\n\nYour previous reply was not valid JSON. "
                     "Reply with ONE JSON object and nothing else.")
            try:
                result = self.complete(prompt + nudge, system=system,
                                       json_mode=True, temperature=temperature,
                                       max_tokens=max_tokens,
                                       category=category)
                return extract_json(result.text), result.provider
            except LLMError as exc:
                last = exc
                log_event("LLM", "JSON parse failed", attempt=i + 1,
                          error=str(exc)[:160])
        raise LLMError(f"could not obtain JSON from any provider: {last}")
