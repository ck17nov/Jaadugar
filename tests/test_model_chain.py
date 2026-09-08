"""One withdrawn beta model must not cost the whole video its look.

All three unmetered Cloudflare image models are flagged `beta: true` in the
account catalogue, and that flag is why they are free - so any of them can be
withdrawn or start failing without notice. Rolling to the next SDXL variant
keeps the medium; degrading straight to the keyless backend does not, and a
video whose second half is soft 0.59 MP frames is worse than one that is
uniformly either.
"""
from __future__ import annotations

import pytest

import engine.visuals.ai_image as mod
from engine.visuals.ai_image import (CloudflareBackend, ModelUnavailable,
                                     QuotaExhausted)

JPEG = bytes([255, 216, 255])


def _client(handler):
    """Install a fake httpx.Client whose post() delegates to `handler(url)`."""
    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            return handler(url)

    return lambda *a, **k: _Client()


class _Resp:
    def __init__(self, status=200, content=JPEG, neurons="0.00",
                 ctype="image/jpeg", payload=None):
        self.status_code = status
        self.content = content
        self.headers = {"content-type": ctype, "cf-ai-neurons": neurons}
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class TestChainShape:
    def test_the_default_chain_is_base_then_lightning(self):
        backend = CloudflareBackend("a", "t")
        assert backend.models[0].endswith("stable-diffusion-xl-base-1.0")
        assert backend.models[1].endswith("stable-diffusion-xl-lightning")

    def test_the_primary_is_never_duplicated(self):
        backend = CloudflareBackend(
            "a", "t", model="@cf/bytedance/stable-diffusion-xl-lightning")
        assert backend.models.count(
            "@cf/bytedance/stable-diffusion-xl-lightning") == 1
        assert len(backend.models) == 2

    def test_fallbacks_can_be_switched_off(self):
        backend = CloudflareBackend("a", "t", fallback_models=[])
        assert len(backend.models) == 1

    def test_both_chain_models_accept_a_size(self):
        """A fallback that cannot make a 9:16 frame is not a fallback - flux
        returns a square the cover crop cuts to the keyless resolution."""
        backend = CloudflareBackend("a", "t")
        for model in backend.models:
            assert backend._sized(model), model


class TestRollForward:
    def test_a_failing_primary_rolls_to_the_next(self, monkeypatch):
        seen = []

        def handler(url):
            seen.append(url)
            if "base-1.0" in url:
                return _Resp(status=503, content=b"")
            return _Resp()

        monkeypatch.setattr(mod.httpx, "Client", _client(handler))
        backend = CloudflareBackend("acct", "tok")
        assert backend.fetch("x", width=1080, height=1920, seed=1) == JPEG
        assert len(seen) == 2
        assert "lightning" in seen[1]

    def test_the_primary_is_used_when_it_works(self, monkeypatch):
        seen = []
        monkeypatch.setattr(mod.httpx, "Client",
                            _client(lambda url: (seen.append(url), _Resp())[1]))
        backend = CloudflareBackend("acct", "tok")
        backend.fetch("x", width=1080, height=1920, seed=1)
        assert len(seen) == 1, "a working primary must not walk the chain"

    def test_every_model_failing_raises(self, monkeypatch):
        monkeypatch.setattr(mod.httpx, "Client",
                            _client(lambda url: _Resp(status=503, content=b"")))
        with pytest.raises(Exception):
            CloudflareBackend("acct", "tok").fetch(
                "x", width=1080, height=1920, seed=1)


class TestAccountLevelFailuresDoNotBurnTheChain:
    def test_an_exhausted_allowance_stops_immediately(self, monkeypatch):
        """Every model shares one allowance, so trying the next proves
        nothing and costs a call."""
        seen = []
        monkeypatch.setattr(
            mod.httpx, "Client",
            _client(lambda url: (seen.append(url), _Resp(status=429))[1]))
        with pytest.raises(QuotaExhausted):
            CloudflareBackend("acct", "tok").fetch(
                "x", width=1080, height=1920, seed=1)
        assert len(seen) == 1

    def test_a_rejected_token_stops_immediately(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            mod.httpx, "Client",
            _client(lambda url: (seen.append(url), _Resp(status=401))[1]))
        with pytest.raises(ModelUnavailable):
            CloudflareBackend("acct", "tok").fetch(
                "x", width=1080, height=1920, seed=1)
        assert len(seen) == 1
