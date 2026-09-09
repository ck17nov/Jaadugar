"""Groq rate limits, and the long-form script bottleneck they caused.

Measured against the live key, and these three facts drive every test here:

  1. THE LIMITS ARE PER MODEL. Four models each reported their own
     x-ratelimit-remaining-tokens (7918 / 7977 / 7979 / 7918), so the account
     has 4 x 8,000 tokens per minute, not 8,000.
  2. THE BINDING LIMIT IS TOKENS, NOT REQUESTS: 8,000 TPM against 1,000 RPM.
     A long-form section call is ~2,600 actual tokens, so ~3 calls a minute
     per model - which is why 45 sections took 26 of the run's 81 minutes.
  3. max_tokens IS NOT RESERVED. A request with max_tokens=4096 and a tiny
     prompt left the remaining count unchanged and billed its actual 123
     tokens, so trimming max_tokens would achieve nothing.
"""
from __future__ import annotations

import time

import pytest

from engine.content.llm import (GroqProvider, LLMError, RateLimited,
                                _is_transient_llm, parse_reset)


class _Headers(dict):
    """httpx headers are case-insensitive; the real ones are all lowercase."""

    def get(self, key, default=None):
        return super().get(key.lower(), default)


class TestParseReset:
    @pytest.mark.parametrize("value,expected", [
        ("1m26.4s", 86.4),      # observed on x-ratelimit-reset-requests
        ("615ms", 0.615),       # observed on x-ratelimit-reset-tokens
        ("157ms", 0.157),       # the smallest observed
        ("2.5s", 2.5),
        ("1m", 60.0),
        ("7.66s", 7.66),
    ])
    def test_real_header_values(self, value, expected):
        assert parse_reset(value) == pytest.approx(expected, abs=0.01)

    @pytest.mark.parametrize("value", ["", None, "garbage", "   "])
    def test_unparseable_means_retry_now(self, value):
        """Not a long sleep: the caller has its own ceiling, and an
        unreadable header must not become a stall."""
        assert parse_reset(value) == 0.0


class TestReportedWait:
    def test_retry_after_wins(self):
        """It is the explicit instruction, so it beats the derived headers."""
        headers = _Headers({"retry-after": "3s",
                            "x-ratelimit-reset-tokens": "30s"})
        assert GroqProvider._reported_wait(headers) == pytest.approx(3.0)

    def test_token_reset_is_the_fallback(self):
        headers = _Headers({"x-ratelimit-reset-tokens": "615ms"})
        assert GroqProvider._reported_wait(headers) == pytest.approx(0.615)

    def test_no_headers_gives_a_modest_default(self):
        assert GroqProvider._reported_wait(_Headers()) == 5.0

    def test_a_wild_header_cannot_hang_the_run(self):
        headers = _Headers({"retry-after": "45m"})
        assert GroqProvider._reported_wait(headers) == 90.0


class TestBudgetTracking:
    def _provider(self):
        return GroqProvider(api_key="x")

    def test_a_response_records_the_remaining_budget(self):
        groq = self._provider()
        groq._note_budget("m1", _Headers({
            "x-ratelimit-remaining-tokens": "7918",
            "x-ratelimit-reset-tokens": "615ms"}))
        left, resets_at = groq._budget["m1"]
        assert left == 7918
        assert resets_at > time.time()

    def test_a_missing_header_is_not_recorded(self):
        """Some responses carry no budget header; guessing zero would send a
        healthy model to the back of the queue."""
        groq = self._provider()
        groq._note_budget("m1", _Headers({}))
        assert "m1" not in groq._budget

    def test_a_malformed_header_is_ignored(self):
        groq = self._provider()
        groq._note_budget("m1", _Headers({
            "x-ratelimit-remaining-tokens": "lots"}))
        assert "m1" not in groq._budget


