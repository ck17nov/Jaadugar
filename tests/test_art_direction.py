"""The art direction has to reach the image generator.

Every test here guards a fault that shipped: the image prompt was being given
the Style dropdown's pacing phrase in the slot meant for how the picture
should look, and the scene brief asked every template for a photograph even
when the template draws its images.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

from engine.core.niche import build_profile
from engine.video.templates import TEMPLATES, apply_to_profile, select_template

ROOT = Path(__file__).resolve().parents[1]


class TestStyleRouting:
    def test_stage_visuals_passes_the_profile_style_not_the_request_style(self):
        """The regression this whole file exists for.

        `request.style` is one of six PACING phrases from the app's Style
        dropdown - "fast-paced, curiosity-driven", "gentle and simple (for
        young children)". None of them describes a picture, and passing one
        into the art-direction slot displaced the template's own look.
        """
        from engine import pipeline
        source = inspect.getsource(pipeline.Pipeline.stage_visuals)
        tree = ast.parse(textwrap.dedent(source))
        styles = [
            ast.unparse(kw.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "style"
        ]
        assert styles, "stage_visuals no longer passes a style at all"
        assert all("profile" in s for s in styles), styles
        assert not any("request.style" in s for s in styles), styles

    def test_the_style_dropdown_options_are_all_about_pacing(self):
        """If someone makes one of them art direction, this stops being true
        and the reasoning above needs revisiting."""
        screen = (ROOT / "android/app/src/main/java/com/autotube/ai/ui"
                  "/screens/CreateAutomationScreen.kt").read_text(encoding="utf-8")
        block = screen.split("val STYLES = listOf(", 1)[1].split(")", 1)[0]
        # No option may name a medium - that is the template's job.
        for banned in ("illustration", "photography", "cel-shaded", "painterly"):
            assert banned not in block.lower(), banned

    def test_the_prompt_does_not_repeat_the_style_it_already_has(self):
        from engine.core.config import load_config
        from engine.visuals.ai_image import AIImageProvider
        from engine.visuals.base import VisualRequest
        provider = AIImageProvider(load_config())
        style = "gentle storybook illustration, soft rounded shapes"
        req = VisualRequest(prompt=f"a girl and a red shoe, {style}",
                            keywords=[], style=style, scene_index=0,
                            width=1080, height=1920)
        assert provider.build_prompt(req).lower().count("soft rounded shapes") == 1

    def test_the_style_is_still_added_when_the_scene_lacks_it(self):
        from engine.core.config import load_config
        from engine.visuals.ai_image import AIImageProvider
        from engine.visuals.base import VisualRequest
        provider = AIImageProvider(load_config())
        req = VisualRequest(prompt="a girl and a red shoe", keywords=[],
                            style="gentle storybook illustration",
                            scene_index=0, width=1080, height=1920)
        assert "gentle storybook illustration" in provider.build_prompt(req)


class TestImageBrief:
    def test_an_illustrated_template_does_not_ask_for_a_photograph(self):
        for name in ("STORYTELLING", "KIDS_STORY", "ILLUSTRATED_EXPLAINER"):
            brief = TEMPLATES[name].image_brief.lower()
            assert "photographable" not in brief, name
            assert "camera" not in brief or "no camera" in brief, name

    def test_a_factual_template_still_asks_for_a_photograph(self):
        assert "photographable" in TEMPLATES["TECH_NEWS"].image_brief

    def test_the_brief_reaches_the_profile(self):
        profile = build_profile("kids bedtime stories", audience="5-7",
                                made_for_kids=True)
        template = select_template("kids bedtime stories", "",
                                   made_for_kids=True)
        profile = apply_to_profile(profile, template)
        assert profile.image_brief == template.image_brief

    def test_the_brief_reaches_both_script_prompts(self):
        """There are two prompt builders - sectioned long-form and one-shot -
        and an earlier fix to one of them missed the other."""
        source = (ROOT / "engine/content/script.py").read_text(encoding="utf-8")
        assert source.count("{profile.image_brief}") == 2
        assert "photographable subject, camera framing" not in source


class TestIllustratedExplainer:
    def test_it_holds_a_frame_long_enough_to_look_at(self):
        assert TEMPLATES["ILLUSTRATED_EXPLAINER"].scene_seconds >= 12.0

    def test_it_carries_no_burnt_in_captions(self):
        assert TEMPLATES["ILLUSTRATED_EXPLAINER"].caption_style == "none"

    def test_it_draws_rather_than_searching_stock(self):
        template = TEMPLATES["ILLUSTRATED_EXPLAINER"]
        assert template.prefer_ai is True
        assert template.visual_style_suffix

    def test_every_template_that_prefers_ai_asks_for_a_drawn_look(self):
        """prefer_ai and the style suffix contradicting each other is the
        failure the template docstring warns about."""
        drawn = ("illustration", "illustrated", "cel-shaded", "animation",
                 "painted", "storybook", "cartoon", "art")
        for name, template in TEMPLATES.items():
            if template.prefer_ai:
                suffix = template.visual_style_suffix.lower()
                assert any(w in suffix for w in drawn), name


class TestTemplateIsReachable:
    """A template nobody can select is not a feature.

    ILLUSTRATED_EXPLAINER was added for the long-form reference look and was
    reachable only by forcing `video.style_template` in config - there is no
    template field in the API or the app at all.
    """

    def test_a_long_form_story_gets_the_held_frame_look(self):
        template = select_template("90s village nostalgia story",
                                   "storytelling", long_form=True)
        assert template.name == "ILLUSTRATED_EXPLAINER"

    def test_the_same_subject_as_a_short_does_not(self):
        """A 45-second story wants 6-second scenes, not 14-second ones."""
        template = select_template("90s village nostalgia story",
                                   "storytelling", long_form=False)
        assert template.name == "STORYTELLING"

    def test_child_directed_still_wins_over_length(self):
        """KIDS_STORY's pacing and caption style are part of the safety
        profile, not a preference a format can override."""
        template = select_template("kids bedtime stories", "gentle",
                                   made_for_kids=True, long_form=True)
        assert template.name == "KIDS_STORY"

    def test_a_long_form_explainer_is_not_hijacked(self):
        """Only narrated STORIES want held frames. An explainer or a news
        piece is not improved by fourteen-second holds."""
        for niche in ("personal finance", "AI news", "pc and laptop tech"):
            template = select_template(niche, "educational and clear",
                                       long_form=True)
            assert template.name != "ILLUSTRATED_EXPLAINER", niche

    def test_the_pipeline_passes_the_format_through(self):
        """Otherwise the routing above can never fire in production."""
        from engine import pipeline
        source = inspect.getsource(pipeline.Pipeline)
        assert "long_form=" in source
        assert "LONGFORM" in source
