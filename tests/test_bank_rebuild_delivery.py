"""The delivery copy must have exactly one line per stored row.

`banks/*.jsonl` is what a deploy copies to the server, and the server
imports it into a fresh database. If the file count and the row count
disagree, the app's "reviewed scripts ready" number is wrong and some lines
ship as entries that can never be claimed - which is the whole reason
`bank_rebuild.py` verifies rather than trusting an incremental absorb.

Found for real: an outside batch of 334 kids entries contained 15 repeated
scripts - 14 identical "Can you say ..." speaking drills and one Hindi pair
where two different spelling words (टब and कल) were given the same
narration. `entry_id` is a content hash, so all 15 pairs collapsed to one
row each on import. The delivery writer filtered lines by MEMBERSHIP in the
set of stored ids, so both copies of every pair survived: 1020 lines
against 1005 rows.
"""
import json

from scripts.bank_rebuild import _id_of, _lines_of


def _entry(narration: str, title: str) -> str:
    return json.dumps({
        "group": "kids", "language": "en", "topic": "kids alphabet learning",
        "shape": "drill", "video_format": "SHORT", "made_for_kids": True,
        "title": title,
        "scenes": [{"narration": narration, "beat": "open"}],
    }, ensure_ascii=False)


def _deliver(lines: list[str], landed: set[str]) -> list[str]:
    """The delivery filter, as `bank_rebuild` applies it."""
    seen: set[str] = set()
    keep = []
    for raw in lines:
        entry_id = _id_of(raw)
        if entry_id in landed and entry_id not in seen:
            seen.add(entry_id)
            keep.append(raw)
    return keep


def test_two_staged_lines_with_one_content_hash_deliver_one_line(tmp_path):
    """The real shape of the bug: same narration, different title."""
    same = "Can you say can I have some water please."
    path = tmp_path / "kids-en-short-kids-alphabet-learning.jsonl"
    path.write_text("\n".join([_entry(same, "first"),
                               _entry(same, "second - a repeat"),
                               _entry("B is for a big blue ball.", "other")])
                    + "\n", encoding="utf-8")

    lines = _lines_of(path)
    assert len(lines) == 3
    ids = {_id_of(raw) for raw in lines}
    assert len(ids) == 2, "the two identical narrations must hash alike"

    # The database stored one row per id; the files must say the same.
    kept = _deliver(lines, ids)
    assert len(kept) == len(ids) == 2
    assert json.loads(kept[0])["title"] == "first", "first occurrence wins"


def test_a_line_that_did_not_land_is_not_delivered(tmp_path):
    """The filter's original job still works - refusals must not ship."""
    path = tmp_path / "kids-en-short-kids-alphabet-learning.jsonl"
    path.write_text("\n".join([_entry("A is for apple.", "kept"),
                               _entry("B is for ball.", "refused")]) + "\n",
                    encoding="utf-8")
    lines = _lines_of(path)
    kept = _deliver(lines, {_id_of(lines[0])})
    assert len(kept) == 1
    assert json.loads(kept[0])["title"] == "kept"


def test_the_live_bank_has_one_line_per_row():
    """The measurement, on the real delivery copy: 1020 vs 1005 before."""
    import hashlib
    from collections import Counter
    from pathlib import Path

    banks = Path("banks")
    if not banks.is_dir():
        return
    ids: Counter[str] = Counter()
    for path in sorted(banks.glob("*.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            entry = json.loads(raw)
            narrations = "\n".join(scene.get("narration", "")
                                   for scene in entry.get("scenes") or [])
            digest = hashlib.blake2b(narrations.encode("utf-8"),
                                     digest_size=16).hexdigest()
            ids[entry.get("entry_id")
                or f"{entry.get('group') or 'x'}-{entry.get('language')}-"
                   f"{digest[:10]}"] += 1
    repeated = {key: n for key, n in ids.items() if n > 1}
    assert not repeated, (
        f"{sum(n - 1 for n in repeated.values())} duplicate line(s) across "
        f"{len(repeated)} entry_id(s): {list(repeated)[:5]}")
