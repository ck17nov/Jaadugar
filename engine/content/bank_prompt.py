"""Build the batch prompt that generates a bank file.

This is generated rather than kept as a text file on purpose. The prompt has
to state the exact JSON shape, the exact beat names, the exact arc and outcome
vocabularies, and the word count implied by the target duration - all of which
live in code. A hand-maintained prompt drifts from the schema, and the failure
mode is 300 entries that all fail import.

It also has to know what is ALREADY in the bank. Asking one model for 300
stories in batches without telling it what it already wrote produces the same
child with the same want in the same kitchen, which is the exact
"mass-produced" impression YouTube's policy prohibits. So every batch prompt
carries the names, refrains and arcs already used, with instructions to avoid
them.

Everything here is measured or policy-derived, not stylistic preference:
  * WORD COUNTS, not durations. Narration pace is fixed per group and topic
    (kids 2.0 words/second, a SQL walkthrough 2.5, a science fact list 2.9),
    so a word count implies a duration deterministically and a duration does
    not imply a word count.
  * CAPTIONS IN THE OTHER LANGUAGE, authored. The pipeline machine-translates
    at render time and the Hindi captions came out wrong.
  * IMAGE BRIEFS IN ENGLISH, content only. The image generator understands
    English far better, and the template owns art direction - a baked style
    string goes stale the day a template changes.
  * NO OPENING QUESTION, a verbatim refrain three times, and the child solving
    the problem themselves. All three are enforced by story_gate, so a batch
    that ignores them is a batch that fails import.
"""
from __future__ import annotations

import json
from typing import Any, Sequence

from ..core.groups import group as get_group
from ..core.logging import log_event
from . import variety
from .bank import ARC_VARIANTS, OUTCOME_CLASSES, words_per_second

# ---------------------------------------------------------------------------
# Beat tables per content shape. The narrative one matches
# engine/content/script.py::_story_structure so a banked script and a
# live-generated one have the same skeleton.
# ---------------------------------------------------------------------------
BEATS: dict[str, list[tuple[str, str]]] = {
    "narrative": [
        ("want", "name the character in the first five words and the ONE "
                 "thing they want"),
        ("attempt", "they try it THEMSELVES and it does not work"),
        # These two lines were the cause, not a symptom.
        #
        # "say what that feels like in the body" produced a body ache as the
        # obstacle in 9 of the first 19 entries - "throat went tight"
        # appeared verbatim in three different stories - and "or notice the
        # thing" produced a turn that is a glance in 10 of 19. The authors
        # did exactly what they were told; the instruction was wrong.
        ("obstacle", "something gets measurably WORSE - a second person who "
                     "wants it too, a limit appearing, or the attempt "
                     "breaking something. Not a feeling in the body"),
        ("turn", "they THEMSELVES do something new: use a thing for a job it "
                 "was not made for, combine two things, trade, ask "
                 "differently, or change what they want. NOT 'they noticed'"),
        ("resolve", "they get there, and one warm line of how that feels"),
        ("refrain", "the refrain, word for word, as the last line"),
    ],
    "poem": [
        ("open", "the picture the whole rhyme is about, in one line"),
        ("verse_a", "four lines, an AABB or ABAB rhyme, all one action"),
        ("refrain", "the refrain, word for word"),
        ("verse_b", "four more lines - a NEW action, not the same one again"),
        ("refrain_2", "the refrain again, word for word, identical"),
        ("verse_c", "four lines that wind down; slower, quieter words"),
        ("close", "the refrain one last time as the final line"),
    ],
    "drill": [
        ("open", "name the one thing we will learn today; NOT a question"),
        ("item_intro", "the letter/number/shape, its sound and its form, "
                       "said twice"),
        ("model", "one everyday object as an example, named concretely"),
        ("call", "invite the child to say it aloud with you"),
        ("response", "say the answer back in the SAME words every time - "
                     "this is the refrain slot"),
        ("vary", "a second and third example from a DIFFERENT domain than "
                 "the first"),
        ("check", "a two-choice question the child can answer aloud, then "
                  "the answer"),
        ("recap", "list what was covered, in order, then the closing refrain"),
    ],
    "explainer": [
        ("hook", "the strongest single fact, stated cold"),
        ("context", "why this matters to the viewer"),
        ("promise", "what they will understand by the end"),
        ("mechanism", "HOW it works - one causal chain, no lists"),
        ("worked_example", "one concrete example with real numbers"),
        ("boundary", "when it does NOT apply, or the common mistake"),
        ("payoff", "the conclusion that reframes the hook"),
        ("cta", "one specific next action"),
    ],
    "procedure": [
        ("hook", "the symptom the viewer already has"),
        ("why", "what causes it, in one causal chain"),
        ("prepare", "what they need before starting"),
        ("steps", "the steps in order, each one checkable"),
        ("verify", "how they know it worked"),
        ("boundary", "when to stop and not attempt it"),
        ("cta", "one specific next action"),
    ],
}

