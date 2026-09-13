#!/usr/bin/env python
"""Deploy the current commit to the Oracle box, and rebuild its bank.

    python scripts/deploy.py            # show what would happen
    python scripts/deploy.py --confirm

WHY THIS EXISTS. The deploy used to be a remembered `ssh -i` one-liner
pointing at a key in Downloads. Downloads got tidied, the key moved, and
the deploy stopped working mid-session with the bank already committed -
the code was safe on GitHub and the server simply sat three commits behind,
silently, because nothing on the box pulls by itself.

So the credential now lives in `.secrets/` INSIDE the repo, and the path is
wired here rather than typed each time. `.secrets/` is gitignored as a
DIRECTORY, which is what protects the files in it - git does not descend
into an ignored directory, so the `*.key` and `*.pem` rules alongside it
never even get consulted for these files. Those two exist to catch a key
dropped anywhere ELSE in the tree, which is the mistake more likely to
happen in a hurry.

The server pulls; it never pushes. A read-only deploy key is all it needs -
nothing on that box generates content any more.
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / ".secrets"

# THE HOST IS NOT IN THIS FILE, and that is deliberate: this repository is
# PUBLIC. Hard-coding `ubuntu@<ip>` here would publish the production box's
# address, its login user and - together with the commands below - its whole
# deploy topology, for a machine that holds a YouTube refresh token. The
# committed docs under deploy/oracle/ have always used placeholder domains
# for the same reason; this keeps that promise.
#
# Set ORACLE_SSH_HOST in .env (gitignored) as user@host-or-ip.
REMOTE = "/opt/autotube"


def host() -> str:
    """user@host for the box, from the environment or .env.

    Reads .env directly rather than importing the engine: a deploy script
    that cannot run until the application imports cleanly is a deploy
    script that cannot fix a broken deploy.
    """
    value = os.environ.get("ORACLE_SSH_HOST", "").strip()
    env = ROOT / ".env"
    if not value and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ORACLE_SSH_HOST=") and not line.startswith("#"):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not value:
        raise SystemExit(
            "ORACLE_SSH_HOST is not set.\n\n"
            "Add it to .env (which is gitignored), as user@host:\n"
            "    ORACLE_SSH_HOST=ubuntu@203.0.113.10\n\n"
            "It is kept out of the code because this repository is public.")
    return value

# Where a key may live, most-preferred first. The in-repo copy wins so that
# clearing Downloads cannot break a deploy; the others are fallbacks for a
# machine that has not copied it in yet.
KEY_CANDIDATES = (
    SECRETS / "oracle.key",
    Path.home() / ".ssh" / "oracle.key",
    Path.home() / ".ssh" / "id_ed25519",
)


def find_key() -> Path:
    """The first key that exists AND can actually be opened.

    Readability is checked, not assumed. The key that broke the deploy was
    present the whole time - its ACL granted read to an orphaned SID, so
    every tool reported "Permission denied (publickey)" and none of them
    said the file could not be opened.
    """
    tried = []
    for path in KEY_CANDIDATES:
        if not path.exists():
            tried.append(f"{path} (missing)")
            continue
        try:
            with path.open("rb") as fh:
                fh.read(1)
        except OSError as exc:
            tried.append(f"{path} (unreadable: {exc.strerror})")
            continue
        return path
    raise SystemExit(
        "no usable SSH key. Tried, in order:\n  "
        + "\n  ".join(tried)
        + f"\n\nPut the Oracle private key at {KEY_CANDIDATES[0]} - that "
          f"path is gitignored and is the one this script prefers."
    )


def staged_key(key: Path) -> Path:
    """A 0600 copy in a temp dir, because OpenSSH refuses a loose key.

    Windows checkouts do not carry POSIX modes, so a key copied out of the
    repo is world-readable as far as ssh is concerned and it exits rather
    than using it.
    """
    tmp = Path(tempfile.mkdtemp(prefix="deploy-key-"))
    dst = tmp / "key"
    shutil.copyfile(key, dst)
    os.chmod(dst, stat.S_IRUSR | stat.S_IWUSR)
    return dst


def ssh(key: Path, command: str, *, check: bool = True) -> str:
    result = subprocess.run(
        ["ssh", "-i", str(key), "-o", "BatchMode=yes",
         "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=20",
         host(), command],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise SystemExit(f"ssh failed ({result.returncode}):\n"
                         f"{result.stderr.strip()}")
    return (result.stdout or "").strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--confirm", action="store_true",
                    help="actually deploy; without it, only reports")
    ap.add_argument("--skip-rebuild", action="store_true",
                    help="pull and restart, but do not rebuild the bank")
    args = ap.parse_args()

    key = find_key()
    print(f"key    : {key}")
    staged = staged_key(key)
    try:
        return _deploy(args, key, staged)
    finally:
        # ALWAYS, on every path. This used to be a single rmtree at the end
        # of the happy path, so any ssh failure - a wrong host key, a
        # timeout, a dirty tree on the box - left a plaintext copy of the
        # private key sitting in the system temp directory until reboot.
        shutil.rmtree(staged.parent, ignore_errors=True)


def _deploy(args, key: Path, staged: Path) -> int:

    local = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=ROOT, capture_output=True, text=True)
    local_head = local.stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    print(f"local  : {local_head}"
          + ("  (UNCOMMITTED CHANGES - they will not deploy)" if dirty
             else ""))

    remote_head = ssh(staged, f"cd {REMOTE} && git rev-parse --short HEAD")
    print(f"remote : {remote_head}")

    if remote_head == local_head and not args.confirm:
        print("\nalready in sync; nothing to do")
        return 0
    if not args.confirm:
        print("\nwould pull, rebuild the bank, and restart autotube.")
        print("re-run with --confirm")
        return 0

    print("\npulling:")
    print(ssh(staged, f"cd {REMOTE} && sudo -u autotube git pull --ff-only"))
    now = ssh(staged, f"cd {REMOTE} && git rev-parse --short HEAD")
    print(f"remote now: {now}")

    if not args.skip_rebuild:
        print("\nrebuilding the bank (several minutes):")
        # --no-promote: banks/ there IS the checkout, and rewriting it would
        # dirty the tree so the next --ff-only pull refuses.
        out = ssh(staged, f"cd {REMOTE} && sudo -u autotube .venv/bin/python "
                          f"scripts/bank_rebuild.py --confirm --no-promote "
                          f"2>&1 | tail -6")
        print(out)

    print("\nrestarting:")
    ssh(staged, "sudo systemctl restart autotube")
    print("autotube:", ssh(staged, "systemctl is-active autotube",
                           check=False))
    print("caddy   :", ssh(staged, "systemctl is-active caddy", check=False))
    print("autofill:", ssh(staged, "systemctl is-enabled autotube-autofill "
                                   "2>&1 || true", check=False),
          "(not-found is correct - it was removed)")
    tree = ssh(staged, f"cd {REMOTE} && sudo -u autotube git status "
                       f"--porcelain | grep -v '^??' | wc -l")
    print(f"tracked files modified on the box: {tree}"
          + ("  <- should be 0" if tree != "0" else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
