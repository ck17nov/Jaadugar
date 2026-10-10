"""Two defects reported together on 11 October 2026.

"One automation fails every day even though quota is remaining" - and it did:
the 20:00 slot was refused "daily video limit reached (5/5)" on most days for
two weeks with the quota ledger never above 9,259 of 10,000. The limit was
counting by `updated_at`, so yesterday's videos re-counted as today's.

"Long form are not full screen images, very small cutouts" - the kids
learning flashcard put every picture in a small square card, which in a
landscape frame filled about a sixth of the screen. Rendering a frame to
check the fix also showed that the card's Hindi letter had always been
drawn as tofu boxes.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AUTOTUBE_WORKSPACE", tempfile.mkdtemp())

from engine.core.db import Database                  # noqa: E402
from engine.core.models import JobStatus, VideoJob    # noqa: E402
from engine.core.util import quota_reset_wait         # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def _at(y, mo, d, h, mi, tz=IST) -> float:
    return datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()


# ==========================================================================
class TestTheQuotaResetWait:
    """Hold a run until it would be charged against TODAY'S quota day."""

    def test_the_morning_burst_waits_for_the_reset(self):
        """The automations fired at 11:30-12:20 IST, which is 23:00-23:50
        Pacific - inside the quota day already holding last night's batch."""
        wait = quota_reset_wait("Asia/Kolkata", _at(2026, 10, 11, 11, 30))
        assert wait == pytest.approx(60 * 60), "12:30 IST is the reset in PDT"

    def test_after_the_reset_it_runs_at_once(self):
        assert quota_reset_wait("Asia/Kolkata", _at(2026, 10, 11, 12, 30)) == 0
        assert quota_reset_wait("Asia/Kolkata", _at(2026, 10, 11, 19, 31)) == 0

    def test_one_minute_before_is_one_minute(self):
        wait = quota_reset_wait("Asia/Kolkata", _at(2026, 10, 11, 12, 29))
        assert wait == pytest.approx(60)

    def test_the_reset_moves_when_the_us_leaves_daylight_time(self):
        """Pacific Standard Time is UTC-8, so the reset is 13:30 IST from the
        first Sunday of November. Hard-coding 12:30 would break every winter."""
        assert quota_reset_wait("Asia/Kolkata",
                                _at(2026, 11, 3, 12, 30)) == pytest.approx(3600)
        assert quota_reset_wait("Asia/Kolkata", _at(2026, 11, 3, 13, 30)) == 0

    def test_just_after_local_midnight_waits_most_of_the_morning(self):
        wait = quota_reset_wait("Asia/Kolkata", _at(2026, 10, 11, 0, 5))
        assert wait == pytest.approx((12 * 60 + 25) * 60)

    def test_a_timezone_behind_pacific_never_waits(self):
        """For Honolulu the Pacific day always begins after the local one, so
        there is no window in which a run joins yesterday's quota."""
        hst = ZoneInfo("Pacific/Honolulu")
        for hour in (0, 6, 12, 20, 23):
            assert quota_reset_wait("Pacific/Honolulu",
                                    _at(2026, 10, 11, hour, 0, hst)) == 0

    def test_an_unknown_timezone_does_not_raise(self):
        assert quota_reset_wait("Mars/Olympus", _at(2026, 10, 11, 11, 0)) >= 0


class TestTheWaitIsWiredSafely:

    def test_the_pipeline_holds_before_preflight(self):
        src = Path("engine/pipeline.py").read_text(encoding="utf-8")
        hold = src.index("self._wait_for_quota_day(job)")
        pre = src.index("problems = self.preflight(request)")
        assert hold < pre, \
            "the limit and quota checks must see the day the run starts in"

    def test_a_dry_run_never_waits(self):
        """A dry run spends no quota - and the suite runs dry, so a run
        started before 12:30 IST must not sleep for hours."""
        src = Path("engine/pipeline.py").read_text(encoding="utf-8")
        gate = src[src.index("_wait_for_quota_day(job)") - 700:
                   src.index("_wait_for_quota_day(job)")]
        assert 'youtube.upload_enabled' in gate
        assert '"dry_run"' in gate
        assert "automation.start_after_quota_reset" in gate

    def test_the_wait_is_cancellable_and_visible(self):
        src = Path("engine/pipeline.py").read_text(encoding="utf-8")
        start = src.index("def _wait_for_quota_day")
        body = src[start:src.index("def _raise_if_cancelled", start)]
        assert "self._raise_if_cancelled(job)" in body, "must honour cancel"
        assert "self.db.save_job(job)" in body, \
            "the app must show WHY the job is waiting"
        assert "WAITING:" in body


