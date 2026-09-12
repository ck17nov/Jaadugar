"""A curated bank of pre-written scripts, and the gates that keep it varied.

WHY THIS EXISTS. Scripts were written at render time by whatever free model
was reachable, and the measured result was flat: structurally correct after the
story gate landed, but the prose was thin - "Milo the boy watches his bright
kite", a participation line about knocking three times in a story about a kite,
and a final scene that was nothing but the refrain. A frontier model with a
long prompt, generating in bulk with a human reading the output, writes better
than a 120B model squeezed into JSON mode inside a 4,096-token budget. This
module is the consumer side of that: the file is authored elsewhere, imported
here, and rendered by the existing pipeline.

Three things ride along in the file that the pipeline currently pays an LLM
call for, or gets wrong:

  * PER-SCENE CAPTIONS in the other language. The pipeline machine-translates
    the narration at render time, which means a machine translation of a
    machine story - and the Hindi captions were wrong. Authored captions are
    the fix, and they cost nothing extra to ask for while generating.
  * PER-SCENE IMAGE BRIEFS. The generated visual prompts drifted from the
    narration. Written alongside the scene they describe, they cannot.
  * A CHARACTER BIBLE. This costs one LLM call per job today and returned
    "a child, blue blanket", which is why the same boy had straight hair in
    scene 2 and curly hair in scene 5.

WHAT THIS MODULE DELIBERATELY DOES NOT DO. It does not generate anything and
it does not call any API. Both OpenAI and Anthropic forbid programmatic
extraction from their consumer products, so the file is produced by a human
pasting a batch prompt and copying the result back. `prompts.py` holds that
prompt.

THE VARIETY PROBLEM IS THE MONETISATION PROBLEM. YouTube's policy prohibits
"AI-generated content made with generic or unoriginal templates giving the
impression of mass production" and specifically names putting characters "in
the same situation over and over again with the same outcome". The existing
story gate makes this WORSE, not better: it requires a named protagonist,
three failed attempts, a verbatim refrain and child-solves-it agency, so a
thousand entries that all pass it share one arc. That is why every entry
carries an explicit `arc_variant` and `outcome_class`, and why import rejects
on a near-identical diversity tuple rather than only on word overlap. The
existing originality checker cannot see this failure mode at all - a
rename-only duplicate scores 0.385 against its 0.80 threshold.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..core.groups import group as get_group
from ..core.logging import log_event

# ---------------------------------------------------------------------------
# Vocabulary. These are closed sets on purpose: a free-text arc label cannot
# be counted, and the whole point is to cap how often any one arc repeats.
# ---------------------------------------------------------------------------

# How the protagonist gets to the end. Eight shapes, because the prohibited
# policy bullet is about the same situation with the same outcome.
ARC_VARIANTS = (
    "alone",           # succeeds unaided
    "by_helping",      # succeeds by helping someone else first
    "reframe",         # fails, then wants something different
    "wrong_want",      # discovers the want was the wrong want
    "cooperate",       # two children together
    "noticed",         # solves it by noticing what the adults missed
    "granted_early",   # gets the want in scene 2; the real problem follows
    "gave_it_away",    # gets it and gives it away
)

# WHAT the ending is, separately from how it was reached. The single
# highest-value field for the policy, because "the same outcome" is named.
OUTCOME_CLASSES = (
    "got_it", "got_better", "gave_away", "changed_mind", "helped_another",
)

# The content shape decides the beat table, and they are genuinely different
# jobs: a bedtime story has an arc, an alphabet video is a drill with
# repetition and no arc to interrupt.
SHAPES = ("narrative", "drill", "poem", "explainer", "procedure")

# Only evergreen material belongs in a bank. Anything time-sensitive must be
# generated live or it publishes stale.
VOLATILITIES = ("evergreen", "seasonal", "live_only")

# Shapes that carry a story arc, and therefore need arc_variant/outcome_class.
ARC_SHAPES = ("narrative", "poem")

MIN_SCENES = 3
MAX_SCENES = 400


def _text(value: Any) -> str:
    """A field's text, or "" when the value is not text at all.

    `str(value)` was used here, and it turned a shape the author plausibly
    writes into a Python repr that then got NARRATED. A narration written as
    ["He jumped high.", "He missed."] became the literal string
    "['He jumped high.', 'He missed.']" - brackets, quotes and commas, read
    aloud by edge-tts - and an image_brief written as {"subject": "a boy"}
    went to the generator as "{'subject': 'a boy'}". validate() saw non-empty
    strings and reported nothing, and the bank path has no later LLM rewrite
    to launder it.

    A list of strings IS recoverable - the author meant consecutive lines -
    so it is joined. Anything else returns empty, which the existing "empty"
    check rejects as fatal, and `load_jsonl` names the offending type.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        parts = [v.strip() for v in value if isinstance(v, str) and v.strip()]
        return " ".join(parts) if len(parts) == len(value) else ""
    return ""


