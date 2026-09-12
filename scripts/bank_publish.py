#!/usr/bin/env python
"""Fold the server's autofilled entries into git, on the server.

    python scripts/bank_publish.py            # show what would happen
    python scripts/bank_publish.py --confirm

Runs on the box. The autofill stages into `workspace/bank-gen`, outside the
checkout, because `git pull --ff-only` refuses a dirty tree. This merges
those into `banks/gen`, rebuilds deterministically, and commits the result
so the entries exist somewhere other than one disk.

WHY A REBASE AND NOT A MERGE. Both this machine and a developer write to
`banks/`, so the histories diverge. A rebase keeps one line of commits and,
because the rebuild is deterministic, the same staged input produces the
same delivery files on either side - so the usual outcome is no diff at all
rather than a conflict. When a rebase does fail, this aborts it and leaves
everything staged: the entries are still in `workspace/bank-gen` and in the
database, so a failed publish costs nothing but a night.

NEVER FORCES. A deploy key with write access can destroy history; the point
of this script is that it cannot.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/opt/autotube")
GEN = ROOT / "banks" / "gen"
STAGE = ROOT / "workspace" / "bank-gen"
SSH = ("ssh -i /opt/autotube/.ssh/id_ed25519 "
       "-o UserKnownHostsFile=/opt/autotube/.ssh/known_hosts "
       "-o StrictHostKeyChecking=yes")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True, text=True, check=check,
        env={"GIT_SSH_COMMAND": SSH, "HOME": str(ROOT),
             "PATH": "/usr/bin:/bin", "GIT_TERMINAL_PROMPT": "0"})


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def _key(raw: str) -> str | None:
    """Identity of a staged line, so a merge cannot double an entry."""
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
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    staged = sorted(STAGE.glob("*.jsonl"))
    total = sum(len(_lines(p)) for p in staged)
    print(f"{len(staged)} staged batches, {total} entries in {STAGE}")
    if not staged:
        return 0

    added = 0
    plan: list[tuple[Path, list[str]]] = []
    for path in staged:
        target = GEN / path.name
        have = {_key(l) for l in _lines(target)}
        fresh = [l for l in _lines(path) if _key(l) and _key(l) not in have]
        if fresh:
            plan.append((target, fresh))
            added += len(fresh)
            print(f"  +{len(fresh):3} -> banks/gen/{path.name}")
    if not added:
        print("nothing new to publish")
        return 0
    if not args.confirm:
        print(f"\n{added} new; pass --confirm")
        return 0

    GEN.mkdir(parents=True, exist_ok=True)
    for target, fresh in plan:
        with target.open("a", encoding="utf-8") as fh:
            for line in fresh:
                fh.write(line + "\n")

    print("\nrebuilding:")
    rebuilt = subprocess.run(
        [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/bank_rebuild.py"),
         "--confirm"], cwd=str(ROOT))
    if rebuilt.returncode:
        print("rebuild failed; not committing")
        return rebuilt.returncode

    git("add", "-A", "banks")
    if not git("diff", "--cached", "--quiet", check=False).returncode == 1:
        print("nothing staged in git after the rebuild")
        return 0

    files = len(list((ROOT / "banks").glob("*.jsonl")))
    entries = sum(len(_lines(p)) for p in (ROOT / "banks").glob("*.jsonl"))
    git("-c", "user.name=Jaadugar Oracle",
        "-c", "user.email=autotube@ck17nov.duckdns.org",
        "commit", "-q", "-m",
        f"Bank autofill: {files} topic files, {entries} entries\n\n"
        f"Written on the server by the free-tier autofill and gated by the "
        f"same import checks as any other entry. Rebuilt and verified "
        f"against a fresh database before committing.\n")

    print("\nrebasing onto origin/main:")
    git("fetch", "origin", "main")
    rebase = git("rebase", "origin/main", check=False)
    if rebase.returncode:
        # Leave nothing half-applied. The entries are still in the staging
        # directory and in the database, so a failed publish costs a night,
        # not a batch.
        git("rebase", "--abort", check=False)
        print("rebase failed, aborted - will retry tomorrow:")
        print((rebase.stderr or rebase.stdout).strip()[:400])
        return 1

    # Never --force. A deploy key with write access can destroy history.
    push = git("push", "origin", "HEAD:main", check=False)
    if push.returncode:
        print("push failed:")
        print((push.stderr or push.stdout).strip()[:400])
        return 1
    print(f"pushed {added} new entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
