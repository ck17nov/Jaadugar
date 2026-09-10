"""The story-shape gate, against the script that actually shipped.

BAD is the bedtime story found on disk, paraphrased to the same shape: an
opening rhetorical question about a feeling, no protagonist, no repetition,
and an adult resolving it. Every blocking check must catch it.
"""
from __future__ import annotations

import inspect

import pytest

from engine.content.story_gate import (MIN_NAME_COVERAGE, REFRAIN_MIN_REPEATS,
                                       evaluate)

BAD = [
    "Can a hug turn a dark room into a starry sky?",
    "A hug is warm and kind.",
    "Hugs make the night feel small.",
    "Mother came and held her tight.",
    "And that is why hugs matter.",
    "Sleep well tonight.",
]

GOOD_EN = [
    "Mia wants the top shelf tin. Not yet, Mia. Not yet.",
    "She jumps up. Her fingers miss. Not yet, Mia. Not yet.",
    "The shelf is too tall. Her tummy feels tight. Can you jump with me?",
    "She sees a small wooden stool and pulls it close.",
    "She climbs up and opens the lid. The cookies smell sweet.",
    "Not yet, Mia. Not yet. Now yes, Mia!",
]

GOOD_HI = [
    "आरव को लाल जूता चाहिए। अभी नहीं आरव। अभी नहीं।",
    "आरव ने पलंग के नीचे देखा। अभी नहीं आरव। अभी नहीं।",
    "जूता वहाँ नहीं था। आरव का मन भारी हुआ।",
    "आरव ने खिड़की के पास कुछ देखा।",
    "आरव ने जूता उठाया और मुस्कुराया।",
    "अभी नहीं आरव। अभी नहीं। अब हाँ!",
]


def _checks(report):
    return {f.check: f for f in report.findings}


class TestTheShippedStoryIsRejected:
    def test_it_fails(self):
        assert evaluate(BAD, words_per_scene_floor=10).passed is False

    def test_it_fails_for_the_right_reasons(self):
        blocked = {f.check for f in evaluate(BAD).blockers}
        assert "named_character" in blocked
        assert "verbatim_refrain" in blocked
        assert "no_opening_question" in blocked

    def test_the_opening_question_is_pinpointed(self):
        finding = _checks(evaluate(BAD))["no_opening_question"]
        assert "[0]" in finding.detail

    def test_the_advisories_also_fire(self):
        warned = {f.check for f in evaluate(BAD, words_per_scene_floor=12).warnings}
        assert {"want_stated_early", "has_obstacle", "enough_words"} <= warned


class TestAWellShapedStoryPasses:
    @pytest.mark.parametrize("scenes,expected_name",
                             [(GOOD_EN, "mia"), (GOOD_HI, "आरव")])
    def test_it_passes_in_both_scripts(self, scenes, expected_name):
        report = evaluate(scenes, words_per_scene_floor=10)
        assert report.passed, report.summary()
        assert report.character == expected_name

    @pytest.mark.parametrize("scenes", [GOOD_EN, GOOD_HI])
    def test_the_refrain_is_found(self, scenes):
        report = evaluate(scenes, words_per_scene_floor=10)
        assert report.refrain
        assert len(report.refrain.split()) >= 4


class TestNameDetection:
    def test_capitalisation_beats_frequency_on_a_tie(self):
        """Frequency alone picked "yet" as the protagonist of a story about
        Mia, because the refrain made both words equally common."""
        assert evaluate(GOOD_EN, words_per_scene_floor=10).character == "mia"

    def test_devanagari_falls_back_to_frequency(self):
        """There are no capitals in Devanagari, so a capital-only heuristic
        would silently pass every Hindi script."""
        assert evaluate(GOOD_HI, words_per_scene_floor=10).character == "आरव"

    def test_a_character_introduced_late_is_not_the_protagonist(self):
        scenes = ["The room was dark and quiet.",
                  "Ravi opened the door.", "Ravi smiled.", "Ravi slept."]
        report = evaluate(scenes, words_per_scene_floor=4)
        assert _checks(report)["named_character"].passed is False

    def test_a_shouted_word_is_not_a_name(self):
        scenes = ["The tin was HIGH on the shelf.",
                  "It was HIGH.", "Still HIGH.", "HIGH again."]
        assert evaluate(scenes, words_per_scene_floor=3).character != "high"


