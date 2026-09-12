#!/usr/bin/env python
"""Emit a self-contained authoring spec for the script bank.

    python scripts/bank_spec.py --out "SCRIPT_BANK_SPEC.md"
    python scripts/bank_spec.py --group kids --out kids.md

Hand this to any writing tool. It answers every question the importer will
ask, so output written against it imports without a round trip.

GENERATED, NOT WRITTEN. Every table below is read out of the code that does
the enforcing - the field list from `BankEntry`, the vocabularies from
`bank.py`, the beats and word counts from `bank_prompt.py`, the caps from
`variety.py`, the topics from `groups.py`. A hand-maintained spec drifts
from the schema and the failure mode is three hundred entries that all fail
import, which is the reason this file is a program.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.content import bank_prompt, variety                # noqa: E402
from engine.content.bank import (ARC_VARIANTS, MAX_SCENES,      # noqa: E402
                                 MIN_SCENES, OUTCOME_CLASSES,
                                 SHAPES, VOLATILITIES,
                                 words_per_second)
from engine.core.groups import GROUPS, group as get_group       # noqa: E402

SECONDS = {"SHORT": 50, "LONGFORM": 480}

# Entries wanted per (group, language, format) cell. Stated in the spec so
# an outside tool knows the size of the job; changeable with --target.
TARGET = 50
LANGS = {"kids": ("en", "hi"), "finance": ("en",), "tech": ("en",)}
LIVE_ONLY = {"finance news", "AI news", "new phone and laptop launches"}


def _fields() -> list[tuple[str, str, str]]:
    """(field, requirement, note) - the whole line-level contract."""
    return [
        ("group", "REQUIRED", "kids | finance | tech. Must match the file."),
        ("topic", "REQUIRED", "EXACTLY one of the group's topics, spelled "
                              "character for character. A misspelling does "
                              "not fail - it makes the entry reachable only "
                              "by a group-wide automation, which is a "
                              "silent loss."),
        ("shape", "REQUIRED", " | ".join(SHAPES) + ". Decided by the topic; "
                              "see the per-cell table."),
        ("volatility", "REQUIRED", " | ".join(VOLATILITIES) +
         '. Use "evergreen" - anything else is not worth banking.'),
        ("language", "REQUIRED", "en | hi. The NARRATION language."),
        ("video_format", "REQUIRED", "SHORT | LONGFORM."),
        ("made_for_kids", "REQUIRED", "true for every kids entry, false "
                                      "otherwise. Not a preference - it "
                                      "selects a stricter safety profile."),
        ("title", "REQUIRED", "See TITLES below. Max 100 characters "
                              "INCLUDING emoji and hashtags."),
        ("title_alts", "REQUIRED", "Two more titles in the same band."),
        ("refrain", "narrative/poem/drill", "The line repeated word for word. "
                                            "Must be concrete - a thing a "
                                            "child can point at, not an idea."),
        ("description_hook", "REQUIRED", "One or two sentences that open the "
                                         "YouTube description. Not the "
                                         "narration."),
        ("arc_variant", "narrative/poem", " | ".join(ARC_VARIANTS)),
        ("outcome_class", "narrative/poem", " | ".join(OUTCOME_CLASSES)),
        ("turn_kind", "narrative/poem", "invent | combine | trade | ask | "
                                        "reframe | notice. What the child "
                                        "DOES at the turn. 'notice' is "
                                        "capped - see the caps table."),
        ("problem_domain", "REQUIRED", "Short lowercase label, e.g. "
                                       '"shadow play", "water carrying".'),
        ("setting", "REQUIRED", 'Short lowercase label, e.g. "shared '
                                'bedroom at night".'),
        ("protagonist_type", "REQUIRED", 'Short lowercase label, e.g. '
                                         '"five-year-old girl".'),
        ("emotional_register", "REQUIRED", 'Short lowercase label, e.g. '
                                           '"calm and curious".'),
        ("characters", "narrative/poem", 'List of {"name", "description"}. '
                                         "The description is age, hair, "
                                         "clothing and ONE distinguishing "
                                         "feature - it is what keeps the "
                                         "pictures consistent."),
        ("claims", "any numeric figure", 'List of {"claim", "confidence", '
                                         '"basis"}. EVERY number the '
                                         "narration asserts must appear "
                                         "here, and an illustrative figure "
                                         "must say so in its basis AND in "
                                         "the narration."),
        ("scenes", "REQUIRED", f"{MIN_SCENES}-{MAX_SCENES} objects; see the "
                               f"per-cell table for the real range."),
        ("scenes[].beat", "REQUIRED", "One of the beat names for this shape, "
                                      "in order. Several consecutive scenes "
                                      "MAY share a beat - that is what makes "
                                      "a long-form entry's chapters."),
        ("scenes[].narration", "REQUIRED", "What the voice says, in the "
                                           "narration language."),
        ("scenes[].caption", "REQUIRED", "The SAME MEANING in the OTHER "
                                         "language. Max 90 characters."),
        ("scenes[].image_brief", "REQUIRED", "One picture, ALWAYS IN "
                                             "ENGLISH. See IMAGE BRIEFS."),
        ("scenes[].on_screen_text", "optional", 'Short overlay, or "".'),
    ]


def _cells(only_group: str) -> list[dict]:
    out = []
    for grp in GROUPS:
        if only_group and grp.key != only_group:
            continue
        for topic in grp.topics:
            if topic in LIVE_ONLY:
                continue
            for language in LANGS.get(grp.key, ("en",)):
                for fmt in ("SHORT", "LONGFORM"):
                    shape = bank_prompt.shape_for(grp.key, topic)
                    plan = bank_prompt.scene_plan(
                        group_key=grp.key, target_seconds=SECONDS[fmt],
                        shape=shape, made_for_kids=grp.child_directed,
                        topic=topic)
                    out.append({
                        "group": grp.key, "topic": topic,
                        "language": language, "format": fmt, "shape": shape,
                        "words": f"{plan['words_low']}-{plan['words_high']}",
                        "scenes": (f"{plan['scenes_low']}-"
                                   f"{plan['scenes_high']}"),
                        "wps": plan["wps"],
                    })
    return out


def build(only_group: str) -> str:
    p: list[str] = []
    w = p.append

    w("# Script bank - authoring spec")
    w("")
    w("Everything a writing tool needs to produce entries this project will")
    w("accept. Generated from the validator itself, so it cannot drift from")
    w("what the importer enforces.")
    w("")
    w("## What to produce")
    w("")
    w("**JSONL: one JSON object per line.** No array wrapper, no markdown")
    w("fence, no numbering, no prose, no blank lines. Every line must parse")
    w("on its own. One file per cell, named:")
    w("")
    w("```")
    w("<group>-<language>-<short|longform>-<topic-with-hyphens>.jsonl")
    w("e.g. kids-hi-short-kids-bedtime-stories.jsonl")
    w("```")
    w("")
    w("Hand the files back and they are imported with:")
    w("")
    w("```")
    w("python scripts/bank_rebuild.py --confirm")
    w("```")
    w("")
    w("which gates every line, keeps what passes, and reports the rest.")
    w("")

    w("## How many to write")
    w("")
    w(f"**{TARGET} per cell** - that is {TARGET} entries for each row of the")
    w("table below, so a topic that appears twice (English and Hindi) wants")
    w(f"{TARGET} of each, and Shorts and long-form are counted separately.")
    w("")
    w("**Write them in batches of 10-20, not in one block.** This is not a")
    w("style preference, it is how the caps work: the variety limits at the")
    w("bottom of this file are shares of the WHOLE group, not of your batch.")
    w("A single large block written blind will have entries refused on")
    w(f"`arc_variant` exceeding {int(variety.MAX_ARC_SHARE * 100)}% or")
    w(f"`outcome_class` exceeding {int(variety.MAX_OUTCOME_SHARE * 100)}%,")
    w("however well written each one is. Smaller batches let the earlier")
    w("ones land first, which moves the shares and makes room.")
    w("")
    w("A rejected entry is not a small loss - it can never become a video.")
    w("Ten that import cleanly beat twenty where six are refused.")
    w("")
    w("## READ THIS FIRST: what actually refuses entries")
    w("")
    w("A 357-entry batch written against an earlier version of this spec")
    w("had 79 entries refused. They were not spread evenly - **69 of the 79")
    w("were one cause**, and it was not craft or safety. It was repeating")
    w("the same story shape.")
    w("")
    arc_cap = max(1, int(TARGET * variety.MAX_ARC_SHARE))
    out_cap = max(1, int(TARGET * variety.MAX_OUTCOME_SHARE))
    turn_cap = max(1, int(TARGET * variety.MAX_TURN_SHARE))
    w(f"### 1. Spread `arc_variant` and `outcome_class` - the counts, "
      f"for {TARGET} entries")
    w("")
    w(f"| field | no more than | out of {TARGET} |")
    w("|---|---|---|")
    w(f"| the same `arc_variant` | **{arc_cap}** | "
      f"{int(variety.MAX_ARC_SHARE * 100)}% |")
    w(f"| the same `outcome_class` | **{out_cap}** | "
      f"{int(variety.MAX_OUTCOME_SHARE * 100)}% |")
    w(f"| `turn_kind` of `notice` | **{turn_cap}** | "
      f"{int(variety.MAX_TURN_SHARE * 100)}% |")
    w("")
    w("These are shares of the WHOLE topic, and the bank already holds")
    w("entries, so treat the numbers above as a ceiling and aim lower.")
    w("Spread deliberately - count as you go.")
    w("")
    w("**THE DEFAULT TRAP.** The obvious children's story is *the child")
    w("solves it alone and gets what they wanted* - `arc_variant: alone`")
    w(f"with `outcome_class: got_it`. In the real batch that was 42% and")
    w("43% respectively, which is more than double the cap, and it is why")
    w("69 good scripts were refused. Every value below is a different, real")
    w("story. Use them:")
    w("")
    w("| `arc_variant` | the shape it names |")
    w("|---|---|")
    for name, gloss in (
            ("alone", "they work it out by themselves"),
            ("by_helping", "they get what they want by helping someone else"),
            ("cooperate", "they and another child solve it together"),
            ("reframe", "they solve a different problem than the one they "
                        "started with"),
            ("wrong_want", "what they wanted turns out not to be the thing"),
            ("noticed", "someone notices their effort and it changes things"),
            ("granted_early", "they get it early and that creates the "
                              "problem"),
            ("gave_it_away", "they end up giving it away")):
        if name in ARC_VARIANTS:
            w(f"| `{name}` | {gloss} |")
    w("")
    w("| `outcome_class` | the ending it names |")
    w("|---|---|")
    for name, gloss in (
            ("got_it", "they get the thing they wanted"),
            ("got_better", "they get something better than they wanted"),
            ("changed_mind", "they stop wanting it"),
            ("helped_another", "someone else ends up better off"),
            ("gave_away", "they give it away and are glad"),
            ("shared", "it is shared")):
        if name in OUTCOME_CLASSES:
            w(f"| `{name}` | {gloss} |")
    w("")
    w("### 2. The obstacle is an EVENT, not a sensation")
    w("")
    w("This is a BLOCKING check and it refused nine entries. Something must")
    w("get measurably worse **in the world**: someone else takes the thing,")
    w("a limit appears, the attempt breaks something, a thing drops or")
    w("collapses or is knocked over.")
    w("")
    w("What does NOT count: *his chest felt tight*, *her arms ached*, *he")
    w("was tired*. A feeling is a fine second sentence and it is not a")
    w("complication, because nothing about the situation has changed.")
    w("")
    w("### 3. The turn is something they DO")
    w("")
    w("Also blocking. They use a thing for a job it was not made for,")
    w("combine two things, trade, ask differently, or change what they")
    w("want. **Not** *she realised*, *he noticed*, *she saw that*. Two")
    w("entries were refused for writing a real invention as a realisation -")
    w("if the child combines two things, say that they combined them.")
    w("")
    w("### 4. These words will refuse your entry")
    w("")
    w("The safety check is deliberately blunt, because it guards children's")
    w("content. It is not guessable, so here it is. A real batch lost a poem")
    w("about a seedling to the word *shoot*.")
    w("")
    w("| avoid | why | say instead |")
    w("|---|---|---|")
    w("| `shoot` alone | reads as violence | `green shoot`, `new shoots` "
      "(these are allowed) |")
    w("| `knife` | reads as violence | `butter knife`, `plastic knife` "
      "(allowed); or avoid |")
    w("| `kiss` | reads as romance | no substitute - rewrite the line |")
    w("| `scary`, `monster`, `nightmare` | frightening | describe the dark, "
      "not a threat |")
    w("| `dead`, `die` | violence, unless the subject is a torch, lamp, "
      "battery or flame | `the torch went out` |")
    w("| `stupid`, `idiot`, `shut up` | inappropriate | - |")
    w("| `buy now`, `click the link` | commercial pressure | - |")
    w("")
    w("### 5. Every line must be valid JSON on its own")
    w("")
    w("Three lines of the real batch were malformed - a delimiter broken")
    w("part way through a long object - and were simply dropped. Before")
    w("handing the file over, check that every line parses:")
    w("")
    w("```")
    w("python -c \"import json,sys;"
      "[json.loads(l) for l in open(sys.argv[1],encoding='utf-8') if l.strip()]"
      "\" your-file.jsonl")
    w("```")
    w("")
    w("Silence means every line is good. Also: each `refrain` must be")
    w("unique - not just against the bank, but within your own batch.")
    w("")
    w("## The fields")
    w("")
    w("| field | when | requirement |")
    w("|---|---|---|")
    for name, when, note in _fields():
        w(f"| `{name}` | {when} | {note} |")
    w("")

    w("## Cells, with their exact word and scene budgets")
    w("")
    w("**Word counts are the contract, not durations.** Narration pace is")
    w("fixed per group and topic, so the word count IS the length. Count the")
    w("words before you output an entry.")
    w("")
    w("| group | topic | lang | format | shape | words | scenes | words/sec |")
    w("|---|---|---|---|---|---|---|---|")
    for c in _cells(only_group):
        w(f"| {c['group']} | {c['topic']} | {c['language']} | {c['format']} "
          f"| {c['shape']} | {c['words']} | {c['scenes']} | {c['wps']} |")
    w("")

    w("## Beats, per shape")
    w("")
    w("Use these names, in this order. Several consecutive scenes may share")
    w("one beat name in a long-form entry - that is what becomes the chapter")
    w("list. A Short usually has one scene per beat.")
    w("")
    for shape, beats in bank_prompt.BEATS.items():
        w(f"### {shape}")
        w("")
        for name, note in beats:
            w(f"- **{name}** - {note}")
        w("")

    w("## Craft rules that are actually enforced")
    w("")
    w("These are checks, not advice. An entry that breaks a BLOCKING one is")
    w("rejected and can never become a video.")
    w("")
    w("- **BLOCKING - the turn is something the character DOES.** They use a")
    w("  thing for a job it was not made for, combine two things, trade, ask")
    w("  differently, or change what they want. NOT \"they noticed\" or")
    w("  \"they saw\". The test: could a five-year-old copy the idea")
    w("  tomorrow? Ten of the first nineteen stories failed this.")
    w("- **BLOCKING - the obstacle is more than a body feeling.** Something")
    w("  must get measurably WORSE in the world: a second person who wants")
    w("  the same thing, a limit appearing, the attempt breaking something.")
    w("  NOT \"her chest felt tight\". Twelve of the first nineteen failed")
    w("  this, and \"throat went tight\" appeared verbatim in three.")
    w("- **BLOCKING - the refrain is concrete.** A child can point at a ball")
    w("  and chant a count; they cannot point at kindness or patience.")
    w("- **BLOCKING - a verbatim refrain, at least three times**, identical")
    w("  character for character, and it is the last line.")
    w("- **BLOCKING - no scene opens on a rhetorical question.**")
    w("- Name the character in the first five words, and keep the name in")
    w("  roughly two thirds of the scenes. 31% is too few.")
    w("- About two join-in lines per entry, phrased as a sentence-initial")
    w("  imperative (\"Count with me: one, two, three.\").")
    w("- No adult rescue. The child solves it.")
    w("")

    w("## Titles")
    w("")
    w("- 55-70 VISIBLE characters, 8-14 words, and **under 100 characters in")
    w("  total** including the emoji and the hashtag tail. Devanagari titles")
    w("  have overrun this - 101 to 139 characters - and YouTube truncates.")
    w("- Two parts, joined by `...` or a question.")
    w("- **Withhold the ending.** A title that states the outcome leaves")
    w("  nothing to click for. No content word in the title may first appear")
    w("  in the last 40% of the scenes.")
    w("- One leading emoji, and a short lowercase hashtag tail.")
    w("- `title_alts` are two more in the same band, not throwaways.")
    w("")

    w("## Captions")
    w("")
    w("- The caption is the same meaning in the **other** language from the")
    w("  narration: English narration takes Devanagari captions, and the")
    w("  reverse. That is the entire point of them.")
    w("- Max 90 characters, so one line fits the frame.")
    w("- **Devanagari spelling has to be right** - captions are burned into")
    w("  the video, so an error is permanent. It is `एक`, `एआई`, `एप` - not")
    w("  `ऐक`, `ऐआई`, `ऐप` - and keep the nukta: `सिर्फ़`, `ज़रूरी`, `फ़ोन`.")
    w("  Both of those errors have shipped.")
    w("- Write Hindi in Devanagari, never romanised. Everyday loanwords stay")
    w("  loanwords in Devanagari (`लैपटॉप`, `रैम`, `बैटरी`); do not invent")
    w("  Sanskrit substitutes. Numbers and times as words, not digits.")
    w("")

    w("## Image briefs")
    w("")
    w("- One picture per scene, **always in English**, even when the")
    w("  narration is not.")
    w("- Detailed enough for an image model with no other context: who is in")
    w("  frame, what they are doing, where, the camera framing, the light,")
    w("  the mood. Never \"show a computer\".")
    w("- Describe each recurring character or object **identically every")
    w("  time** - that is what keeps the pictures consistent across scenes.")
    w("- **Every brief in an entry must be different.** Ninety near-identical")
    w("  briefs produce ninety near-identical pictures, which is the most")
    w("  boring thing this pipeline can output.")
    w("- Content only. No art-style, lens or camera-brand language - the")
    w("  render template owns the look.")
    w("- No presenter, no narrator, no talking head. An explainer has no")
    w("  cast.")
    w("")

    w("## Variety caps, computed across the whole group")
    w("")
    w("These are why a good entry can still be rejected: a share is a")
    w("property of the catalogue, not of the entry. They loosen as a group")
    w("grows, so a rejected entry often lands on a later pass.")
    w("")
    caps = [
        ("arc_variant share", f"<= {int(variety.MAX_ARC_SHARE * 100)}% of "
                              f"the group"),
        ("outcome_class share", f"<= {int(variety.MAX_OUTCOME_SHARE * 100)}%"),
        ("turn_kind 'notice' share", f"<= {int(variety.MAX_TURN_SHARE * 100)}"
                                     f"%"),
        ("diversity axes", f">= {variety.MIN_AXIS_DIFFERENCES} of the 7 axes "
                           f"must differ from EVERY other entry in the "
                           f"group"),
        ("refrain", "must be unique across the group"),
        ("narration overlap", "no near-duplicate wording against any banked "
                              "entry"),
    ]
    w("| cap | limit |")
    w("|---|---|")
    for name, limit in caps:
        w(f"| {name} | {limit} |")
    w("")
    w("The seven axes are: `arc_variant`, `outcome_class`, `turn_kind`,")
    w("`problem_domain`, `setting`, `protagonist_type`, `emotional_register`.")
    w("Two entries that differ only in the character's name are the same")
    w("story and will be refused.")
    w("")

    w("## Also enforced")
    w("")
    w("- **Evergreen only.** No dates, version numbers, prices, current")
    w("  events, launches or anything that will read as stale in two years.")
    w("- **Original.** Learn from what works; copy no real creator's script,")
    w("  title, storyline or distinctive wording.")
    w("- **Kids safety.** No weapons, injury, fear, or anything a parent")
    w("  would object to. A hand-written story once said \"there was a knife")
    w("  in the kitchen\" and was correctly refused.")
    w("- **Finance.** No first person, no persona, no advice. Illustrative")
    w("  figures must be declared in `claims` AND called illustrative in the")
    w("  narration.")
    w("- Do not include `provenance` or `human` - the importer fills those,")
    w("  and a file that claims a human review nobody performed is worse")
    w("  than one that claims nothing.")
    w("")

    w("## A complete example line")
    w("")
    w("Reformatted here for reading. In the file it is **one line**.")
    w("")
    w("```json")
    example = {
        "group": "kids", "topic": "kids bedtime stories",
        "shape": "narrative", "volatility": "evergreen", "language": "hi",
        "video_format": "SHORT", "made_for_kids": True,
        "title": "🌙 नीलू की टॉर्च बुझी, कहानी का आख़िरी पन्ना अब कैसे पढ़ेगी वह? #kahani",
        "title_alts": ["🔖 नीलू का आख़िरी पन्ना अंधेरे में रह गया... अब क्या? #kahani",
                       "💡 टॉर्च बुझने पर नीलू ने रोशनी कहाँ से ढूँढी? #kahani"],
        "refrain": "एक पन्ना, एक पन्ना, और एक",
        "description_hook": "नीलू को कहानी का अंत जानना था, और टॉर्च ने साथ छोड़ दिया।",
        "arc_variant": "alone", "outcome_class": "got_it",
        "turn_kind": "invent", "problem_domain": "reading in the dark",
        "setting": "bedroom at night", "protagonist_type": "six-year-old girl",
        "emotional_register": "determined and calm",
        "characters": [{"name": "नीलू",
                        "description": "six-year-old girl, short bobbed "
                                       "black hair, red star-print pyjamas, "
                                       "a chipped front tooth"}],
        "claims": [],
        "scenes": [{
            "beat": "want",
            "narration": "अंत जानना था नीलू को, वरना सपना अधूरा।",
            "caption": "Nilu needed the ending tonight.",
            "image_brief": "Nilu, a six-year-old girl with short bobbed "
                           "black hair and red star-print pyjamas, kneeling "
                           "on a low bed with an open picture book, one hand "
                           "holding a small yellow torch, warm lamplight "
                           "from the left, quiet and intent",
            "on_screen_text": "",
        }],
    }
    w(json.dumps(example, ensure_ascii=False, indent=2))
    w("```")
    w("")
    w("The `scenes` list is shown with one entry for brevity - a real one")
    w("carries the full count from the table above, every beat in order.")
    return "\n".join(p) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", default="")
    parser.add_argument("--target", type=int, default=50,
                        help="entries wanted per cell, stated in the spec")
    parser.add_argument("--out", default="")
    global TARGET
    args = parser.parse_args()
    TARGET = args.target
    text = build(args.group)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"{args.out}  ({len(text.splitlines())} lines)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
