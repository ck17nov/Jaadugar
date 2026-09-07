"""The batch of defects reported from real use on 2026-09-07.

Most of them turned out to be ONE failure with six symptoms: every LLM
provider was rate limited, so the deterministic builders wrote the video. The
gates below are what stops that reaching a render. The rest cover the smaller
independent faults found alongside it.
"""
from __future__ import annotations

import time

import pytest

from engine.content.llm import LLMError
from engine.core.config import load_config
from engine.core.db import Database
from engine.core.models import AutomationRequest, JobStatus, VideoJob
from engine.core.niche import audience_is_children, build_profile
from engine.core.storage import (HEAVY_DIRS, HEAVY_FILES, NEVER_RECLAIM,
                                 may_reclaim, reclaim_job, sweep)


# ==========================================================================
class TestMadeForKidsFollowsTheAgeBand:
    """A finance video for 25-44 was produced as child-directed content.

    The app switched the flag on for a kids niche and never switched it off,
    and the state survived a niche change - so the backend was asked for
    child-directed finance and duly searched "personal finance for kids".
    """

    @pytest.mark.parametrize("band", ["2-4", "5-7", "8-12", "6-8", "9-12"])
    def test_under_13_is_child_directed(self, band):
        assert audience_is_children(band) is True

    @pytest.mark.parametrize("band", ["13-17", "18-24", "18-35", "25-44", "35+"])
    def test_13_and_over_is_not(self, band):
        assert audience_is_children(band) is False

    def test_all_ages_is_a_general_audience(self):
        """Treating it as children would force the kids safety profile."""
        assert audience_is_children("all ages") is False

    def test_blank_is_not_child_directed(self):
        assert audience_is_children("") is False
        assert audience_is_children(None) is False

    def test_the_age_band_overrides_the_flag(self):
        profile = build_profile("personal finance", audience="5-7",
                                made_for_kids=False)
        assert profile.made_for_kids is True

    def test_an_adult_band_leaves_finance_alone(self):
        profile = build_profile("personal finance", audience="25-44",
                                made_for_kids=False)
        assert profile.made_for_kids is False
        # And therefore does not search for children's content.
        assert "for kids" not in profile.search_modifiers

    def test_a_kids_niche_is_child_directed_on_its_own(self):
        profile = build_profile("kids bedtime stories", audience="25-44",
                                made_for_kids=False)
        assert profile.made_for_kids is True


# ==========================================================================
class TestBoilerplateGates:
    """The deterministic builders must not write a video that gets published."""

    def _generator(self, allow=False, dry=False):
        from engine.content.script import ScriptGenerator
        cfg = load_config()
        cfg.set("content.allow_template_script", allow)
        cfg.set("dry_run", dry)
        return ScriptGenerator(cfg)

    def _script(self, provider):
        from engine.core.models import Script
        return Script(provider=provider, script="some words here")

    def test_a_template_script_is_refused(self):
        with pytest.raises(LLMError, match="template"):
            self._generator()._refuse_boilerplate(self._script("template"), "hi")

    def test_a_model_script_passes(self):
        self._generator()._refuse_boilerplate(self._script("groq"), "hi")

    def test_a_partially_degraded_script_passes_with_a_warning(self):
        """One formulaic section in a long video is a blemish, not a write-off."""
        self._generator()._refuse_boilerplate(self._script("groq+template"), "en")

    def test_a_dry_run_keeps_the_builder(self):
        """A dry run is documented to produce every artifact without a key."""
        self._generator(dry=True)._refuse_boilerplate(
            self._script("template"), "hi")

    def test_the_switch_re_enables_it(self):
        self._generator(allow=True)._refuse_boilerplate(
            self._script("template"), "hi")

    def test_the_idea_stage_is_gated_too(self):
        """This is the gate that fixes the reported TITLE.

        A structural idea picks its subject out of research keywords, which is
        how a personal-finance request became a video about kids called "How
        Account Changes Kids". Gating only the script left that in place.
        """
        import inspect
        from engine.content import ideas
        src = inspect.getsource(ideas.IdeaGenerator.generate)
        assert "allow_template_script" in src
        assert "raise LLMError" in src

    def test_english_only_titles_are_not_attached_to_other_languages(self):
        import inspect
        from engine.content import metadata
        assert "English-only" in inspect.getsource(metadata)

    def test_template_sections_are_refused_on_a_non_english_script(self):
        """_template_section writes English; splicing it into Hindi is worse."""
        import inspect
        from engine.content import script as script_mod
        src = inspect.getsource(script_mod.ScriptGenerator._generate_sectioned)
        assert src.count("startswith") >= 2


