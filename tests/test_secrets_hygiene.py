"""Credentials must not reach a public repository, or a log file.

This repository is public and `.secrets/` now lives inside it, holding the
Oracle SSH key and the Google OAuth client secrets. An audit probed both
halves of that and found real gaps in each, so these are regression tests.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from engine.core.logging import redact


# ==========================================================================
class TestTheRedactorKnowsTheShapesThatMatter:
    """`_SECRET_KEYS` only fires when the dict KEY is telltale, so a secret
    interpolated into a message - a traceback, an httpx error body - relied
    entirely on `_LONG_KEYISH`. That list was AIza / gsk_ / sk- / ya29. and
    it missed the most valuable string in the project: a Google OAuth
    REFRESH token starts `1//`, does not expire on its own, and this
    project's carries youtube.upload scope.
    """

    @pytest.mark.parametrize("label,text", [
        ("refresh token",
         "refresh_token 1//04xYzAbCdEfGhIjKlMnOpQrStUvWxYz09-_abc"),
        ("client secret",
         "client secret is GOCSPX-AbCdEfGh1234567890abcdefg"),
        ("jwt",
         "Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6.eyJpc3MiOiJhY2NvdW50cy5nb28."
         "SflKxwRJSMeKKF2QT4"),
        ("google api key", "key AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz01234"),
        ("groq key", "gsk_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"),
        ("hex digest", "digest 0123456789abcdef0123456789abcdef"),
    ])
    def test_a_bare_credential_in_a_message_is_removed(self, label, text):
        assert "REDACTED" in str(redact(text)), f"{label} leaked: {text!r}"

    @pytest.mark.parametrize("text", [
        "scenes=13 sources=generated:cloudflarex13",
        "final render complete seconds=37.76 resolution=1080x1920",
        "entry kids-hi-6df883e5b9 stored",
        "job 20260913-084423_kids-toys_89359c",
        "quota 8200 of 10000 used",
    ])
    def test_an_ordinary_log_line_is_untouched(self, text):
        """A redactor that mangles ordinary output gets turned off. The hex
        rule is the risky one - entry ids and job ids are hex-ish."""
        assert redact(text) == text, f"mangled: {redact(text)!r}"

    def test_a_secret_named_key_is_still_redacted_by_name(self):
        assert redact({"refresh_token": "anything"})["refresh_token"] \
            == "***REDACTED***"


# ==========================================================================
class TestNothingSecretIsTracked:

    def test_no_credential_file_is_tracked(self):
        listed = subprocess.run(["git", "ls-files"], capture_output=True,
                                text=True).stdout.splitlines()
        bad = [p for p in listed
               if re.search(r"\.(key|pem|ppk)$|_rsa$|_ed25519$|"
                            r"^\.secrets/|client_secret", p, re.I)]
        assert not bad, f"tracked credential files: {bad}"

    def test_no_private_key_material_in_any_tracked_file(self):
        """Content, not names. The hook does this per-commit; this does it
        for the whole tree."""
        header = re.compile(
            r"-----BEGIN (?:OPENSSH|RSA|DSA|EC|PGP) PRIVATE KEY-----")
        allow = {"scripts/install_hooks.py", "tests/test_secrets_hygiene.py"}
        listed = subprocess.run(["git", "ls-files"], capture_output=True,
                                text=True).stdout.splitlines()
        bad = []
        for rel in listed:
            if rel in allow:
                continue
            path = Path(rel)
            if not path.exists() or path.stat().st_size > 2_000_000:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if header.search(text):
                bad.append(rel)
        assert not bad, f"private key material in: {bad}"

    def test_the_real_deploy_host_is_not_in_any_tracked_file(self):
        """The committed docs under deploy/oracle/ have always used
        placeholder domains. scripts/deploy.py nearly broke that by
        hard-coding user@ip, which would have published the production box's
        address for a machine holding a YouTube refresh token."""
        listed = subprocess.run(["git", "ls-files"], capture_output=True,
                                text=True).stdout.splitlines()
        ip = re.compile(r"\b80\.225\.220\.108\b")
        bad = []
        for rel in listed:
            path = Path(rel)
            if not path.exists() or path.stat().st_size > 2_000_000:
                continue
            try:
                if ip.search(path.read_text(encoding="utf-8",
                                            errors="ignore")):
                    bad.append(rel)
            except OSError:
                continue
        assert not bad, f"the production host address appears in: {bad}"


# ==========================================================================
class TestTheIgnoreRulesCoverPlausibleNames:
    """An audit saved a key under names a person would actually use and
    found several stageable. `sub/youtube_token.json` slipped through
    because the rule was `token*.json`, which anchors at the start of the
    basename.
    """

    @pytest.mark.parametrize("name", [
        ".secrets/oracle.key",
        ".secrets/client_secret_x.apps.googleusercontent.com.json",
        "id_rsa",
        "id_ed25519",
        "my_ed25519",
        "server.ppk",
        "credentials.yaml",
        "credentials.json",
        "sub/youtube_token.json",
        "token.json",
        "backup_refresh_token.txt",
        ".env",
    ])
    def test_the_name_is_ignored(self, name):
        result = subprocess.run(["git", "check-ignore", "-q", name],
                                capture_output=True)
        assert result.returncode == 0, f"{name} is NOT ignored"

    def test_an_installer_exists_for_the_content_based_backstop(self):
        """No filename pattern can cover a key called `oracle_private`, so
        the last line of defence reads staged content. Hooks are not
        tracked by git, so the installer must be."""
        src = Path("scripts/install_hooks.py")
        assert src.exists(), "the hook installer is gone"
        body = src.read_text(encoding="utf-8")
        assert "BEGIN (?:OPENSSH|RSA|DSA|EC|PGP) PRIVATE KEY" in body
        assert "1//" in body, "the refresh-token shape is not covered"
