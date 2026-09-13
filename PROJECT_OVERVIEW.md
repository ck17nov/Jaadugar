# Jaadugar — Project Overview

**The single handover document for this project.** Written 14 September 2026,
against commit `4f4591b` and later. Everything below was checked against the
code; where something is not implemented, it says so.

---

## 1. What this is, in three minutes

Jaadugar researches, writes, illustrates, narrates, renders and publishes
short videos to **three YouTube channels its owner controls** — Kids,
Finance and Technical — without a human in the loop for each video.

It runs on **free tiers only**. The design constraint is not "cheap", it is
₹0/month: a free-forever cloud VM, free LLM and image APIs, open-source
tooling. Every architectural decision follows from that.

The pieces, plainly:

| Piece | What it is | Where it runs |
|---|---|---|
| **Backend** | FastAPI app + the production pipeline | Oracle Cloud free ARM VM |
| **Android app** | The control surface AND the scheduler | Owner's phone |
| **Script bank** | 1,193 pre-written, gated scripts in JSONL + SQLite | In the repo, and on the VM |
| **Datastore** | SQLite (WAL) — **there is no Oracle Database** | On the VM |
| **AI services** | Gemini, Groq (text); Cloudflare Workers AI, Pollinations (images); Edge TTS, gTTS (voice) | External free APIs |
| **Publishing** | YouTube Data API v3, OAuth per channel | From the VM |

**What is automated:** research, script selection, image generation, voice,
subtitles, music, rendering, quality and safety gating, metadata, thumbnail
choice, upload, scheduling, and a learned preference loop from your own
analytics.

**What still needs a person:**

1. **Creating an automation** — someone chooses the niche, schedule and mode.
2. **The Made-for-Kids declaration** — a legal statement to YouTube about the
   content. The code requires an explicit tick and will not infer it.
3. **Supplying scripts** — the bank holds only what the owner provides.
4. **Deploying** — the VM does not update itself; a person runs a script.

---

## 2. ⚠️ "Oracle" here is NOT Oracle Database

This trips up everyone, so it is the second section rather than a footnote.

**Oracle** in this project means **Oracle Cloud Infrastructure** — specifically
one *Always Free* ARM compute instance (a Linux VM). It is rented hardware.

**There is no Oracle Database, no schema, no tables in Oracle, no PL/SQL, no
connection string, no `cx_Oracle`/`oracledb` driver.** Verified: zero
occurrences anywhere in the repo or in `requirements.txt`.

The datastore is **SQLite** in WAL mode, one file
(`engine/core/db.py:258-261`). If a handover brief asks you to document
"Oracle tables", the honest answer is that they do not exist — document the
SQLite schema instead (§6).

---

## 3. Architecture

```
                    ┌─────────────────────────────┐
                    │  ANDROID APP (the phone)    │
                    │  • create/edit automations   │
                    │  • WorkManager = THE         │
                    │    SCHEDULER                 │
                    └──────────────┬──────────────┘
                                   │ HTTPS + X-API-Key
                                   ▼
                    ┌─────────────────────────────┐
                    │  Caddy (TLS, port 443)      │
                    └──────────────┬──────────────┘
                                   │ 127.0.0.1:8099
                                   ▼
   ┌───────────────────────────────────────────────────────────┐
   │  ORACLE CLOUD FREE ARM VM                                  │
   │                                                            │
   │  FastAPI (backend/api/main.py)                             │
   │    POST /automations ──► WORKER.submit() ──► queue         │
   │                                                            │
   │  Worker thread (daemon, in-process)                        │
   │    └─► Pipeline (engine/pipeline.py), stage by stage:      │
   │          stage_research  → YouTube Data API (public data)   │
   │          stage_bank      → claim a script from the bank     │
   │          stage_idea      → from the bank entry, or live     │
   │          stage_script    → banked script, or LLM writes one │
   │          stage_voice     → Edge TTS / gTTS                  │
   │          stage_visuals   → Cloudflare AI / Pollinations /   │
   │                            Pexels / Pixabay                │
   │          stage_render    → ffmpeg (zoompan, xfade, subs)    │
   │          stage_quality   → quality + safety + originality   │
   │          stage_publish   → YouTube upload / schedule        │
   │                                                            │
   │  SQLite (workspace/autotube.db, WAL)                       │
   │  Script bank (banks/*.jsonl + bank_entries table)          │
   │  OAuth token store (workspace/secrets/, 3 channels)        │
   │                                                            │
   │  systemd: autotube.service, caddy.service,                 │
   │           autotube-bankrebuild.timer (nightly 03:20 UTC)   │
   └───────────────────────────────────────────────────────────┘
                                   │
                                   ▼
                         YouTube Data API v3
                    (upload, metadata, captions,
                     playlist, own-channel analytics)
```

