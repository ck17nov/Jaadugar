"""Captions were failing on every upload for a week, silently.

Every publish logged `captions failed ... 403 Request had insufficient
authentication scopes` and carried on, so every video went out with no
caption track. `captions.insert` needs `youtube.force-ssl`; the tokens
carried `youtube.upload`, `youtube` and `yt-analytics.readonly`.

FOUND IN THE QUOTA LEDGER, not the log: six consecutive Pacific days read
1,600 units per upload where 2,000 was budgeted, and the missing 400 was the
caption call that never succeeded.

The resolution was to leave captions OFF and take the quota back. The
renderer already burns subtitles into the frame, so a YouTube track adds the
CC toggle, search indexing and screen-reader access - and 400 units, which is
the difference between four uploads a day and five. The fifth video won.

So these tests pin the opposite of what they first pinned: the scope must NOT
be requested, the call must NOT be made, and the reason must be visible as a
CHOICE rather than a fault.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("AUTOTUBE_WORKSPACE", tempfile.mkdtemp())

from engine.core.config import load_config             # noqa: E402
from engine.research.youtube import QuotaGuard         # noqa: E402
from engine.youtube.auth import (CAPTIONS_SCOPE, SCOPES,  # noqa: E402
                                 missing_scopes)


class TestTheScopeIsNotRequested:
    """Asking for a permission nothing uses widens the consent screen and
    forces a reconnect of every channel, for nothing."""

    def test_force_ssl_is_named_but_not_granted(self):
        assert CAPTIONS_SCOPE == \
            "https://www.googleapis.com/auth/youtube.force-ssl"
        assert CAPTIONS_SCOPE not in SCOPES, \
            "captions are off; this scope buys nothing"

    def test_the_android_list_matches_the_backend_list(self):
        """The phone mints the token, so its list is the one that decides.
        A backend-only change would look correct and do nothing."""
        src = Path("android/app/src/main/java/com/autotube/ai/auth/"
                   "YouTubeAuthManager.kt").read_text(encoding="utf-8")
        start = src.index("val SCOPES = listOf(")
        block = src[start:src.index(")", start)]
        for scope in SCOPES:
            assert scope in block, f"the app does not request {scope}"
        assert CAPTIONS_SCOPE not in block, \
            "the two lists have drifted - the app asks for force-ssl"

    def test_the_scopes_actually_used_are_still_there(self):
        assert "https://www.googleapis.com/auth/youtube.upload" in SCOPES
        assert "https://www.googleapis.com/auth/youtube" in SCOPES
        assert "https://www.googleapis.com/auth/yt-analytics.readonly" in SCOPES


class TestTheCallIsNotMade:

    def test_the_captions_block_is_gated_on_the_config(self):
        src = Path("engine/youtube/upload.py").read_text(encoding="utf-8")
        assert 'self.cfg.get("youtube.attach_captions", False)' in src
        gate = src.index("youtube.attach_captions")
        insert = src.index("yt.captions().insert")
        assert gate < insert, \
            "the flag must be read before the call, not after"

    def test_the_default_is_off(self):
        assert bool(load_config().get("youtube.attach_captions", False)) is False

    def test_skipping_is_logged_as_a_choice_not_a_failure(self):
        """"Off by config" and "off because a 403 fired" must not read the
        same in the log - that ambiguity is what hid this for a week."""
        src = Path("engine/youtube/upload.py").read_text(encoding="utf-8")
        assert "caption track not attached, by config" in src

    def test_the_403_still_explains_itself_if_captions_are_turned_on(self):
        src = Path("engine/youtube/upload.py").read_text(encoding="utf-8")
        assert "insufficient authentication scopes" in src
        assert "youtube.force-ssl" in src
        assert "reconnect the channel" in src

    def test_a_caption_failure_still_does_not_fail_the_upload(self):
        """The video is already published by this point."""
        src = Path("engine/youtube/upload.py").read_text(encoding="utf-8")
        start = src.index('log_event("YOUTUBE", "captions uploaded")')
        block = src[start:start + 1600]
        assert "result.warnings.append(advice)" in block
        assert "raise" not in block.split("playlist")[0]


class TestTheQuotaMathsThatBuysTheFifthUpload:

    def test_an_upload_costs_1650_with_captions_off(self):
        q = QuotaGuard(load_config())
        assert q.per_upload == 1650, \
            "1600 insert + 50 thumbnail, and no caption track"

    def test_the_ceiling_is_five(self):
        q = QuotaGuard(load_config())
        assert q.max_uploads_per_day == 5

    def test_research_is_subtracted_before_the_division(self):
        """`limit // per_upload` models no research. At 1,650 it answers SIX,
        which leaves 100 units - and the first search.list of the day costs
        exactly 100, so that call would be the one that failed."""
        q = QuotaGuard(load_config())
        assert q.limit // q.per_upload == 6, "the naive sum still says six"
        assert q.research_reserve >= 1200, \
            "measured research is 900-1,200 units a day"
        assert q.max_uploads_per_day == 5

    def test_turning_captions_on_costs_the_fifth_upload(self):
        """The trade, stated as a test. This is why the two settings cannot
        be changed independently."""
        cfg = load_config()
        cfg.data["youtube"]["attach_captions"] = True
        q = QuotaGuard(cfg)
        assert q.per_upload == 2050
        assert q.max_uploads_per_day == 4

    def test_the_configured_limit_does_not_exceed_the_ceiling(self):
        """A limit that cannot be reached fails at the last video of the day
        instead of refusing it up front."""
        cfg = load_config()
        q = QuotaGuard(cfg)
        wanted = int(cfg.get("automation.daily_video_limit", 0))
        assert wanted == 5
        assert wanted <= q.max_uploads_per_day

    def test_five_uploads_plus_research_fit_in_the_day(self):
        q = QuotaGuard(load_config())
        # 1,200 is the worst day measured on the live box.
        assert 5 * q.per_upload + 1200 <= q.limit
        assert 6 * q.per_upload + 1200 > q.limit, \
            "six would not fit, which is why the ceiling is five"


class TestTheGapIsReportedNotDiscovered:
    """`missing_scopes` stays, because it is what would catch someone
    enabling attach_captions without widening the consent."""

    def test_a_token_missing_a_required_scope_is_flagged(self):
        assert missing_scopes(
            ["https://www.googleapis.com/auth/youtube.upload"]) != []

    def test_a_fully_scoped_token_is_clean(self):
        assert missing_scopes(list(SCOPES)) == []

    def test_extra_scopes_do_not_count_as_missing(self):
        assert missing_scopes(list(SCOPES) + [CAPTIONS_SCOPE]) == []

    def test_an_unknown_scope_list_is_not_reported_as_broken(self):
        """Tokens imported from the phone before scopes were recorded have
        nothing stored. A warning that cries wolf gets ignored on the day it
        is real."""
        assert missing_scopes(None) == []
        assert missing_scopes([]) == []

    def test_whitespace_in_a_stored_scope_is_tolerated(self):
        assert missing_scopes([f"  {s}  " for s in SCOPES]) == []


class TestTheOperatorCanSeeIt:

    def test_auth_channels_reports_the_choice_before_the_scope(self):
        src = Path("backend/cli.py").read_text(encoding="utf-8")
        assert '"Captions"' in src
        assert "off (burned in)" in src
        assert "if not captions_on:" in src
        assert "missing_scopes(ch.scopes)" in src