# Scene fields that must be text, checked by `load_jsonl` where the line
# number is still known.
_SCENE_TEXT_FIELDS = ("beat", "narration", "caption", "image_brief",
                      "on_screen_text")


@dataclass
class BankScene:
    """One scene: what is said, what is shown, and what is captioned."""
    beat: str = ""
    narration: str = ""
    # The OTHER language. Hindi narration gets English captions and vice
    # versa, which is what was asked for and what the render-time translation
    # kept getting wrong.
    caption: str = ""
    # Scene CONTENT only - who, what, where. The template appends its own art
    # direction, so a baked style string here would go stale the day a
    # template changes.
    image_brief: str = ""
    on_screen_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"beat": self.beat, "narration": self.narration,
                "caption": self.caption, "image_brief": self.image_brief,
                "on_screen_text": self.on_screen_text}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BankScene:
        return cls(beat=_text(raw.get("beat")),
                   narration=_text(raw.get("narration")),
                   caption=_text(raw.get("caption")),
                   image_brief=_text(raw.get("image_brief")),
                   on_screen_text=_text(raw.get("on_screen_text")))


@dataclass
class BankEntry:
    """One ready-to-render script."""
    entry_id: str = ""
    group: str = ""                 # kids | finance | tech
    # Which topic WITHIN the group. The Create screen selects a group and then
    # topics under it, so without this a "kids bedtime stories" automation
    # would happily claim an alphabet drill.
    topic: str = ""
    shape: str = "narrative"
    volatility: str = "evergreen"
    language: str = "en"
    video_format: str = "SHORT"     # SHORT | LONGFORM
    made_for_kids: bool = False

    title: str = ""
    title_alts: list[str] = field(default_factory=list)
    refrain: str = ""
    description_hook: str = ""

    # Variety axes. See the module docstring.
    arc_variant: str = ""
    outcome_class: str = ""
    problem_domain: str = ""
    setting: str = ""
    protagonist_type: str = ""
    emotional_register: str = ""
    # WHAT THE CHILD DOES AT THE TURN, from a closed vocabulary.
    #
    # A seventh axis, and the one the others could not see: 10 of the first
    # 19 narratives turned on the child merely LOOKING somewhere else, and
    # every pair of them differed on enough of the original six to pass. The
    # variety gate was measuring the furniture while the plot machinery was
    # identical. "notice" is allowed - a story may legitimately turn on
    # seeing something - but it is capped like any other share.
    turn_kind: str = ""

    characters: list[dict[str, str]] = field(default_factory=list)
    # Numbers the script asserts, declared so the fact checker can tell an
    # illustrative figure from an unverified claim.
    #
    # Without this every finance entry is flagged medium-risk: the prompt
    # tells authors to use "round illustrative figures and say they are
    # illustrative", and the fact checker then flags all twelve of them as
    # "numeric claim not declared in claims array" - measured on a real
    # 72-scene expense-ratio explainer. Correct as far as it goes, but it
    # made risk=medium permanent noise rather than a signal, and forced
    # approval on every finance video for a reason nobody could act on.
    #
    # Shape matches Script.claims: {"claim", "confidence", "basis"}.
    claims: list[dict[str, str]] = field(default_factory=list)
    scenes: list[BankScene] = field(default_factory=list)

    provenance: dict[str, Any] = field(default_factory=dict)
    human: dict[str, Any] = field(default_factory=dict)

    # Derived at import; never authored by hand.
    word_count: int = 0
    scene_count: int = 0
    estimated_seconds: float = 0.0
    content_hash: str = ""

    # ------------------------------------------------------------------
    def narrations(self) -> list[str]:
        return [s.narration for s in self.scenes if s.narration.strip()]

    def beats(self) -> list[str]:
        """The beat names, aligned index-for-index with narrations().

        Same filter as narrations() on purpose. The story gate uses
        these to look at the RIGHT scene and to skip questions a form
        does not have - a poem has no obstacle beat - so a list that
        drifted by one scene would be worse than no list at all.
        """
        return [s.beat for s in self.scenes if s.narration.strip()]

    def diversity_tuple(self) -> tuple[str, ...]:
        """The six axes that must not all coincide with another entry.

        Word overlap cannot catch "the same story with different names";
        this can, because the axes describe the story rather than its wording.
        """
        return (self.problem_domain.lower(), self.setting.lower(),
                self.protagonist_type.lower(), self.emotional_register.lower(),
                self.outcome_class.lower(), self.arc_variant.lower(),
                self.turn_kind.lower())

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id, "group": self.group,
            "topic": self.topic,
            "shape": self.shape, "volatility": self.volatility,
            "language": self.language, "video_format": self.video_format,
            "made_for_kids": self.made_for_kids,
            "title": self.title, "title_alts": list(self.title_alts),
            "refrain": self.refrain,
            "description_hook": self.description_hook,
            "arc_variant": self.arc_variant,
            "turn_kind": self.turn_kind,
            "outcome_class": self.outcome_class,
            "problem_domain": self.problem_domain, "setting": self.setting,
            "protagonist_type": self.protagonist_type,
            "emotional_register": self.emotional_register,
            "characters": [dict(c) for c in self.characters],
            "claims": [dict(c) for c in self.claims],
            "scenes": [s.to_dict() for s in self.scenes],
            "provenance": dict(self.provenance), "human": dict(self.human),
            "word_count": self.word_count, "scene_count": self.scene_count,
            "estimated_seconds": round(self.estimated_seconds, 1),
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BankEntry:
        entry = cls(
            entry_id=str(raw.get("entry_id", "")).strip(),
            group=str(raw.get("group", "")).strip().lower(),
            topic=str(raw.get("topic", "")).strip().lower(),
            shape=str(raw.get("shape", "narrative")).strip().lower(),
            volatility=str(raw.get("volatility", "evergreen")).strip().lower(),
            language=str(raw.get("language", "en")).strip().lower(),
            video_format=str(raw.get("video_format", "SHORT")).strip().upper(),
            made_for_kids=bool(raw.get("made_for_kids", False)),
            title=str(raw.get("title", "")).strip(),
            title_alts=[str(t).strip() for t in (raw.get("title_alts") or [])
                        if str(t).strip()],
            refrain=str(raw.get("refrain", "")).strip(),
            description_hook=str(raw.get("description_hook", "")).strip(),
            arc_variant=str(raw.get("arc_variant", "")).strip().lower(),
            turn_kind=str(raw.get("turn_kind", "")).strip().lower(),
            outcome_class=str(raw.get("outcome_class", "")).strip().lower(),
            problem_domain=str(raw.get("problem_domain", "")).strip().lower(),
            setting=str(raw.get("setting", "")).strip().lower(),
            protagonist_type=str(raw.get("protagonist_type", "")).strip().lower(),
            emotional_register=str(
                raw.get("emotional_register", "")).strip().lower(),
            characters=[{"name": str(c.get("name", "")).strip(),
                         "description": str(c.get("description", "")).strip()}
                        for c in (raw.get("characters") or [])
                        if isinstance(c, dict)],
            claims=[{"claim": str(c.get("claim", "")).strip(),
                     "confidence": str(c.get("confidence", "medium")).strip(),
                     "basis": str(c.get("basis", "")).strip()}
                    for c in (raw.get("claims") or [])
                    if isinstance(c, dict) and str(c.get("claim", "")).strip()],
            scenes=[BankScene.from_dict(s) for s in (raw.get("scenes") or [])
                    if isinstance(s, dict)],
            provenance=dict(raw.get("provenance") or {}),
            human=dict(raw.get("human") or {}),
        )
        entry.recompute()
        return entry

    # ------------------------------------------------------------------
    def recompute(self) -> None:
        """Fill the derived fields. Called on load and after any edit.

        `estimated_seconds` is the important one: it becomes the render's
        DURATION TARGET. The pipeline re-synthesises the voice at a corrected
        speaking rate when measured narration drifts more than 8% from the
        target, so a banked script rendered against an unrelated requested
        duration would have its voice stretched or squeezed to fit. The
        script's own length has to be the target.
        """
        from ..core.util import words as split_words

        self.scene_count = len(self.scenes)
        self.word_count = sum(len(split_words(s.narration))
                              for s in self.scenes)
        self.estimated_seconds = self.word_count / max(
            words_per_second(self.group, self.made_for_kids, self.topic),
            0.5)
        self.content_hash = hashlib.blake2b(
            "\n".join(self.narrations()).encode("utf-8"),
            digest_size=16).hexdigest()
        if not self.entry_id:
            self.entry_id = f"{self.group or 'x'}-{self.language}-" \
                            f"{self.content_hash[:10]}"


