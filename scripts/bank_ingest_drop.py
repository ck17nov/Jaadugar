#!/usr/bin/env python
"""Turn a directory of operator-supplied script files into one staged JSONL.

    python scripts/bank_ingest_drop.py "<dir>" --out banks/gen/kids-<name>.jsonl
    python scripts/bank_ingest_drop.py "<dir>" --out ... --confirm

Two jobs, because every drop so far has needed both.

MERGING MULTI-PART STORIES
--------------------------
A long-form script does not fit in one generation response, so it arrives
split across files carrying `story_id` (1-5) and `part_flag`
(PART_1_OF_3 ...). `story_id` is only unique WITHIN a batch of five, so
grouping by it across a 134-file drop would weld unrelated stories together.

The TITLE is the join key. It is repeated on every part - verified across
both drops: 89 of 89 and 134 of 134 parts carry one - and it is what the
operator actually wrote. `story_id` is used only to order parts when a title
somehow appears twice, and `part_flag` gives the sequence.

Metadata comes from part 1; `scenes` are concatenated in part order; the two
tracking keys are dropped. A story missing any part is REPORTED AND SKIPPED,
never emitted half-length: a 55-scene story published as if complete is worse
than one that never ships.

REPAIRING GENERATION GLITCHES
-----------------------------
Six of 89 files in one drop would not parse. Every fault was punctuation
damage around a known schema key, or a character injected outside a string -
never anything that required deciding what the operator meant:

    "narration: "text"        the key's closing quote is missing
    "narration", "text"       the colon became a comma
    "narrating": "text"       the key name itself is mistyped
    "...।्"                   a virama after a sentence-ending danda,
                              which carries no meaning in Devanagari
    },िक्षे {                  Devanagari injected between two objects

Only these five shapes are repaired, only for keys the schema names, and
every edit is counted and printed. NARRATION AND CAPTION TEXT IS NEVER
TOUCHED beyond stripping that trailing virama - the bank holds what the
operator wrote, and a model rewriting Hindi it cannot read is how a script
bank stops being operator-supplied.

Nothing is written without `--confirm`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]

# Keys the entry schema names. A delimiter is only repaired in front of one
# of these, so a mangled key this script does not know stays a hard error.
SCHEMA_KEYS = (
    "group", "topic", "shape", "volatility", "language", "video_format",
    "made_for_kids", "title", "title_alts", "refrain", "description_hook",
    "arc_variant", "outcome_class", "turn_kind", "problem_domain", "setting",
    "protagonist_type", "emotional_register", "characters", "scenes", "beat",
    "narration", "caption", "image_brief", "on_screen_text", "name",
    "description", "story_id", "part_flag", "tags", "hook_type",
)

PART_ORDER = {"PART_1_OF_3": 1, "PART_2_OF_3": 2, "PART_3_OF_3": 3}


def repair(raw: str) -> tuple[str, Counter]:
    """Fix only unambiguous punctuation damage. Returns (text, what changed)."""
    edits: Counter = Counter()

    # A mistyped key name. Not a delimiter problem, but the same class: the
    # schema has no "narrating", so there is nothing to decide.
    fixed, n = re.subn(r'"narrating"(\s*:)', r'"narration"\1', raw)
    if n:
        edits["key_narrating_to_narration"] = n
    raw = fixed

    # KEY POSITION ONLY, and this guard is not theoretical: without it the
    # rule below rewrote `{"beat": "refrain", "narration": ...}` - valid
    # JSON - into `{"beat": "refrain": "narration": ...}`, because "refrain"
    # is both a schema key and a legitimate VALUE of "beat". Two files that
    # parsed fine were broken by the repair meant to save them.
    #
    # A key can only follow `{` or `,`. A value follows `:`.
    for key in SCHEMA_KEYS:
        # "key: "value"  ->  "key": "value"
        raw, n = re.subn(rf'(?<=[\{{,])\s*"{key}:\s*"', f'"{key}": "', raw)
        if n:
            edits[f"missing_quote:{key}"] += n
        # "key", "value"  ->  "key": "value"
        raw, n = re.subn(rf'(?<=[\{{,])\s*"{key}",\s*"', f'"{key}": "', raw)
        if n:
            edits[f"comma_for_colon:{key}"] += n

    # A virama immediately after a sentence-ending danda. In Devanagari a
    # virama suppresses the inherent vowel of the consonant BEFORE it; after
    # a full stop there is no consonant, so it cannot be meaningful text.
    raw, n = re.subn("।्", "।", raw)
    if n:
        edits["stray_virama_after_danda"] = n

    # Devanagari sitting between two JSON objects - outside any string, so it
    # cannot be content.
    raw, n = re.subn(r'(\}\s*,)[ऀ-ॿ]+(\s*\{)', r'\1\2', raw)
    if n:
        edits["devanagari_between_objects"] = n

    return raw, edits


def strip_wrappers(text: str) -> str:
    """Remove chat-transcript decoration from around the JSON.

    Drops are pasted out of a chat window, so a file can arrive wrapped in a
    ```json fence, or end with one, or carry a `[cite: 1]` marker the UI
    added. None of it is JSON and none of it is the operator's content. Five
    files in one drop decoded ten objects each and then died on a trailing
    fence - and the objects already found were being thrown away with it.
    """
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            continue
        if re.fullmatch(r"\[cite:[^\]]*\]", stripped):
            continue
        out.append(re.sub(r"\[cite:[^\]]*\]", "", line))
    return "\n".join(out).strip()


def scan_objects(text: str) -> tuple[list[dict], str | None]:
    """Every JSON object in a blob, whatever the formatting.

    Drops have arrived as one object per file, one per LINE, and
    pretty-printed across 40 lines - sometimes two of those shapes in the
    same directory. Splitting on newlines handled the first two and silently
    destroyed the third, so this scans with `raw_decode` instead and does not
    care about whitespace at all.

    Returns the objects found and the error that stopped the scan, if any.
    """
    decoder = json.JSONDecoder()
    found: list[dict] = []
    pos = 0
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            return found, None
        try:
            obj, pos = decoder.raw_decode(text, pos)
        except ValueError as exc:
            return found, str(exc)[:70]
        if isinstance(obj, dict):
            found.append(obj)
        elif isinstance(obj, list):
            found.extend(o for o in obj if isinstance(o, dict))


def load_objects(directory: Path) -> tuple[list[dict], dict]:
    """Every JSON object in the directory, repairing what is repairable."""
    objects: list[dict] = []
    report = {"files": 0, "repaired": 0, "unreadable": [],
              "edits": Counter()}
    for path in sorted(directory.glob("*.txt")) + sorted(directory.glob("*.jsonl")):
        report["files"] += 1
        text = strip_wrappers(path.read_text(encoding="utf-8"))
        if not text:
            continue
        found, err = scan_objects(text)
        if err is None:
            objects.extend(found)
            continue
        # Repair the WHOLE file and rescan, rather than the fragment that
        # failed: a missing delimiter early on swallows everything after it,
        # so the fragment boundary is not where the fault is.
        mended, edits = repair(text)
        found, err = scan_objects(mended)
        if err is not None:
            report["unreadable"].append((path.name, err))
            continue
        objects.extend(found)
        report["repaired"] += 1
        report["edits"].update(edits)
        print(f"  repaired {path.name}: "
              + ", ".join(f"{k}x{v}" for k, v in sorted(edits.items())))
    return objects, report


def merge_parts(objects: list[dict]) -> tuple[list[dict], dict]:
    """Join multi-part stories by title. Complete stories only."""
    singles = [o for o in objects if not o.get("part_flag")]
    parts = [o for o in objects if o.get("part_flag")]

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for obj in parts:
        title = str(obj.get("title") or "").strip()
        if not title:
            # Without a title there is no safe key: story_id repeats every
            # five stories, so guessing would weld unrelated scripts.
            groups[("__untitled__", id(obj))].append(obj)
            continue
        groups[(title, obj.get("language", ""))].append(obj)

    merged: list[dict] = []
    report = {"stories": 0, "incomplete": [], "untitled": 0,
              "deduped": 0}
    for key, found in groups.items():
        if key[0] == "__untitled__":
            report["untitled"] += 1
            continue
        # DEDUPLICATE PER PART, LATER DROP WINS.
        #
        # The second drop turned out to REGENERATE much of the first rather
        # than continue it: pooling them produced title groups holding six
        # parts - two complete copies of 1/2/3 - and treating "not exactly
        # three" as incomplete threw away 20 stories that were perfectly
        # whole. Directories are read in the order given, so keeping the last
        # object for each part_flag prefers the operator's newer take.
        by_flag: dict[str, dict] = {}
        for obj in found:
            by_flag[str(obj.get("part_flag"))] = obj
        if len(by_flag) != len(found):
            report["deduped"] += len(found) - len(by_flag)
        found = [by_flag[f] for f in sorted(by_flag, key=lambda x:
                                            PART_ORDER.get(x, 99))]
        flags = [str(o.get("part_flag")) for o in found]
        expected = sorted(PART_ORDER, key=PART_ORDER.get)
        if flags != expected:
            report["incomplete"].append((key[0][:44], flags))
            continue
        head = dict(found[0])
        scenes: list = []
        for part in found:
            scenes.extend(part.get("scenes") or [])
        head["scenes"] = scenes
        head.pop("story_id", None)
        head.pop("part_flag", None)
        merged.append(head)
        report["stories"] += 1
    return singles + merged, report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("directory", nargs="+",
                    help="the drop(s) to read. Several are pooled BEFORE "
                         "merging, because a story's parts can be split "
                         "across two drops - 10 stories in one drop were "
                         "missing parts that sat in the next folder.")
    ap.add_argument("--out", required=True,
                    help="staged JSONL to write, e.g. banks/gen/kids-x.jsonl")
    ap.add_argument("--confirm", action="store_true",
                    help="write the file; without it, report only")
    args = ap.parse_args()

    directories = [Path(d) for d in args.directory]
    for directory in directories:
        if not directory.is_dir():
            print(f"not a directory: {directory}")
            return 2

    objects: list[dict] = []
    rep = {"files": 0, "repaired": 0, "unreadable": [], "edits": Counter()}
    for directory in directories:
        print(f"reading {directory}")
        found, one = load_objects(directory)
        objects.extend(found)
        rep["files"] += one["files"]
        rep["repaired"] += one["repaired"]
        rep["unreadable"].extend(one["unreadable"])
        rep["edits"].update(one["edits"])
    print(f"\nfiles read     : {rep['files']}")
    print(f"objects parsed : {len(objects)}  ({rep['repaired']} needed repair)")
    if rep["edits"]:
        print("repairs        : "
              + ", ".join(f"{k}x{v}" for k, v in sorted(rep["edits"].items())))
    for name, err in rep["unreadable"]:
        print(f"  UNREADABLE {name}: {err}")

    entries, mrep = merge_parts(objects)
    print(f"\nmulti-part stories merged : {mrep['stories']}")
    if mrep["untitled"]:
        print(f"  parts with no title (cannot be grouped): {mrep['untitled']}")
    for title, flags in mrep["incomplete"]:
        print(f"  INCOMPLETE, skipped: {title} has {flags}")
    print(f"entries to stage          : {len(entries)}")

    scenes = Counter(len(e.get("scenes") or []) for e in entries)
    print(f"scene counts              : "
          + ", ".join(f"{k}:{v}" for k, v in sorted(scenes.items())))
    langs = Counter(e.get("language") for e in entries)
    fmts = Counter(e.get("video_format") for e in entries)
    print(f"languages / formats       : {dict(langs)} / {dict(fmts)}")

    if not args.confirm:
        print("\ndry run. re-run with --confirm to write.")
        return 0

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"\nwrote {len(entries)} line(s) to {out.relative_to(ROOT)}")
    print("next: python scripts/bank_absorb_all.py   (imports through the gate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
