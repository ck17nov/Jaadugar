# Deployment and restart guide

> "If my laptop disappears tomorrow and I hand this repository to another
> engineer, how do they get the system running again?"

That is what this document answers. Everything here has been done at least
once on the live system; where a step has *not* been exercised from scratch,
it says so.

**Read `PROJECT_OVERVIEW.md` §2 first if you expect an Oracle Database.**
There isn't one. "Oracle" means an Oracle Cloud free ARM VM, and the datastore
is SQLite.

---

## 0. What you are rebuilding

| Piece | Where |
|---|---|
| API + pipeline + SQLite + script bank | One Oracle Cloud Always Free ARM VM |
| TLS | Caddy on the same VM |
| Scheduler | **The Android app on a phone** — not the server |
| Source of truth | This GitHub repository |

Time to a working system: roughly **2–3 hours**, most of it waiting on account
verification and OAuth screens.

---

## 1. Prerequisites

**Accounts** (all free):

| Account | For | Card needed? |
|---|---|---|
| Oracle Cloud | The VM | Yes — identity verification only |
| Google Cloud | YouTube Data + Analytics API, OAuth | No |
| A YouTube channel per niche | Publishing | No |
| Groq | Script generation | No |
| Google AI Studio | Gemini API key | No |
| Cloudflare | Workers AI images | No |
| DuckDNS (or any dynamic DNS) | A hostname for TLS | No |
| GitHub | The repository | No |

**Local tools** (for the dev machine, not the server): Python 3.12, git,
ffmpeg, and — only if you want to build the app — JDK 17 + Android SDK.

---

## 2. Provision the VM

1. Oracle Cloud → **Compute → Instances → Create**.
2. Shape: **VM.Standard.A1.Flex**, Always Free eligible. 4 OCPU / 24 GB is the
   free ceiling; the live box runs comfortably below that.
3. Image: **Ubuntu 24.04** (ARM).
4. Add your SSH public key. Save the private key somewhere you will not tidy
   away — see §4.
5. Networking: allow **443** inbound (and 80 for the ACME challenge). Leave
   8099 closed; the app never binds a public address.

**KNOWN LIMITATION:** Oracle reclaims Always Free instances that stay idle
over a rolling 7-day window. A box that renders a video a day is near that
profile. `deploy/oracle/README.md` covers keeping it busy enough.

---

## 3. Provision the software

```bash
ssh ubuntu@<your-host>
sudo apt update && sudo apt install -y git
sudo git clone https://github.com/ck17nov/Jaadugar.git /opt/autotube
cd /opt/autotube
sudo bash deploy/oracle/setup.sh --domain your.duckdns.org
```

`setup.sh` creates the `autotube` service user, builds the venv, installs
ffmpeg, tunes `render_parallel` to the core count, writes and enables
`autotube.service`, installs Caddy, and enables the nightly
`autotube-bankrebuild.timer`.

> The timer installation was added on 14 September 2026. A box provisioned
> before that has the service file but **no timer** — check with
> `systemctl list-timers | grep bankrebuild` and copy the two units from
> `deploy/oracle/` if it is missing.

Then write the secrets file **on the server** (never commit it):

```bash
sudo -u autotube cp /opt/autotube/.env.example /opt/autotube/.env
sudo -u autotube nano /opt/autotube/.env     # fill in the keys
sudo systemctl restart autotube
```

Verify:

```bash
curl -s http://127.0.0.1:8099/health
systemctl is-active autotube caddy
```

---

## 4. The deploy key and `scripts/deploy.py`

The server **pulls; it never pushes.** A read-only deploy key is all it needs —
nothing on the box generates content any more.

On the server:

```bash
sudo -u autotube ssh-keygen -t ed25519 -C "jaadugar-oracle-deploy" \
     -f /home/autotube/.ssh/id_ed25519 -N ""
sudo -u autotube cat /home/autotube/.ssh/id_ed25519.pub
```

Add that public key at **GitHub → repo → Settings → Deploy keys**, **without**
write access. Then point the remote at SSH so pulls use it.

On the dev machine, put the *instance* SSH private key at `.secrets/oracle.key`
(gitignored) and set the host in `.env`:

```
ORACLE_SSH_HOST=ubuntu@<your-host>
```

The host is **not** in the code because this repository is public and the box
holds a YouTube refresh token.

Deploy:

```bash
python scripts/deploy.py             # dry run: shows local vs remote commit
python scripts/deploy.py --confirm   # pull, rebuild the bank, restart
```

---

## 5. Google Cloud, YouTube API and OAuth

Full walkthrough: `docs/YOUTUBE_SETUP.md`. The essentials:

1. Create a project. Enable **YouTube Data API v3** and **YouTube Analytics
   API**.
2. **API key** → `YOUTUBE_API_KEY` (research only, public data).
3. **Google Auth Platform → Branding**: app name, support email, developer
   email. Add the home page, privacy policy and terms links — the ones this
   repo serves:
   - `https://<user>.github.io/<repo>/`
   - `https://<user>.github.io/<repo>/privacy/`
   - `https://<user>.github.io/<repo>/terms/`
   Authorized domain: `<user>.github.io`. Enable GitHub Pages from `/docs` on
   `main` to serve them.
4. **Data Access**: add exactly three scopes — `youtube.upload`, `youtube`,
   `yt-analytics.readonly`.