# Relative share of the scenes each beat gets when the beats become sections.
# An even split gave a 600-second explainer an eleven-scene "hook", which is
# not a hook. Anything not listed weighs 1.
BEAT_WEIGHTS: dict[str, float] = {
    # explainer / procedure - the substance is in the middle
    "hook": 0.6, "context": 1.0, "promise": 0.5, "mechanism": 2.6,
    "worked_example": 2.4, "boundary": 1.4, "payoff": 1.0, "cta": 0.15,
    "why": 1.4, "prepare": 0.8, "steps": 3.4, "verify": 1.0,
    # narrative, when a story is long enough to need sections
    "want": 1.0, "attempt": 1.8, "obstacle": 1.8, "turn": 1.2,
    "resolve": 1.0, "refrain": 0.12,
    # poem
    "open": 0.6, "verse_a": 1.6, "verse_b": 1.6, "verse_c": 1.6,
    "refrain_2": 0.12, "close": 0.12,
    # drill
    "item_intro": 1.4, "model": 1.2, "call": 0.8, "response": 0.8,
    "vary": 2.0, "check": 1.0, "recap": 0.8,
}


# The disclaimer that opens every finance script.
#
# NOTE THIS DOES NOT SOLVE THE MONETISATION QUESTION. YouTube prohibits
# monetising channels that use "AI-generated personas to deliver information
# on sensitive topics", naming "AI-generated podcast hosts offering financial
# guidance" specifically, and finance is a named sensitive topic. A disclaimer
# does not change that the narrator is synthetic. It is here because it is
# correct practice regardless, and because the accompanying instruction - no
# first person, no persona, no advice - is the part that addresses the policy.
FINANCE_DISCLAIMER_EN = (
    "This video is for general education only and is not financial advice. "
    "Please speak to a qualified adviser before making any money decision.")
FINANCE_DISCLAIMER_HI = (
    "यह वीडियो केवल सामान्य जानकारी के लिए है, वित्तीय सलाह नहीं है। "
    "पैसे से जुड़ा कोई भी फ़ैसला लेने से पहले किसी योग्य सलाहकार से बात करें।")


# On-screen seconds per visual, matching what niche.py imposes on the live
# path: long-form (>180s) is floored at 4.5s and kids at 4.0s. The bank has to
# use the same rule, because scene count IS image count - a 600-second script
# written in nine scenes is nine stills held for a minute each, whatever the
# narration says.
BANK_SCENE_SECONDS = 4.5
KIDS_SCENE_SECONDS = 4.0

# A practical ceiling on scenes per banked entry. Pure pacing would ask for
# 133 scenes for a ten-minute explainer, and 133 hand-written image briefs per
# script is not something anyone will paste. At 90 a fifteen-minute video
# still holds each visual for ten seconds.
MAX_BANK_SCENES = 90

# The longest a single still may hold. Beyond this a video stops reading as a
# video. It is the reason a long story cannot simply be its six beats: at
# 420 seconds that is 70 seconds per picture.
#
# NOT the same as retention.py's pacing ceiling, which is
# `profile.scene_seconds * 1.9` - about 8 seconds for kids. A short kids story
# deliberately runs above that: script.py forces a kids story's scene count to
# its beat count because a story beat is a unit of meaning and splitting one
# mid-thought reads worse than holding the picture, and at 2.0 words per second
# a 4-second scene is seven words, which arrives as a fragment. So a banked
# short story takes retention's pacing note on the chin, exactly as a
# live-generated one does. This number bounds the case that is genuinely
# indefensible rather than merely imperfect.
MAX_SCENE_SECONDS = 12.0

# How long a STORY may grow by repeating its beats before it becomes a
# sectioned piece instead. Four minutes: past that, a bedtime story wants
# chapters rather than a longer spine, and the sectioned mode is the right
# shape for it. The researched kids median is 158 seconds, comfortably
# inside this.
BEAT_REPEAT_CEILING = 240

# Roughly how many words of JSON output one entry costs, used only to keep the
# suggested batch size inside a single chat response.
_BATCH_WORD_BUDGET = 9000


