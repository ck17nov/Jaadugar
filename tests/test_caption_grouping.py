"""Caption grouping across writing systems and scene boundaries.

Every case here is a defect seen in a real rendered frame, not a hypothetical.
"""
from __future__ import annotations

from engine.video.captions import (CLAUSE_END, SENTENCE_END, CaptionWord,
                                   group_words)

DANDA = "।"


def _words(spec, step=0.35, dur=0.3):
    """[(scene, "some text"), ...] -> a flat CaptionWord timeline."""
    out, t = [], 0.0
    for scene, text in spec:
        for token in text.split():
            out.append(CaptionWord(start=t, end=t + dur, text=token, scene=scene))
            t += step
    return out


def _texts(groups):
    return [" ".join(w.text for w in g.words) for g in groups]


class TestSentenceTerminators:
    def test_the_danda_ends_a_hindi_sentence(self):
        """The bug this file exists for.

        The rule was endswith((".", "!", "?", ":", ";")), so a Devanagari
        sentence - which ends with the danda - never ended. Grouping fell
        through to the word and character limits and produced captions
        spliced from two different sentences.
        """
        assert DANDA in SENTENCE_END
        words = _words([(0, f"क्या यह सही है{DANDA}"),
                        (0, "आरव सो गया")])
        groups = group_words(words, max_words=8, max_chars=60)
        assert _texts(groups)[0].endswith(DANDA), _texts(groups)

    def test_the_other_scripts_terminators_are_covered(self):
        for mark in ("॥", "۔", "؟", "。", "！", "？"):
            assert mark in SENTENCE_END, mark

    def test_a_comma_is_a_weaker_break_than_a_full_stop(self):
        """Breaking on every comma would flash two-word captions."""
        assert "," in CLAUSE_END and "," not in SENTENCE_END
        words = _words([(0, "one, two three four five six")])
        # With room to spare, the comma must NOT end the caption on its own.
        assert len(group_words(words, max_words=8, max_chars=60)) == 1


class TestSceneBoundaries:
    def test_a_caption_never_spans_two_scenes(self):
        """The picture changes at a scene boundary, so text carried across it
        describes an image no longer on screen. A rendered Hindi frame showed
        the last two words of one scene followed by the first of the next."""
        words = _words([(0, "alpha beta"), (1, "gamma delta")])
        groups = group_words(words, max_words=8, max_chars=90)
        for group in groups:
            assert len({w.scene for w in group.words}) == 1, _texts(groups)

    def test_unknown_scene_numbers_do_not_force_a_break(self):
        """scene defaults to -1, and older callers must keep working."""
        words = [CaptionWord(start=i * 0.3, end=i * 0.3 + 0.25, text=t)
                 for i, t in enumerate(["one", "two", "three"])]
        assert len(group_words(words, max_words=8, max_chars=60)) == 1


class TestOrphans:
    def test_a_lone_closing_word_is_folded_back(self):
        """Adding the terminator break created these: the word limit ends a
        caption and the next word carries the full stop, leaving it alone on
        screen for a third of a second."""
        words = _words([(0, f"क्या यह सही है{DANDA}")])
        groups = group_words(words, max_words=3, max_chars=60)
        assert not any(len(g.words) == 1 for g in groups), _texts(groups)

    def test_folding_back_never_overrides_a_long_pause(self):
        """A first version of the fold re-joined two words either side of a
        2.3-second pause, silently reversing the long-pause rule."""
        words = [CaptionWord(start=0.0, end=0.2, text="one", scene=0),
                 CaptionWord(start=2.5, end=2.7, text="two.", scene=0)]
        assert len(group_words(words, max_words=8, max_chars=60)) == 2

    def test_folding_back_never_overflows_the_line(self):
        words = _words([(0, "aaaaaaaa bbbbbbbb cccccccc dddddddd.")])
        for group in group_words(words, max_words=3, max_chars=20):
            assert sum(len(w.text) + 1 for w in group.words) <= 24, _texts(
                group_words(words, max_words=3, max_chars=20))

    def test_folding_back_never_crosses_a_scene(self):
        words = _words([(0, "alpha beta gamma"), (1, "delta.")])
        for group in group_words(words, max_words=8, max_chars=90):
            assert len({w.scene for w in group.words}) == 1