5. **Audience → PUBLISH APP.** This matters: while the consent screen is in
   *Testing*, refresh tokens expire **7 days after they are granted**, so you
   would reconnect every channel weekly. Publishing removes that.
   **Do not submit for verification** — it is not required for personal use,
   and unverified only costs an "Advanced → Go to (unsafe)" click plus a
   100-new-user cap you will never reach.
6. **OAuth client → Desktop app** → `YOUTUBE_CLIENT_ID` /
   `YOUTUBE_CLIENT_SECRET`.
7. Authorise each channel:

```bash
sudo -u autotube env PYTHONPATH=/opt/autotube \
  /opt/autotube/.venv/bin/python -m backend.cli auth login
```

Repeat once per channel, then confirm:

```bash
... -m backend.cli auth channels
```

You should see every channel with **"no 7-day limit"** in the last column. If
it shows a countdown instead, `youtube.oauth_published` in `config.yaml` is
still `false`.

---

## 6. AI service keys

| Variable | Where from | Free tier |
|---|---|---|
| `GEMINI_API_KEY` | aistudio.google.com | Generous; rate-limits under load |
| `GROQ_API_KEY` | console.groq.com | Generous; the fallback that carries the load |
| `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` | Workers AI dashboard | ~10,000 neurons/day ≈ 100 images |
| `PEXELS_API_KEY`, `PIXABAY_API_KEY` | Optional | Sharper stock photos |

`content.llm_provider_order` is `[gemini, groq, template]`. **Ollama is
deliberately excluded** — CPU-only it was measured at over 10 minutes a call.

---

## 7. Database and script bank initialisation

The schema is created automatically on first run — there are **no migration
files to apply**.

The bank ships in the repository, so initialising it is one command:

```bash
cd /opt/autotube
sudo -u autotube env PYTHONPATH=/opt/autotube .venv/bin/python \
  scripts/bank_rebuild.py --confirm --no-promote
```

`--no-promote` is **required on the server**: `banks/` there is the git
checkout, and rewriting it dirties the tree so the next `git pull --ff-only`
refuses. Expect it to report the delivery copy and the database agreeing on
the same count.

To add scripts later, see `SCRIPT_BANK_GENERATION_PROMPT.md` and
`PROJECT_OVERVIEW.md` §7.

---

## 8. The app, and starting the automation

```bash
cd android && ./gradlew assembleDebug
# app/build/outputs/apk/debug/app-debug.apk
```

Install it, then in **Settings**: backend URL (`https://your.duckdns.org/`),
the API token matching `AUTOTUBE_API_TOKEN`, and map each niche to a channel.

**Release builds:** there is **no signing config**, so `assembleRelease`
produces an unsigned, uninstallable APK. Debug is what ships. A debug APK is
signed with the standard debug keystore, and **your OAuth client is tied to
that certificate's SHA-1** — switching to a release keystore breaks YouTube
sign-in until you register the new fingerprint.

Then create an automation in the app. **This is the step that starts
production**, and two things about it:

- Recurring automations are fired by **the phone** (`AutomationWorker`). Phone
  off or app force-stopped means no video that day. Exempt the app from battery
  optimisation.
- A kids automation requires the **Made-for-Kids tick** on the Create screen.
  The pipeline will not infer it.

---

## 9. Monitoring

```bash
# service health
systemctl is-active autotube caddy
curl -s http://127.0.0.1:8099/health

# errors only
sudo journalctl -u autotube --since "24 hours ago" -p warning --no-pager

# bank and channels
... -m backend.cli auth channels
... -m backend.cli jobs list

# quota — 2,050 units per upload against 10,000/day
curl -s -H "X-API-Key: $TOKEN" http://127.0.0.1:8099/quota
```

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No videos being produced | No recurring automation, or the phone is not firing | Check `frequency` is not `once`; exempt the app from battery optimisation |
| Kids video stuck `AWAITING_APPROVAL` | Made-for-Kids never confirmed | Tick the disclosure on Create |
| `git pull --ff-only` refuses on the server | Something wrote into the checkout | `git status`; never run `bank_rebuild.py` there without `--no-promote` |
| Tokens expiring weekly | Consent screen still in *Testing* | Publish the app, then **re-grant every channel** |
| `no usable SSH key` from deploy.py | Key missing or unreadable | Put it at `.secrets/oracle.key`; the script checks readability, not just existence |
| Renders fail with an `xfade` timebase error | ffmpeg ≥ 9 is stricter | Already fixed with `settb=AVTB`; verify `compose.py` still sets it |
| Gemini "rate limit reached" | Free tier resting | Expected — the router falls forward to Groq |
| `autotube: command not found` | There is no packaging file | Use `python -m backend.cli` |
| Bank not growing after a deploy | Nightly timer missing | `systemctl list-timers \| grep bankrebuild` |

More in `docs/TROUBLESHOOTING.md`.

---

## 11. What is NOT backed up

**Nothing is backed up off the instance.** If the VM is lost you lose:

- the OAuth token store → re-authorise three channels;
- `bank_entries` → rebuildable from the committed JSONL;
- job history, `published_videos`, `analytics`, `strategy_weights` → gone.

The scripts themselves are safe because they are committed. Everything else is
on one boot volume. This is the largest single operational risk in the project
and it is **NOT CURRENTLY IMPLEMENTED**.