**The one surprising edge:** the arrow from the phone is *load-bearing*. See
§5.

### Components and how they talk

- **Android → backend:** Retrofit over HTTPS, `X-API-Key` header matching
  `AUTOTUBE_API_TOKEN`. Base URL is set in Settings. `ignoreUnknownKeys` is on,
  so a backend that grows a field does not crash an older app — it silently
  ignores it.
- **Caddy → app:** Caddy terminates TLS and reverse-proxies to
  `127.0.0.1:8099`. The app **never** binds a public address
  (`deploy/oracle/autotube.service:16`), so the only way in is through Caddy.
- **API → pipeline:** `POST /automations` records the automation, then
  `WORKER.submit(request)` (`backend/api/main.py:1052`) puts it on an
  in-process queue drained by one daemon thread. There is no Celery, no Redis,
  no external broker.
- **Pipeline → services:** plain `httpx` calls. Every provider has a fallback
  chain; a provider that fails is skipped and logged.
- **Pipeline → datastore:** direct SQLite through `engine/core/db.py`.

---

## 4. End-to-end flow of one video

What actually happens, in order:

1. **Trigger.** Either a person taps *Create* in the app, or the phone's
   `AutomationWorker` fires on schedule and POSTs `/automations`.
2. **Accepted.** The API validates the body (pydantic), writes an
   `automations` row, returns `202`, and queues the request. The HTTP call does
   not wait for the video.
3. **Research** (`stage_research`). Queries the YouTube Data API for public
   videos in the niche to find a content gap. Costs quota units. Skipped if no
   API key.
4. **Bank claim** (`stage_bank`). If `script_source` is `bank` or
   `bank_first`, claims an unused entry matching group/language/format/topic
   and marks it used. On `bank` with nothing available it raises; on
   `bank_first` it falls through to live generation.
5. **Idea + script** (`stage_idea`, `stage_script`). A banked entry supplies
   both. Otherwise an LLM writes them — Gemini first, then Groq, then a
   template.
6. **Voice** (`stage_voice`). Edge TTS, falling back to gTTS. One call per
   scene.
7. **Visuals** (`stage_visuals`). Per scene, from the entry's own
   `image_brief` where present: Cloudflare Workers AI → Pollinations →
   Pexels/Pixabay stock.
8. **Render** (`stage_render`). ffmpeg: Ken Burns `zoompan` per shot, hard
   cuts within a scene, `xfade` between scenes, burned-in subtitles via
   libass, ducked music bed, transition SFX.
9. **Quality + safety** (`stage_quality`). Score against a configured minimum;
   black-frame, freeze, clipping, loudness checks; kids-safety vocabulary;
   originality against research and against your own back catalogue. A failure
   here stops the publish.
10. **Publish** (`stage_publish`). Checks quota, checks approval mode, checks
    the Made-for-Kids confirmation, then inserts the video, sets captions and
    thumbnail, and optionally adds it to a playlist. `AUTO` publishes; anything
    else parks the job at `AWAITING_APPROVAL`.
11. **Record.** Job status, `published_videos` row, quota ledger.
12. **Learn.** `analytics` collects your own view/retention figures later and
    updates `strategy_weights`, which nudges future idea ranking.

