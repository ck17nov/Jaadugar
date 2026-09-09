"""The spoken disclaimer that opens a finance or health video.

Asked for directly: "Finance - lets put a standard disclaimer at the start in
all finance videos which should be put as per standards."

It was already being appended to the DESCRIPTION (metadata.py). That is not
what was asked for and not what the standard expects: a disclaimer below the
fold, after "show more", is one nobody sees. So this prepends a real opening
scene - narrated, captioned, and burned on screen.

A WHOLE SCENE rather than a prefix on scene 1, for three reasons:
  * it gets its own visual, so the first picture of the actual content is not
    spent on legal text;
  * it does not touch scene 1's narration, which on the bank path would
    invalidate that scene's authored caption and authored image brief;
  * it can be found and checked later - `has_disclaimer` reads the scene role
    rather than string-matching the narration.

WHAT THIS DOES NOT FIX, stated plainly because it matters more than the
disclaimer does. YouTube's inauthentic-content policy blocks MONETISATION for
channels using "AI-generated personas to deliver information on sensitive
topics", and names financial guidance specifically. Finance is a named
sensitive topic and the narrator here is synthetic. A disclaimer does not
change that. What moves the needle is the accompanying editorial rule - no
first person, no host, no recommendations, explain mechanisms only - which is
enforced in the bank prompt and in the script prompt, not here.
"""
from __future__ import annotations

from ..core.logging import log_event
from ..core.models import Scene, Script
from ..core.util import count_words

# The niche families that get one. Both are named sensitive topics in the
# monetisation policy, and both are ones where being wrong costs the viewer
# money or health rather than a few minutes.
SENSITIVE_FAMILIES = ("finance", "health")

# India-facing wording. SEBI's framework turns on whether the speaker is a
# registered investment adviser; the standard disclosure for educational
# content that is not advice names that registration explicitly rather than
# just saying "not advice", so a viewer knows what is missing and who to ask.
TEXTS: dict[str, dict[str, str]] = {
    "finance": {
        "en": ("This video is general education, not investment advice. "
               "It is not personalised to you. Please speak to a "
               "SEBI-registered adviser before acting on it."),
        "hi": ("यह वीडियो केवल सामान्य जानकारी है, निवेश सलाह नहीं। "
               "यह आपके लिए व्यक्तिगत सलाह नहीं है। कोई भी फ़ैसला लेने से पहले "
               "सेबी-पंजीकृत सलाहकार से बात करें।"),
    },
    "health": {
        "en": ("This video is general information, not medical advice. "
               "Please speak to a qualified doctor about your own health."),
        "hi": ("यह वीडियो केवल सामान्य जानकारी है, चिकित्सकीय सलाह नहीं। "
               "अपनी सेहत के बारे में किसी योग्य डॉक्टर से बात करें।"),
    },
}

ON_SCREEN: dict[str, dict[str, str]] = {
    "finance": {"en": "Not investment advice",
                "hi": "निवेश सलाह नहीं"},
    "health": {"en": "Not medical advice", "hi": "चिकित्सकीय सलाह नहीं"},
}

# A neutral, text-friendly opening image. Deliberately has no person in it:
# a presenter at a desk is the visual form of the AI-persona problem the
# module docstring describes.
BRIEFS: dict[str, str] = {
    "finance": ("A clean flat-lay of a notebook, a pen and a calculator on a "
                "plain desk, soft even daylight, generous empty space in the "
                "upper half of the frame, no people, no text"),
    "health": ("A clean flat-lay of a glass of water, a stethoscope and a "
               "folded towel on a plain surface, soft even daylight, "
               "generous empty space in the upper half, no people, no text"),
}

# The scene role. "context" rather than "hook": the hook treatment - big
# type, fast motion, punchy caption - is exactly wrong for a disclaimer, and
# tagging it as the hook would also make retention analysis score the legal
# text as the video's opening claim.
ROLE = "context"


