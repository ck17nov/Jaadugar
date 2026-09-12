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


# ==========================================================================
class TestNoPromoteWritesNothing:
    """`--no-promote` exists for the server, where `banks/` IS the git
    checkout - so dirtying it makes the next `git pull --ff-only` fail and
    the deploy stops working.

    It did exactly that. `delivered` was set to `staged` under the flag, and
    the prune loop rewrites and unlinks every path in `delivered`, so a
    server run deleted six files out of `banks/gen` and modified six more.
    It also pruned `workspace/bank-gen`, where an autofilled entry is the
    only copy in existence until `bank_publish.py` commits it.

    This test reads the source rather than running a rebuild, because a
    rebuild takes ten minutes and clears the live database. The invariant is
    structural: the flag must return before the loop that writes.
    """

    def _source(self) -> str:
        from pathlib import Path
        return Path("scripts/bank_rebuild.py").read_text(encoding="utf-8")

    def test_the_flag_returns_before_the_prune_loop(self):
        src = self._source()
        guard = src.index("if args.no_promote:")
        loop = src.index("# 4. Verify against a FRESH database")
        assert guard < loop, "the flag must be checked before the loop"
        between = src[guard:loop]
        assert "return 0" in between, (
            "the --no-promote branch must return before reaching the prune "
            "loop, not fall through into it")

    def test_delivered_is_never_the_staged_paths(self):
        """The specific bug: `delivered = staged` made the prune loop
        rewrite the checkout and the autofill's staging directory."""
        src = self._source()
        assert "delivered = (staged if args.no_promote" not in src
        assert "staged if args.no_promote" not in src

    def test_the_loop_only_ever_writes_inside_banks(self):
        """Every path the prune loop can touch comes from globbing
        `banks/`, so the worst it can do is rewrite a delivery file the
        developer is about to commit."""
        src = self._source()
        loop = src.index("# 4. Verify against a FRESH database")
        body = src[loop:src.index("# 5. Make the live database")]
        assert "path.write_text" in body, "the loop does still prune"
        assert "path.unlink" in body
        for assignment in ("delivered = sorted(BANKS.glob", ):
            assert assignment in body
        assert "workspace" not in body