**Retries:** each stage retries 3 times with backoff
(`[RENDER] stage failed, retrying attempt=1/3 wait=20s`). Final failure marks
the job `FAILED` with the error recorded.

---

## 5. Is this dependent on my laptop?

**No — and this section is deliberately precise, because it is the project's
stated goal.**

| Question | Answer |
|---|---|
| Backend serving with the laptop off? | **Yes.** `autotube.service` is `enabled`, survives reboot. |
| Video rendering without the laptop? | **Yes.** ffmpeg runs on the VM, invoked by the VM's worker thread. |
| YouTube publishing without the laptop? | **Yes.** Tokens live on the VM. |
| Script bank available without the laptop? | **Yes.** In the repo checkout and the VM's SQLite. |
| **Scheduled automations without the laptop?** | **Yes — but they need the PHONE.** |

### The real dependency is the phone, not the laptop

Recurring automations are **Android WorkManager** jobs
(`android/.../workers/Workers.kt`). `AutomationWorker` wakes on the phone,
decides the schedule is due, and POSTs to the backend. **The server has no
scheduler of its own** — its worker thread only drains a queue that something
else fills.

Consequences, stated plainly:

- Phone off, dead battery, app force-stopped, or aggressive battery
  optimisation → **that day's video does not get made.** Nothing on the server
  notices or catches up.
- WorkManager is deliberately inexact; Android may defer a job. Expect
  "around 19:00", not 19:00:00.
- This is a **KNOWN LIMITATION**, not a bug. Making it truly server-side means
  a systemd timer (or an in-app loop) that reads due automations and submits
  them — see §11.

### What genuinely requires the laptop

All development convenience, none of it in the publishing path:

| Operation | Laptop needed? | Note |
|---|---|---|
| Deploy a new commit | Yes | `python scripts/deploy.py --confirm`. The VM never self-updates. |
| Build the APK | Yes | Android SDK + JDK. |
| Author/ingest new bank scripts | Yes, in practice | The gates run anywhere, but the tooling is used from here. |
| Nightly bank rebuild | **No** | `autotube-bankrebuild.timer` does it on the VM. |

### If a machine died right now

- **Laptop dies:** nothing in production stops. The repo is on GitHub, the
  bank is committed, the VM keeps publishing. You lose the local scratch
  workspace and need a new machine to deploy from.
- **VM dies:** you lose the OAuth token store (re-authorise 3 channels), the
  live `bank_entries` table (rebuildable from the committed JSONL), job history
  and `published_videos` rows, and the `analytics`/`strategy_weights` learned
  state. **Nothing is backed up off the instance.** KNOWN LIMITATION.

---

## 6. The datastore

SQLite, WAL mode, one file at `workspace/autotube.db` (path from
`app.workspace` in `config.yaml`, overridable with `AUTOTUBE_WORKSPACE`).

**There are three independent databases** and they do not sync:

1. The VM's — the production one.
2. The laptop's — development and tests only.
3. The phone's Room database, also called `autotube.db` — a local cache of
   automations and jobs for the UI. Room uses **destructive migration**, so an
   app upgrade empties it; `AutomationWorker` re-fetches from the backend
   rather than concluding an automation was deleted.

### Tables

**16 project tables.** A live database reports 17 — the extra is
`sqlite_sequence`, which SQLite creates itself for `AUTOINCREMENT` columns and
is not ours.

| Table | Holds |
|---|---|
| `bank_entries` | The live script bank: one row per script, with `used_at`/`used_job_id`. |
| `video_jobs` | One row per production run, with status and payload. |
| `automations` | Configured automations, including `last_topic` for rotation. |
| `published_videos` | What actually went out, with the YouTube video id. |
| `scripts` | Render history of generated script text, for duplicate detection. |
| `content_ideas` | Ideas produced by research. |
| `research_videos` | Public YouTube videos seen while researching. |
| `analytics` | Own-channel performance figures. |
| `strategy_weights` | The learned preference derived from `analytics`. |
| `quota_usage` | Daily YouTube API unit ledger. |
| `kids_confirmations` | Records an explicit Made-for-Kids confirmation. |
| `assets` | Generated media registry. |
| `schedules` | **Dead.** Created with an index, and not one SQL statement anywhere reads or writes it. Scheduling state lives in `automations` and in YouTube's own `publishAt`. |
| `user_settings` | Key/value settings. |
| `niche_profiles` | **Created and never read or written. Dead.** |
| `service_configs` | **Created and never read or written. Dead.** |

