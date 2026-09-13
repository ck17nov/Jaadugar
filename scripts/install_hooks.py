#!/usr/bin/env python
"""Install a pre-commit hook that refuses to commit a credential.

    python scripts/install_hooks.py

WHY A HOOK AND NOT MORE .gitignore PATTERNS. An audit of this repo tried
saving a private key under plausible names and found that `oracle_private`
and `deploy_id` were both stageable - no extension, nothing to match on. No
filename pattern can cover a file named after nothing in particular, and
this repository is PUBLIC, so the failure mode is a published key rather
than a bad diff.

So the hook reads the staged CONTENT instead, which does not care what the
file is called. It is a backstop, not the primary defence: `.gitignore` and
`.secrets/` still do the everyday work.

Hooks live in `.git/hooks/`, which git does not track, so a fresh clone has
no protection until this is run. That is why this installer is a tracked
file and the hook is not.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HOOK = r'''#!/usr/bin/env python
"""Refuse a commit that would publish a credential. Installed by
scripts/install_hooks.py - edit that, not this."""
import re
import subprocess
import sys

# Shapes, not names. A file called `deploy_id` with an OPENSSH header in it
# is a private key whatever the extension says.
PATTERNS = (
    ("private key", re.compile(
        r"-----BEGIN (?:OPENSSH|RSA|DSA|EC|PGP) PRIVATE KEY-----")),
    ("Google API key", re.compile(r"AIza[A-Za-z0-9_\-]{30,}")),
    ("Google OAuth client secret", re.compile(r"GOCSPX-[A-Za-z0-9_\-]{10,}")),
    ("Google OAuth refresh token", re.compile(r"1//[A-Za-z0-9_\-]{30,}")),
    ("Groq API key", re.compile(r"gsk_[A-Za-z0-9]{30,}")),
    ("OpenAI API key", re.compile(r"sk-[A-Za-z0-9]{30,}")),
    ("OAuth access token", re.compile(r"ya29\.[A-Za-z0-9_\-]{20,}")),
)

# Files whose whole purpose is to describe these shapes.
ALLOW = ("scripts/install_hooks.py", "engine/core/logging.py",
         "tests/test_secrets_hygiene.py", ".gitignore", ".env.example")


def staged():
    out = subprocess.run(["git", "diff", "--cached", "--name-only",
                          "--diff-filter=ACM"],
                         capture_output=True, text=True).stdout
    return [p for p in out.splitlines() if p.strip()]


def main():
    bad = []
    for path in staged():
        if path.replace("\\", "/") in ALLOW:
            continue
        blob = subprocess.run(["git", "show", f":{path}"],
                              capture_output=True).stdout
        try:
            text = blob.decode("utf-8", errors="replace")
        except Exception:
            continue
        for label, pattern in PATTERNS:
            m = pattern.search(text)
            if m:
                line = text[:m.start()].count("\n") + 1
                bad.append((path, line, label))
                break
    if bad:
        sys.stderr.write("\nCOMMIT REFUSED - this looks like a credential, "
                         "and this repository is public.\n\n")
        for path, line, label in bad:
            sys.stderr.write(f"  {path}:{line}  {label}\n")
        sys.stderr.write(
            "\nPut it in .secrets/ (gitignored) and reference it from there.\n"
            "If this is genuinely a false positive: git commit --no-verify\n\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def main() -> int:
    hooks = subprocess.run(["git", "rev-parse", "--git-path", "hooks"],
                           cwd=ROOT, capture_output=True, text=True)
    if hooks.returncode != 0:
        print("not a git repository", file=sys.stderr)
        return 1
    hook_dir = (ROOT / hooks.stdout.strip()).resolve()
    hook_dir.mkdir(parents=True, exist_ok=True)
    target = hook_dir / "pre-commit"
    target.write_text(HOOK, encoding="utf-8", newline="\n")
    os.chmod(target, os.stat(target).st_mode | stat.S_IEXEC | stat.S_IXGRP)
    print(f"installed {target}")

    # Prove it works rather than asserting it does.
    probe = ROOT / ".hook-probe"
    probe.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1r\n",
                     encoding="utf-8")
    try:
        subprocess.run(["git", "add", "-f", str(probe)], cwd=ROOT,
                       capture_output=True)
        result = subprocess.run([sys.executable, str(target)], cwd=ROOT,
                                capture_output=True, text=True)
        subprocess.run(["git", "reset", "-q", "HEAD", str(probe)], cwd=ROOT,
                       capture_output=True)
        if result.returncode == 0:
            print("SELF-TEST FAILED: the hook allowed a private key through")
            return 1
        print("self-test: a staged private key is refused")
    finally:
        probe.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
