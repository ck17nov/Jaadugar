---
title: Jaadugar — Privacy Policy
---

# Privacy Policy — Jaadugar

**Last updated: 13 September 2026**

Jaadugar is a personal, single-operator tool that researches, produces and
publishes videos to **its own operator's YouTube channels**. It is not a
service offered to other people, it has no user accounts, and it collects
nothing from visitors to this page.

The only person whose Google data Jaadugar ever touches is the operator who
installed it and signed in. This policy describes exactly what that means.

**Contact:** technicaljaadugar@gmail.com

---

## 1. Who this applies to

One person: the operator running their own copy of Jaadugar, signed in with
their own Google account, publishing to their own channels.

There are no other users. The application is not distributed through the
Google Play Store or any app store, and the OAuth client is not offered to
third parties.

---

## 2. What Google user data is accessed

Jaadugar requests exactly three OAuth scopes, and nothing else:

| Scope | Why it is needed |
|---|---|
| `https://www.googleapis.com/auth/youtube.upload` | to upload a finished video file to the operator's channel |
| `https://www.googleapis.com/auth/youtube` | to set the title, description, tags, category, privacy status and Made-for-Kids declaration on those uploads, and to add a video to the operator's own playlist |
| `https://www.googleapis.com/auth/yt-analytics.readonly` | to read view counts, watch time and retention **for the operator's own published videos**, so the tool can favour formats that performed better |

Jaadugar does not request, and cannot access, contacts, email, Drive,
calendar, photos, location, payment information, or any data belonging to
any other person or channel.

---

## 3. What is stored, and where

**On hardware the operator controls.** There is no Jaadugar-operated cloud
service. Nothing is uploaded to the developer, because the operator is the
developer.

| Data | Where it lives | Why |
|---|---|---|
| OAuth refresh token, one per channel | a file on the operator's own server, readable only by the service account that runs Jaadugar | so uploads can continue without re-authorising daily |
| Channel id, channel title | the same file | to upload the right video to the right channel |
| Analytics figures for the operator's own videos | a local SQLite database on the same server | to compute the performance weights described in section 2 |
| Scripts, generated images, audio and rendered video | the local filesystem | they are the product |

No data is sold, rented, shared for advertising, or used to train any
machine-learning model.

---

## 4. What is sent to third parties — and what is not

Jaadugar contacts these services while producing a video:

| Service | What it receives |
|---|---|
| Google / YouTube Data API | the video file and its metadata, being uploaded to the operator's channel |
| Google Gemini, Groq | a topic or a script brief, as text, to write or improve narration |
| Cloudflare Workers AI, Pollinations | a text description of a scene, to generate an illustration |
| Pexels, Pixabay | a search keyword, to find a stock photograph |
| Microsoft Edge TTS, Google Translate TTS | the narration text, to synthesise a voice |

**No Google user data is sent to any of them.** This is a deliberate design
property, not an intention: the analytics figures from section 2 are reduced
to a single numeric weight used locally when ranking ideas, and that number
never enters a prompt or leaves the machine. The text sent to the writing and
image services is content the tool is authoring, not anything read from a
Google account.

Each of those third parties handles the data it receives under its own
privacy policy.

---

## 5. Retention and deletion

Tokens and local data persist until the operator deletes them. Two ways:

* **In Jaadugar** — Settings → *Clear stored credentials*, or the command
  `python -m backend.cli auth logout`, which deletes the stored refresh
  tokens.
* **In a Google Account** — <https://myaccount.google.com/permissions>,
  which revokes Jaadugar's access from Google's side and invalidates the
  tokens immediately, whether or not the local copy is removed.

Deleting the tokens does not delete videos already published to YouTube;
those are managed in YouTube Studio like any other upload.

Analytics rows and rendered media are removed by deleting the workspace
directory on the operator's server.

---

## 6. Children

Some of the videos Jaadugar produces are made for children and are declared
as such to YouTube under the Made-for-Kids setting, which is a legal
declaration the operator makes about the **content**.

Jaadugar does not collect data from children, or from anyone. It has no
audience-facing surface: the only person who interacts with it is the
operator. Viewers of the published videos interact with YouTube, under
Google's privacy policy, not with this application.

---

## 7. Security

* Credentials are held in environment variables and in files that are
  excluded from version control, never in the source code. Some of those
  files sit inside the project directory for convenience; they are ignored
  by git, and a pre-commit hook reads staged content for key material so a
  credential cannot be published by a mistyped filename.
* Log output is passed through a redactor that removes tokens, API keys and
  OAuth secrets before anything is written.
* The server exposes only an HTTPS endpoint, and the application itself
  listens on the loopback interface behind it.

No system is perfect, and this one is maintained by one person. If you
believe you have found a security problem, please write to the contact
address above.

---

## 8. Limited Use

Jaadugar's use of information received from Google APIs adheres to the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements. Specifically, data obtained through
these scopes is used only to provide the operator's own publishing and
reporting features described above; it is not transferred to others except as
necessary to provide those features, and it is not used for advertising, sold,
or used to build profiles.

---

## 9. Changes

Material changes will be published on this page with a new date at the top.
Because the operator is the only user, there is no notification mechanism
beyond this page.