**No foreign keys are declared.** Relationships are by convention
(`video_jobs.automation_id` → `automations.id`), enforced in code, not by the
database.

### Backups

`banks/archive/<timestamp>/` snapshots the bank before a rebuild or reset.
That is content only, it is gitignored, and **nothing else is backed up
anywhere**. KNOWN LIMITATION.

---

## 7. The script bank

The largest subsystem, and the reason output quality is not left to a model at
render time. See `SCRIPT_BANK_GENERATION_PROMPT.md` for the authoring standard.

### Three representations

| Layer | Path | Role |
|---|---|---|
| **Staged** | `banks/gen/*.jsonl` | What was authored. 1,279 lines. Committed. |
| **Delivered** | `banks/*.jsonl` | What passed the gates. 1,193 lines. Committed. |
| **Live** | `bank_entries` table | What the pipeline claims from. 1,193 rows. |

`scripts/bank_rebuild.py` reconciles them: archive, clear, import staged,
verify against a throwaway database, prune anything that would not survive a
fresh import, then write the delivery copy and rebuild the live table. It
holds an `O_EXCL` lock so two rebuilds cannot interleave.

On the **server** it runs with `--no-promote`, which verifies read-only and
never writes into the git checkout — because `banks/` there *is* the checkout,
and dirtying it breaks `git pull --ff-only`.

### Identity and duplicates

`entry_id` = `{group}-{language}-{blake2b(narration)[:10]}`. It is a hash of
the **narration only** — not the title. Two entries with identical narration
under different titles are one entry, and the second replaces the first. This
is why batches routinely lose entries to duplication.

Note: `entry_id`, `content_hash`, `provenance` and `human` are **not** present
in the committed JSONL. They are computed or stamped at import.

### Gates, in order

0. **Provenance** — refuses anything a model wrote on a schedule
   (`batch: autofill`, or a `provider:model` tool handle).
1. **Schema** — well-formed entry, correct cell, caption language.
2. **Story shape** — narrative/poem only: named character, want, obstacle,
   turn, verbatim refrain. 2b: a declared refrain must actually repeat (any
   shape). 2c: a kids refrain must not tell the child they were right
   (advisory).
3. **Variety** — 7 diversity axes, 4-gram overlap, and share caps.
4. **Safety** — violence, weapons, romance, frightening content, commercial
   pressure; stricter for child-directed entries.
5. **Review** — recorded, not performed. Off by default.

### Share caps — the commonest cause of rejection

`MAX_ARC_SHARE` 20%, `MAX_OUTCOME_SHARE` 30%, `MAX_TURN_SHARE` 25%, computed
over **group + language** — *not* per topic, and including shapes that carry
no arc. So all Hindi kids entries compete for the same 20%, and getting the
spread right in English does not help Hindi. One real batch lost 20 entries to
this.

### Selection at render time

`bank_use.claim()` picks an unused entry matching group, language, format and
topic, and marks it used. There is **no ranking** — no hook score, no quality
ordering. First available wins. Release happens if the job fails.

### Current contents

| group | lang | format | count |
|---|---|---|---|
| kids | en | SHORT | 433 |
| kids | hi | SHORT | 422 |
| finance | en | SHORT | 170 |
| tech | en | SHORT | 162 |
| tech | en | LONGFORM | 3 |
| kids/finance | en/hi | LONGFORM | 1 each |

**1,193 total across 46 cells.** Shapes: 418 drill, 338 narrative, 281
explainer, 101 poem, 55 procedure.

**Long-form is effectively empty** — 6 cells hold one entry each, so a single
long-form render exhausts its cell. KNOWN LIMITATION.

