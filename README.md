# YouTube Shorts Automation

Generates one faceless YouTube Short end-to-end — topic → script (Groq) →
voiceover (edge-tts, free) → stock footage (Pixabay if configured, else free
Mixkit scraping, else a plain color background as last resort) → background
music (free Mixkit tracks, mood picked by the script writer to match that
video's specific topic) → rendered vertical video (ffmpeg) → upload (YouTube
Data API v3, single `youtube.upload` scope) — runs as a single always-on
Railway **web service** with an authenticated HTTP trigger (`POST /run`) that
an external scheduler calls daily. See [section 4](#4-deploy-to-railway).

## 1. Credentials you need

| Credential | Required? | Where to get it |
|---|---|---|
| `GROQ_API_KEY` | Yes | https://console.groq.com/keys |
| `PIXABAY_API_KEY` | No — leave blank to use the free Mixkit fallback instead | https://pixabay.com/api/docs/ (instant, free) |
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REFRESH_TOKEN` | Yes | See step 2 below |

**Note on `GROQ_MODEL` and rate limits:** the current default (`qwen/qwen3.8-27b`) has
a free/on-demand tier limit of 1,000 Output Tokens Per Minute (OTPM). The script writer
defaults `GROQ_MAX_TOKENS=600` (and retries on rate limits) to guarantee completions
stay under this limit. For YouTube Shorts (6-8 sentences), ~300 output tokens is typical.

Step-by-step walkthrough for Pixabay + YouTube OAuth (including why YouTube
can't skip Google's consent screen, and how to avoid the 7-day-refresh-token
trap) is in **[docs/GETTING_CREDENTIALS.md](docs/GETTING_CREDENTIALS.md)**.

## 2. One-time YouTube OAuth setup (do this locally, not on Railway)

Full walkthrough with screenshots-in-words is in
[docs/GETTING_CREDENTIALS.md](docs/GETTING_CREDENTIALS.md). Short version:

1. https://console.cloud.google.com → create a project → enable
   **YouTube Data API v3**.
2. OAuth consent screen → External → add yourself as a test user → then
   **Publish App** (moves it out of Testing, otherwise your refresh token
   dies after 7 days).
3. Credentials → Create Credentials → OAuth client ID → **Desktop app** →
   download as `client_secret.json`.
4. Run:
   ```
   python -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   python scripts/authorize_youtube.py path/to/client_secret.json
   ```
   This opens a browser, asks you to approve one scope (`youtube.upload`),
   and prints `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` /
   `YOUTUBE_REFRESH_TOKEN`.
5. Put all three in `.env` (copy `.env.example` first).

## 3. Run one video locally

```
cp .env.example .env   # fill in all values
pip install -r requirements.txt
brew install ffmpeg    # macOS; apt-get install ffmpeg on Linux
python main.py          # renders only, does not upload (AUTO_UPLOAD=false)
python main.py --upload # also uploads, as UPLOAD_PRIVACY_STATUS (default: public)
```

Check `output/` for the rendered file before you ever flip uploads to public.

## 4. Deploy to Railway

This runs as a **normal always-on web service**, *not* a Railway Cron
Service. It exposes a small authenticated HTTP trigger (`POST /run`) that an
external scheduler calls once a day. `railway.toml` already carries the right
config (`Dockerfile` builder, `startCommand = "python app.py"`,
`healthcheckPath = "/health"`, `restartPolicyType = "ON_FAILURE"`).

> **If the scheduler keeps failing, check this first.** A **Cron Schedule** set
> in *Settings → Cron Schedule* only boots the container for that one window
> and stops it again, so `/run` is unreachable for the rest of the day and
> every scheduled call fails to connect. The service must have **no cron
> schedule set**. (Earlier versions of this app were a Cron Service with
> `startCommand = "python main.py --upload"`; if you deployed from those
> instructions, clear the schedule.)

1. `railway login`, then `railway init` in this directory (or connect the
   GitHub repo from the Railway dashboard instead — either works, since
   `railway.toml` carries the config).
2. Confirm *Settings → Cron Schedule* is **empty**, and that the service is
   not sleeping (a service that sleeps on inactivity will not answer `/run`).
3. **Project → Variables**: paste in every value from your local `.env`
   (`GROQ_API_KEY`, `YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN`, `PIXABAY_API_KEY`
   if you have one, `CHANNEL_NICHE`, etc). Set `AUTO_UPLOAD=true` only once
   you've reviewed a few local renders and trust the output. Also set
   `RUN_TOKEN` to a long random string — `/run` and `/dry-run` return `500`
   until it exists, and `401` unless the caller sends
   `Authorization: Bearer $RUN_TOKEN`.
4. Attach a **volume** mounted at `/app/data` to the service. Topic-dedup
   history (`data/posted_topics.json`) is written inside the container, which
   is otherwise wiped on every restart — without the volume, the script writer
   won't remember what topics it already covered, and you'll get more repeats.
5. Verify the deploy end to end, in this order:

   ```sh
   curl -s https://<your-service>.up.railway.app/health
   # -> {"run_token_configured":true,"running":false,"status":"ok"}
   # run_token_configured MUST be true, or /run rejects every call with 500

   curl -s -X POST -H "Authorization: Bearer $RUN_TOKEN" \
     https://<your-service>.up.railway.app/dry-run
   # synchronous, no upload; returns the full error if the pipeline is broken

   curl -s -X POST -H "Authorization: Bearer $RUN_TOKEN" \
     https://<your-service>.up.railway.app/run
   # -> {"status":"started"}, then poll /status
   ```

   `/dry-run` is the one to reach for when debugging: it runs the whole
   pipeline synchronously and returns the real exception in the response body,
   instead of a background thread whose failure you have to go read the logs
   for.

### Scheduler troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Connection refused / timeout | A Cron Schedule is set, or the service sleeps | Clear the schedule; keep the service always-on |
| `500 RUN_TOKEN is not configured` | `RUN_TOKEN` missing in Railway — **check this first**, it rejects every call and no video is ever made | Set it in *Variables*. `GET /health` reports `run_token_configured`, and the startup log warns about it |
| `401 unauthorized` | Caller's token doesn't match | Resend as `Authorization: Bearer $RUN_TOKEN` |
| `409 busy` every day | A previous run was killed (OOM, restart, deploy mid-run) and left `running: true` on the volume | Self-heals now: cleared at startup and after `RUN_STALE_AFTER_SECONDS` (default 1800). To clear immediately: `POST /run?force=true` |
| Runs start but never finish | Render OOM-killed on a small instance | Check `/status` for `last_error`; raise the instance memory |

`GET /status` reports `running`, `last_result`, `last_error` and `stale`. A
`stale: true` means a lock is being held by a run that is already gone — the
next `/run` will reclaim it rather than refusing.

## 5. Content policy — read before turning on `AUTO_UPLOAD`

YouTube monetization/visibility enforcement in 2026 targets **mass-produced,
templated content** at the channel level, not automation itself. To stay on
the right side of that:

- Keep scripts/visuals/pacing varied — the script writer already avoids
  repeating recent titles, but review output periodically.
- Toggle "Altered or synthetic content" in YouTube Studio for each upload
  (required for AI-narrated content).
- Don't upload at a volume/cadence a human couldn't plausibly sustain,
  especially on a new channel.
- Use one real, verified channel/account. Don't run multiple accounts to
  multiply output — that's a distinct, bannable TOS violation.
- `TTS_VOICE` / `TTS_RATE` / `TTS_PITCH` control narration. The default is
  `en-US-ChristopherNeural` at `-12%` / `-15Hz` — a deep, heavy, deliberate
  read suited to the Stoic/philosophy niche. Alternatives: `en-US-GuyNeural`
  (more intense), `en-GB-ThomasNeural` (British gravitas). Lowering `TTS_PITCH`
  darkens the tone further, but `-15Hz` is the practical floor before it starts
  to sound synthetic.
- `UPLOAD_PRIVACY_STATUS=public` publishes uploads immediately; set it to
  `private` instead if you want to review outputs before they go live. Only
  `public`, `private` and `unlisted` are accepted (case-insensitive) — anything
  else logs a warning and falls back to `public`. After each upload the applied
  privacy status is read back from YouTube's response and logged; a mismatch
  (e.g. YouTube holding the video back) logs a warning naming the video ID.

## Project layout

```
main.py                     CLI entrypoint
src/config.py                Loads/validates env vars
src/script_writer.py         Groq call -> title/description/tags/sentences
src/voice.py                 edge-tts synthesis per sentence
src/footage.py                Pixabay search (if keyed) -> Mixkit fallback -> color-card fallback
src/music.py                   Mixkit mood-matched background music search
src/render.py                 ffmpeg: normalize clips, concat, mix music under voice, burn captions
src/uploader.py                YouTube resumable upload
src/youtube_client.py          OAuth client (single youtube.upload scope)
src/history.py                  Local JSON log of past titles (topic dedup)
src/pipeline.py                  Orchestrates one full run
scripts/authorize_youtube.py     One-time local OAuth flow
```