# Pace by TOPIC, checked before the group. AI, science and programming were
# separate groups with distinct paces - 2.7, 2.9 and 2.5 - and merging them
# into "tech" would have flattened all three to one number, which is a real
# loss: a science fact list is read faster than a SQL walkthrough, and getting
# it wrong makes the estimated duration wrong for every entry in the group.
_TOPIC_PACE: tuple[tuple[tuple[str, ...], float], ...] = (
    (("science", "space", "physics", "biology"), 2.9),
    (("ai ", "ai explained", "ai news", "ai tools"), 2.7),
    (("youtube", "phone", "laptop", "pc and laptop", "unboxing"), 2.7),
    (("sql", "database", "programming", "coding", "developer",
      "excel", "office"), 2.5),
)

_GROUP_PACE: dict[str, float] = {"kids": 2.0, "finance": 2.5, "tech": 2.7}


def words_per_second(group: str, made_for_kids: bool = False,
                     topic: str = "") -> float:
    """Narration pace, so a word count implies a duration.

    Measured from the templates each group and topic actually select. This is
    why the generation prompt asks for a WORD COUNT rather than a duration:
    the word count is the thing an author can control, and it maps to seconds
    deterministically.
    """
    if made_for_kids or (group or "").strip().lower() == "kids":
        return 2.0
    needle = (topic or "").strip().lower()
    if needle:
        for words, pace in _TOPIC_PACE:
            if any(w in needle for w in words):
                return pace
    key = (group or "").strip().lower()
    return _GROUP_PACE.get(key, 2.5)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