### Review state

Every one of the 1,193 entries is `human.kind = "machine"`, reviewer
`claude-opus-5`. **No entry has been read by a person.** This is honest by
design — nothing may claim a human read it — but it means
`bank.require_human_review: true` would reduce the claimable pool to zero.

### Adding scripts

```bash
python scripts/bank_fill.py plan                 # what is thin
python scripts/bank_fill.py prompt --group kids  # the authoring prompt
python scripts/bank_fill.py check batch.jsonl    # gate without storing
python scripts/bank_fill.py absorb batch.jsonl   # store
python scripts/bank_rebuild.py --confirm         # reconcile and deliver
```

---

## 8. Configuration

Every variable is documented in `.env.example`, which is verified complete
against the code. `config.yaml` holds non-secret operational settings. The
ones that matter:

| Key | Value | Meaning |
|---|---|---|
| `youtube.upload_enabled` | true | Master switch. |
| `youtube.default_privacy` | public | |
| `youtube.force_private` | **false** | Rehearsal switch, off — AUTO publishes publicly. |
| `youtube.oauth_published` | true | Consent screen is in production, so no 7-day token expiry. |
| `automation.approval_required` | true | A **default** that per-automation `AUTO` overrides. Not a floor. |
| `automation.daily_video_limit` | 4 | Clamped to the quota ceiling. |
| `quality.minimum_score` | see config | A request may raise this, never lower it. |
| `content.llm_provider_order` | gemini, groq, template | Ollama deliberately excluded. |

**Quota:** each published upload costs **2,050 units** (1,600 insert + 400
captions + 50 thumbnail) against 10,000/day, so **4 uploads/day** is the hard
ceiling, leaving room for research.

`api.cors_origins` and `api.rate_limit_per_minute` are read by the backend but
have **no `api:` section in config.yaml**, so both run on code defaults.

---

## 9. Security

- Secrets live in `.env` and `.secrets/` — both gitignored, both inside the
  project directory so that tidying Downloads cannot break a deploy.
- **This repository is public.** No credential, host address or token may be
  committed. The production host lives in `ORACLE_SSH_HOST` in `.env`, not in
  code.
- A pre-commit hook (`scripts/install_hooks.py`) reads **staged content** for
  private-key headers and credential shapes, because no filename pattern can
  catch a key called `oracle_private`. Run the installer after a fresh clone —
  `.git/hooks` is not tracked.
- The log redactor strips API keys, OAuth client secrets, JWTs, bare hex
  digests and **Google refresh tokens** (`1//…`), the last of which it was
  blind to until an audit found it.
- The app binds loopback only; Caddy terminates TLS. Root SSH is disabled.
- Verified: nothing credential-shaped is tracked, and nothing ever was, across
  the whole history.

---

## 10. Current project status

### Works and has been exercised for real

- 9 videos published to YouTube through the full pipeline.
- 1,193-entry bank, delivery files and database agreeing exactly.
- Render path verified on **both** ffmpeg 6.1.1 (server) and 9.0 (laptop).
- 3 channels connected, all verified refreshing; OAuth published so tokens no
  longer expire weekly.
- **1,487 tests pass, 0 skipped.**
- Server rebooted onto current glibc, 2 GB swap, root SSH off.

### Partially working

- **Long-form:** implemented and rendered, but 6 cells hold one entry each.
- **Learned strategy:** `analytics` is empty, so `strategy_weights` has
  nothing to learn from yet.
- **Scheduling:** works, but only while the phone does (§5).

### Not implemented

- Any off-instance backup.
- A server-side scheduler.
- Human review of bank entries.
- `schedules`, `niche_profiles`, `service_configs` tables — unused.
- Release-signed APK: there is **no signing config**, so `assembleRelease`
  produces an unsigned, uninstallable file. Debug builds are what ship.

### Needs watching

- **Nothing is producing videos right now.** Both enabled automations are
  `frequency: once` and the `schedules` table is empty. Create a recurring
  automation to start.
