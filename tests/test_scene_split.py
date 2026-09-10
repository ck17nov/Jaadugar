"""Splitting an overlong scene must not lose anything attached to it.

`auto_improve` rebuilds a split scene as two NEW Scene objects. It built them
field by field, so every field nobody remembered to list was silently reset
to its default - and one of those was the authored caption.

That is not a cosmetic loss. `stage_render` picks the caption path on
COVERAGE: if fewer than 60% of scenes carry a caption it falls back to
narration captions for the WHOLE video, which on a cross-language render is
the wrong language on every cue.
"""
from __future__ import annotations

from engine.content.retention import RetentionReport, auto_improve
from engine.core.models import Scene, Script
from engine.core.niche import build_profile

LONG = ("The market fell by nine percent over three weeks and nobody could "
        "say exactly why it had started. Then the same buyers came back in "
        "the second week of the month and quietly bought everything they had "
        "sold, which is the part that matters here.")


def _profile():
    return build_profile("personal finance", audience="18-35", language="en",
                         duration_seconds=300)


def _script(scene: Scene) -> Script:
    return Script(scenes=[scene.to_dict()], script=scene.narration,
                  visual_plan=[scene.visual_prompt])


def test_the_premise_that_this_scene_does_get_split():
    """If it stops being splittable the tests below prove nothing."""
    scene = Scene(index=0, narration=LONG, visual_prompt="a trading floor")
    _, applied = auto_improve(_script(scene), _profile(), RetentionReport())
    assert any("split scene" in note for note in applied), applied


def test_a_captioned_scene_is_left_long_instead_of_split():
    """A caption translates the WHOLE narration.

    Splitting the narration leaves no mechanical way to split the caption
    with it: duplicating it captions both halves with the full text, and
    dropping it from one half makes that half fall back to the narration -
    which is the other language. An extra visual change is not worth either.
    """
    scene = Scene(index=0, narration=LONG, visual_prompt="a trading floor",
                  caption_text="बाज़ार नौ प्रतिशत गिरा और कोई कारण नहीं बता सका।")
    script, applied = auto_improve(_script(scene), _profile(),
                                   RetentionReport())

    assert not any("split scene" in note for note in applied), applied
    scenes = script.scene_objects()
    assert len(scenes) == 1
    assert scenes[0].caption_text


def test_a_split_carries_every_other_field_across():
    """`motion` was reset to the default too, by the same mechanism."""
    scene = Scene(index=0, narration=LONG, visual_prompt="a trading floor",
                  visual_keywords=["chart", "desk"], role="payoff",
                  on_screen_text="9%", motion="pan_left")
    script, _ = auto_improve(_script(scene), _profile(), RetentionReport())
    halves = script.scene_objects()

    assert len(halves) == 2
    for half in halves:
        assert half.role == "payoff"
        assert half.motion == "pan_left"
        assert half.visual_keywords == ["chart", "desk"]
        assert half.duration == 0.0 and half.start == 0.0

    # The title card belongs to the first half only - repeating it would put
    # the same words on screen twice in a row.
    assert halves[0].on_screen_text == "9%"
    assert halves[1].on_screen_text == ""
    # And the second half needs a different image.
    assert halves[1].visual_prompt != halves[0].visual_prompt


def test_the_two_halves_keep_the_whole_narration():
    scene = Scene(index=0, narration=LONG, visual_prompt="a trading floor")
    script, _ = auto_improve(_script(scene), _profile(), RetentionReport())
    halves = script.scene_objects()
    joined = " ".join(h.narration for h in halves)
    assert joined.split() == LONG.split()
    assert script.script.split() == LONG.split()
