# AI handoff prompt

Copy the block below and send it as your **first message** to a new AI or
developer. It points them at the documentation in the right order and states
the constraints that are not obvious from the code.

The last instruction is the load-bearing one: it asks them to report back
three specific things, which is how you tell whether they read the documents
or improvised. Someone who actually read them will say the blocker is that
**no recurring automation exists**, and will name the kids-safety gates and
`engine/video/compose.py` as the handle-with-care areas.

---

```text
You are taking over an existing, working YouTube automation project called
Jaadugar. It publishes publicly and automatically to three real channels, and
most of its content is child-directed, so mistakes have real consequences.

Repository: https://github.com/ck17nov/Jaadugar

Read these before doing or suggesting anything, in this order:
  1. README.md
  2. PROJECT_OVERVIEW.md          <- the main handover doc, read it fully
  3. PROJECT_STRUCTURE.md
  4. DEPLOYMENT.md
  5. SCRIPT_BANK_GENERATION_PROMPT.md

Two things everyone gets wrong, so take them from the docs, not from the name:
  - "Oracle" here means Oracle Cloud COMPUTE (a free ARM VM). There is NO
    Oracle Database. The datastore is SQLite.
  - The scheduler is NOT on the server. It is Android WorkManager on my
    phone. The server only executes what is sent to it.

Constraints you must not break (PROJECT_OVERVIEW.md explains each):
  - Free tiers only. No paid dependency.
  - The script bank holds only scripts I supply. Do not add any background or
    scheduled generation.
  - Nothing may be recorded as human-reviewed. Machine review is stamped
    kind="machine" with the real model name.
  - Made-for-Kids is my declaration. Never infer it.
  - The repository is PUBLIC. No key, token or host address in any file.
  - The server pulls from git; it never pushes.

How to work with me:
  - Verify against the code before claiming anything works. Run
    `python -m pytest tests/ -q` (1,487 tests, ~5 min).
  - Tell me plainly when something is broken, missing or your mistake.
  - Note that `autotube` is not a command; use `python -m backend.cli`.

Start by reading those five files, then tell me: what the project does, what
is currently blocking it from producing videos, and what you would NOT touch
without asking.
```

---

## Before they can do much

| Thing | Why it matters |
|---|---|
| **A clone, not the web page** | The tests are what keep them honest, and those need Python 3.12 + ffmpeg locally. Reading GitHub in a browser is not enough. |
| **`python scripts/install_hooks.py`** | The pre-commit secret hook lives in `.git/hooks`, which git does not track. A fresh clone has no protection against committing a key. |
| **Your secrets** | `.env` and `.secrets/` are gitignored, so they cannot reach the server, publish, or call any AI provider until you supply them. This is correct — just be ready for "I can't deploy", which is the honest answer and not a fault. |

## If they need less than the whole project

- **Only writing scripts for the bank?** `SCRIPT_BANK_GENERATION_PROMPT.md`
  alone is enough — it is generated from the importer, so it cannot drift from
  what will actually be accepted.
- **Only rebuilding the infrastructure?** `DEPLOYMENT.md` is self-contained.
- **Only understanding the design?** `PROJECT_OVERVIEW.md` §§1–5.
