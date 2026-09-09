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
        return cls(beat=str(raw.get("beat", "")).strip(),
                   narration=str(raw.get("narration", "")).strip(),
                   caption=str(raw.get("caption", "")).strip(),
                   image_brief=str(raw.get("image_brief", "")).strip(),
                   on_screen_text=str(raw.get("on_screen_text", "")).strip())


@dataclass
class BankEntry:
    """One ready-to-render script."""
    entry_id: str = ""
    group: str = ""                 # kids | finance | tech | ai | science | programming
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

    characters: list[dict[str, str]] = field(default_factory=list)
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

    def diversity_tuple(self) -> tuple[str, ...]:
        """The six axes that must not all coincide with another entry.

        Word overlap cannot catch "the same story with different names";
        this can, because the axes describe the story rather than its wording.
        """
        return (self.problem_domain.lower(), self.setting.lower(),
                self.protagonist_type.lower(), self.emotional_register.lower(),
                self.outcome_class.lower(), self.arc_variant.lower())

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
            "outcome_class": self.outcome_class,
            "problem_domain": self.problem_domain, "setting": self.setting,
            "protagonist_type": self.protagonist_type,
            "emotional_register": self.emotional_register,
            "characters": [dict(c) for c in self.characters],
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
            words_per_second(self.group, self.made_for_kids), 0.5)
        self.content_hash = hashlib.blake2b(
            "\n".join(self.narrations()).encode("utf-8"),
            digest_size=16).hexdigest()
        if not self.entry_id:
            self.entry_id = f"{self.group or 'x'}-{self.language}-" \
                            f"{self.content_hash[:10]}"


def words_per_second(group: str, made_for_kids: bool = False) -> float:
    """Narration pace for a group, so a word count implies a duration.

    Measured from the templates each group actually selects: kids 2.0,
    finance and programming 2.5, tech and AI 2.7, science 2.9. This is why
    the generation prompt asks for a WORD COUNT rather than a duration - the
    word count is the thing that can be controlled, and it maps to seconds
    deterministically.
    """
    table = {"kids": 2.0, "finance": 2.5, "programming": 2.5,
             "tech": 2.7, "ai": 2.7, "science": 2.9}
    key = (group or "").strip().lower()
    if key in table:
        return table[key]
    return 2.0 if made_for_kids else 2.5


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
    with_caption = sum(1 for s in entry.scenes if s.caption.strip())
    if with_caption == 0:
        bad("scenes[].caption", "no scene has a caption; the pipeline would "
                                "fall back to machine translation", fatal=False)
    elif with_caption < entry.scene_count:
        bad("scenes[].caption",
            f"only {with_caption} of {entry.scene_count} scenes have one",
            fatal=False)
    else:
        # Captions must be in the OTHER language, or they are pointless.
        narration_non_latin = _looks_non_latin(" ".join(entry.narrations()))
        caption_non_latin = _looks_non_latin(
            " ".join(s.caption for s in entry.scenes))
        if narration_non_latin == caption_non_latin:
            bad("scenes[].caption",
                "appears to be the SAME language as the narration; the point "
                "is Hindi captions on English narration and vice versa")

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
        if not entry.characters:
            bad("characters", "empty - this is what keeps a face consistent "
                              "between shots", fatal=False)

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
    """
    latin = len(_LATIN.findall(text or ""))
    other = len(_NON_LATIN.findall(text or ""))
    return other > latin


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
        try:
            entries.append(BankEntry.from_dict(raw))
        except Exception as exc:                # noqa: BLE001
            problems.append(Problem("", f"line {number}",
                                    f"could not be read: {exc}"))
    return entries, problems


def write_jsonl(entries: Iterable[BankEntry], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
            count += 1
    log_event("BANK", "wrote bank file", path=path.name, entries=count)
    return count