- `kids_confirmations` is empty — a kids AUTO run needs the disclosure ticked.
- Gemini free tier rate-limits under load; the router falls forward to Groq.

### The next logical improvement

Move the scheduler server-side. It is the single largest gap between what the
project claims (cloud-independent automation) and what it does (cloud
execution, phone-triggered).

---

## 11. Known limitations and future improvements

| Limitation | Impact | Fix |
|---|---|---|
| Scheduler lives on the phone | No phone, no video that day | systemd timer that submits due automations |
| No off-instance backup | VM loss costs tokens, job history, learned state | Periodic export to object storage or git |
| Long-form cells hold 1 entry | One render empties the cell | Author more long-form |
| No human review | `require_human_review` would empty the pool | Review, or accept machine-reviewed |
| 4 uploads/day hard ceiling | Cannot exceed without a quota increase | Apply to YouTube with an audit |
| Free-tier LLM rate limits | Gemini rests under load | Already falls forward to Groq |
| Bank selection is first-available | No quality ordering | Rank claim candidates |
| A test leaves a render running past teardown | 8.5 MB of scratch per suite run | Shut the executor down |
| No packaging file | `autotube` is not a command; use `python -m backend.cli` | Add `pyproject.toml` if wanted |
| Release APK unsigned | Cannot install a release build | Add a keystore + register its SHA-1 |

---

# Instructions for another AI

You are taking over a working YouTube automation POC. It publishes publicly
and automatically to three real channels, and most of its content is
child-directed. **Read before you change.**

### Reading order

1. `README.md` — quick start, commands.
2. **This file** — architecture, flow, real state.
3. `PROJECT_STRUCTURE.md` — where everything lives.
4. `DEPLOYMENT.md` — rebuilding from nothing.
5. `SCRIPT_BANK_GENERATION_PROMPT.md` — the authoring standard.
6. Then: `engine/pipeline.py` (the spine), `engine/content/bank_import.py`
   (the gates), `backend/api/main.py` (the surface).

### Architectural decisions you should not casually reverse

- **Free tiers only.** A paid dependency defeats the project.
- **The bank holds only what the owner supplied.** A background generator was
  built, produced 110 entries, and was deleted at the owner's instruction.
  Gate 0 enforces it. Do not add scheduled generation.
- **Nothing may claim a human read it.** Machine review is recorded as
  `kind="machine"` with the real model name. Never stamp `human`.
- **Made-for-Kids is the owner's declaration.** Requires an explicit tick;
  never infer it from a sibling job's status.
- **The repo is public.** No host, no key, no token in tracked files.
- **The server pulls, never pushes.** Read-only deploy key by design.

### Where things live

| Concern | File |
|---|---|
| Pipeline spine | `engine/pipeline.py` |
| Bank gates | `engine/content/bank_import.py` |
| Bank selection | `engine/content/bank_use.py` |
| Variety/share caps | `engine/content/variety.py` |
| Story craft gate | `engine/content/story_gate.py` |
| Safety | `engine/quality/gate.py` |
| Render | `engine/video/compose.py` |
| YouTube | `engine/youtube/` |
| HTTP surface | `backend/api/main.py` |
| CLI | `backend/cli.py` |
| Scheduler | `android/.../workers/Workers.kt` — **on the phone** |
| Deploy | `scripts/deploy.py` |

### Safe to modify / handle with care

- **Safe:** docs, new tests, new scripts, new bank content, prompt wording.
- **Care:** the gates (they protect monetisation — measure false positives
  against all 1,193 entries before tightening), `compose.py` (ffmpeg version
  differences bite: verify on 6.x *and* 9.x), quota arithmetic, anything
  touching kids safety.
- **Do not** weaken a kids gate for convenience, commit a secret, or make the
  server push to git.

### Before you claim something works

Run `python -m pytest tests/ -q` (1,487 tests, ~5 minutes). The suite renders
real video with ffmpeg. A `conftest.py` fixture deletes what the run created;
if you see `workspace/jobs` growing, that fixture broke.