@dataclass
class Problem:
    entry_id: str
    field_name: str
    message: str
    fatal: bool = True

    def __str__(self) -> str:
        mark = "REJECT" if self.fatal else "warn  "
        return f"{mark} {self.entry_id or '(no id)'} [{self.field_name}] {self.message}"


def validate(entry: BankEntry, *, expect_group: str = "") -> list[Problem]:
    """Everything wrong with one entry. Empty list means it is importable.

    Fatal problems reject the entry. Non-fatal ones are reported and let it
    through, because a missing `description_hook` is a shame and a missing
    protagonist is not a story.
    """
    out: list[Problem] = []

    def bad(fieldname: str, message: str, fatal: bool = True) -> None:
        out.append(Problem(entry.entry_id, fieldname, message, fatal))

    if expect_group and entry.group != expect_group.lower():
        bad("group", f"is {entry.group!r} but this file is for "
                     f"{expect_group.lower()!r}")
    found = get_group(entry.group)
    if not entry.group or found is None:
        bad("group", f"{entry.group!r} is not a known channel group")
    elif entry.topic and entry.topic not in [t.lower() for t in found.topics]:
        # Non-fatal. A topic outside the list still renders and still
        # publishes to the right channel; it just cannot be matched by a
        # topic-filtered automation, so it is worth saying out loud.
        bad("topic", f"{entry.topic!r} is not a listed topic of "
                     f"{entry.group!r}; this entry will only be reachable by "
                     f"a group-wide automation", False)
    if entry.shape not in SHAPES:
        bad("shape", f"{entry.shape!r} not one of {SHAPES}")
    if entry.volatility not in VOLATILITIES:
        bad("volatility", f"{entry.volatility!r} not one of {VOLATILITIES}")
    if entry.volatility == "live_only":
        bad("volatility", "live_only material must not be banked - it would "
                          "publish stale")
    if entry.video_format not in ("SHORT", "LONGFORM"):
        bad("video_format", f"{entry.video_format!r} must be SHORT or LONGFORM")
    if not entry.title:
        bad("title", "missing")
    elif len(entry.title) > 100:
        bad("title", f"{len(entry.title)} chars; YouTube truncates at 100")

    # ---- scenes ----
    if entry.scene_count < MIN_SCENES:
        bad("scenes", f"{entry.scene_count} scenes; at least {MIN_SCENES}")
    if entry.scene_count > MAX_SCENES:
        bad("scenes", f"{entry.scene_count} scenes exceeds {MAX_SCENES}")
    for index, scene in enumerate(entry.scenes):
        if not scene.narration:
            bad(f"scenes[{index}].narration", "empty")
        if not scene.image_brief:
            bad(f"scenes[{index}].image_brief",
                "empty - the visual would fall back to keyword search")
        if scene.image_brief and _looks_non_latin(scene.image_brief):
            bad(f"scenes[{index}].image_brief",
                "must be in ENGLISH - it is sent to an image generator")

    # ---- captions: the whole point of carrying them ----
    #
    # CHECKED PER SCENE, and that matters. The test used to compare all
    # narrations joined against all captions joined, and only when EVERY
    # scene had a caption - so two very likely authoring mistakes walked
    # through it. One English caption among five Hindi ones still left the
    # joined caption text predominantly Devanagari, so the aggregate passed;
    # and if some scenes had no caption at all the language comparison was
    # skipped entirely. Verified on a shipped entry: setting scene 1's
    # caption to its own English narration produced not even a warning, and
    # the English text was then burned onto an English-narrated frame. That
    # is the reported "captions aren't correct" defect reaching the render
    # through the gate built to stop it.
    with_caption = sum(1 for s in entry.scenes if s.caption.strip())
    if with_caption == 0:
        bad("scenes[].caption", "no scene has a caption; the pipeline would "
                                "fall back to machine translation", fatal=False)
    elif with_caption < entry.scene_count:
        bad("scenes[].caption",
            f"only {with_caption} of {entry.scene_count} scenes have one",
            fatal=False)

    # The narration language is the entry's own declaration, not a guess from
    # the text: a Hindi story can contain an English loanword and an English
    # one can name a person in Devanagari.
    narration_is_devanagari = entry.language.strip().lower().startswith("hi") \
        and not entry.language.strip().lower().startswith("hi-latn")

    # But the declaration has to be TRUE of the text, which was never
    # checked. Two things came through that gap. An honest mislabel sends a
    # Devanagari script to an English voice, which reads it as gibberish or
    # silence. And a deliberate one is a way past the variety gate: peers are
    # matched on group AND language, so relabelling `language` on an
    # otherwise identical entry leaves it with no peers to be compared
    # against, and the byte-identical check never runs.
    #
    # A MAJORITY test, matching `_looks_non_latin`, so the loanwords and
    # names the comment above describes are still fine either way.
    joined = " ".join(entry.narrations())
    if joined.strip():
        looks_devanagari = _looks_non_latin(joined)
        if narration_is_devanagari and not looks_devanagari:
            bad("language",
                f"declared {entry.language!r} but the narration is not in "
                f"Devanagari - a Hindi voice cannot read it")
        elif not narration_is_devanagari and looks_devanagari:
            bad("language",
                f"declared {entry.language!r} but the narration is in "
                f"Devanagari. Set language to 'hi', or write it in Latin "
                f"letters for 'hi-Latn'")
    for index, scene in enumerate(entry.scenes):
        if not scene.caption.strip():
            continue
        caption_is_devanagari = _bears_devanagari(scene.caption)
        if caption_is_devanagari == narration_is_devanagari:
            wanted = "English" if narration_is_devanagari else "Hindi"
            bad(f"scenes[{index}].caption",
                f"is in the SAME script as the narration; this entry is "
                f"{entry.language!r} so its captions must be {wanted}. "
                f"Got: {scene.caption[:56]!r}")

    # ---- arc shapes carry the variety axes ----
    if entry.shape in ARC_SHAPES:
        if entry.arc_variant not in ARC_VARIANTS:
            bad("arc_variant", f"{entry.arc_variant!r} not one of "
                               f"{ARC_VARIANTS}")
        if entry.outcome_class not in OUTCOME_CLASSES:
            bad("outcome_class", f"{entry.outcome_class!r} not one of "
                                 f"{OUTCOME_CLASSES}")
        for name in ("problem_domain", "setting", "protagonist_type",
                     "emotional_register"):
            if not getattr(entry, name):
                bad(name, "missing - it is one of the six diversity axes")
        if not entry.refrain:
            bad("refrain", "missing; the story gate requires a verbatim "
                           "refrain repeated three times")
        # The NAME follows the narration - a Hindi story's characters are
        # named in Devanagari, and the prompt asks for that so the name masks
        # correctly against the narration. The DESCRIPTION does not: it is
        # concatenated into the image prompt as "<name> is <description>" and
        # sent to SDXL, whose text encoder cannot read Devanagari at all.
        # Found in three banked Hindi entries, where the whole clause was
        # noise - so the cast the bible exists to keep consistent was drawn
        # differently in every single frame.
        for index, person in enumerate(entry.characters or []):
            described = str(person.get("description", "")).strip()
            if described and _looks_non_latin(described):
                bad(f"characters[{index}].description",
                    "must be in ENGLISH - it goes into the image prompt. "
                    "The NAME stays as the narration writes it")
        if not entry.characters:
            # FATAL, not a warning. The cast is not only the image bible: it
            # is the ONLY input to the variety gate's rename detector, which
            # masks these names before comparing two stories. An entry with
            # no cast masks nothing, so the same story under a new name
            # compares as a different one and passes the gate that exists to
            # keep the channel out of "mass production" review.
            bad("characters", "empty - it keeps a face consistent between "
                              "shots AND it is what the variety gate masks "
                              "to catch a renamed duplicate")

    if not entry.description_hook:
        bad("description_hook", "missing", fatal=False)
    if not entry.provenance.get("tool"):
        bad("provenance.tool", "missing - which tool wrote this", fatal=False)

    return out


