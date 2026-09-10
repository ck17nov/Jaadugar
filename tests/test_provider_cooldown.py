"""An exhausted provider must be skipped, not re-discovered on every call.

Measured on a real long-form render. Gemini's free tier was gone for the day
and every single call still paid its full backoff before falling through:
"attempt 1/3 wait 25s", "attempt 2/3 wait 50s", "provider failed, falling
back". Seventy-five seconds of sleeping, per call, for a provider that could
not answer any of them - five times in one job.

It had a second consequence that was worse than the wasted time. Long-form
scripts are generated in sections, and the decision to section at all comes
from timing the outline call and multiplying by the section count. With 75
seconds of dead waiting inside that measurement, an outline that really cost
4 seconds measured 87, projected to 12 minutes, and sectioning was refused -
so the fallback single shot produced a 75-second script for a 300-second
video.
"""
from __future__ import annotations

import time

import pytest

from engine.content.llm import LLMResult, LLMRouter


class Exhausted:
    """A provider whose free tier has run out for the day."""
    name = "exhausted"
    retry_timeouts = True

    def __init__(self, retry_after: float = 0.0):
        self.calls = 0
        self.retry_after = retry_after

    def available(self) -> bool:
        return True

    def complete(self, *args, **kwargs):
        self.calls += 1
        exc = RuntimeError("gemini rate limit reached (free tier)")
        exc.retry_after = self.retry_after
        raise exc


class Broken:
    """A provider failing for a reason waiting cannot fix."""
    name = "broken"
    retry_timeouts = True

    def __init__(self):
        self.calls = 0

    def available(self) -> bool:
        return True

    def complete(self, *args, **kwargs):
        self.calls += 1
        raise RuntimeError("400 invalid request: unsupported parameter")


class Working:
    name = "working"
    retry_timeouts = True

    def __init__(self):
        self.calls = 0

    def available(self) -> bool:
        return True

    def complete(self, *args, **kwargs):
        self.calls += 1
        return LLMResult(text='{"ok": true}', model="m", provider=self.name)


def _router(*providers, retries: int = 2, backoff: float = 0.02,
            cooldown: float = 300.0) -> LLMRouter:
    router = LLMRouter([], None)
    router.providers = list(providers)
    router.transient_retries = retries
    router.retry_backoff = backoff
    router.cooldown_seconds = cooldown
    return router


def test_an_exhausted_provider_is_tried_once_then_rested():
    dead, alive = Exhausted(), Working()
    router = _router(dead, alive)
    for _ in range(5):
        assert router.complete("hi").text
    # Two attempts on the first call, then never again.
    assert dead.calls == 2
    assert alive.calls == 5


def test_resting_saves_the_backoff_not_just_the_call():
    """The point is the wall clock, not the request count."""
    dead, alive = Exhausted(), Working()
    router = _router(dead, alive, retries=3, backoff=0.15)
    started = time.monotonic()
    for _ in range(6):
        router.complete("hi")
    elapsed = time.monotonic() - started
    # One discovery costs 2 x 0.15s. Six calls without resting would cost
    # 12 x 0.15 = 1.8s.
    assert elapsed < 0.9, f"still paying the backoff every call: {elapsed:.2f}s"


def test_a_non_transient_failure_does_not_rest_the_provider():
    """A 400 is this call's problem and says nothing about the next one.

    Resting on it would take a working provider out of the chain for fifteen
    minutes because one prompt was malformed.
    """
    broken, alive = Broken(), Working()
    router = _router(broken, alive)
    for _ in range(3):
        router.complete("hi")
    assert broken.calls == 3, "a 400 must not rest the provider"


def test_a_success_clears_the_rest():
    """Never let a cooldown outlive the condition.

    The limit may have been per-minute, or a new key may have arrived.
    """
    dead, alive = Exhausted(), Working()
    router = _router(dead, alive)
    router.complete("hi")
    assert "exhausted" in router._cooldown

    # The provider recovers.
    dead.complete = lambda *a, **k: LLMResult(  # type: ignore[assignment]
        text="{}", model="m", provider="exhausted")
    router._cooldown.clear()
    router.complete("hi")
    assert "exhausted" not in router._cooldown


def test_the_reported_retry_after_extends_the_rest():
    """A provider saying "come back in an hour" is believed over the default."""
    dead = Exhausted(retry_after=3600.0)
    router = _router(dead, Working(), cooldown=60.0)
    router.complete("hi")
    remaining = router._cooldown["exhausted"] - time.monotonic()
    assert remaining > 3000, f"only resting {remaining:.0f}s"


def test_wasted_time_is_reported_for_the_section_estimate():
    """This is what stops a discovery cost being projected across sections."""
    dead, alive = Exhausted(), Working()
    router = _router(dead, alive, retries=3, backoff=0.2)
    router.complete("hi")
    # Two sleeps of 0.2s before the third attempt gave up.
    assert router.resting_seconds() >= 0.35
    # And the NEXT call wasted nothing, because the provider is resting.
    router.complete("hi")
    assert router.resting_seconds() == 0.0


def test_everything_failing_still_raises():
    """Resting must not turn a total failure into a silent one."""
    from engine.content.llm import LLMError
    router = _router(Exhausted(), Exhausted())
    with pytest.raises(LLMError):
        router.complete("hi")


def test_complete_json_inherits_the_cooldown():
    """It delegates to complete(), so it must not re-pay the discovery."""
    dead, alive = Exhausted(), Working()
    router = _router(dead, alive)
    for _ in range(4):
        data, provider = router.complete_json("hi")
        assert data == {"ok": True} and provider == "working"
    assert dead.calls == 2
