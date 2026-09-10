"""What opens the published description.

Two things were wrong with the first line of every description. A banked
entry carries a `description_hook` written for exactly this slot, authored
and validated since the bank existed, which nothing ever read. And the
fallback - the narration's first two sentences - is the DISCLAIMER on every
finance and health video, because `disclaimer.apply` prepends a scene and
rebuilds `script.script` from the scenes. So a finance description opened
with the disclaimer and then repeated it under the disclosures.
"""
from __future__ import annotations

import pytest

from engine.content.metadata import MetadataGenerator
from engine.core.config import load_config
from engine.core.models import ContentIdea, Scene, Script, VideoMetadata
from engine.core.niche import build_profile


@pytest.fixture()
def gen():
    return MetadataGenerator(load_config(), None)


def _script(*scenes: Scene, hook: str = "") -> Script:
    return Script(hook=hook, script="\n".join(s.narration for s in scenes),
                  scenes=[s.to_dict() for s in scenes],
                  visual_plan=[s.visual_prompt for s in scenes])


BODY = (Scene(index=0, narration="A one percent fee sounds small."),
        Scene(index=1, narration="Over twenty years it is not small at all."),
        Scene(index=2, narration="Here is what it costs on ten lakh."))

DISCLAIMER = Scene(
    index=0,
    narration=("This video is for general education and is not investment "
               "advice. Consider your own situation before acting."),
    role="context")


def test_an_authored_hook_opens_the_description(gen):
    script = _script(*BODY)
    script.description_hook = ("A fee you never see is still a fee you pay. "
                              "Here is the arithmetic.")
    text = gen.build_description(
        script, ContentIdea(topic="fees", angle="compounding costs"),
        build_profile("personal finance", duration_seconds=300),
        VideoMetadata(title="t"), video_format="LONGFORM")
    assert text.startswith("A fee you never see")


def test_without_one_it_falls_back_to_the_narration(gen):
    text = gen.build_description(
        _script(*BODY), ContentIdea(topic="fees", angle="compounding costs"),
        build_profile("personal finance", duration_seconds=300),
        VideoMetadata(title="t"), video_format="LONGFORM")
    assert text.startswith("A one percent fee sounds small.")


def test_the_disclaimer_does_not_open_the_description(gen):
    """It was the lead AND the disclosure, in one description."""
    script = _script(DISCLAIMER, *BODY)
    text = gen.build_description(
        script, ContentIdea(topic="fees", angle="compounding costs"),
        build_profile("personal finance", duration_seconds=300),
        VideoMetadata(title="t"), video_format="LONGFORM")
    assert not text.startswith("This video is for general education")
    assert text.startswith("A one percent fee sounds small.")


def test_a_closing_mention_does_not_cost_the_first_scene(gen):
    """Only an OPENING disclaimer is dropped.

    A video may legitimately say "not financial advice" in its last line
    without that making its first scene a disclaimer.
    """
    closing = Scene(index=3, narration="None of this is financial advice.")
    text = gen.build_description(
        _script(*BODY, closing),
        ContentIdea(topic="fees", angle="compounding costs"),
        build_profile("personal finance", duration_seconds=300),
        VideoMetadata(title="t"), video_format="LONGFORM")
    assert text.startswith("A one percent fee sounds small.")


def test_a_disclaimer_only_script_still_describes_something(gen):
    """The guard against emptying the lead entirely."""
    text = gen.build_description(
        _script(DISCLAIMER), ContentIdea(topic="fees", angle="the cost"),
        build_profile("personal finance", duration_seconds=300),
        VideoMetadata(title="t"), video_format="LONGFORM")
    assert text.strip()


def test_a_banked_entry_carries_its_hook_onto_the_script():
    """The field existed, was validated, and was read by nothing."""
    from engine.content import bank_use
    from tests.test_bank import kids_entry

    entry = kids_entry()
    assert entry.description_hook
    script = bank_use.to_script(entry)
    assert script.description_hook == entry.description_hook
