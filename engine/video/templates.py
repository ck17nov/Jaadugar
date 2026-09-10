"""Reusable video style templates (spec section 45).

A NicheProfile says what the content *is*. A StyleTemplate says how the video
*looks and moves*. They are separate because the same niche can be shot several
ways: "science" can be a FAST_FACTS rapid-fire Short or a calm
SCIENCE_EXPLAINER.

Each template controls: scene duration, typography, transitions, caption style,
pacing, background treatment and visual frequency.

Templates are selected automatically from the niche + user style text, or forced
with `video.style_template` in config.yaml.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..core.niche import NicheProfile
from ..core.util import words


@dataclass
class StyleTemplate:
    name: str
    description: str

    # --- pacing / structure ---
    scene_seconds: float                  # target on-screen time per visual
    visual_frequency: float = 1.0          # multiplier on images per minute
    words_per_second: float = 2.6

    # --- typography ---
    font: str = "Anton"                    # file stem in assets/fonts/
    font_scale: float = 1.0                # multiplier on captions.font_size
    uppercase: bool = True
    letter_spacing: float = 1.2

    # --- captions ---
    caption_style: str = "karaoke"         # karaoke | block | none
    highlight_color: str = "&H0000E5FF"    # ASS BGR
    outline: int = 7
    safe_bottom: float = 0.16          # clears the Shorts action bar

    # --- motion / transitions ---
    transition: str = "fade"               # fade | smoothleft | slideup | auto
    transition_duration: float = 0.35
    motion_cycle: list[str] = field(default_factory=lambda: [
        "zoom_in", "pan_right", "zoom_out", "pan_left"])
    kenburns: bool = True

    # --- look ---
    contrast: float = 1.045
    saturation: float = 1.07
    visual_style_suffix: str = ""          # appended to every image prompt
    # What ONE `visual_prompt` should describe, in the script writer's words.
    #
    # This was a single hard-coded sentence asking for "a literal,
    # photographable subject, camera framing and lighting". For a template
    # that draws its images that is the wrong brief twice over: it asks the
    # writer for a photograph, and it asks for camera language - focal
    # length, depth of field - that an illustration has no use for. The
    # writer then hands the image model a photographic brief with an
    # illustration suffix stapled on, and the two fight.
    image_brief: str = ("a literal, photographable subject, camera framing "
                        "and lighting")
    music_mood: str = "cinematic"
    # Generate the images instead of searching stock libraries.
    #
    # A property of the TEMPLATE, not of the deployment, because it has to
    # agree with visual_style_suffix. Those two contradicting each other is
    # the failure this prevents: a template asking for illustration while the
    # pipeline serves photographs produces a slideshow that changes medium
    # every four seconds, and a template asking for photography while the
    # pipeline draws produces the soft airbrushed look people call AI slop.
    prefer_ai: bool = False
    # Draw a flashcard - a letter or word on a card - instead of a full-frame
    # picture. Right for teaching the alphabet, where the letter IS the
    # content; wrong for a story, where it puts a small picture in the middle
    # of an empty frame with a word underneath that nobody asked for.
    flashcards: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Subjects whose LONG-FORM version wants a held frame rather than a cut every
# six seconds. Deliberately narrow: a long-form explainer or news piece is not
# improved by fourteen-second holds, only a narrated story is.
LONGFORM_STORY_HINTS = (
    "story", "stories", "nostalgia", "memoir", "folklore", "tale", "tales",
    "legend", "legends", "childhood", "village", "mystery", "history",
)


TEMPLATES: dict[str, StyleTemplate] = {
    "FAST_FACTS": StyleTemplate(
        name="FAST_FACTS",
        description="Rapid-fire facts. Maximum cuts, big punchy captions.",
        scene_seconds=2.4, visual_frequency=1.35, words_per_second=2.9,
        font_scale=1.10, caption_style="karaoke",
        highlight_color="&H0000E5FF",           # amber
        transition="fade", transition_duration=0.22,
        motion_cycle=["zoom_in", "pan_left", "zoom_out", "pan_right",
                      "zoom_in", "pan_up"],
        contrast=1.07, saturation=1.14,
        visual_style_suffix="bold high-contrast composition, single clear subject",
        music_mood="tension",
    ),
    "TECH_NEWS": StyleTemplate(
        name="TECH_NEWS",
        description="Clean, current, slightly clinical. Product-shot feel.",
        scene_seconds=3.0, words_per_second=2.7,
        font_scale=0.95, highlight_color="&H00FFD34D",   # cyan-ish
        transition="smoothleft", transition_duration=0.30,
        motion_cycle=["pan_right", "zoom_in", "pan_left", "zoom_out"],
        contrast=1.05, saturation=1.05,
        visual_style_suffix=("modern product photography, cool blue and teal "
                             "light, shallow depth of field"),
        music_mood="tech",
    ),
    "SCIENCE_EXPLAINER": StyleTemplate(
        name="SCIENCE_EXPLAINER",
        description="Awe-driven, cinematic, room to breathe.",
        scene_seconds=3.6, words_per_second=2.5,
        font_scale=1.0, highlight_color="&H00FFC46A",
        transition="fade", transition_duration=0.45,
        motion_cycle=["zoom_in", "zoom_out", "pan_up", "pan_down"],
        contrast=1.06, saturation=1.10,
        visual_style_suffix=("cinematic astrophotography, deep blacks, one "
                             "luminous subject, no text"),
        music_mood="cinematic",
    ),
    "STORYTELLING": StyleTemplate(
        name="STORYTELLING",
        description="Illustrated narrative. Slow pushes, drawn scenes.",
        # 6.0s, not 3.8s.
        #
        # Measured against the illustrated story channels this is aimed at:
        # one image every 6.7 seconds (26 cuts in 173s). A narrative shot needs
        # time to be read - who is in it, where they are, what changed - and
        # cutting every 3.8s turns a story into a montage. It also halves the
        # number of images, which matters when each one takes 8 to 45 seconds
        # to generate.
        scene_seconds=6.0, words_per_second=2.4,
        font_scale=0.92, caption_style="karaoke",
        highlight_color="&H00B0B0FF", outline=6,
        transition="fade", transition_duration=0.55,
        motion_cycle=["zoom_in", "pan_left", "zoom_in", "pan_right"],
        contrast=1.02, saturation=1.04,
        # Drawn, not photographed - and prefer_ai below is what makes that
        # instruction true rather than a description of a stock photo nobody
        # is going to find.
        visual_style_suffix=("2D illustrated storybook scene, clean line art, "
                             "flat warm colours, hand-painted background, "
                             "consistent art style"),
        image_brief=("one drawn moment: WHO is in frame, WHAT they are doing, "
                     "WHERE they are, and the time of day. Name the people by "
                     "the names used in the narration so the same characters "
                     "recur. No camera or lens language"),
        prefer_ai=True,
        music_mood="sombre",
    ),
    "TOP_5": StyleTemplate(
        name="TOP_5",
        description="Countdown list. Hard cuts, numbers on screen.",
        scene_seconds=2.8, visual_frequency=1.2, words_per_second=2.8,
        font_scale=1.08, highlight_color="&H004DFF4D",   # green
        transition="slideup", transition_duration=0.26,
        motion_cycle=["zoom_out", "pan_right", "zoom_in", "pan_left"],
        contrast=1.07, saturation=1.12,
        visual_style_suffix="bold graphic composition, strong subject separation",
        music_mood="tech",
    ),
    "ILLUSTRATED_EXPLAINER": StyleTemplate(
        name="ILLUSTRATED_EXPLAINER",
        description=("Long-form illustrated narration. One drawn scene held "
                     "for a long beat, no captions."),
        # 14 seconds, and that is not a typo.
        #
        # Measured off the 34-minute nostalgia video this template exists to
        # match: it holds a single drawn frame for 12 to 16 seconds while the
        # narrator talks over it, and the only movement is a slow push. At the
        # 6s of STORYTELLING a half-hour video needs 340 images, which at the
        # keyless generator's 8-45s per image is hours of generation and a
        # near-certain rate limit. At 14s it needs 145, and - more to the
        # point - it looks like the reference instead of a montage.
        scene_seconds=14.0, visual_frequency=0.5, words_per_second=2.3,
        font_scale=0.92, uppercase=False,
        # No captions at all.
        #
        # The reference videos carry none: the picture is the whole frame and
        # the voice does the work. Burnt-in subtitles over a held illustration
        # are the thing that makes this format look like an automated upload.
        caption_style="none",
        highlight_color="&H00B0B0FF", outline=6, safe_bottom=0.16,
        # A held frame wants the gentlest possible cut between shots.
        transition="fade", transition_duration=0.9,
        # Dollies, not slides. A frame held for fourteen seconds needs the
        # movement to read as depth rather than as a picture being dragged
        # sideways, and a combined zoom+pan costs nothing extra.
        motion_cycle=["dolly_in_right", "zoom_in", "dolly_in_left",
                      "dolly_out_down"],
        kenburns=True,
        contrast=1.02, saturation=1.05,
        visual_style_suffix=("cel-shaded 2D animation still, soft painted "
                             "background, warm nostalgic palette, gentle rim "
                             "light, consistent character design"),
        image_brief=("one held frame from an animated film: WHO is in it, "
                     "WHAT they are doing, WHERE, and the light. Name people "
                     "by the names in the narration. Compose it to be looked "
                     "at for fifteen seconds - depth, a foreground and a "
                     "background. No camera or lens language"),
        prefer_ai=True,
        music_mood="sombre",
    ),
    "MYSTERY": StyleTemplate(
        name="MYSTERY",
        description="Withholds. Long holds, cold grade, slow reveals.",
        scene_seconds=4.0, visual_frequency=0.85, words_per_second=2.3,
        font_scale=0.95, highlight_color="&H00FF6AD1",
        transition="fade", transition_duration=0.60,
        motion_cycle=["zoom_in", "zoom_in", "pan_up", "zoom_out"],
        contrast=1.12, saturation=0.88,
        visual_style_suffix=("dark cinematic photography, fog, negative space, "
                             "unresolved composition"),
        music_mood="sombre",
    ),
    "KIDS_STORY": StyleTemplate(
        name="KIDS_STORY",
        description="Gentle, bright, calm. Whole-phrase captions.",
        scene_seconds=4.2, visual_frequency=0.8, words_per_second=2.0,
        font_scale=0.90, uppercase=False,
        caption_style="block",                  # never flash single words at kids
        highlight_color="&H0080E0FF", outline=6, safe_bottom=0.16,
        transition="fade", transition_duration=0.65,
        motion_cycle=["dolly_in_right", "zoom_in", "dolly_in_left",
                      "zoom_out"],
        contrast=1.02, saturation=1.06,
        # A bedtime story is a PICTURE BOOK, not a set of flashcards.
        #
        # It used to get the flashcard treatment, which produced a small
        # image floating in an empty frame with a single word printed under
        # it - and the word came from the narration, so a story about sea
        # otters showed a stock photograph of a drifting CAR captioned
        # "DRIFT". Full-frame illustration instead, and the word label only
        # survives in KIDS_LEARNING where the letter is the point.
        prefer_ai=True,
        visual_style_suffix=("gentle children's storybook illustration, "
                             "soft rounded shapes, warm friendly colours, "
                             "hand-drawn picture book art, nothing scary"),
        image_brief=("one picture-book page: the named character, what they "
                     "are doing right now, and where. Keep it to one or two "
                     "characters and one clear action a small child can read "
                     "at a glance. No camera or lens language"),
        music_mood="playful",
    ),
    "KIDS_LEARNING": StyleTemplate(
        name="KIDS_LEARNING",
        description="Alphabet and counting. Flashcards, because the letter is the point.",
        scene_seconds=4.0, visual_frequency=0.8, words_per_second=1.9,
        font_scale=0.90, uppercase=False,
        caption_style="block",
        highlight_color="&H0080E0FF", outline=6, safe_bottom=0.16,
        transition="fade", transition_duration=0.65,
        motion_cycle=["zoom_in", "zoom_out", "zoom_in", "zoom_out"],
        contrast=1.02, saturation=1.06,
        flashcards=True,
        visual_style_suffix=("bright friendly cartoon illustration, rounded "
                             "shapes, soft primary colours, nothing scary"),
        music_mood="playful",
    ),
    "EDUCATIONAL": StyleTemplate(
        name="EDUCATIONAL",
        description="Clear and unhurried. One idea per frame.",
        scene_seconds=3.4, words_per_second=2.5,
        font_scale=0.95, highlight_color="&H00FFD34D",
        transition="fade", transition_duration=0.38,
        motion_cycle=["pan_right", "zoom_in", "pan_left", "zoom_out"],
        contrast=1.04, saturation=1.03,
        visual_style_suffix="clean minimal composition, generous negative space",
        music_mood="warm",
    ),
    "MOTIVATIONAL": StyleTemplate(
        name="MOTIVATIONAL",
        description="Rising energy, warm grade, strong typography.",
        scene_seconds=3.0, words_per_second=2.7,
        font_scale=1.12, highlight_color="&H0040D0FF",
        transition="auto", transition_duration=0.34,
        motion_cycle=["zoom_in", "pan_up", "zoom_in", "pan_right"],
        contrast=1.08, saturation=1.16,
        visual_style_suffix=("golden hour light, human silhouette, wide open "
                             "landscape, aspirational"),
        music_mood="cinematic",
    ),
}

DEFAULT_TEMPLATE = "EDUCATIONAL"

# Words in the niche / user style that point at a template.
# Child-directed niches that TEACH rather than tell a story. Exported because
# the script prompt needs the same distinction: a drill wants repetition and
# call-and-response, a story wants a character and something that happens.
KIDS_LEARNING_HINTS: tuple[str, ...] = (
    "alphabet", "letter", "letters", "abc", "number", "numbers", "counting",
    "count", "phonics", "spelling", "shapes", "colours", "colors", "word",
    "words", "sentence", "sentences", "speaking",
)

_HINTS: dict[str, tuple[str, ...]] = {
    "KIDS_STORY": ("kids", "children", "toddler", "nursery", "bedtime",
                   "preschool", "cartoon"),
    "FAST_FACTS": ("facts", "fast", "rapid", "punchy", "quick", "did you know",
                   "trivia"),
    "TOP_5": ("top", "countdown", "ranked", "ranking", "best", "list",
              "listicle"),
    "MYSTERY": ("mystery", "unsolved", "strange", "creepy", "unexplained",
                "disappeared", "conspiracy"),
    "STORYTELLING": ("story", "storytelling", "narrative", "tale", "reddit",
                     "confession", "horror", "scary", "creepypasta"),
    "TECH_NEWS": ("tech", "technology", "ai", "gadget", "software", "startup",
                  "news", "laptop", "pc", "youtube", "tools"),
    # "explained" and "explainer" deliberately NOT here.
    #
    # They were in this list AND in EDUCATIONAL's, so they discriminated
    # nothing - and because _PRIORITY puts SCIENCE_EXPLAINER first, every tie
    # went to it. Measured: "mutual funds explained" selected
    # SCIENCE_EXPLAINER, so a finance video got the science template's look
    # and pacing. Almost every explainer in every niche has "explained" in
    # its topic, which is exactly why it carries no subject information.
    "SCIENCE_EXPLAINER": ("science", "space", "physics", "biology", "astronomy",
                          "cosmos", "quantum", "chemistry", "experiment",
                          "experiments"),
    "MOTIVATIONAL": ("motivation", "motivational", "discipline", "mindset",
                     "success", "inspire", "productivity"),
    "EDUCATIONAL": ("education", "learn", "learning", "tutorial", "how to",
                    "guide", "study", "history", "health",
                    # The finance vocabulary, for the same reason it was
                    # added to the niche family: "finance" alone matched none
                    # of the channel's actual topics. EDUCATIONAL is the right
                    # home - a money explainer wants slower pacing and
                    # one-idea-per-frame captions, not a news template's
                    # hard cuts.
                    "finance", "financial", "money", "invest", "investing",
                    "investment", "mutual", "fund", "funds", "sip", "stock",
                    "stocks", "tax", "insurance", "loan", "emi", "ppf",
                    "nps", "savings", "budget", "credit", "retirement",
                    "inflation", "interest", "excel", "office",
                    "spreadsheet",
                    # The programming and database niches belong here rather
                    # than with TECH_NEWS: a SQL walkthrough is a lesson, and
                    # wants EDUCATIONAL's slower pacing and one-idea-per-frame
                    # captions, not a news template's hard cuts.
                    "sql", "database", "databases", "programming", "coding",
                    "developer", "code", "course", "courses", "explained"),
}

# Tie-break order, so selection never depends on dict iteration.
_PRIORITY = ("KIDS_STORY", "MYSTERY", "TOP_5", "FAST_FACTS", "STORYTELLING",
             "TECH_NEWS", "SCIENCE_EXPLAINER", "MOTIVATIONAL", "EDUCATIONAL")


def select_template(niche: str, style: str = "", *,
                    made_for_kids: bool = False,
                    long_form: bool = False,
                    forced: str = "") -> StyleTemplate:
    """Choose a template from the niche and the user's style text.

    Child-directed content always gets KIDS_STORY: its caption style and pacing
    are part of the safety profile, not a preference.

    `long_form` exists because the right look for a story depends on its
    LENGTH, not only its subject. A 45-second story wants STORYTELLING's
    6-second scenes; a half-hour narrated story wants a frame held for
    fourteen and no burnt-in captions, which is what the reference channels
    for this format actually do - and at 6 seconds a 34-minute video needs 340
    images and looks like a montage. Without this, ILLUSTRATED_EXPLAINER was
    reachable only by forcing it in config, so the template existed and could
    not be chosen.
    """
    if forced:
        template = TEMPLATES.get(forced.strip().upper())
        if template:
            return template

    if made_for_kids:
        # Teaching letters or numbers is a different job from telling a story,
        # and it wants different visuals: a flashcard where the character IS
        # the content, against a full-frame illustration where the picture is.
        learning = KIDS_LEARNING_HINTS
        # Rhymes and poems are deliberately NOT here. They are performances
        # with illustrated scenes, not drills - a flashcard showing one word
        # at a time is the wrong shape for a nursery rhyme.
        haystack = f"{niche} {style}".lower()
        if any(hint in haystack for hint in learning):
            return TEMPLATES["KIDS_LEARNING"]
        return TEMPLATES["KIDS_STORY"]

    haystack = f"{niche} {style}".lower()
    # A long-form STORY gets the held-frame treatment before the keyword
    # matching below can route it to STORYTELLING's faster cutting.
    if long_form and any(hint in haystack for hint in LONGFORM_STORY_HINTS):
        return TEMPLATES["ILLUSTRATED_EXPLAINER"]
    # Plural-tolerant token set: "true horror stories" must match the "story"
    # hint, otherwise it silently falls through to the default template.
    tokens: set[str] = set()
    for w in words(haystack):
        tokens.add(w)
        if w.endswith("ies") and len(w) > 4:
            tokens.add(w[:-3] + "y")
        elif w.endswith("es") and len(w) > 3:
            tokens.add(w[:-2])
        if w.endswith("s") and len(w) > 3:
            tokens.add(w[:-1])
        else:
            tokens.add(w + "s")

    scores: dict[str, float] = {}
    for name, hints in _HINTS.items():
        score = 0.0
        for hint in hints:
            if " " in hint:
                score += 1.0 if hint in haystack else 0.0
            else:
                score += 1.0 if hint in tokens else 0.0
        scores[name] = score

    best = max(scores.values(), default=0.0)
    if best <= 0:
        return TEMPLATES[DEFAULT_TEMPLATE]
    for name in _PRIORITY:
        if scores.get(name, 0.0) == best:
            return TEMPLATES[name]
    return TEMPLATES[DEFAULT_TEMPLATE]


def apply_to_profile(profile: NicheProfile,
                     template: StyleTemplate) -> NicheProfile:
    """Fold the template's look-and-feel into the niche profile.

    The niche keeps authority over CONTENT (restrictions, fact-check needs,
    disclaimers); the template governs PRESENTATION. Kids restrictions are never
    relaxed by a template.
    """
    profile.scene_seconds = template.scene_seconds
    profile.words_per_second = template.words_per_second
    profile.caption_style = template.caption_style
    profile.music_mood = template.music_mood
    if template.visual_style_suffix:
        profile.visual_style = template.visual_style_suffix
    if template.image_brief:
        profile.image_brief = template.image_brief
    return profile


def caption_overrides(template: StyleTemplate,
                      base_font_size: int) -> dict[str, Any]:
    """Caption engine settings for this template."""
    return {
        "captions.font_file": template.font,
        "captions.font_size": max(24, int(base_font_size * template.font_scale)),
        "captions.uppercase": template.uppercase,
        "captions.highlight_color": template.highlight_color,
        "captions.outline": template.outline,
        "captions.safe_bottom": template.safe_bottom,
        # The style had 1.2 hard-coded, so this field was decorative.
        "captions.letter_spacing": template.letter_spacing,
        "captions.style": template.caption_style,
    }


def visual_overrides(template: StyleTemplate) -> dict[str, Any]:
    """Visual-source settings for this template.

    Always states whether flashcards apply, because that is a decision only
    the template can make correctly - the visual engine sees `made_for_kids`
    but not whether the video teaches the alphabet or tells a story.
    """
    out: dict[str, Any] = {"visuals.kids_animation": template.flashcards}
    if template.prefer_ai:
        out["visuals.prefer_ai"] = True
    return out


def video_overrides(template: StyleTemplate) -> dict[str, Any]:
    """Composer settings for this template."""
    return {
        "video.transition": template.transition,
        "video.transition_duration": template.transition_duration,
        "video.kenburns": template.kenburns,
        "video.contrast": template.contrast,
        "video.saturation": template.saturation,
    }


def list_templates() -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description,
         "scene_seconds": t.scene_seconds, "caption_style": t.caption_style,
         "music_mood": t.music_mood}
        for t in TEMPLATES.values()
    ]