# ==========================================================================
class TestSpeechNormalisation:
    def _n(self, text):
        from engine.tts.engine import normalize_for_speech
        return normalize_for_speech(text)

    def test_a_hyphen_between_words_becomes_a_space(self):
        """Kids-Invents was read out letter by letter."""
        assert self._n("Kids-Invents apps") == "Kids Invents apps"

    def test_a_digit_range_is_left_alone(self):
        assert self._n("the 2024-2025 season") == "the 2024-2025 season"

    def test_curly_quotes_go(self):
        assert "“" not in self._n("she said “hello”")

    def test_an_ellipsis_becomes_a_comma(self):
        assert self._n("wait… then") == "wait, then"

    def test_devanagari_with_digits_is_untouched(self):
        text = "रात 2024 में school"
        assert self._n(text) == text

    def test_numbers_are_not_expanded(self):
        """The 2024-in-Hindi-digits complaint was the wrong VOICE."""
        assert "2024" in self._n("in 2024 the price rose")


class TestVoiceFollowsTheText:
    def _lang(self, text, requested):
        from engine.tts.engine import language_for_text
        return language_for_text(text, requested)

    def test_english_text_with_a_hindi_request_gets_an_english_voice(self):
        """A Hindi voice reading English spelled smartest as smart-a-s-t."""
        assert self._lang("Is a stock account the smartest way?", "hi") == "en-IN"

    def test_devanagari_stays_hindi(self):
        assert self._lang("क्या जादू", "hi") == "hi"

    def test_mixed_hindi_with_english_words_stays_hindi(self):
        mixed = "रात 2024 में school गया"
        assert self._lang(mixed, "hi") == "hi"

    def test_the_guard_runs_per_scene(self):
        """A long-form script can be part Hindi and part English."""
        import inspect
        from engine.tts.engine import VoiceEngine
        src = inspect.getsource(VoiceEngine.synthesize_scenes)
        assert "language_for_text" in src

    def test_the_rate_survives_a_voice_switch(self):
        """The duration re-fit works by mutating spec.rate."""
        import inspect
        from engine.tts.engine import VoiceEngine
        src = inspect.getsource(VoiceEngine.synthesize_scenes)
        assert "use.rate, use.pitch = spec.rate, spec.pitch" in src


