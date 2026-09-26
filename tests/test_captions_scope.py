"""Captions were failing on every upload for a week, silently.

Every publish logged `captions failed ... 403 Request had insufficient
authentication scopes` and carried on, so every video went out with no
caption track. `captions.insert` needs `youtube.force-ssl`; the tokens
carried `youtube.upload`, `youtube` and `yt-analytics.readonly`.

Found by reading the live quota ledger: 4 uploads a day cost 1,600 units
each and nothing else, when a caption track should have added 400 apiece.
The missing spend was the evidence.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("AUTOTUBE_WORKSPACE", tempfile.mkdtemp())

from engine.youtube.auth import (CAPTIONS_SCOPE, SCOPES,  # noqa: E402
                                 missing_scopes)


class TestTheScopeIsRequested:

    def test_force_ssl_is_in_the_backend_list(self):
        assert CAPTIONS_SCOPE == \
            "https://www.googleapis.com/auth/youtube.force-ssl"
        assert CAPTIONS_SCOPE in SCOPES

    def test_the_android_list_matches_the_backend_list(self):
        """The phone mints the token, so its list is the one that decides
        what consent actually grants. A backend-only change would have
        looked correct and fixed nothing."""
        src = Path("android/app/src/main/java/com/autotube/ai/auth/"
                   "YouTubeAuthManager.kt").read_text(encoding="utf-8")
        start = src.index("val SCOPES = listOf(")
        block = src[start:src.index(")", start)]
        for scope in SCOPES:
            assert scope in block, f"the app does not request {scope}"

    def test_the_narrower_scopes_are_kept(self):
        """force-ssl subsumes upload, but dropping the others would change
        what an existing token is checked against for no benefit."""
        assert "https://www.googleapis.com/auth/youtube.upload" in SCOPES
        assert "https://www.googleapis.com/auth/yt-analytics.readonly" in SCOPES


class TestTheGapIsReportedNotDiscovered:

    def test_a_token_without_force_ssl_is_flagged(self):
        old = ["https://www.googleapis.com/auth/youtube.upload",
               "https://www.googleapis.com/auth/youtube",
               "https://www.googleapis.com/auth/yt-analytics.readonly"]
        assert CAPTIONS_SCOPE in missing_scopes(old)

    def test_a_fully_scoped_token_is_clean(self):
        assert missing_scopes(list(SCOPES)) == []

    def test_extra_scopes_do_not_count_as_missing(self):
        assert missing_scopes(list(SCOPES) + ["https://example/other"]) == []

    def test_an_unknown_scope_list_is_not_reported_as_broken(self):
        """Tokens imported from the phone before scopes were recorded have
        nothing stored. Calling those broken would be a false alarm, and a
        warning that cries wolf gets ignored on the day it is real."""
        assert missing_scopes(None) == []
        assert missing_scopes([]) == []

    def test_whitespace_in_a_stored_scope_is_tolerated(self):
        padded = [f"  {s}  " for s in SCOPES]
        assert missing_scopes(padded) == []


class TestTheFailureExplainsItself:

    def test_the_403_names_the_scope_and_the_remedy(self):
        src = Path("engine/youtube/upload.py").read_text(encoding="utf-8")
        assert "insufficient authentication scopes" in src
        assert "youtube.force-ssl" in src
        assert "reconnect the channel" in src

    def test_a_caption_failure_still_does_not_fail_the_upload(self):
        """The video is already published by this point. Losing captions
        must stay a warning."""
        src = Path("engine/youtube/upload.py").read_text(encoding="utf-8")
        start = src.index('log_event("YOUTUBE", "captions uploaded")')
        block = src[start:start + 1400]
        assert "result.warnings.append(advice)" in block
        assert "raise" not in block.split("playlist")[0]


class TestTheOperatorCanSeeIt:

    def test_auth_channels_shows_a_captions_column(self):
        src = Path("backend/cli.py").read_text(encoding="utf-8")
        assert '"Captions"' in src
        assert "reconnect" in src.lower()
        assert "missing_scopes(ch.scopes)" in src