# ==========================================================================
class TestTheLimitCountsWhenAVideoWasMade:

    @pytest.fixture()
    def db(self, tmp_path):
        d = Database(tmp_path / "t.db")
        yield d
        d.close()

    def _job(self, db, *, created: float, updated: float,
             status=JobStatus.PUBLISHED.value) -> None:
        job = VideoJob(status=status)
        job.created_at, job.updated_at = created, updated
        db.save_job(job)
        # save_job may stamp updated_at itself; force the measured state.
        db.execute("UPDATE video_jobs SET created_at=?, updated_at=? "
                   "WHERE job_id=?", (created, updated, job.job_id))

    def test_yesterdays_video_touched_today_is_not_counted(self):
        """THE BUG. Measured at the refusal on 10 October: two of the five
        jobs counted had been created on 8 and 9 October, and a nightly pass
        had written their rows at 00:09 on the 10th."""
        d = Database(Path(tempfile.mkdtemp()) / "t.db")
        try:
            today = _at(2026, 10, 10, 0, 0)
            self._job(d, created=_at(2026, 10, 9, 11, 58),
                      updated=_at(2026, 10, 10, 0, 9))
            self._job(d, created=_at(2026, 10, 8, 19, 33),
                      updated=_at(2026, 10, 10, 0, 9))
            self._job(d, created=_at(2026, 10, 10, 11, 56),
                      updated=_at(2026, 10, 10, 18, 10))
            made = d.count_jobs_since(today, (JobStatus.PUBLISHED.value,))
            assert made == 1, "only the one MADE today counts"
        finally:
            d.close()

    def test_the_query_reads_created_at(self):
        src = Path("engine/core/db.py").read_text(encoding="utf-8")
        start = src.index("def count_jobs_since")
        body = src[start:src.index("def ", start + 10)]
        assert "WHERE created_at >= ?" in body
        assert "WHERE updated_at >= ?" not in body


# ==========================================================================
class TestTheLearningCardShowsThePictureFullFrame:

    def test_a_scene_with_no_letter_returns_the_plain_still(self):
        """Words, numbers, shapes and sentences have no letter. For them the
        card only shrank the picture."""
        from PIL import Image
        from engine.core.models import Asset
        from engine.visuals.base import VisualRequest
        import engine.visuals.kids_animation as ka

        class Still:
            name = "ai_image"
            calls = 0
            def available(self): return True
            def fetch(self, req, out):
                Still.calls += 1
                Image.new("RGB", (req.width, req.height), (200, 40, 40)
                          ).save(out, "JPEG")
                return Asset(asset=Path(out).name, source="ai_image",
                             license="t", prompt="", scene_index=0)

        fields = set(VisualRequest.__dataclass_fields__)
        req = VisualRequest(**{k: v for k, v in dict(
            prompt="an apple", keywords=["apple"], scene_index=0,
            width=1920, height=1080, min_seconds=1.0).items() if k in fields})
        prov = ka.KidsAnimationProvider(fps=30, subject_provider=Still())
        real_letter, real_word = ka.headline_letter, ka.headline_word
        try:
            ka.headline_letter = lambda r: ""
            ka.headline_word = lambda r: "सेब"
            asset = prov.fetch(req, Path(tempfile.mkdtemp()) / "s0.jpg")
        finally:
            ka.headline_letter, ka.headline_word = real_letter, real_word
        assert asset.source == "ai_image", "the still, not a card animation"
        assert asset.asset.endswith(".jpg")

    def test_a_letter_scene_has_no_card_and_no_pastel_border(self):
        """The corners of the frame must be PICTURE, not the pastel wash a
        card sits on."""
        from PIL import Image
        from engine.visuals.base import VisualRequest
        import engine.visuals.kids_animation as ka
        from engine.video.fonts import display_font

        subject = Image.new("RGB", (768, 1344), (10, 200, 30))   # pure green
        fields = set(VisualRequest.__dataclass_fields__)
        req = VisualRequest(**{k: v for k, v in dict(
            prompt="x", keywords=["x"], scene_index=0,
            width=1920, height=1080).items() if k in fields})
        prov = ka.KidsAnimationProvider(fps=30, subject_provider=None)
        fp, _ = display_font(language="hi")
        frame = list(prov._frames(6, req, ka.PALETTES[0][1], "अ", "अनार",
                                  None, subject, fp))[-1]
        assert frame.size == (1920, 1080)
        for corner in ((5, 5), (1914, 5), (5, 1074), (1914, 1074)):
            r, g, b = frame.getpixel(corner)
            assert g > 150 and r < 60, \
                f"corner {corner} is {frame.getpixel(corner)}, not the picture"

    def test_cover_fills_the_frame_exactly(self):
        from PIL import Image
        from engine.visuals.kids_animation import _cover
        for src, frame in (((768, 1344), (1920, 1080)),
                           ((1344, 768), (1080, 1920)),
                           ((1024, 1024), (1920, 1080))):
            assert _cover(Image.new("RGB", src), *frame).size == frame


class TestTheLetterIsNotDrawnAsBoxes:

    def test_devanagari_is_detected_from_the_text(self):
        from engine.visuals.kids_animation import _language_for_text
        assert _language_for_text("अ", "अनार") == "hi"
        assert _language_for_text("", "क") == "hi"
        assert _language_for_text("A", "apple") == ""
        assert _language_for_text("", "") == ""

    def test_the_font_call_passes_the_language(self):
        """`display_font()` with no language returned Anton, which has no
        Devanagari - so every Hindi letter was tofu."""
        src = Path("engine/visuals/kids_animation.py").read_text(
            encoding="utf-8")
        assert "display_font(\n            language=_language_for_text(letter, word))" in src
        assert "font_path, _family = display_font()\n" not in src

    def test_the_chosen_face_actually_has_the_glyph(self):
        """Not just a name check: a Devanagari face draws अ wider than the
        notdef box a Latin face would substitute."""
        from PIL import ImageFont
        from engine.video.fonts import display_font
        path, family = display_font(language="hi")
        assert "Devanagari" in family
        font = ImageFont.truetype(str(path), 120)
        assert font.getmask("अ").getbbox() is not None
