"""Cloudflare reports what each request cost. Read it, do not assume it.

The claim these tests protect: SDXL is unmetered today (cf-ai-neurons 0.00,
measured repeatedly) while flux costs 172.80 per image, and a 145-image
long-form video is affordable only because of that. If Cloudflare starts
metering SDXL, the accounting below is what surfaces it.
"""
from __future__ import annotations

import pytest

from engine.visuals.ai_image import CloudflareBackend, QuotaExhausted, _as_float


class TestHeaderParsing:
    def test_real_values_parse(self):
        assert _as_float("172.80") == pytest.approx(172.8)
        assert _as_float("0.45") == pytest.approx(0.45)
        assert _as_float("0.00") == 0.0

    def test_a_bad_header_never_loses_an_image(self):
        """The image already arrived; a malformed accounting header must not
        turn a success into an exception."""
        for junk in (None, "", "garbage", "NaN-ish", "  "):
            assert _as_float(junk) == 0.0

    def test_a_negative_charge_is_floored(self):
        assert _as_float("-5") == 0.0


class TestAccounting:
    def _backend(self, **kw):
        return CloudflareBackend("acct", "token", **kw)

    def test_it_starts_at_zero(self):
        backend = self._backend()
        assert backend.spent_neurons == 0.0
        assert backend.last_neurons == 0.0

    def test_a_charged_request_accumulates(self, monkeypatch):
        backend = self._backend()
        self._fetch(backend, monkeypatch, neurons="172.80")
        assert backend.last_neurons == pytest.approx(172.8)
        assert backend.spent_neurons == pytest.approx(172.8)
        self._fetch(backend, monkeypatch, neurons="172.80")
        assert backend.spent_neurons == pytest.approx(345.6)

    def test_an_unmetered_request_adds_nothing(self, monkeypatch):
        """SDXL today. The running total must stay at zero rather than
        drifting on rounding."""
        backend = self._backend()
        for _ in range(5):
            self._fetch(backend, monkeypatch, neurons="0.00")
        assert backend.spent_neurons == 0.0

    def test_an_exhausted_allowance_reports_what_was_spent(self, monkeypatch):
        backend = self._backend()
        self._fetch(backend, monkeypatch, neurons="172.80")
        with pytest.raises(QuotaExhausted) as caught:
            self._fetch(backend, monkeypatch, neurons="0.00", status=429)
        message = str(caught.value)
        # Formatted with :.0f, so 172.8 renders as 173.
        assert "173" in message, message
        assert "10,000" in message, message

    # ------------------------------------------------------------------
    @staticmethod
    def _fetch(backend, monkeypatch, *, neurons: str, status: int = 200):
        import engine.visuals.ai_image as mod

        class _Resp:
            status_code = status
            content = bytes([255, 216, 255])
            headers = {"content-type": "image/jpeg", "cf-ai-neurons": neurons}

            def json(self):
                return {}

            def raise_for_status(self):
                return None

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, json=None, headers=None):
                return _Resp()

        monkeypatch.setattr(mod.httpx, "Client", lambda *a, **k: _Client())
        return backend.fetch("a cat", width=1080, height=1920, seed=1)
