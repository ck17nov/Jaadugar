"""Non-English video.

These exist because asking for Hindi produced an English video and nothing in
the logs said why. The chain was: the model writes a correct Devanagari script
-> the word counter is ASCII-only and reports 0 words -> the word-floor check
decides the script is empty and re-asks -> the retry counts 0 as well -> the
pipeline falls back to the structural template, which only speaks English.

Six of the eight offered languages were affected, Hindi being the default.
"""
from __future__ import annotations

import pytest

from engine.core.util import keywords, words

# Plain sentences with no punctuation, so the expected count is simply the
# number of space-separated tokens. Asserting against that invariant rather
# than hand-written numbers is deliberate: the first version of this test had
# invented counts in it, which "failed" against correct behaviour and hid the
# real second bug.
SAMPLES = {
    "english": "A hug can turn a dark room into a starry sky",
    "hindi": "एक प्यारी झप्पी अंधेरे कमरे को तारों से भरे आकाश में बदल सकती है",
    "tamil": "ஒரு அணைப்பு இருண்ட அறையை நட்சத்திர வானமாக மாற்றும்",
    "bengali": "একটি আলিঙ্গন অন্ধকার ঘরকে তারার আকাশে পরিণত করতে পারে",
    "telugu": "ఒక కౌగిలి చీకటి గదిని నక్షత్రాల ఆకాశంగా మారుస్తుంది",
    "marathi": "एक मिठी अंधाऱ्या खोलीला ताऱ्यांच्या आकाशात बदलते",
    "gujarati": "એક ભેટ અંધારા રૂમને તારાઓના આકાશમાં બદલી નાખે છે",
}


class TestWordCounting:
    @pytest.mark.parametrize("name", sorted(SAMPLES))
    def test_every_offered_language_counts_words(self, name):
        """Zero here is what turned Hindi requests into English videos."""
        assert len(words(SAMPLES[name])) > 0, f"{name} counted zero words"

    @pytest.mark.parametrize("name", sorted(SAMPLES))
    def test_one_token_per_spoken_word(self, name):
        r"""The word budget and the measured speech rate both divide by this.

        Python's \w does not match Unicode combining marks, so a class-based
        tokeniser split Devanagari and Tamil at every vowel sign and counted
        roughly three times too many words - skewing the duration budget as
        badly as the zero did, just in the other direction.
        """
        text = SAMPLES[name]
        assert len(words(text)) == len(text.split())

    def test_an_apostrophe_does_not_split_a_word(self):
        assert words("don't stop") == ["don't", "stop"]

    def test_digits_still_count(self):
        assert len(words("in 1947 there were 3 million")) == 6

    def test_punctuation_is_not_counted(self):
        assert len(words("Hello, world! -- really?")) == 3

    def test_a_devanagari_danda_is_punctuation(self):
        """It ends a sentence the way a full stop does."""
        assert words("यह एक वाक्य है। दूसरा वाक्य।") == [
            "यह", "एक", "वाक्य", "है", "दूसरा", "वाक्य"]

    def test_empty_and_none_are_safe(self):
        assert words("") == []
        assert words(None) == []


class TestKeywordsAcrossScripts:
    def test_devanagari_yields_keywords(self):
        """Empty keywords degrade every image prompt for the whole video."""
        text = SAMPLES["hindi"]
        assert keywords(text, limit=5)

    def test_english_stopwords_are_still_removed(self):
        assert "the" not in keywords("the cat sat on the mat with the hat")


class TestScriptPromptsPinVisualLanguage:
    """A Hindi visual_prompt returns a worse picture from every provider."""

    def test_both_prompt_builders_ask_for_english_visuals(self):
        from pathlib import Path
        src = Path("engine/content/script.py").read_text(encoding="utf-8")
        assert src.count(
            "Write `visual_prompt` and `visual_keywords` in ENGLISH") == 2

    def test_the_language_line_demands_the_native_script(self):
        from engine.content.script import _language_line
        line = _language_line("hi")
        assert "Devanagari" in line
        assert "Do NOT romanise" in line

    def test_hinglish_is_exempt_from_the_native_script_rule(self):
        from engine.content.script import _language_line
        line = _language_line("hi-latn")
        assert "Latin" in line
        assert "Do not write in Devanagari" in line

    def test_english_gets_no_script_instruction(self):
        from engine.content.script import _language_line
        assert _language_line("en") == "Write the narration in English."


class TestScriptFonts:
    """Devanagari captions rendered as tofu boxes until this existed.

    The display faces cover Latin only, and the deployed box has no Indic
    fonts installed, so the fallback was empty rectangles rather than a
    different-looking face.
    """

    def test_indic_languages_map_to_a_script(self):
        from engine.video.fonts import script_for_language
        for lang in ("hi", "mr", "ta", "te", "bn", "gu"):
            assert script_for_language(lang), lang

    def test_english_needs_no_script_font(self):
        from engine.video.fonts import script_for_language
        assert script_for_language("en") == ""
        assert script_for_language("en-IN") == ""

    def test_hinglish_uses_the_latin_display_face(self):
        """It is Hindi written in Latin letters, so Anton is correct."""
        from engine.video.fonts import script_for_language
        assert script_for_language("hi-latn") == ""

    def test_a_regional_code_resolves_by_base_language(self):
        from engine.video.fonts import script_for_language
        assert script_for_language("hi-IN") == "deva"

    def test_every_script_has_a_bundled_font(self):
        """Bundled, not downloaded: a failed download would ship tofu."""
        from engine.video.fonts import FONT_DIR, SCRIPT_FONTS
        for script, (family, _url) in SCRIPT_FONTS.items():
            path = FONT_DIR / f"{family}.ttf"
            assert path.exists(), f"{script}: {family}.ttf is not bundled"
            assert path.stat().st_size > 20000, f"{family}.ttf looks truncated"

    def test_the_font_actually_contains_the_glyphs(self):
        """The real bug was a font without the characters in it."""
        from PIL import ImageFont
        from engine.video.fonts import display_font
        font_file, _ = display_font("Anton", language="hi")
        face = ImageFont.truetype(str(font_file), 48)
        # A face missing these would report the same width for both, because
        # every character would fall back to the same .notdef box.
        assert face.getlength("क्या जादू") != face.getlength("XXXXXXXXX")

    def test_hindi_does_not_get_the_latin_face(self):
        from engine.video.fonts import display_font
        latin, _ = display_font("Anton", language="en")
        hindi, _ = display_font("Anton", language="hi")
        assert latin.name != hindi.name

    def test_the_caption_width_budget_is_measured_per_script(self):
        """One constant for both scripts wraps one and wastes the other."""
        from engine.core.config import load_config
        from engine.video.captions import CaptionEngine
        engine = CaptionEngine(load_config())
        latin = engine._glyph_ratio("en", 112)
        deva = engine._glyph_ratio("hi", 112)
        assert 0.2 < deva < 0.8 and 0.2 < latin < 0.8
        assert abs(deva - latin) > 0.01, "expected different measurements"
