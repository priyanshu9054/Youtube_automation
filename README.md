# YouTube Shorts Automation

Generates one faceless YouTube Short end-to-end — topic → script (Groq /
Llama-3.3-70b) → voiceover (edge-tts, free) → stock footage (Pixabay) →
rendered vertical video (ffmpeg) → upload (YouTube Data API v3, single
`youtube.upload` scope) — and optionally runs on a schedule via Railway's
cron service.

## 1. Credentials you need

| Credential | Where to get it |
|---|---|
| `GROQ_API_KEY` | https://console.groq.com/keys |
| `PIXABAY_API_KEY` | https://pixabay.com/api/docs/ (instant, free) |
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REFRESH_TOKEN` | See step 2 below |

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
python main.py --upload # also uploads, as UPLOAD_PRIVACY_STATUS (default: private)
```

Check `output/` for the rendered file before you ever flip uploads to public.

## 4. Deploy to Railway

1. `railway init` in this directory (or connect the GitHub repo in the Railway
   dashboard).
2. Set all the env vars from `.env` as Railway variables (Project → Variables).
   Set `AUTO_UPLOAD=true` once you trust the pipeline.
3. Railway builds from the `Dockerfile` automatically.
4. For scheduling: add a **second Railway service** pointed at the same
   repo/image, and set its Cron Schedule in the dashboard (e.g. `0 15 * * *`
   for once daily at 15:00 UTC). See `railway.toml` for the reference deploy
   config. Railway's cron minimum granularity is 1 minute; don't run this more
   than a few times a day — see the content-policy note below.
5. Topic-dedup history (`data/posted_topics.json`) is written inside the
   container, which is ephemeral on Railway — attach a **volume** mounted at
   `/app/data` to the service so it survives between cron runs. Without it,
   the script writer just won't know what it already covered.

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
- Default `UPLOAD_PRIVACY_STATUS=private` until you've manually reviewed
  several outputs for quality and accuracy.

## Project layout

```
main.py                     CLI entrypoint
src/config.py                Loads/validates env vars
src/script_writer.py         Groq call -> title/description/tags/sentences
src/voice.py                 edge-tts synthesis per sentence
src/footage.py                Pixabay search + download
src/render.py                 ffmpeg: normalize clips, concat, burn captions
src/uploader.py                YouTube resumable upload
src/youtube_client.py          OAuth client (single youtube.upload scope)
src/history.py                  Local JSON log of past titles (topic dedup)
src/pipeline.py                  Orchestrates one full run
scripts/authorize_youtube.py     One-time local OAuth flow
```