class TestCandidateOrdering:
    def _provider(self):
        return GroqProvider(api_key="x")

    def test_untouched_models_keep_the_configured_order(self):
        groq = self._provider()
        assert groq._candidates() == [groq.model] + [
            m for m in groq.FALLBACK_MODELS if m != groq.model]

    def test_a_spent_model_goes_to_the_back(self):
        """Otherwise the rotation tries the throttled model first every time."""
        groq = self._provider()
        spent = groq.model
        groq._budget[spent] = (12, time.time() + 60)
        assert groq._candidates()[-1] == spent

    def test_more_headroom_is_tried_first(self):
        groq = self._provider()
        a, b = groq.FALLBACK_MODELS[0], groq.FALLBACK_MODELS[1]
        groq._budget[a] = (100, time.time() + 60)
        groq._budget[b] = (6000, time.time() + 60)
        order = groq._candidates()
        assert order.index(b) < order.index(a)

    def test_an_expired_window_counts_as_full_again(self):
        """The limit is per MINUTE, so a stale record must not keep a model
        parked at the back forever."""
        groq = self._provider()
        spent = groq.model
        groq._budget[spent] = (0, time.time() - 1)      # already reset
        assert groq._candidates()[0] == spent

    def test_every_model_is_always_offered(self):
        groq = self._provider()
        for name in groq.FALLBACK_MODELS:
            groq._budget[name] = (0, time.time() + 60)
        assert sorted(groq._candidates()) == sorted(
            set([groq.model] + list(groq.FALLBACK_MODELS)))


class TestRotationOnRateLimit:
    def _provider(self, monkeypatch, responses):
        """A GroqProvider whose _call plays back a scripted sequence."""
        groq = GroqProvider(api_key="x")
        tried: list[str] = []

        def fake_call(model, *_a, **_kw):
            tried.append(model)
            outcome = responses.get(model, "ok")
            if outcome == "limited":
                raise RateLimited(f"limited {model}", retry_after=0.6,
                                  model=model)
            if outcome == "json":
                raise LLMError("groq 400: json_validate_failed")
            from engine.content.llm import LLMResult
            return LLMResult(text="{}", provider="groq", model=model)

        monkeypatch.setattr(groq, "_call", fake_call)
        return groq, tried

    def test_a_limited_model_rotates_instead_of_sleeping(self, monkeypatch):
        """THE fix. A 429 on one model says nothing about the next."""
        groq, tried = self._provider(monkeypatch,
                                     {GroqProvider.FALLBACK_MODELS[0]: "limited"})
        result = groq.complete("hi")
        assert len(tried) == 2
        assert result.model == tried[1]

    def test_it_does_not_sleep_while_rotating(self, monkeypatch):
        """Rotation must be immediate; the sleep only belongs at the end."""
        groq, _tried = self._provider(
            monkeypatch, {GroqProvider.FALLBACK_MODELS[0]: "limited"})
        slept: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
        groq.complete("hi")
        assert slept == []

    def test_all_limited_raises_with_the_soonest_reset(self, monkeypatch):
        groq, tried = self._provider(
            monkeypatch, {m: "limited" for m in GroqProvider.FALLBACK_MODELS})
        with pytest.raises(RateLimited) as caught:
            groq.complete("hi")
        assert len(tried) == len(groq._candidates())
        assert caught.value.retry_after == pytest.approx(0.6)

    def test_a_rate_limit_is_classified_transient(self):
        """So _with_backoff waits and retries rather than degrading the whole
        provider chain down to the template builder."""
        assert _is_transient_llm("groq rate limit on m (retry in 0.6s)")

    def test_a_json_failure_still_rotates(self, monkeypatch):
        """The two rotation reasons must not interfere: gpt-oss cannot do
        JSON mode on the story prompt, and qwen can."""
        groq, tried = self._provider(monkeypatch,
                                     {GroqProvider.FALLBACK_MODELS[0]: "json"})
        groq.complete("hi")
        assert len(tried) == 2


class TestTheBackoffHonoursTheApi:
    def test_it_uses_retry_after_when_present(self, monkeypatch):
        """A flat 25s against a reported 157ms was up to 40x too long, and on
        a 45-call script that is most of the wall clock."""
        import inspect

        from engine.content.llm import LLMRouter
        source = inspect.getsource(LLMRouter._with_backoff)
        assert 'getattr(exc, "retry_after"' in source
        assert "min(reported, delay)" in source

    def test_there_is_a_floor(self):
        """A near-zero reset must not become a hot loop."""
        import inspect

        from engine.content.llm import LLMRouter
        assert "max(0.5" in inspect.getsource(LLMRouter._with_backoff)