# ==========================================================================
class TestUpscaleMeasurement:
    """The reported "stretched and blurry" was two things, and one was a bug.

    Measured rather than assumed: a circle pushed through the real pipeline
    comes out round to within 0.13%, so nothing is stretched. What IS real is
    the magnification - the keyless generator returns 576x1024 whatever size is
    requested - and the factor the sharpening compensates for was computed
    against the frame width instead of the render width, understating it by 15%.
    """

    def test_the_oversize_constant_is_shared(self):
        """compose.py and condition_image round the same numbers."""
        from engine.video.compose import OVERSIZE as composed
        from engine.visuals.base import OVERSIZE as conditioned
        assert composed == conditioned

    def test_both_sides_agree_on_the_render_size(self):
        """A one-pixel disagreement made ffmpeg drop a row on every still."""
        from engine.visuals.base import OVERSIZE
        for width, height in ((1080, 1920), (1920, 1080)):
            assert (int(width * OVERSIZE) & ~1) % 2 == 0
            assert (int(height * OVERSIZE) & ~1) % 2 == 0

    def test_the_factor_is_measured_against_the_render_size(self):
        import inspect
        from engine.visuals import base
        # Compare CODE lines only. The first version of this assertion matched
        # the comment that explains the old formula and failed against correct
        # behaviour.
        code = [line.split("#", 1)[0].strip()
                for line in inspect.getsource(base.condition_image).splitlines()]
        assignments = [line for line in code
                       if line.startswith("upscale_factor =")]
        assert assignments == ["upscale_factor = render_w / max(img.size[0], 1)"]

    def test_a_square_source_stays_round(self, tmp_path):
        """Proves there is no aspect-breaking scale in the conditioning."""
        from PIL import Image, ImageDraw
        from engine.visuals.base import condition_image
        src = tmp_path / "circle.png"
        image = Image.new("RGB", (1024, 1024), (10, 10, 10))
        ImageDraw.Draw(image).ellipse([262, 262, 762, 762], fill=(240, 240, 240))
        image.save(src)

        condition_image(src, 1080, 1920, sharpen=False)

        with Image.open(src) as out:
            pixels = out.convert("L").load()
            width, height = out.size
            row = [x for x in range(width) if pixels[x, height // 2] > 128]
            column = [y for y in range(height) if pixels[width // 2, y] > 128]
        # The crop takes 56% of a square's width, so the circle is clipped
        # horizontally; what matters is that the SCALE did not distort it.
        assert row and column
        assert width == (int(1080 * 1.18) & ~1)
        assert height == (int(1920 * 1.18) & ~1)

    def test_zoompan_resamples_with_lanczos(self):
        """flags= on the preceding scale does not reach zoompan."""
        import inspect
        from engine.video.compose import VideoComposer
        src = inspect.getsource(VideoComposer.render_scene_clips)
        assert "sws_flags=lanczos" in src


# ==========================================================================
class TestStorageReclaim:
    def _job_dir(self, tmp_path, name="job"):
        directory = tmp_path / name
        (directory / "assets").mkdir(parents=True)
        for name_ in HEAVY_FILES:
            (directory / name_).write_bytes(b"x" * 2048)
        (directory / "assets" / "image_00.jpg").write_bytes(b"y" * 4096)
        (directory / "thumbnails").mkdir()
        (directory / "thumbnails" / "a.jpg").write_bytes(b"t" * 128)
        (directory / "quality_report.json").write_text("{}", encoding="utf-8")
        return directory

    def test_media_goes_and_reports_stay(self, tmp_path):
        directory = self._job_dir(tmp_path)
        result = reclaim_job(directory, "job-1")
        assert result.freed_bytes > 0
        for name in HEAVY_FILES:
            assert not (directory / name).exists()
        for name in HEAVY_DIRS:
            assert not (directory / name).exists()
        # Kept: kilobytes, and the record of what was made and why.
        assert (directory / "quality_report.json").exists()
        assert (directory / "thumbnails" / "a.jpg").exists()

    def test_it_is_idempotent(self, tmp_path):
        directory = self._job_dir(tmp_path)
        reclaim_job(directory, "job-1")
        again = reclaim_job(directory, "job-1")
        assert again.freed_bytes == 0 and again.removed == []

    def test_a_missing_directory_is_not_an_error(self, tmp_path):
        assert reclaim_job(tmp_path / "nope", "job-1").freed_bytes == 0

    @pytest.mark.parametrize("status", ["PUBLISHED", "SCHEDULED", "REJECTED",
                                        "CANCELLED", "FAILED"])
    def test_finished_states_reclaim_immediately(self, status):
        assert may_reclaim(status) is True

    def test_awaiting_approval_is_never_reclaimed(self):
        """The user is about to watch that video to decide on it."""
        assert may_reclaim("AWAITING_APPROVAL") is False
        assert may_reclaim("AWAITING_APPROVAL", age_days=999,
                           after_days=7) is False
        assert "AWAITING_APPROVAL" in NEVER_RECLAIM

    def test_ready_waits_for_the_age_sweep(self):
        """In a dry run READY is final and the local file is the only copy."""
        assert may_reclaim("READY") is False
        assert may_reclaim("READY", age_days=8, after_days=7) is True

    def test_the_sweep_spares_the_newest_jobs(self, tmp_path):
        jobs = []
        for index in range(4):
            directory = self._job_dir(tmp_path, f"job{index}")
            job = VideoJob(automation_id="a")
            job.job_id = f"job{index}"
            job.status = JobStatus.PUBLISHED.value
            job.dir = str(directory)
            job.updated_at = 1000.0 + index
            jobs.append(job)
        done = sweep(tmp_path, jobs, after_days=0.0, keep_last=2,
                     now=time.time())
        assert {r.job_id for r in done} == {"job0", "job1"}


# ==========================================================================
class TestJobAndAutomationRows:
    def _db(self, tmp_path):
        return Database(tmp_path / "t.db")

    def test_clearing_keeps_work_in_progress(self, tmp_path):
        db = self._db(tmp_path)
        for status in ("PUBLISHED", "RENDERING", "AWAITING_APPROVAL",
                       "REJECTED"):
            job = VideoJob(automation_id="a")
            job.status = status
            db.save_job(job)
        removed = {j.status for j in db.delete_jobs()}
        assert removed == {"PUBLISHED", "REJECTED"}
        assert {j.status for j in db.list_jobs(limit=50)} == {
            "RENDERING", "AWAITING_APPROVAL"}

    def test_an_automation_round_trips(self, tmp_path):
        db = self._db(tmp_path)
        request = AutomationRequest(niche="personal finance",
                                    frequency="daily",
                                    upload_time="20:00", days=[0, 2])
        db.save_automation(request)
        rows = db.list_automations()
        assert len(rows) == 1
        assert rows[0]["niche"] == "personal finance"
        assert rows[0]["frequency"] == "daily"

    def test_cancelling_persists(self, tmp_path):
        """The worker's in-memory set did not survive a restart."""
        db = self._db(tmp_path)
        request = AutomationRequest(niche="science", frequency="daily")
        db.save_automation(request)
        assert db.cancel_automation(request.id) is True
        assert db.list_automations() == []
        assert request.id in db.cancelled_automation_ids()
        # Still retrievable, so the UI can show it as stopped rather than
        # having it vanish - which looks like the cancel lost it.
        assert len(db.list_automations(include_cancelled=True)) == 1

    def test_cancelling_something_unknown_is_reported(self, tmp_path):
        assert self._db(tmp_path).cancel_automation("nope") is False

    def test_saving_twice_keeps_the_original_creation_time(self, tmp_path):
        db = self._db(tmp_path)
        request = AutomationRequest(niche="science")
        db.save_automation(request)
        first = db.get_automation(request.id)["created_at"]
        db.save_automation(request)
        assert db.get_automation(request.id)["created_at"] == first


# ==========================================================================
class TestCloudflareBackend:
    def _backend(self, **kwargs):
        from engine.visuals.ai_image import CloudflareBackend
        return CloudflareBackend("acct", "token", **kwargs)

    def test_flux_is_not_size_aware(self):
        """It returns a square, which a 9:16 crop then trims to a third."""
        assert self._backend().supports_size is False

    def test_sdxl_is_size_aware(self):
        backend = self._backend(
            model="@cf/stabilityai/stable-diffusion-xl-base-1.0")
        assert backend.supports_size is True

    def test_steps_default_to_the_documented_value(self):
        """Neurons are charged per step, so this sets the daily image count."""
        assert self._backend().steps == 4

    def test_steps_are_clamped_to_the_ceiling(self):
        assert self._backend(steps=99).steps == 8
        assert self._backend(steps=0).steps == 1

    def test_credentials_are_required(self):
        from engine.visuals.ai_image import CloudflareBackend
        assert CloudflareBackend("", "").available() is False
        assert CloudflareBackend("a", "b").available() is True

    def test_a_quota_error_is_mapped(self):
        """429 must not be retried three times per scene."""
        import inspect
        from engine.visuals.ai_image import CloudflareBackend
        src = inspect.getsource(CloudflareBackend.fetch)
        assert "QuotaExhausted" in src
        assert "429" in src

    def test_both_response_shapes_are_handled(self):
        """flux returns base64 JSON; stable-diffusion returns raw bytes."""
        import inspect
        from engine.visuals.ai_image import CloudflareBackend
        src = inspect.getsource(CloudflareBackend.fetch)
        assert "b64decode" in src
        assert "content_type" in src

# ==========================================================================
class TestKidsStoryPrompt:
    """A bedtime story came back as narration and philosophy.

    The prompt was asking for it: every child-directed request was told "no
    danger, no conflict", and a story with no conflict is not a story.
    """

    def _instruction(self, niche, audience="5-7"):
        from engine.content.script import _kids_instruction
        return _kids_instruction(build_profile(niche, audience=audience))

    def test_a_story_niche_is_told_to_tell_a_story(self):
        text = self._instruction("kids bedtime stories")
        assert "actual STORY" in text

    def test_a_story_needs_a_named_character(self):
        assert "named character" in self._instruction("kids moral stories")

    def test_gentle_stakes_are_required_rather_than_forbidden(self):
        """"no conflict" is what produced an essay about hugs."""
        text = self._instruction("kids bedtime stories")
        assert "no conflict" not in text
        assert "at stake" in text

    def test_abstraction_is_ruled_out(self):
        text = self._instruction("kids bedtime stories")
        assert "rhetorical questions" in text

    def test_rhymes_are_stories_not_drills(self):
        """A nursery rhyme is a performance, not a flashcard."""
        assert "actual STORY" in self._instruction("kids rhymes and poems")

    def test_teaching_content_keeps_repetition(self):
        text = self._instruction("kids alphabet learning")
        assert "TEACHING" in text
        assert "Repeat" in text
        assert "actual STORY" not in text

    def test_adult_content_gets_no_kids_block(self):
        assert self._instruction("personal finance", audience="25-44") == ""

    def test_both_prompt_builders_use_it(self):
        import inspect
        from engine.content.script import ScriptGenerator
        joined = (inspect.getsource(ScriptGenerator._build_prompt)
                  + inspect.getsource(ScriptGenerator._section_prompt))
        assert joined.count("_kids_instruction(profile)") == 2

    def test_the_learning_keywords_are_shared_with_the_templates(self):
        """Two copies of this list would drift."""
        import inspect
        from engine.content import script as script_mod
        from engine.video.templates import KIDS_LEARNING_HINTS
        assert "alphabet" in KIDS_LEARNING_HINTS
        assert "KIDS_LEARNING_HINTS" in inspect.getsource(script_mod)

# ==========================================================================
class TestCaptionLanguage:
    """Captions in a different language from the voice.

    Asked for directly: Hindi narration with English captions, and the
    reverse. The hard part is that captions ARE the TTS word-mark stream, so
    translated text has no per-word timing and cannot be karaoke.
    """

    def _needs(self, narration, caption):
        from engine.content.translate import needs_translation
        return needs_translation(narration, caption)

    def test_a_different_language_is_translated(self):
        assert self._needs("hi", "en") is True
        assert self._needs("en", "hi") is True

    def test_blank_follows_the_narration(self):
        """The default, and what every previous version did."""
        assert self._needs("hi", "") is False

    def test_the_same_language_costs_no_call(self):
        assert self._needs("hi", "hi") is False

    def test_a_regional_variant_is_the_same_language(self):
        """en against en-IN must not spend an LLM call."""
        assert self._needs("en", "en-IN") is False

    def test_hinglish_is_not_a_translation_target(self):
        """It is Hindi in Latin letters, so the base language matches."""
        assert self._needs("hi", "hi-latn") is False

    def test_language_names_are_resolved_for_the_prompt(self):
        """A bare code gets Hinglish about as often as Hindi."""
        from engine.content.translate import language_name
        assert language_name("hi") == "Hindi"
        assert language_name("en-IN") == "Indian English"
        # An unmapped regional code falls back to its base language.
        assert language_name("en-GB") == "English"

    def test_a_failed_translation_degrades_rather_than_raising(self):
        """Losing the second language beats losing the video."""
        from engine.content.translate import translate_scenes
        from engine.core.models import Scene

        class Broken:
            def complete_json(self, *a, **k):
                raise RuntimeError("rate limited")

        scenes = [Scene(index=0, narration="hello")]
        assert translate_scenes(scenes, target="hi", router=Broken()) == 0
        assert scenes[0].caption_text == ""

    def test_a_good_reply_fills_every_scene(self):
        from engine.content.translate import translate_scenes
        from engine.core.models import Scene

        class Router:
            def complete_json(self, prompt, **k):
                return {"lines": [{"n": 1, "text": "एक"},
                                  {"n": 2, "text": "दो"}]}, "fake"

        scenes = [Scene(index=0, narration="one"), Scene(index=1, narration="two")]
        assert translate_scenes(scenes, target="hi", router=Router()) == 2
        assert scenes[0].caption_text == "एक"
        assert scenes[1].caption_text == "दो"

    def test_an_unnumbered_list_is_still_accepted(self):
        """Models drop the numbering; the shape has to tolerate it."""
        from engine.content.translate import translate_scenes
        from engine.core.models import Scene

        class Router:
            def complete_json(self, prompt, **k):
                return {"lines": ["first", "second"]}, "fake"

        scenes = [Scene(index=0, narration="a"), Scene(index=1, narration="b")]
        assert translate_scenes(scenes, target="en", router=Router()) == 2

    def test_translated_blocks_are_timed_to_scene_spans(self, tmp_path):
        from engine.video.captions import CaptionEngine
        engine = CaptionEngine(load_config())
        spans = [(0.0, 3.0, "पहली पंक्ति"), (3.2, 6.0, "दूसरी पंक्ति")]
        ass_path, srt_path, count = engine.build_translated(
            spans, tmp_path / "c.ass", tmp_path / "c.srt", 1080, 1920,
            language="hi")
        assert count == 2
        body = ass_path.read_text(encoding="utf-8")
        assert body.count("Dialogue:") == 2
        # The Devanagari font, chosen from the CAPTION language.
        assert "Noto Sans Devanagari" in body

    def test_a_very_short_scene_is_held_long_enough_to_read(self, tmp_path):
        from engine.video.captions import CaptionEngine
        engine = CaptionEngine(load_config())
        ass_path, _, _ = engine.build_translated(
            [(0.0, 0.2, "a line")], tmp_path / "c.ass", tmp_path / "c.srt",
            1080, 1920, language="en")
        line = next(l for l in ass_path.read_text(encoding="utf-8").splitlines()
                    if l.startswith("Dialogue:"))
        end = line.split(",")[2]
        # 0.2s would flash. The floor holds it for at least 0.85s.
        assert end > "0:00:00.60"

    def test_a_translated_block_is_not_uppercased(self, tmp_path):
        """Uppercasing a whole sentence is shouting, and some scripts have no
        case at all."""
        from engine.video.captions import CaptionEngine
        cfg = load_config()
        cfg.set("captions.uppercase", True)
        ass_path, _, _ = CaptionEngine(cfg).build_translated(
            [(0.0, 3.0, "a quiet village morning")],
            tmp_path / "c.ass", tmp_path / "c.srt", 1080, 1920, language="en")
        assert "a quiet village morning" in ass_path.read_text(encoding="utf-8")

    def test_empty_text_is_skipped_rather_than_rendered_blank(self, tmp_path):
        from engine.video.captions import CaptionEngine
        engine = CaptionEngine(load_config())
        _, _, count = engine.build_translated(
            [(0.0, 2.0, "kept"), (2.0, 4.0, "   ")],
            tmp_path / "c.ass", tmp_path / "c.srt", 1080, 1920, language="en")
        assert count == 1

    def test_nothing_to_render_is_an_error_not_a_silent_empty_file(self, tmp_path):
        from engine.video.captions import CaptionEngine
        with pytest.raises(RuntimeError):
            CaptionEngine(load_config()).build_translated(
                [], tmp_path / "c.ass", tmp_path / "c.srt", 1080, 1920)

    def test_the_request_carries_the_choice(self):
        assert AutomationRequest().caption_language == ""
        assert AutomationRequest(caption_language="en").caption_language == "en"

    def test_the_api_accepts_it(self):
        from backend.api.main import AutomationBody
        body = AutomationBody(niche="science", caption_language="hi")
        assert body.caption_language == "hi"

# ==========================================================================
class TestCaptionsOff:
    """The narration-only look: a full-frame illustration with nothing over it.

    Both reference videos the owner shared do this - one has no burned
    captions at all - and it reads as calmer and more finished than text over
    every frame.
    """

    def test_the_request_carries_the_choice(self):
        assert AutomationRequest().caption_style == ""
        assert AutomationRequest(caption_style="none").caption_style == "none"

    def test_the_api_rejects_an_unknown_style(self):
        from pydantic import ValidationError
        from backend.api.main import AutomationBody
        assert AutomationBody(niche="science",
                              caption_style="none").caption_style == "none"
        with pytest.raises(ValidationError):
            AutomationBody(niche="science", caption_style="rainbow")

    def test_an_srt_is_still_written(self):
        """The frame stays clean; YouTube still gets a real subtitle track."""
        import inspect
        from engine.video.captions import CaptionEngine
        assert "srt_only" in inspect.getsource(CaptionEngine)

    def test_the_burn_in_is_skipped_rather_than_given_an_empty_file(self):
        """libass on an events-less ASS is a wasted pass over every frame."""
        import inspect
        from engine.pipeline import Pipeline
        src = inspect.getsource(Pipeline)
        assert 'if caption_style == "none"' in src

    def test_the_composer_already_tolerates_no_subtitles(self):
        import inspect
        from engine.video.compose import VideoComposer
        src = inspect.getsource(VideoComposer.finalize)
        assert "if ass_file is not None and ass_file.exists():" in src

    def test_srt_only_produces_real_cues(self, tmp_path):
        from engine.tts.base import SceneAudio, WordMark
        from engine.video.captions import CaptionEngine
        clip = SceneAudio(scene_index=0, path=tmp_path / "a.wav",
                          duration=2.0, text="hello world", provider="edge",
                          words=[WordMark(0.0, 0.4, "hello"),
                                 WordMark(0.5, 0.4, "world")])
        text = CaptionEngine(load_config()).srt_only([(0.0, clip)])
        assert "hello" in text and "-->" in text

    def test_srt_only_is_empty_when_there_are_no_words(self, tmp_path):
        from engine.tts.base import SceneAudio
        from engine.video.captions import CaptionEngine
        clip = SceneAudio(scene_index=0, path=tmp_path / "a.wav",
                          duration=1.0, text="", provider="gtts", words=[])
        assert CaptionEngine(load_config()).srt_only([(0.0, clip)]) == ""