_LATIN = re.compile(r"[A-Za-z]")
_NON_LATIN = re.compile(r"[ऀ-ॿ஀-௿ఀ-౿"
                        r"ঀ-৿઀-૿]")


def _looks_non_latin(text: str) -> bool:
    """True when the text is predominantly an Indic script.

    Counting both alphabets rather than testing for any non-Latin character:
    a Hindi narration legitimately contains digits and the odd loanword, and
    an English caption of a Hindi story legitimately contains a name.

    Used for IMAGE BRIEFS, where "must be English" has to tolerate a
    Devanagari character name - the prompt asks authors to name characters as
    the narration does, and for a Hindi story those names are Devanagari.
    """
    latin = len(_LATIN.findall(text or ""))
    other = len(_NON_LATIN.findall(text or ""))
    return other > latin


# How much Devanagari makes a CAPTION a Hindi caption.
#
# Majority is the wrong test here. An alphabet drill teaching the letter B to
# Hindi speakers must print "B" in its caption - "B कहता है buh, buh, B।" is
# eight Latin characters against six Devanagari, so a majority test calls a
# perfectly good Hindi caption English and rejects the entry. Measured on a
# shipped drill.
#
# Presence with a share instead: enough Devanagari to be deliberate, and
# enough of the text to be the caption's language rather than a name inside
# an English one. "Meera saw Grandmother's slipper" - the correct English
# caption for a Hindi story - is 0%, and a Devanagari name inside an English
# caption lands around 14%.
_DEVANAGARI_MIN_LETTERS = 3
_DEVANAGARI_MIN_SHARE = 0.20


