"""Test-session housekeeping.

WHY THIS EXISTS. The render tests run the real pipeline against the real
workspace, which is correct - they are testing ffmpeg output, and a fake
workspace would test a fake. But nothing removed what they produced, so
every full suite run left its renders behind. That is how
`workspace/jobs` reached 214 directories and 20 GB: one full run adds about
1.1 GB, and there were two weeks of them.

Deleting them by hand is not a fix. This removes what the SESSION created
and nothing else: the set of job directories present before collection is
recorded, and anything new is deleted afterwards. A pre-existing directory
is never touched, so running the suite cannot destroy a render someone
wanted to keep.

Deliberately NOT done here: pointing AUTOTUBE_WORKSPACE at a temp directory
for the whole session. Twenty test files call `load_config()`, and some read
cached prompts and font assets out of the workspace; redirecting it wholesale
would change what those tests exercise, which is a bigger change than the
problem justifies. The rule that DOES apply globally is the narrower one -
never mutate the live BANK from a test - and the tests that touch it already
set their own temp workspace.
"""
from __future__ import annotations

import os
import shutil
import time

import pytest

from engine.core.config import load_config


def _job_dirs(jobs) -> set[str]:
    if not jobs.exists():
        return set()
    try:
        return set(os.listdir(jobs))
    except OSError:
        return set()


@pytest.fixture(scope="session", autouse=True)
def _clean_up_renders_this_session_made():
    """Delete job directories created during this run, keep the rest.

    The workspace path is resolved ONCE, at session start, and reused.
    Re-resolving it at teardown is what broke the first version of this
    fixture: several tests set AUTOTUBE_WORKSPACE to a temp directory and do
    not unset it, so by the end of the session `load_config()` answers with
    whichever temp path a test left behind. The fixture then compared that
    empty directory against the real one, found nothing new, and deleted
    nothing - silently, because the early return skipped the print too.
    """
    jobs = load_config().workspace / "jobs"
    before = _job_dirs(jobs)
    started = time.time()
    yield
    after = _job_dirs(jobs)

    # Two signals, because the set difference alone has a one-second race:
    # a test that creates its job directory in the same second as the
    # snapshot above gets listed in `before` and survives. That is exactly
    # what happened - `20260913-223935_science_...` was created at 22:39:35,
    # the same second the session started, and outlived the cleanup.
    #
    # A job directory is named `<YYYYMMDD-HHMMSS>_<slug>_<hash>`, so its own
    # name says when it was made. Parsing that is precise and cannot misfire
    # on a directory that predates the run, which a plain mtime check could:
    # a test appending to an older directory would update its mtime without
    # having created it.
    created = set(after - before)
    for name in after:
        stamp = name.split("_", 1)[0]
        try:
            made = time.mktime(time.strptime(stamp, "%Y%m%d-%H%M%S"))
        except ValueError:
            continue                # not a job directory name; leave it
        if made >= started - 2.0:
            created.add(name)
    created = sorted(created)
    if not created:
        return
    freed = 0
    for name in created:
        path = jobs / name
        if not path.exists():
            continue
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    freed += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        shutil.rmtree(path, ignore_errors=True)
    # Printed, not silent: a suite that quietly deletes gigabytes should say
    # so, and the number is how anyone would notice this growing again.
    print(f"\n[conftest] removed {len(created)} job directory/ies created by "
          f"this run, freeing {freed / 1024 ** 3:.2f} GB")