class TestRefrain:
    def test_two_repeats_is_not_enough(self):
        scenes = ["Pip wants the ball. Roll it back, Pip.",
                  "Pip jumps. Roll it back, Pip.",
                  "Pip laughs at the sun.", "Pip has the ball now."]
        report = evaluate(scenes, words_per_scene_floor=4)
        assert _checks(report)["verbatim_refrain"].passed is False
        assert str(REFRAIN_MIN_REPEATS) in _checks(report)["verbatim_refrain"].detail

    def test_a_paraphrase_does_not_count(self):
        """"Verbatim" is the whole point - a refrain a child can join in with
        has to be the same words every time."""
        scenes = ["Pip wants the ball. Roll it back.",
                  "Pip jumps. Please roll the ball back.",
                  "Pip waits. Roll that ball backwards.",
                  "Pip has the ball."]
        report = evaluate(scenes, words_per_scene_floor=4)
        assert _checks(report)["verbatim_refrain"].passed is False

    def test_the_longest_repeated_phrase_wins(self):
        report = evaluate(GOOD_EN, words_per_scene_floor=10)
        assert "not yet" in report.refrain


class TestAgency:
    def test_an_adult_resolving_it_is_blocking(self):
        scenes = ["Tara wants the kite. Up, up, Tara. Up, up.",
                  "She pulls the string. Up, up, Tara. Up, up.",
                  "The kite will not rise.",
                  "Her mother took the string and flew it for her."]
        report = evaluate(scenes, words_per_scene_floor=4)
        assert _checks(report)["child_resolves_it"].passed is False

    def test_an_adult_present_but_not_resolving_is_fine(self):
        """A kind grown-up earlier in the story is normal; only one at the
        resolution is the agency inversion."""
        scenes = ["Tara wants the kite. Up, up, Tara. Up, up.",
                  "Her mother waves from the step.",
                  "She pulls the string. Up, up, Tara. Up, up.",
                  "Tara runs faster and the kite climbs. Up, up, Tara."]
        report = evaluate(scenes, words_per_scene_floor=4)
        assert _checks(report)["child_resolves_it"].passed is True

    def test_an_adult_with_the_child_at_the_end_is_fine(self):
        scenes = ["Anya wants the shell. Dig, dig, Anya. Dig, dig.",
                  "She digs. Dig, dig, Anya. Dig, dig.",
                  "The sand is heavy.",
                  "Anya digs harder and finds it while her mother watches. "
                  "Dig, dig, Anya."]
        assert _checks(evaluate(scenes, words_per_scene_floor=4))[
            "child_resolves_it"].passed is True


class TestContract:
    def test_an_empty_script_is_blocked_not_crashed(self):
        report = evaluate([])
        assert report.passed is False
        assert report.blockers

    def test_blank_scenes_are_dropped(self):
        assert evaluate(["", "   ", ""]).passed is False

    def test_it_costs_no_network_call(self):
        """A gate that costs a round trip is a gate somebody will disable."""
        import engine.content.story_gate as mod
        source = inspect.getsource(mod)
        for banned in ("httpx", "requests", "urllib", "router", "complete("):
            assert banned not in source, banned

    def test_the_report_is_json_serialisable(self):
        """It is written into job.json so a boring story can be diagnosed
        after the fact rather than re-run."""
        import json
        json.dumps(evaluate(GOOD_EN, words_per_scene_floor=10).to_dict())

    def test_the_coverage_threshold_is_a_named_constant(self):
        assert 0.0 < MIN_NAME_COVERAGE <= 1.0


class TestWiring:
    def test_the_gate_runs_before_the_render(self):
        """In the script generator, not the media gate - by the time a video
        exists the script has already cost an image budget."""
        from engine.content.script import ScriptGenerator
        source = inspect.getsource(ScriptGenerator.generate)
        assert "_ensure_story_shape" in source

    def test_only_child_directed_narrative_is_gated(self):
        from engine.content.script import ScriptGenerator
        source = inspect.getsource(ScriptGenerator._ensure_story_shape)
        assert "is_kids_story(profile)" in source

    def test_a_failed_re_ask_keeps_the_first_draft(self):
        """Refusing outright would turn a soft quality problem into a failed
        job, and a 3-of-4 story still beats the boilerplate."""
        from engine.content.script import ScriptGenerator
        source = inspect.getsource(ScriptGenerator._ensure_story_shape)
        assert "keeping the first draft" in source