def _bears_devanagari(text: str) -> bool:
    """True when this text is written IN Devanagari, not merely near it."""
    other = len(_NON_LATIN.findall(text or ""))
    if other < _DEVANAGARI_MIN_LETTERS:
        return False
    latin = len(_LATIN.findall(text or ""))
    total = latin + other
    return total > 0 and (other / total) >= _DEVANAGARI_MIN_SHARE


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def load_jsonl(path: Path) -> tuple[list[BankEntry], list[Problem]]:
    """Read a bank file. One JSON object per line.

    JSONL rather than one big array so the file is appendable, diffable, and
    a single malformed line cannot cost the other 299 entries.
    """
    entries: list[BankEntry] = []
    problems: list[Problem] = []
    if not path.exists():
        problems.append(Problem("", "file", f"{path} does not exist"))
        return entries, problems

    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(),
                                  start=1):
        text = line.strip()
        if not text or text.startswith("//"):
            continue
        try:
            raw = json.loads(text)
        except ValueError as exc:
            problems.append(Problem("", f"line {number}",
                                    f"is not valid JSON: {exc}"))
            continue
        if not isinstance(raw, dict):
            problems.append(Problem("", f"line {number}",
                                    "is not a JSON object"))
            continue
        # SHAPE problems are reported HERE, where the line number is still
        # known. `from_dict` can only return empty for a value that is not
        # text, which validate() then rejects as "empty" - true but useless
        # to the author, who wrote something and wants to know what was wrong
        # with it.
        #
        # A fatal shape problem skips the line entirely rather than yielding a
        # partial entry. Otherwise the importer reported it as rejected AND
        # stored it, which is the worst of both: a scene silently missing from
        # a video the report said was refused.
        shape = _shape_problems(raw, number)
        problems.extend(shape)
        if any(p.fatal for p in shape):
            continue
        try:
            entries.append(BankEntry.from_dict(raw))
        except Exception as exc:                # noqa: BLE001
            problems.append(Problem("", f"line {number}",
                                    f"could not be read: {exc}"))
    return entries, problems