def scene_plan(*, group_key: str, target_seconds: int, shape: str,
               made_for_kids: bool = False, topic: str = "") -> dict[str, Any]:
    """How many scenes an entry of this length should have, and how long each.

    Returns the numbers the prompt quotes, so the prompt and the renderer
    cannot disagree about pacing.
    """
    beats = BEATS.get(shape, BEATS["narrative"])
    wps = words_per_second(group_key, made_for_kids, topic)
    words = int(target_seconds * wps)

    pace = KIDS_SCENE_SECONDS if made_for_kids else BANK_SCENE_SECONDS
    beat_mode = shape in ("narrative", "poem", "drill")
    if beat_mode and target_seconds / max(len(beats), 1) <= MAX_SCENE_SECONDS:
        # Short-form storytelling: the beats ARE the scenes. This matches
        # script.py, which overrides the pacing-derived count with
        # len(structure) for kids stories for exactly this reason - a story
        # beat is a unit of meaning, and splitting it mid-thought reads worse
        # than holding the picture a moment longer.
        low, high = len(beats), len(beats) + 2
    elif beat_mode and target_seconds <= BEAT_REPEAT_CEILING:
        # THE MIDDLE BAND: still a story, just a longer one.
        #
        # There used to be a one-second cliff here. A six-beat narrative at
        # 72 seconds got 6-8 scenes; at 73 it got 18-20 and switched to
        # sectioned mode. Nothing about a story changes in that second, and
        # the effect was that every one of the 22 banked kids entries is a
        # SHORT - while 86 of the 225 researched kids videos run past 90
        # seconds, at a 158-second median and 500k median views.
        #
        # The cliff existed because one beat had to be one picture, so a
        # long beat meant a long hold. Shots removed that: a beat's span is
        # now covered by two or three framings. So the story simply grows by
        # REPEATING beats - a second "attempt", a second "obstacle" - which
        # is what the beat table already tells authors to do.
        low = max(len(beats),
                  -(-target_seconds // int(MAX_SCENE_SECONDS)))
        high = low + 2
    else:
        # Either an explainer, or a story too long for one picture per beat.
        # Both become sections covering several scenes each.
        ideal = max(len(beats), int(round(target_seconds / pace)))
        low = min(ideal, MAX_BANK_SCENES)
        high = min(ideal + max(2, ideal // 10), MAX_BANK_SCENES)
    sections = len(beats) if low > len(beats) + 2 else 0

    per_scene = max(9, int(words * 0.92 / max(high, 1)))
    return {"words": words, "words_low": int(words * 0.92),
            "words_high": int(words * 1.12), "wps": wps,
            "scenes_low": low, "scenes_high": high,
            "words_per_scene": per_scene, "sections": sections,
            "seconds_per_scene": round(target_seconds / max(low, 1), 1),
            "capped": low >= MAX_BANK_SCENES}


def recommended_count(*, group_key: str, target_seconds: int,
                      shape: str = "", made_for_kids: bool = False,
                      topic: str = "") -> int:
    """How many entries to ask for in one paste.

    A 50-second short costs a few hundred words of JSON, so twenty fit in one
    response; a ten-minute explainer with sixty image briefs costs thousands,
    so asking for twenty guarantees a truncated batch where the last entries
    silently lose their captions.
    """
    shape = shape or shape_for(group_key, topic)
    plan = scene_plan(group_key=group_key, target_seconds=target_seconds,
                      shape=shape, made_for_kids=made_for_kids, topic=topic)
    # narration + caption + brief is about three times the narration, plus
    # per-scene JSON scaffolding.
    per_entry = plan["words"] * 3 + plan["scenes_high"] * 12 + 80
    return max(1, min(20, int(_BATCH_WORD_BUDGET / max(per_entry, 1))))


def _spread(total: int, names: Sequence[str]) -> list[int]:
    """Split `total` scenes over sections, by BEAT_WEIGHTS.

    Every section gets at least one scene; the rest goes by weight, with the
    remainder handed to the heaviest sections. An even split is wrong here:
    the mechanism and the worked example are the video, and the CTA is one
    picture.
    """
    count = len(names)
    if count <= 0:
        return []
    total = max(total, count)
    weights = [max(BEAT_WEIGHTS.get(n, 1.0), 0.01) for n in names]
    spare = total - count
    raw = [w / sum(weights) * spare for w in weights]
    out = [1 + int(x) for x in raw]
    # Remainder to the sections with the largest fractional part, breaking
    # ties towards the heavier section.
    short = total - sum(out)
    order = sorted(range(count), key=lambda i: (-(raw[i] % 1), -weights[i]))
    for i in range(short):
        out[order[i % count]] += 1
    return out


def shape_for(group_key: str, topic_kind: str = "") -> str:
    """The natural content shape for a group."""
    key = (group_key or "").lower()
    if key == "kids":
        if any(w in topic_kind.lower()
               for w in ("alphabet", "number", "count", "word", "spell",
                         "shape", "colour", "color", "sentence")):
            return "drill"
        if "rhyme" in topic_kind.lower() or "poem" in topic_kind.lower():
            return "poem"
        return "narrative"
    if key == "tech" and any(
            w in topic_kind.lower()
            for w in ("fix", "clean", "install", "repair", "setup",
                      "speed up", "tips and tricks", "how to",
                      "troubleshoot", "backup", "file management",
                      # Excel and Office are step-by-step by nature: the
                      # value is "click here, then here", which is a
                      # procedure, not an explanation of a mechanism.
                      "excel", "office", "shortcut", "formula")):
        return "procedure"
    return "explainer"


def build(*, group_key: str, language: str, video_format: str,
          target_seconds: int, count: int, shape: str = "",
          used_names: Sequence[str] = (), used_refrains: Sequence[str] = (),
          used_titles: Sequence[str] = (), arc_tally: dict[str, int] | None = None,
          viral_titles: Sequence[str] = (), topic: str = "") -> str:
    """The prompt to paste into ChatGPT or Claude.

    `used_*` and `arc_tally` come from what is already banked, so batch N+1
    does not retell batch N. `viral_titles` are real high-performing titles
    from the niche, fetched by the seeded-channel research at 9 quota units -
    supplied as PATTERN input with an explicit instruction not to copy.
    """
    found = get_group(group_key)
    label = found.label if found else group_key
    topics = list(found.topics) if found else []
    shape = shape or shape_for(group_key, topic)
    beats = BEATS.get(shape, BEATS["narrative"])
    kids = bool(found and found.child_directed)
    finance = group_key.lower() == "finance"
    plan = scene_plan(group_key=group_key, target_seconds=target_seconds,
                      shape=shape, made_for_kids=kids, topic=topic)
    wps = plan["wps"]
    words_low, words_high = plan["words_low"], plan["words_high"]
    per_scene = plan["words_per_scene"]

    parts: list[str] = []
    parts.append(
        f"You are writing {count} COMPLETE, ready-to-narrate video scripts "
        f"for a YouTube channel about {label}. Output is JSONL: one JSON "
        f"object per line, no array, no prose, no code fence, nothing else.")

    # ---- the hard numbers ----
    scenes_low, scenes_high = plan["scenes_low"], plan["scenes_high"]
    scene_range = (f"exactly {scenes_low} scenes" if scenes_low == scenes_high
                   else f"{scenes_low} to {scenes_high} scenes")
    parts.append(f"""
LENGTH - THIS IS THE MOST COMMON FAILURE
- Each script totals {words_low} to {words_high} words of NARRATION, in
  {scene_range}.
- That is roughly {per_scene} words per scene. A scene of six words is an
  outline, not a script.
- Do not think in seconds. The narration is read at {wps} words per second, so
  the word count IS the duration. Count your words.
- ONE SCENE IS ONE PICTURE, held for about {plan["seconds_per_scene"]} seconds.
  That is why the scene count is what it is: fewer scenes does not make a
  shorter video, it makes the same video with each picture held longer.
""")
    if count > 1:
        vary = (f" and the {scenes_low}-{scenes_high} scene range"
                if scenes_high > scenes_low else "")
        parts.append(
            f"- Do NOT make every script the same size. Spread them across "
            f"the whole {words_low}-{words_high} word band{vary}. A bank "
            f"where every video is the same length reads as machine-made "
            f"before anyone presses play.")

    # ---- what the retention analyser actually measures ----
    #
    # Not style advice. These are the three things `retention.py` scores, and
    # a banked entry is deliberately NOT auto-improved - rewriting narration
    # would invalidate that scene's authored caption and image brief - so the
    # author has to get them right the first time.
    interrupts = max(2, int(target_seconds / 12))
    parts.append(f"""
RHYTHM - these are scored automatically and cannot be fixed later
- FIRST SENTENCE UNDER 12 WORDS. It has to land before anyone decides to
  leave. A 30-word opening sentence is the single most common defect.
- NO SENTENCE OVER 20 WORDS anywhere. Average about 11. Two short sentences
  beat one long one every time.
- At least {interrupts} PATTERN INTERRUPTS across the script: a sentence of
  five words or fewer, a question, or a specific number. Spread them out; they
  are what stops the narration turning into a drone.
- No filler. Never "in this video", "let's dive in", "as you can see",
  "without further ado", "at the end of the day".""")

    # ---- the shape ----
    # Which beat may be repeated to reach the upper scene count. For a story
    # that is the failed attempt (real stories have more than one); for a
    # rhyme, another verse; for a lesson, another worked example.
    repeatable = {"narrative": "attempt", "poem": "verse_b",
                  "drill": "vary", "explainer": "worked_example",
                  "procedure": "steps"}.get(shape, beats[1][0])
    beat_lines = "\n".join(f"  {i + 1}. {name} - {purpose}"
                           for i, (name, purpose) in enumerate(beats))
    if plan["sections"]:
        # Long form. There are far more scenes than beats, so the beats are
        # sections and each one covers several consecutive scenes. The JSON
        # stays flat - every scene still carries its section's beat name - so
        # nothing downstream needs to know the difference.
        spread = _spread(scenes_high, [name for name, _ in beats])
        section_lines = "\n".join(
            f"  {i + 1}. {name} ({spread[i]} scene"
            f"{'s' if spread[i] != 1 else ''}) - {purpose}"
            for i, (name, purpose) in enumerate(beats))
        parts.append(f"""
SECTIONS AND SCENES - {len(beats)} sections covering {scenes_low}-{scenes_high} scenes
{section_lines}
Every scene carries its SECTION's name in its "beat" field, spelled exactly as
above, so several consecutive scenes share a beat name. The scene counts per
section are a guide - shift a scene between neighbouring sections if the
material wants it, but keep the order and never drop a section.""")
    else:
        parts.append(f"""
SCENES - use these beats, in this order, one scene each
{beat_lines}
Put the beat name in each scene's "beat" field, spelled exactly as above.
These beats are the spine, not a ceiling: to reach the upper scene count,
repeat a beat rather than inventing a new one - a second "{repeatable}"
scene, reusing that same beat name. Never drop a beat.""")

    if shape in ("narrative", "poem"):
        parts.append("""
STORY RULES - every one of these is checked by an automated gate, and a script
that breaks one is rejected without being read
- ONE named character. The name appears in the FIRST FIVE WORDS of scene 1 and
  in most scenes after it. Never "a little girl" - a name.
- THE CHARACTER SOLVES IT THEMSELVES. No adult rescues them. A grown-up may be
  present and kind, but the idea that fixes it must be the child's own.
- A REFRAIN of four to eight words, repeated WORD FOR WORD at least three
  times: near the start, in the middle, and as the very last line. Identical
  every time - not paraphrased. Put it in the "refrain" field too.
- DO NOT OPEN WITH A QUESTION. Not "Have you ever", not "What if", not "Can a".
  Scene 1 opens on the character doing something, somewhere, right now.
- Invite the child to join in aloud EXACTLY TWICE, and make the invitation fit
  the story - if it is about a kite, do not ask them to knock three times.
- No moral, no "and that is why". The last line is what the character feels.

CRAFT - what separates a story from the SHAPE of a story
The rules above are satisfiable by a script that is dull, and nineteen of them
were: measured across the existing bank, 10 of 19 turn on the child merely
looking somewhere else, 9 of 19 make the obstacle a feeling in the body, and
"throat went tight" appears word for word in three different stories. All
nineteen passed every gate. These four rules are what the gates now also
check, and they are the difference between a story and a template.
- THE TURN MUST BE AN IDEA, NEVER A PERCEPTION. Banned as the turn's opening
  move: noticed, saw, spotted, looked, peeked, peered, glanced, remembered,
  realised, heard, listened, watched, found - and "तभी ... (देखा|सुना|झाँका|
  दिखा|सूझा)". The test: if the turn can be restated as "they looked
  somewhere else", it is not a turn. The child must INVENT something - use an
  object for a job it was not made for, combine two things, trade, ask
  differently, or change what they want. A five-year-old should be able to
  copy it tomorrow.
- SOMETHING MUST GET MEASURABLY WORSE, and the script must name the
  worsening: a second person who wants the same thing, a limit that appears,
  or the attempt breaking something. A body sensation - an ache, a tight
  throat, tired arms - is at most ONE CLAUSE in the whole script, and never
  the obstacle on its own. Nothing about the situation has changed when a
  shoulder hurts.
- THE FIRST SENTENCE CARRIES THE WANT and what is lost if it fails, in
  fourteen words or fewer. Posture, weather and furniture wait for sentence
  two. Do not open every script with the character's name as the literal
  first word - at most one in five of this batch may.
- THE REFRAIN IS SOMETHING A CHILD CAN POINT AT, DO OR COUNT. Not sharing,
  kindness, quiet, enough, brave, patience, हिम्मत or सब्र - those are ideas.
  It must contain a repeated word, a rhyme or a count; the CHARACTER says it
  out loud inside the action at least once, not only as a line tagged onto
  the end of a scene; and it should mean something different the last time
  from the first.""")

    if finance:
        parts.append("""
FINANCE RULES - these are compliance requirements, not style
- NO HOST PERSONA. Never "I", never "we", never "my advice", never "trust me".
  There is no presenter in this channel and no expert character. Explain the
  mechanism; do not counsel the viewer.
- NO RECOMMENDATIONS. Never name a product, fund, stock, bank or app to buy.
  Explain how a KIND of thing works, never which one to choose.
- NO NUMBERS THAT EXPIRE. No current rates, prices, tax slabs, limits or
  returns. Use round illustrative figures and say they are illustrative.
- DO NOT WRITE A DISCLAIMER. The renderer prepends its own as a separate
  opening scene, narrated, captioned and burned on screen, in the right
  language and inside the duration budget. Writing one here produces TWO
  disclaimers back to back - measured, on a real entry - so scene 1 is the
  first line of the actual content.
- Indian context is welcome as CONCEPTS - what an SIP is, what PPF is for, how
  UPI settles - never as current figures.""")

    if kids and shape == "drill":
        parts.append("""
TEACHING RULES
- One thing per video. One letter, one number, one shape - not the alphabet.
- Repeat the thing being taught in EVERY scene.
- Nothing scary, no danger, no competition, no losing.""")

    # ---- declared numbers ----
    #
    # Only for the shapes that actually carry figures. A bedtime story has no
    # claims and asking for an empty array on every one of them is noise.
    if shape in ("explainer", "procedure"):
        parts.append("""
NUMBERS - declare every one
- "claims" lists each figure the narration states, so the fact checker can
  tell an illustrative example from an unverified assertion. Without it every
  number you write is flagged, and the video is held for manual review on a
  reason nobody can act on.
- One entry per figure: {"claim": "the text as narrated", "confidence":
  "high" | "medium" | "low", "basis": "why this number is defensible"}.
- For an ILLUSTRATIVE figure say so in the basis: "round illustrative
  assumption, stated as such in the narration". That is the honest label and
  it is what most of these will be.
- Use "high" only for arithmetic that follows from the figures you already
  stated. Never for a market return, a rate, or anything that changes.""")

    # ---- captions, the thing that was broken ----
    other = "English" if language.startswith("hi") else "Hindi (Devanagari)"
    narration_language = "Hindi (Devanagari script)" if language.startswith("hi") \
        else "English"
    parts.append(f"""
LANGUAGE AND CAPTIONS
- "narration" is in {narration_language}. This is what the voice says.
- "caption" is the SAME MEANING in {other}. This is what appears on screen, so
  a viewer who does not follow the spoken language can still read along. It
  must be a natural translation of that scene's narration, not a transcription
  of it, and it must be at most 90 characters so it fits one line.""")
    if not language.startswith("hi"):
        # The captions on an ENGLISH entry are the Devanagari ones, and this
        # is where the spelling goes wrong. Real errors from one batch:
        # "सिर्य ऐक" for "सिर्फ़ एक", and "ऐआई" for "एआई".
        parts.append("""- THE DEVANAGARI SPELLING HAS TO BE RIGHT. Captions are
  burned into the finished video, so an error is permanent and a Hindi
  speaker sees it immediately. Two traps in particular: write ए, not ऐ,
  unless the word really takes ऐ - it is एक, एआई, एप, not ऐक, ऐआई, ऐप - and
  keep the nukta where a word needs one: सिर्फ़, ज़रूरी, फ़ोन. Read each
  caption back before you move on. If you are unsure how a technical term is
  normally written in Hindi, use the everyday loanword in Devanagari rather
  than guessing at a spelling.""")
    if language.startswith("hi"):
        parts.append("""- Write the Hindi narration in Devanagari. Do not
  romanise it. Everyday English loanwords that Hindi speakers actually use -
  लैपटॉप, रैम, ऑनलाइन, बैटरी - should be written in Devanagari; do NOT invent
  Sanskrit substitutes for them. Write numbers and times as WORDS, not digits.""")

    # ---- image briefs ----
    # An explainer has no cast, and telling the model to "name the characters"
    # is how a finance video ends up illustrated with a presenter at a desk -
    # which is exactly the AI-persona shape the policy is about.
    if shape in ("narrative", "poem"):
        cast_line = ("\n  Name the characters by the names used in the "
                     "narration, so the same\n  people recur from scene to "
                     "scene.")
    else:
        cast_line = ("\n  There is no cast and no presenter in this channel: "
                     "show the thing being\n  explained - the object, the "
                     "place, the situation - not a person talking.")
    parts.append(f"""
IMAGE BRIEFS
- "image_brief" describes ONE picture for that scene: WHAT is in frame, WHERE,
  and the light.{cast_line}
- Write it in ENGLISH even when the narration is Hindi - it is sent to an
  image generator that understands English far better.
- Describe CONTENT ONLY. No art style, no "cel-shaded", no "watercolour", no
  camera or lens language. The renderer adds the art direction itself, and a
  style baked in here would fight it.
- It must match its own scene. A brief describing something that is not
  happening in that scene is the single most visible defect in the finished
  video.""")

    # ---- variety ----
    if shape in ("narrative", "poem"):
        arcs = ", ".join(ARC_VARIANTS)
        outcomes = ", ".join(OUTCOME_CLASSES)
        tally = ""
        if arc_tally:
            tally = ("\n- Already used, so prefer the others: "
                     + ", ".join(f"{k} x{v}" for k, v in
                                 sorted(arc_tally.items(), key=lambda kv: -kv[1])))

        # The caps have to be SATISFIABLE, and they were not.
        #
        # A flat share of the batch size asked for the impossible at the very
        # counts `recommended_count` returns: at 9 scripts the arc cap came
        # out as 1, and there are only 8 arcs; at 6 scripts the outcome cap
        # came out as 1, and there are only 5 outcomes. A model handed a
        # contradiction resolves it by ignoring one instruction, and which
        # one is anyone's guess.
        #
        # The floor is the pigeonhole count - you cannot spread N scripts
        # across K values with fewer than ceil(N/K) in the biggest bucket -
        # and the share comes from the constants the gate actually enforces
        # rather than a second copy of them.
        arc_cap = max(-(-count // len(ARC_VARIANTS)),
                      int(count * variety.MAX_ARC_SHARE))
        outcome_cap = max(-(-count // len(OUTCOME_CLASSES)),
                          int(count * variety.MAX_OUTCOME_SHARE))
        parts.append(f"""
VARIETY - a bank of similar stories cannot be monetised, so this is enforced
- "arc_variant" must be one of: {arcs}
- "outcome_class" must be one of: {outcomes}
- Spread them. In {count} scripts, no arc_variant may appear more than
  {arc_cap} times and no outcome_class more than {outcome_cap} times.{tally}
- "turn_kind" says what the child DOES at the turn, and must be one of:
  invent, combine, trade, ask, reframe, notice. "notice" is capped at a
  quarter of the batch, because a story that turns on looking somewhere
  else is the commonest way one of these comes out flat.
- Also fill "problem_domain", "setting", "protagonist_type" and
  "emotional_register" with short lowercase labels. Two scripts may not share
  five of those six axes - vary the problem, not just the name.
- Two scripts in this batch may not share the same setting AND the same
  problem_domain. Vary the problem, not just the name.""")

    if used_names:
        parts.append("- Do NOT use these character names, they are taken: "
                     + ", ".join(sorted(set(used_names))[:60]))
    if used_refrains:
        parts.append("- Do NOT reuse these refrains: "
                     + "; ".join(list(used_refrains)[:40]))
    if used_titles:
        parts.append("- These titles already exist; do not retell them: "
                     + "; ".join(list(used_titles)[:60]))

    # ---- titles ----
    #
    # Rewritten against MEASURED data, because the previous instruction -
    # "at most 70 characters, specific, and true of THIS script" - produced
    # 13 bare noun phrases with no verb out of 22, 8 that printed the
    # ending, and 0 that asked a question. The top thirty performers in this
    # niche, from the seeded-channel research, run a median 11 words and 66
    # characters, 46% carry a question mark or an exclamation, 50% open with
    # an emoji and 83% end in hashtags.
    #
    # The render's own scorer now agrees with these rules - it used to
    # reward the opposite - so a title written to them wins on merit rather
    # than by being authored.
    if shape in ("narrative", "poem"):
        part_two = ("an OPEN QUESTION or a felt consequence. The viewer must "
                    "not be able to finish the sentence in their head")
        alts_line = ("Exactly one of the three opens on a question word, "
                     "exactly one opens on the character acting, and AT MOST "
                     "ONE may use the \"X and the Y\" noun-list frame.")
        spoiler = """
- NO SPOILERS, and this is checked mechanically. Take the last 40% of your
  scenes - for 7 scenes, scenes 5, 6 and 7. No content word that FIRST
  appears in those scenes may appear in the title or either alt. Only the
  character's name and an object named in scene 1 may cross over. The check
  compares word STEMS, so an inflected form does not get past it: if the
  narration says "दूसरे सिरे" you may not write "दूसरा सिरा" either."""
    else:
        part_two = ("THE MISTAKE IT PREVENTS, or what it costs not to know "
                    "this")
        alts_line = ("One names the thing and what it does; one names the "
                     "mistake it prevents.")
        spoiler = """
- Keep the term people actually SEARCH for in the visible title (VLOOKUP,
  expense ratio, Word styles). A how-to is found by matching its words."""

    limit = ("100 characters INCLUDING the tail below - YouTube truncates at "
             "100 and the importer rejects anything longer")
    parts.append(f"""
TITLES - the operator's stated main concern, so read this twice
- The VISIBLE title - what is left after removing a leading emoji, a
  trailing " | English gloss" and the hashtag tail - is 55 to 70 characters
  and 8 to 14 WORDS. Count them. The whole string must still fit {limit}.
- TWO PARTS. Part one: the character and the trouble, with the character's
  name inside the first four words. Part two: {part_two}.
  Join them with "..." or end on "?".{spoiler}
- Exactly ONE emoji, at the front, chosen for the FEELING rather than the
  object. No emoji anywhere else. At most one "!". No ALL CAPS.
- End with 2 to 3 lowercase hashtags. A Devanagari title may add
  " | <4-6 word English gloss>" before them IF the whole string still fits
  100 characters; drop the gloss before you drop the sentence.
- "title_alts" holds two more, in the same length band. {alts_line}
- The question the title poses MUST be answered on screen. No "Amazing",
  no "You won't believe", no keyword stuffing, no promise the script does
  not keep.
- "description_hook" is one or two sentences to open the description.""")

    if viral_titles:
        parts.append(
            "- For SHAPE ONLY, here are real high-performing titles from this "
            "niche. Learn the length and the pattern. Do NOT copy them, do "
            "not reword one, and do not write a title for THEIR video:\n"
            + "\n".join(f"    {t}" for t in list(viral_titles)[:10]))

    # ---- topics ----
    #
    # The "topic" field has to be one of these, spelled exactly, because an
    # automation for "kids bedtime stories" filters the bank on it. A
    # misspelled topic does not fail - it just makes the entry reachable only
    # by a group-wide automation, which is a silent loss.
    # Only the topics that BELONG to this shape.
    #
    # The whole group used to be offered, so a drill prompt invited "kids
    # bedtime stories" and then told the model to spread across them. A model
    # obeying that writes six alphabet drills and labels two of them as
    # bedtime stories; the topic check passes, and a bedtime-stories
    # automation later claims a letter-B drill. Two instructions that cannot
    # both be satisfied get one of them ignored, and which one is a guess.
    for_shape = [t for t in topics if shape_for(group_key, t) == shape]
    pinned = (topic or "").strip()
    if pinned:
        # ONE topic for the whole batch.
        #
        # Filling the bank topic by topic is the only way to get even
        # coverage: a batch told to "spread across these nine" reliably
        # writes six bedtime stories and one of everything else, so the
        # thin topics stay thin no matter how many batches are run. The
        # caller asks for a topic and gets a batch entirely on it.
        parts.append(
            f"\nTOPIC - every script in this batch is about ONE subject. Set "
            f'"topic" to EXACTLY this string on every line, character for '
            f"character, and write nothing that belongs under another "
            f"heading:\n  {pinned}\n"
            f"Vary the SUBJECT MATTER within it - different angles, "
            f"examples, questions and situations under the same heading - "
            f"not the heading itself.")
    elif for_shape:
        chosen = ", ".join(f'"{t}"' for t in for_shape)
        dropped = [t for t in topics if t not in for_shape]
        note = ""
        if dropped:
            note = (f"\nThese belong to a DIFFERENT shape and are not "
                    f"available in this batch: {', '.join(dropped)}. Ask for "
                    f"them with their own --shape.")
        parts.append(
            f"\nTOPICS - every script here is a {shape}, so set \"topic\" to "
            f"EXACTLY one of these strings, and cover a spread across the "
            f"batch rather than writing every script on the first one:\n"
            + "\n".join(f"  - {t}" for t in for_shape)
            + f"\nAllowed values for \"topic\": {chosen}{note}")
    elif topics:
        # The caller forced a shape no listed topic maps to. Say so, rather
        # than offering topics the import will warn about.
        parts.append(
            f"\nTOPICS - no listed topic of {label} is normally written as a "
            f"{shape}, so this batch is off the standard map. Use whichever "
            f"of these fits best: " + ", ".join(f'"{t}"' for t in topics))

    # ---- the exact shape ----
    example = {
        "group": group_key.lower(),
        "topic": pinned or (topics[0] if topics else ""),
        "shape": shape,
        "volatility": "evergreen",
        "language": language,
        "video_format": video_format.upper(),
        "made_for_kids": kids,
        "title": "...",
        "title_alts": ["...", "..."],
        "refrain": "..." if shape in ("narrative", "poem", "drill") else "",
        "description_hook": "...",
        "arc_variant": ARC_VARIANTS[0] if shape in ("narrative", "poem") else "",
        "outcome_class": OUTCOME_CLASSES[0] if shape in ("narrative", "poem") else "",
        # THE SEVENTH AXIS, and it has to be in the example or it is not
        # written. The output block says "exactly these fields, nothing
        # else", so a field described in prose above but missing from the
        # example comes back empty every time - measured on a real batch.
        # An empty turn_kind switches off the one variety check that caught
        # ten of the first nineteen stories turning on the child merely
        # looking somewhere else.
        "turn_kind": "invent" if shape in ("narrative", "poem") else "",
        "problem_domain": "...",
        "setting": "...",
        "protagonist_type": "...",
        "emotional_register": "...",
        "characters": [{"name": "...",
                        "description": "age, hair, clothing, one "
                                       "distinguishing feature"}],
        "scenes": [{"beat": beats[0][0], "narration": "...",
                    "caption": "...", "image_brief": "...",
                    "on_screen_text": ""}],
    }
    parts.append("""
OUTPUT - one JSON object per line, exactly these fields, nothing else. No
markdown, no commentary, no numbering. Every line must be valid JSON on its
own.""")
    parts.append(json.dumps(example, ensure_ascii=False))
    parts.append(f"\nNow write {count} scripts. Count the words in each one "
                 f"before you output it.")
    return "\n".join(parts)


def viral_titles_for(cfg, db, *, group_key: str, video_format: str = "SHORT",
                     limit: int = 10) -> list[str]:
    """Real high-performing titles from this group's niche, or [].

    `build` has taken a `viral_titles` argument since the bank existed and
    nothing ever supplied one, so the batch prompt never actually saw what
    performs in the niche - which is the "get ideas from the most-watched
    videos" half of the bank workflow.

    Ranked by VIEWS PER DAY rather than views, so a five-year-old video with
    a large lifetime count does not crowd out what is working now.

    Costs YouTube quota, so the caller has to ask for it. Returns [] on
    anything going wrong - no key, no quota left, no network - because a
    prompt without the shape hints is still a usable prompt.
    """
    found = get_group(group_key)
    if found is None or not found.topics:
        return []
    try:
        from ..core.niche import build_profile
        from ..research.youtube import YouTubeResearch

        researcher = YouTubeResearch(cfg, db)
        if not researcher.configured:      # a property, not a call
            log_event("BANK", "no YouTube key, so the batch prompt gets no "
                              "title patterns")
            return []
        niche = found.topics[0]
        profile = build_profile(niche, made_for_kids=found.child_directed)
        videos = researcher.research_channels(niche, profile)
        if not videos:
            videos = researcher.research(niche, profile,
                                         video_format=video_format)
    except Exception as exc:                    # noqa: BLE001
        log_event("BANK", "could not fetch title patterns for the prompt",
                  error=str(exc)[:160])
        return []

    ranked = sorted(videos, key=lambda v: (v.view_velocity, v.views),
                    reverse=True)
    titles: list[str] = []
    for video in ranked:
        title = (video.title or "").strip()
        if title and title not in titles:
            titles.append(title)
        if len(titles) >= limit:
            break
    log_event("BANK", "title patterns for the batch prompt",
              group=group_key, titles=len(titles), corpus=len(videos))
    return titles


def context_from_bank(db, *, group_key: str, language: str,
                      topic: str = "") -> dict[str, Any]:
    """Names, refrains, titles and arc counts already in the bank.

    Fed back into the next batch prompt so the model does not repeat itself
    across batches - which is the single biggest cause of a bank that reads as
    mass-produced.

    `topic` puts that topic's own entries FIRST. The prompt only shows the
    first 40-60 of each list, and once a group holds hundreds of entries an
    unordered slice is mostly other topics - so a batch of Excel procedures
    would be warned off bedtime-story titles and told nothing about the
    eleven Excel titles it is actually at risk of repeating.

    Newest first, for the same reason: the entries most likely to be
    repeated are the ones written last.
    """
    import json as _json

    names: list[str] = []
    refrains: list[str] = []
    titles: list[str] = []
    arcs: dict[str, int] = {}
    rows = list(db.bank_entries(group=group_key, language=language,
                                limit=5000))
    rows.reverse()                          # newest first
    wanted = (topic or "").strip().lower()
    if wanted:
        rows.sort(key=lambda r: (r["topic"] or "").strip().lower() != wanted)
    for row in rows:
        try:
            data = _json.loads(row["payload"])
        except Exception:                       # noqa: BLE001
            continue
        for character in (data.get("characters") or []):
            name = str(character.get("name", "")).strip()
            if name:
                names.append(name)
        refrain = str(data.get("refrain", "")).strip()
        if refrain:
            refrains.append(refrain)
        title = str(data.get("title", "")).strip()
        if title:
            titles.append(title)
        arc = str(data.get("arc_variant", "")).strip()
        if arc:
            arcs[arc] = arcs.get(arc, 0) + 1
    return {"used_names": names, "used_refrains": refrains,
            "used_titles": titles, "arc_tally": arcs}
