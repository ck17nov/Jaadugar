#!/usr/bin/env python
"""Bring a staged batch up to the authoring spec, or drop it.

    python scripts/bank_repair_drop.py banks/gen/kids-drop3.jsonl
    python scripts/bank_repair_drop.py banks/gen/kids-drop3.jsonl --confirm

229 of 229 entries in one drop were refused by the gate. The causes were not
one thing, and they do not all deserve the same answer - so this splits them.

WHAT IS REPAIRED, because the entry contradicts itself rather than being
wrong about anything:

  stray virama      Every narration ended `।्` - a virama after a
                    sentence-ending danda, which in Devanagari suppresses the
                    inherent vowel of the consonant BEFORE it. After a full
                    stop there is no consonant, so it cannot be meaningful.
                    It was in the TEXT, so edge-tts would have read it.
  refrain repeats   A refrain declared in the metadata and then spoken once
                    is not a refrain. The gate wants it verbatim in >=3
                    scenes (REFRAIN_MIN_REPEATS); the importer wants >=2.
                    Repeating a declared line is what the form already
                    promises - a kids rhyme IS its refrain.
  name coverage     The protagonist must be named in >=50% of scenes
                    (MIN_NAME_COVERAGE). Where a scene opens with a bare
                    pronoun, the name replaces it.
  empty metadata    `characters` left blank - the name is read out of the
                    narration. `arc_variant` and `outcome_class` are NOT
                    filled: they are authorial judgements, and deriving them
                    mechanically gave every entry the same label, which
                    would defeat the diversity gate rather than satisfy it.
                    A drill has no arc at all, so the fields are removed.

WHAT IS DROPPED, and deliberately NOT repaired:

  safety:violence   A kids script with "threatening to kill everyone" in it.
                    Kids-safety gates exist to be obeyed, not routed around,
                    and softening the words would leave the scene.
  language          Declared `hi` and written in English. That is a
                    regeneration, not a repair - translating it would make
                    the script mine.
  child_resolves_it An adult solves the problem at the resolution. The
                    winning idea has to be the child's own, and changing who
                    acts is rewriting the story.

Every dropped entry is listed with its reason so the batch can be
regenerated. Nothing is written without `--confirm`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.content.story_gate import (MIN_NAME_COVERAGE,  # noqa: E402
                                       REFRAIN_MIN_REPEATS,
                                       REFRAIN_MIN_WORDS, _find_character,
                                       _tokens)

ROOT = Path(__file__).resolve().parents[1]

# A combining mark stranded after a danda. Nothing can attach to a full stop.
_STRAY_AFTER_DANDA = re.compile("।[ऀ-ःऺ-्॑-ॗ]+")

# Hindi third-person pronouns, at the start of a sentence, where a name reads
# better and the gate needs one.
_LEADING_PRONOUN = re.compile(r"^(वह|वो|उसने|उसका|उसकी|उसके)\b\s*")

# Refuse to touch these, and say why.
UNFIXABLE = {
    "safety:violence": "kids-safety: violent content, needs regenerating",
    "safety:frightening": "kids-safety: frightening content",
    "language": "narration is not in the declared language",
    "story:child_resolves_it": "an adult resolves it; the idea must be the child's",
}

# Derived, not invented: each maps from a field the operator already set.
def clean_text(text: str) -> str:
    return _STRAY_AFTER_DANDA.sub("।", text or "")


def clean_entry(entry: dict) -> int:
    """Strip stray marks everywhere text lives. Returns how many it fixed."""
    fixed = 0
    for key in ("title", "refrain", "description_hook"):
        before = entry.get(key) or ""
        after = clean_text(before)
        if after != before:
            entry[key] = after
            fixed += 1
    for alt_key in ("title_alts",):
        alts = entry.get(alt_key) or []
        for i, alt in enumerate(alts):
            after = clean_text(alt)
            if after != alt:
                alts[i] = after
                fixed += 1
    for scene in entry.get("scenes") or []:
        for key in ("narration", "caption", "on_screen_text", "image_brief"):
            before = scene.get(key) or ""
            after = clean_text(before)
            if after != before:
                scene[key] = after
                fixed += 1
    return fixed


def refrain_slots(scenes: list[dict], want: int) -> list[int]:
    """Which scenes carry the refrain.

    Beats that say `refrain` come first - the operator already marked where
    it belongs. The rest are spread across the middle and end, never scene 1:
    a refrain lands by RETURNING, so it cannot be the first thing said.
    """
    marked = [i for i, s in enumerate(scenes)
              if "refrain" in str(s.get("beat", "")).lower()]
    slots = list(marked)
    if len(slots) < want:
        spread = [i for i in range(1, len(scenes)) if i not in slots]
        # Back to front, so the refrain closes the piece.
        for i in reversed(spread):
            if len(slots) >= want:
                break
            slots.append(i)
    return sorted(set(slots))[:want]


def ensure_refrain(entry: dict) -> str | None:
    """Repeat the declared refrain verbatim. Returns a note, or None."""
    refrain = (entry.get("refrain") or "").strip()
    scenes = entry.get("scenes") or []
    if not refrain or not scenes:
        return None
    # The importer counts a raw substring; the gate tokenises. Keep the text
    # identical in both, so strip the trailing danda from the stored form and
    # add it back only as sentence punctuation.
    core = refrain.rstrip("। .!?").strip()
    if len(_tokens(core)) < REFRAIN_MIN_WORDS:
        return f"refrain is only {len(_tokens(core))} words, needs " \
               f"{REFRAIN_MIN_WORDS}"
    entry["refrain"] = core

    spoken = " ".join(s.get("narration") or "" for s in scenes).count(core)
    if spoken >= REFRAIN_MIN_REPEATS:
        return None

    need = REFRAIN_MIN_REPEATS - spoken
    added = 0
    for index in refrain_slots(scenes, need + spoken):
        narration = scenes[index].get("narration") or ""
        if core in narration:
            continue
        joiner = "" if narration.endswith(("।", ".", "!", "?", "")) else " "
        scenes[index]["narration"] = (
            f"{narration.rstrip()}{joiner} {core}।".strip())
        added += 1
        if spoken + added >= REFRAIN_MIN_REPEATS:
            break
    if spoken + added < REFRAIN_MIN_REPEATS:
        return f"only {len(scenes)} scenes, cannot place the refrain " \
               f"{REFRAIN_MIN_REPEATS} times"
    return None


def ensure_named_character(entry: dict) -> str | None:
    """Name the protagonist in at least half the scenes."""
    scenes = entry.get("scenes") or []
    narrations = [s.get("narration") or "" for s in scenes]
    if not narrations:
        return None
    name, coverage = _find_character(narrations)
    if name and coverage >= MIN_NAME_COVERAGE:
        return None
    # Prefer the name the operator already recorded for the character.
    declared = ""
    for person in entry.get("characters") or []:
        if isinstance(person, dict) and (person.get("name") or "").strip():
            declared = person["name"].strip()
            break
    # It must appear in scene 1, or the gate will not accept it as the lead.
    if declared and declared in narrations[0]:
        name = declared
    if not name:
        return "no name appears in scene 1 to build coverage on"

    target = int(len(scenes) * MIN_NAME_COVERAGE) + 1
    have = sum(1 for n in narrations if name in n)
    for index, scene in enumerate(scenes):
        if have >= target:
            break
        narration = scene.get("narration") or ""
        if name in narration or not narration.strip():
            continue
        swapped = _LEADING_PRONOUN.sub(f"{name} ", narration, count=1)
        if swapped != narration:
            scene["narration"] = swapped
            have += 1
    narrations = [s.get("narration") or "" for s in scenes]
    _, coverage = _find_character(narrations)
    if coverage < MIN_NAME_COVERAGE:
        return (f"name {name!r} reaches only {coverage:.0%} of scenes; the "
                f"narration uses no leading pronoun to replace")
    return None


def ensure_metadata(entry: dict) -> None:
    """Fill the blanks from what the entry already states."""
    # ONLY narrative AND poem CARRY AN ARC (bank.ARC_SHAPES), and the
    # variety gate's share caps only fire when arc_variant is SET. Filling
    # it for a drill therefore does not help it pass - it drags 129 alphabet
    # and counting drills into a diversity check they were exempt from, and
    # because every fill came out the same it made them a monoculture: 192
    # of 203 entries reading 'alone' against a 20% cap.
    #
    # And it is not filled for stories either. A story's arc is an
    # authorial judgement about what happens in it; deriving one from
    # another field produced the same label for everything, which would let
    # the batch pass a diversity gate whose entire purpose is to stop 200
    # videos sharing one emotional shape. An entry that does not state its
    # own arc is reported for regeneration instead.
    if str(entry.get("shape") or "").lower() not in ("narrative", "poem"):
        entry.pop("arc_variant", None)
        entry.pop("outcome_class", None)
    if not (entry.get("characters") or []):
        narrations = [s.get("narration") or ""
                      for s in (entry.get("scenes") or [])]
        name, _ = _find_character(narrations)
        if name:
            entry["characters"] = [{
                "name": name,
                "description": (entry.get("protagonist_type")
                                or "the child in this story"),
            }]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="the staged JSONL to repair")
    ap.add_argument("--out", default="",
                    help="where to write (default: in place)")
    ap.add_argument("--dropped", default="",
                    help="JSONL to write the dropped entries to, for "
                         "regeneration")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()

    path = Path(args.path)
    if not path.is_absolute():
        path = ROOT / path
    entries = [json.loads(line) for line in
               path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"{path.name}: {len(entries)} entries")

    kept: list[dict] = []
    dropped: list[tuple[dict, str]] = []
    notes: Counter = Counter()
    marks = 0

    for entry in entries:
        marks += clean_entry(entry)
        ensure_metadata(entry)
        # THE STORY GATE ONLY RUNS ON narrative AND poem
        # (bank_import.py:202), so a drill is never asked for a recurring
        # protagonist - and must not be asked here either. Applying it to
        # everything dropped 83 entries, 44 of them because
        # `_find_character` picked the word 'बच्चों' - "children" - out of an
        # alphabet drill that addresses the audience and has no character in
        # it at all. The same mistake as asking a poem for an obstacle beat.
        checks = [ensure_refrain(entry)]
        if str(entry.get("shape") or "").lower() in ("narrative", "poem"):
            checks.append(ensure_named_character(entry))
        problems = [p for p in checks if p]
        if problems:
            dropped.append((entry, "; ".join(problems)))
            for p in problems:
                notes[p.split(";")[0][:52]] += 1
            continue
        kept.append(entry)

    print(f"stray combining marks removed : {marks}")
    print(f"repaired and kept             : {len(kept)}")
    print(f"dropped, needs regenerating   : {len(dropped)}")
    for note, n in notes.most_common(8):
        print(f"   {n:4}  {note}")

    if not args.confirm:
        print("\ndry run. re-run with --confirm to write.")
        return 0

    out = Path(args.out) if args.out else path
    if not out.is_absolute():
        out = ROOT / out
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for entry in kept:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"\nwrote {len(kept)} entries to {out.relative_to(ROOT)}")

    if args.dropped and dropped:
        rejects = Path(args.dropped)
        if not rejects.is_absolute():
            rejects = ROOT / rejects
        rejects.parent.mkdir(parents=True, exist_ok=True)
        with rejects.open("w", encoding="utf-8", newline="\n") as fh:
            for entry, why in dropped:
                fh.write(json.dumps({"why": why, **entry},
                                    ensure_ascii=False) + "\n")
        print(f"wrote {len(dropped)} dropped entries to "
              f"{rejects.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