def _shape_problems(raw: dict[str, Any], number: int) -> list[Problem]:
    """Fields whose JSON type is wrong, named precisely."""
    out: list[Problem] = []
    scenes = raw.get("scenes")
    if scenes is not None and not isinstance(scenes, list):
        out.append(Problem("", f"line {number}.scenes",
                           f"is a {type(scenes).__name__}, not a list"))
        return out
    for index, scene in enumerate(scenes or []):
        if not isinstance(scene, dict):
            # Silently dropped before this: from_dict filters on isinstance
            # and recompute() then derives scene_count from the survivors, so
            # a 6-scene entry imported as 5 with nothing reported and one
            # beat - its narration, its authored caption, its brief - simply
            # gone from the video.
            out.append(Problem(
                "", f"line {number}.scenes[{index}]",
                f"is a {type(scene).__name__}, not an object; it would be "
                f"dropped and the entry would render a scene short"))
            continue
        for name in _SCENE_TEXT_FIELDS:
            value = scene.get(name)
            if value is None or isinstance(value, str):
                continue
            if _text(value):
                continue                    # a list of strings, recoverable
            out.append(Problem(
                "", f"line {number}.scenes[{index}].{name}",
                f"is a {type(value).__name__}, not text; as a string it would "
                f"read {str(value)[:48]!r} - which the voice would narrate"))
    for name in ("characters", "claims", "title_alts"):
        value = raw.get(name)
        if value is not None and not isinstance(value, list):
            out.append(Problem("", f"line {number}.{name}",
                               f"is a {type(value).__name__}, not a list"))
    for index, person in enumerate(raw.get("characters") or []):
        if not isinstance(person, dict):
            out.append(Problem(
                "", f"line {number}.characters[{index}]",
                f"is a {type(person).__name__}, not an object; it would be "
                f"dropped, and the variety gate needs the cast to detect a "
                f"renamed duplicate"))
    return out


def write_jsonl(entries: Iterable[BankEntry], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
            count += 1
    log_event("BANK", "wrote bank file", path=path.name, entries=count)
    return count