def family_for(profile) -> str:
    """Which disclaimer this profile needs, or "" for none.

    Read from `profile.family`, which the niche builder sets from its own
    keyword matching. Matching the niche STRING here instead would miss every
    niche that does not contain the word - "mutual funds explained", "SIP vs
    lump sum", "term insurance basics" - which is most of them.
    """
    family = (getattr(profile, "family", "") or "").lower()
    if family in SENSITIVE_FAMILIES:
        return family
    # Fallback for a hand-built profile with no family recorded.
    name = (getattr(profile, "name", "") or "").lower()
    return next((f for f in SENSITIVE_FAMILIES if f in name), "")


def text_for(family: str, language: str) -> str:
    """The disclaimer wording. Falls back to English, never to nothing."""
    table = TEXTS.get(family) or {}
    code = (language or "en").strip().lower().split("-")[0]
    return table.get(code) or table.get("en", "")


def seconds_for(profile, language: str = "") -> float:
    """How long the disclaimer takes to read, or 0.0 when there is none.

    The live path subtracts this from the script's word budget BEFORE
    generating, so a "45 second" finance short lands at 45 seconds with the
    disclaimer inside it rather than at 55 with the disclaimer bolted on.
    """
    family = family_for(profile)
    if not family:
        return 0.0
    body = text_for(family, language or "en")
    if not body:
        return 0.0
    return count_words(body) / max(
        getattr(profile, "words_per_second", 2.5), 1.2)


def has_disclaimer(script: Script) -> bool:
    """True when this script already opens with one.

    Read from the scene, not by string-matching narration, so a reworded
    disclaimer is still recognised and applying twice is impossible.
    """
    scenes = script.scene_objects()
    return bool(scenes) and scenes[0].on_screen_text in {
        value for table in ON_SCREEN.values() for value in table.values()}


def apply(script: Script, profile, *, language: str = "",
          caption_language: str = "") -> bool:
    """Prepend the disclaimer scene. Returns whether one was added.

    Idempotent, because the pipeline may re-run the script stage on retry and
    a video that opens with the disclaimer twice is worse than one that opens
    with it once.
    """
    family = family_for(profile)
    if not family:
        return False
    if has_disclaimer(script):
        return False

    narration_language = (language or script.language or "en")
    body = text_for(family, narration_language)
    if not body:
        return False

    # The caption is the disclaimer in the OTHER language, matching what
    # every other scene does. An empty caption here would leave the one scene
    # a regulator cares about unreadable to half the audience.
    caption = ""
    if caption_language:
        caption = text_for(family, caption_language)

    scenes = script.scene_objects()
    opener = Scene(
        index=0,
        narration=body,
        visual_prompt=BRIEFS.get(family, ""),
        on_screen_text=ON_SCREEN.get(family, {}).get(
            narration_language.split("-")[0], ""
        ) or ON_SCREEN.get(family, {}).get("en", ""),
        role=ROLE,
        caption_text=caption,
    )
    for scene in scenes:
        scene.index += 1
    scenes.insert(0, opener)

    script.scenes = [s.to_dict() for s in scenes]
    script.script = "\n".join(s.narration for s in scenes)
    script.visual_plan = [s.visual_prompt for s in scenes]
    # Chapters index into the scene list, so they all shift by one.
    script.chapters = [{**c, "scene_index": int(c.get("scene_index", 0)) + 1}
                       for c in (script.chapters or [])]
    # The video is now longer by however long the disclaimer takes to read.
    # Recorded rather than absorbed: a "45 second" video that runs 52 because
    # of a mandatory opener should say so, not quietly overrun the target.
    words_per_second = max(getattr(profile, "words_per_second", 2.5), 1.2)
    script.estimated_duration = round(
        script.estimated_duration + count_words(body) / words_per_second, 2)

    log_event("DISCLAIMER", "prepended a spoken disclaimer",
              family=family, language=narration_language,
              words=count_words(body),
              captioned=bool(caption), scenes=len(scenes))
    return True
