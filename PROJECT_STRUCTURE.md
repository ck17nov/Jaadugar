# Repository structure

The real tree, as committed. File counts are tracked files per top-level
directory. Read `PROJECT_OVERVIEW.md` first for what the parts are *for*.

```text
Vid App/
├── README.md                        quick start
├── PROJECT_OVERVIEW.md              the handover document — read this second
├── PROJECT_STRUCTURE.md             this file
├── DEPLOYMENT.md                    rebuild from nothing
├── SCRIPT_BANK_GENERATION_PROMPT.md the script authoring standard
├── .env.example                     every environment variable, documented
├── .gitignore                       secrets, workspace, archives, build output
├── config.yaml                      non-secret operational settings
├── requirements.txt                 pinned Python dependencies
├── pytest.ini                       testpaths, pythonpath, markers
│
├── engine/          (59 files)  the production pipeline — no web framework here
├── backend/         (7 files)   FastAPI surface + Typer CLI
├── android/         (58 files)  Kotlin/Compose app; ALSO the scheduler
├── banks/           (141 files) the script bank, as JSONL
├── tests/           (55 files)  1,487 tests
├── scripts/         (12 files)  operator tooling
├── deploy/oracle/   (6 files)   systemd units, Caddyfile, setup.sh
├── docs/            (12 files)  setup guides + the public privacy/terms pages
└── assets/          (9 files)   fonts, logo, optional music library
```

---

## engine/ — the pipeline

No HTTP, no framework. Importable and testable on its own.

| Path | What it does |
|---|---|
| `pipeline.py` | **The spine.** Every `stage_*` method, in order, plus publish and approval logic. Start here. |
| `core/config.py` | Loads `.env` + `config.yaml`; resolves the workspace path. |
| `core/db.py` | All SQLite access. Schema creation, migrations, every query. |
| `core/models.py` | `AutomationRequest`, `VideoJob`, `JobStatus`, `VideoMetadata`. |
| `core/groups.py` | Channel groups (kids/finance/tech), their topics, keywords, topic rotation. |
| `core/logging.py` | Structured logging **and the secret redactor**. |
| `content/bank.py` | `BankEntry` — the schema of a banked script, and its validation. |
| `content/bank_import.py` | **The gates.** Every reason an entry is refused. |
| `content/bank_use.py` | Claiming, releasing, and converting an entry into a script. |
| `content/bank_prompt.py` | Beat tables and word/scene budgets per cell. |
| `content/variety.py` | Diversity axes, 4-gram overlap, share caps. |
| `content/story_gate.py` | Story craft: character, want, obstacle, turn, refrain. |
| `content/ideas.py` | Live idea generation and scoring (incl. the hook scorer). |
| `content/script.py` | Live script generation. |
| `content/llm.py` | Provider router: Gemini → Groq → template, with rate-limit handling. |
| `quality/gate.py` | Quality score, black/freeze/clipping checks, **safety vocabulary**. |
| `video/compose.py` | ffmpeg: zoompan, cuts vs cross-fades, subtitles, mixing. |
| `video/music.py` | Procedural ambient beds and transition SFX. Nothing downloaded. |
| `video/fonts.py` | Font resolution for burned-in captions. |
| `visuals/` | Image generation: Cloudflare AI, Pollinations, stock providers. |
| `tts/` | Edge TTS and gTTS. |
| `youtube/auth.py` | OAuth, token store, per-channel credentials. |
| `youtube/channels.py` | The multi-channel store; niche → channel mapping. |
| `youtube/upload.py` | Insert, captions, thumbnail, playlist. |
| `research/youtube.py` | Public-data research **and `QuotaGuard`**. |
| `analytics/collect.py` | Own-channel figures → `strategy_weights`. |
| `thumbnail/` | Thumbnail selection/generation. |

## backend/ — the surface

| Path | What it does |
|---|---|
| `api/main.py` | 27 endpoints, the worker thread and queue, auth, rate limiting. |
| `cli.py` | Typer CLI. Invoke as `python -m backend.cli` — there is no packaging file, so `autotube` is **not** a command. |
| `stories_cli.py` | `stories` subcommands for bank import/export. |

## android/ — control surface and scheduler

| Path | What it does |
|---|---|
| `workers/Workers.kt` | **The scheduler.** `AutomationWorker` fires automations; `SyncWorker` refreshes state every 15 min. |
| `data/remote/` | Retrofit service and DTOs (`ignoreUnknownKeys` on). |
| `data/local/Database.kt` | Room cache. Destructive migration — an upgrade empties it. |
| `data/repo/AutoTubeRepository.kt` | The single path between UI and backend. |
| `ui/screens/` | Create, Dashboard, Settings, Preview, Content & Scheduler. |
| `app/build.gradle.kts` | `applicationId com.autotube.ai`, versionCode 2 / 0.2.0. No release signing config. |

## banks/ — the script bank

| Path | Role |
|---|---|
| `banks/gen/*.jsonl` | **Staged**: everything authored. 1,279 lines. |
| `banks/*.jsonl` | **Delivered**: what passed the gates. 1,193 lines. |
| `banks/archive/` | Pre-rebuild snapshots. **Gitignored** — local only. |

File naming is load-bearing: `<group>-<lang>-<short|longform>-<topic>.jsonl`.
The importer derives the expected group from the filename.

## scripts/ — operator tooling

| Script | Purpose |
|---|---|
| `deploy.py` | **Deploy to the VM.** Finds the key, pulls, rebuilds, restarts. |
| `bank_rebuild.py` | Reconcile staged → delivered → live. `--no-promote` on the server. |
| `bank_fill.py` | `plan` / `prompt` / `check` / `absorb` — the add-scripts workflow. |
| `bank_spec.py` | Generates the authoring spec handed to an outside AI. |
| `bank_absorb_all.py` | Import every staged batch at once. |
| `bank_reset.py` | Archive and clear the bank. Destructive. |
| `install_hooks.py` | Installs the pre-commit secret hook. **Run after cloning.** |
| `e2e_check.py` | Full pipeline smoke test across niches. |
| `longform_e2e.py` | Long-form specific end-to-end. |
| `verify_dry_run.py` | Confirms dry-run mode uploads nothing. |
| `check_ffmpeg.py` | ffmpeg capability probe. |
| `make_icons.py` | Android launcher icons. |

## deploy/oracle/

| File | Purpose |
|---|---|
| `setup.sh` | One-shot provisioning of a fresh VM. |
| `autotube.service` | The API + worker. Binds `127.0.0.1` only. |
| `autotube-bankrebuild.service` / `.timer` | Nightly DB rebuild, 03:20 UTC. |
| `Caddyfile` | TLS termination and reverse proxy. |
| `README.md` | Provisioning notes, deploy-key guidance. |

## docs/

Setup and troubleshooting guides, plus the **public** pages served by GitHub
Pages: `docs/index.md`, `docs/privacy/index.md`, `docs/terms/index.md` — these
are what the Google OAuth consent screen links to.

## Not in git, by design

| Path | Why |
|---|---|
| `.env` | Real secrets. |
| `.secrets/` | SSH key and OAuth client secrets. Inside the project so tidying Downloads cannot break a deploy. |
| `workspace/` | SQLite database, rendered jobs, logs, token store. |
| `banks/archive/` | Local snapshots. |
| `android/build/`, `.gradle/`, `local.properties` | Build output and machine SDK path. |
