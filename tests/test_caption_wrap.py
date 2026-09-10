"""Block captions must fit inside the frame.

This is the "captions aren't correct" report. A cross-language caption is a
whole sentence, and the ASS header sets WrapStyle 2 - wrap only at an explicit
break - so nothing was breaking it. Measured on a real render: a 56-character
caption is 2,192px wide against 983px of usable frame, and the video shipped
with the "M" missing from "Meera" and the "ce." missing from "terrace.".

Only the cross-language captions were affected, because the karaoke path is
pre-split into three-word groups and never reaches the frame edge.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from engine.core.config import load_config
from engine.video.captions import CaptionEngine

# Frames the app actually renders.
SHORT = (1080, 1920)
LONG = (1920, 1080)

SENTENCES = [
    "Meera saw Grandmother's slipper still up on the terrace.",
    "She reached from below, but the slipper was too far away.",
    "Then Meera saw the wall to hold. One step, then one more step.",
    "किरन ने बरसाती पानी में अपनी कागज़ की नाव रखी।",
    "तभी किरन ने देखा कि पानी खुद बह रहा था।",
    "यह वीडियो केवल सामान्य जानकारी है, निवेश सलाह नहीं। यह आपके लिए "
    "व्यक्तिगत सलाह नहीं है।",
    "This video is general education, not investment advice. It is not "
    "personalised to you. Please speak to a SEBI-registered adviser first.",
]


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
    return CaptionEngine(load_config())


@pytest.mark.parametrize("size", [SHORT, LONG])
@pytest.mark.parametrize("language", ["en", "hi"])
@pytest.mark.parametrize("text", SENTENCES)
def test_no_line_exceeds_the_measured_width(engine, size, language, text):
    """The whole point. Every line must fit, in every frame, in both scripts."""
    width, height = size
    per_line = engine._chars_per_line(width, height, language, "block")
    lines, scale = engine._wrap_block(text, per_line)
    # Shrinking the type widens the line, so the budget scales with it.
    budget = int(per_line / scale)
    for line in lines:
        # A single word longer than the line has nowhere to break.
        assert len(line) <= budget or " " not in line, \
            f"{len(line)} chars over {budget}: {line!r}"


@pytest.mark.parametrize("text", SENTENCES)
def test_nothing_is_ever_dropped(engine, text):
    """Losing the tail of a caption is worse than an ugly one.

    A cross-language caption is the only text some viewers of this video can
    follow at all, so a sentence too long for the line budget shrinks rather
    than being truncated.
    """
    per_line = engine._chars_per_line(1080, 1920, "en", "block")
    lines, _ = engine._wrap_block(text, per_line)
    assert " ".join(lines).split() == text.split()


def test_it_never_exceeds_the_line_budget(engine):
    """Three lines of large type is already a third of a portrait frame."""
    per_line = engine._chars_per_line(1080, 1920, "en", "block")
    lines, scale = engine._wrap_block(
        "A very long sentence indeed, one that goes on well past any "
        "reasonable subtitle length and keeps going for a while yet, "
        "because somebody wrote it that way.", per_line)
    assert len(lines) <= engine.MAX_BLOCK_LINES
    assert scale < 1.0, "it should have shrunk rather than added a line"


def test_lines_are_balanced(engine):
    """Greedy filling strands the remainder.

    A 41-character Hindi caption came out as one full line and then "रखी"
    alone, which reads as two captions rather than one.
    """
    per_line = engine._chars_per_line(1080, 1920, "en", "block")
    lines, _ = engine._wrap_block(
        "किरन ने बरसाती पानी में अपनी कागज़ की नाव रखी।", per_line)
    assert len(lines) == 2
    shortest, longest = min(map(len, lines)), max(map(len, lines))
    assert longest - shortest <= max(6, longest // 3), \
        f"unbalanced: {[len(x) for x in lines]}"


def test_a_short_caption_stays_on_one_line(engine):
    per_line = engine._chars_per_line(1080, 1920, "en", "block")
    lines, scale = engine._wrap_block("One step, then one more step.", per_line)
    assert lines == ["One step, then one more step."]
    assert scale == 1.0


def test_a_block_caption_is_smaller_than_a_karaoke_one(engine):
    """96px is right for "STILL UP ON THE" and hopeless for a sentence."""
    karaoke = engine._scaled_font_size(1080, 1920)
    block = engine._scaled_font_size(1080, 1920, "block")
    assert block < karaoke
    # And it therefore fits more characters per line.
    assert (engine._chars_per_line(1080, 1920, "en", "block")
            > engine._chars_per_line(1080, 1920, "en"))


# ---------------------------------------------------------------------------
# The rendered ASS
# ---------------------------------------------------------------------------
def test_the_ass_carries_real_line_breaks(engine, tmp_path):
    r"""Escaped per line, then joined - not joined then escaped.

    `_escape` doubles backslashes, so escaping the joined string would print
    a literal "\N" in the middle of the caption instead of breaking the line.
    """
    out_ass, out_srt = tmp_path / "c.ass", tmp_path / "c.srt"
    engine.build_translated(
        [(0.0, 4.0, "Meera saw Grandmother's slipper still up on the terrace.")],
        out_ass, out_srt, 1080, 1920, language="en")
    body = out_ass.read_text(encoding="utf-8")
    dialogue = [ln for ln in body.splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogue) == 1
    assert "\\N" in dialogue[0]
    assert "\\\\N" not in dialogue[0], "the break got escaped into a literal"


def test_the_srt_has_no_ass_markup(engine, tmp_path):
    """The SRT goes to YouTube as a subtitle track, not through libass."""
    out_ass, out_srt = tmp_path / "c.ass", tmp_path / "c.srt"
    engine.build_translated(
        [(0.0, 4.0, "Meera saw Grandmother's slipper still up on the terrace.")],
        out_ass, out_srt, 1080, 1920, language="en")
    srt = out_srt.read_text(encoding="utf-8")
    assert "\\N" not in srt
    assert "{" not in srt
    assert "Grandmother's slipper still up on the terrace." in srt
