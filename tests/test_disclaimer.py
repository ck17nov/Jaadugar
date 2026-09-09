"""The spoken disclaimer on finance and health videos.

Asked for directly: "Finance - lets put a standard disclaimer at the start in
all finance videos which should be put as per standards."

The interesting tests are the CLASSIFICATION ones. The disclaimer machinery is
simple; deciding that "SIP vs lump sum" is a finance video is the part that was
broken, and it was broken silently - the video rendered, published and looked
fine, it just had no disclaimer, no fact-check requirement and no sensitive
flag on it.
"""
from __future__ import annotations

import pytest

from engine.content import disclaimer
from engine.core.models import Scene, Script
from engine.core.niche import build_profile


def _script(scenes: int = 3, **kwargs) -> Script:
    script = Script(language="en", estimated_duration=100.0, **kwargs)
    script.scenes = [
        Scene(index=i, narration=f"Body sentence number {i} goes here.",
              visual_prompt=f"brief {i}", role="value").to_dict()
        for i in range(scenes)]
    return script


# ---------------------------------------------------------------------------
# Classification - the part that was silently wrong
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("niche", [
    "personal finance",
    # None of these contain the word "finance", "money" or "investing", and
    # every one of them matched the EDUCATION family before the keyword set
    # was widened. These are the channel's actual topics.
    "mutual funds explained",
    "SIP vs lump sum",
    "term insurance basics",
    "how EMI works",
    "PPF vs NPS",
    "credit score explained",
    "income tax basics",
    "emergency fund",
    "UPI explained",
])
def test_real_finance_topics_get_a_disclaimer(niche):
    profile = build_profile(niche, duration_seconds=300)
    assert profile.family == "finance", f"{niche} classified as {profile.family}"
    assert disclaimer.family_for(profile) == "finance"
    # The same classification drives two other protections.
    assert profile.is_sensitive
    assert profile.requires_fact_check


@pytest.mark.parametrize("niche", [
    "kids bedtime stories", "kids numbers and counting", "science facts",
    "pc and laptop tech", "AI explained", "programming and coding",
    "kids rhymes and poems",
])
def test_everything_else_gets_none(niche):
    """Widening the finance keywords must not drag other niches in."""
    profile = build_profile(niche, duration_seconds=300)
    assert disclaimer.family_for(profile) == ""


def test_a_profile_with_no_family_falls_back_to_the_name():
    """Hand-built profiles exist - a test, a stored profile from an older DB."""
    profile = build_profile("personal finance", duration_seconds=60)
    profile.family = ""
    assert disclaimer.family_for(profile) == "finance"


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
def test_it_becomes_a_real_opening_scene():
    """A whole scene, not a prefix on scene 1.

    A prefix would corrupt scene 1's narration, and on the bank path that
    silently invalidates that scene's authored caption and image brief.
    """
    script = _script()
    profile = build_profile("personal finance", duration_seconds=300)
    original_first = script.scene_objects()[0].narration

    assert disclaimer.apply(script, profile, language="en",
                            caption_language="hi") is True
    scenes = script.scene_objects()
    assert len(scenes) == 4
    assert "not investment advice" in scenes[0].narration.lower()
    assert scenes[0].visual_prompt and "no people" in scenes[0].visual_prompt
    # Scene 1's narration is untouched, and it is now index 1.
    assert scenes[1].narration == original_first
    assert [s.index for s in scenes] == [0, 1, 2, 3]


def test_the_disclaimer_scene_is_not_tagged_as_the_hook():
    """Hook treatment - big type, fast motion - is wrong for legal text.

    It would also make retention analysis score the disclaimer as the video's
    opening claim.
    """
    script = _script()
    profile = build_profile("personal finance", duration_seconds=300)
    disclaimer.apply(script, profile, language="en")
    assert script.scene_objects()[0].role == "context"


def test_it_is_captioned_in_the_other_language():
    """The one scene a regulator cares about must not be the unreadable one."""
    script = _script()
    profile = build_profile("personal finance", duration_seconds=300)
    disclaimer.apply(script, profile, language="en", caption_language="hi")
    caption = script.scene_objects()[0].caption_text
    assert caption and "सेबी" in caption


def test_a_hindi_video_gets_the_hindi_disclaimer():
    script = _script()
    script.language = "hi"
    profile = build_profile("personal finance", language="hi",
                            duration_seconds=300)
    disclaimer.apply(script, profile, language="hi", caption_language="en")
    scenes = script.scene_objects()
    assert "निवेश सलाह नहीं" in scenes[0].narration
    assert "investment advice" in scenes[0].caption_text.lower()


def test_applying_twice_does_nothing():
    """The script stage can re-run on retry."""
    script = _script()
    profile = build_profile("personal finance", duration_seconds=300)
    assert disclaimer.apply(script, profile, language="en") is True
    assert disclaimer.apply(script, profile, language="en") is False
    assert len(script.scenes) == 4


def test_chapters_shift_with_the_scenes():
    """Chapters index into the scene list, so a stale index mislabels them."""
    script = _script(chapters=[{"heading": "Mechanism", "scene_index": 1},
                               {"heading": "Example", "scene_index": 2}])
    profile = build_profile("personal finance", duration_seconds=300)
    disclaimer.apply(script, profile, language="en")
    assert [c["scene_index"] for c in script.chapters] == [2, 3]


def test_the_estimated_duration_grows_by_the_read_time():
    """Recorded, not absorbed. A 45s video that runs 52 should say so."""
    script = _script()
    profile = build_profile("personal finance", duration_seconds=300)
    before = script.estimated_duration
    disclaimer.apply(script, profile, language="en")
    grew = script.estimated_duration - before
    assert grew == pytest.approx(
        disclaimer.seconds_for(profile, "en"), rel=0.02)


def test_nothing_happens_for_a_kids_video():
    script = _script()
    profile = build_profile("kids bedtime stories", made_for_kids=True,
                            duration_seconds=45)
    assert disclaimer.apply(script, profile, language="en") is False
    assert len(script.scenes) == 3
    assert disclaimer.seconds_for(profile, "en") == 0.0


def test_health_gets_its_own_wording():
    """Not the finance one. "SEBI-registered adviser" would be nonsense."""
    script = _script()
    profile = build_profile("health and nutrition", duration_seconds=300)
    assert disclaimer.family_for(profile) == "health"
    disclaimer.apply(script, profile, language="en")
    narration = script.scene_objects()[0].narration
    assert "medical advice" in narration.lower()
    assert "sebi" not in narration.lower()
