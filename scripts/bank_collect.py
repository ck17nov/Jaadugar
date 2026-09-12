#!/usr/bin/env python
"""Bring the server's autofilled entries back into git.

    python scripts/bank_collect.py            # show what is there
    python scripts/bank_collect.py --confirm  # fetch, merge, rebuild

The autofill runs on the server and stages into
`/opt/autotube/workspace/bank-gen`, outside the git checkout - because
`git pull --ff-only` refuses a dirty working tree, so writing into
`banks/gen` there would break every future deploy.

That leaves those entries on one machine. The server cannot push (it has no
GitHub credentials, and putting a token on a box that already holds a
YouTube refresh token is a decision for its owner, not a convenience). So
the trip back into git is made from here: fetch, merge into `banks/gen`,
rebuild, and the result is committed and deployed like any other change.

Duplicates are not a problem. An entry_id is a hash of its narration, so
re-importing one that is already banked replaces it rather than doubling
it - but the merge de-duplicates anyway, to keep the staged files honest
about their own size.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "banks" / "gen"

HOST = "ubuntu@80.225.220.108"
KEY = "C:/Users/chand/Downloads/ssh-key-2026-09-05.key"
REMOTE = "/opt/autotube/workspace/bank-gen"


def _ssh(command: str) -> str:
    out = subprocess.run(
        ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", HOST, command],
        capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[:300] or "ssh failed")
    return out.stdout


def _fetch(into: Path) -> list[Path]:
    into.mkdir(parents=True, exist_ok=True)
    listing = _ssh(f"ls {REMOTE}/*.jsonl 2>/dev/null || true").split()
    if not listing:
        return []
    subprocess.run(
        ["scp", "-i", KEY, "-o", "StrictHostKeyChecking=no",
         f"{HOST}:{REMOTE}/*.jsonl", str(into)],
        capture_output=True, text=True, check=False)
    return sorted(into.glob("*.jsonl"))


def _lines(path: Path) -> list[str]:
    return [ln for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def _key(raw: str) -> str | None:
    """A stable identity for a staged line, so a merge cannot double it."""
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    scenes = obj.get("scenes") or []
    return json.dumps([obj.get("group"), obj.get("topic"),
                       obj.get("language"), obj.get("video_format"),
                       [s.get("narration") for s in scenes]],
                      ensure_ascii=False, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true",
                        help="merge into banks/gen and rebuild")
    args = parser.parse_args()

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        fetched = _fetch(Path(tmp))
        if not fetched:
            print(f"nothing staged on the server ({REMOTE} is empty)")
            return 0
        total = sum(len(_lines(p)) for p in fetched)
        print(f"{len(fetched)} batches on the server, {total} entries")

        if not args.confirm:
            for path in fetched:
                print(f"  {path.name}: {len(_lines(path))}")
            print("\npass --confirm to merge and rebuild")
            return 0

        added = 0
        for path in fetched:
            target = GEN / path.name
            have = {_key(l) for l in _lines(target)} if target.exists() \
                else set()
            fresh = [l for l in _lines(path)
                     if _key(l) and _key(l) not in have]
            if not fresh:
                continue
            GEN.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                for line in fresh:
                    fh.write(line + "\n")
            added += len(fresh)
            print(f"  +{len(fresh):3} -> banks/gen/{path.name}")

    print(f"\nmerged {added} new entries")
    if not added:
        return 0

    print("\nrebuilding:")
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "bank_rebuild.py"),
         "--confirm"], cwd=str(ROOT)).returncode


if __name__ == "__main__":
    sys.exit(main())