# ---------------------------------------------------------------------------
# Craft, not just shape
# ---------------------------------------------------------------------------
class TestTheTurnIsAnIdea:
    """A story where the child just looks somewhere else is not a story.

    Measured across the bank: 7 of 19 narratives turn on a perception -
    "Then Aarav peeked at the far end of the cot", "तभी ... देखा". Nothing
    is invented, combined, traded or reframed; the camera pans. Advisory
    for now, because the entries that fail are already banked.
    """

    def _story(self, turn: str) -> list[str]:
        return [
            "Aarav wanted his red ball more than anything this morning.",
            "The ball rolled under the heavy wooden cot and stopped.",
            "He pushed his hand in but his fingers fell short again.",
            turn,
            "He worked at it until the ball came free at last.",
            "Aarav hugged the ball and laughed out loud.",
        ]

    def _finding(self, narrations, check):
        from engine.content.story_gate import evaluate
        report = evaluate(narrations)
        return next(f for f in report.findings if f.check == check)

    def test_a_glance_is_not_a_turn(self):
        finding = self._finding(
            self._story("Then Aarav noticed the far end of the cot."),
            "turn_is_an_idea")
        assert finding.passed is False
        assert "perception" in finding.detail

    def test_a_hindi_glance_is_not_a_turn_either(self):
        narrations = [
            "आरव को अपनी लाल गेंद बहुत पसंद थी और वह खेलना चाहता था।",
            "गेंद लुढ़ककर भारी खाट के नीचे चली गई और रुक गई।",
            "उसने हाथ अंदर डाला पर उँगलियाँ गेंद तक नहीं पहुँचीं।",
            "तभी आरव ने खाट के दूसरे सिरे पर झाँका।",
            "उसने वहाँ से हाथ डाला और गेंद बाहर आ गई।",
            "आरव ने गेंद को सीने से लगाया और हँस पड़ा।",
        ]
        assert self._finding(narrations, "turn_is_an_idea").passed is False

    def test_an_invention_is_a_turn(self):
        finding = self._finding(
            self._story("Aarav slid his kite stick along the floor to sweep "
                        "the ball out."),
            "turn_is_an_idea")
        assert finding.passed is True

    def test_the_beat_names_pick_the_right_scene(self):
        """Position is a fallback; the entry's own beats are better."""
        from engine.content.story_gate import evaluate

        narrations = self._story("Aarav tied two sticks together to reach it.")
        # Put a perception in a scene that is NOT the turn.
        narrations[1] = "Then Aarav saw the cot in the middle of the room."
        beats = ["want", "attempt", "obstacle", "turn", "resolve", "refrain"]
        report = evaluate(narrations, beats=beats)
        finding = next(f for f in report.findings
                       if f.check == "turn_is_an_idea")
        assert finding.passed is True, finding.detail


class TestTheObstacleComplicatesSomething:
    """An ache is a feeling, not a complication - nothing has changed.

    "throat went tight" appears verbatim in three different banked stories.
    """

    def _finding(self, obstacle: str):
        from engine.content.story_gate import evaluate

        narrations = [
            "Tara wanted the blue ribbon for the school race today.",
            "She reached for it on the shelf but it sat too high.",
            obstacle,
            "Tara dragged the wooden stool across and climbed up carefully.",
            "The ribbon was hers and she tied it in her hair.",
            "Tara ran to the race, ribbon flying behind her.",
        ]
        report = evaluate(narrations,
                          beats=["want", "attempt", "obstacle", "turn",
                                 "resolve", "refrain"])
        return next(f for f in report.findings
                    if f.check == "obstacle_is_more_than_a_feeling")

    def test_a_body_ache_alone_fails(self):
        finding = self._finding("Her shoulder ached and her throat went tight.")
        assert finding.passed is False
        assert "body feeling" in finding.detail

    def test_an_ache_with_a_real_complication_passes(self):
        finding = self._finding(
            "Her shoulder ached, and then another girl asked for the same "
            "ribbon.")
        assert finding.passed is True

    def test_a_plain_complication_passes(self):
        finding = self._finding(
            "The shelf wobbled and the last ribbon slipped further back.")
        assert finding.passed is True


class TestTheRefrainIsSomethingYouCanPointAt:
    def _finding(self, refrain: str):
        from engine.content.story_gate import evaluate

        narrations = [
            f"Devi wanted the last guava on the plate. {refrain}",
            f"She reached out slowly for it. {refrain}",
            "Her little cousin Nanu started to cry for it too.",
            "Devi broke the guava into two halves instead.",
            f"They ate together on the step. {refrain}",
        ]
        report = evaluate(narrations)
        return next(f for f in report.findings
                    if f.check == "refrain_is_concrete")

    def test_an_abstract_refrain_fails(self):
        assert self._finding("Sharing is caring, sharing is kind.").passed \
            is False

    def test_a_concrete_refrain_passes(self):
        assert self._finding("One guava, two hands, one guava, two hands.") \
            .passed is True
