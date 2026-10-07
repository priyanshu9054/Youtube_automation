# YouTube Shorts Automation

Generates one faceless YouTube Short end-to-end — topic → script (Groq) →
voiceover (edge-tts, free) → stock footage (Pixabay if configured, else free
Mixkit scraping, else a plain color background as last resort) → background
music (free Mixkit tracks, mood picked by the script writer to match that
video's specific topic) → rendered vertical video (ffmpeg) → upload (YouTube
Data API v3, single `youtube.upload` scope) — runs as a single Railway
**Cron Service**.

## 1. Credentials you need

| Credential | Required? | Where to get it |
|---|---|---|
| `GROQ_API_KEY` | Yes | https://console.groq.com/keys |
| `PIXABAY_API_KEY` | No — leave blank to use the free Mixkit fallback instead | https://pixabay.com/api/docs/ (instant, free) |
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REFRESH_TOKEN` | Yes | See step 2 below |

**Note on `GROQ_MODEL`:** the current default (`qwen/qwen3.8-27b`) is listed
by Groq as a *preview* model — fine for testing, but Groq can deprecate
preview models without notice, which would silently break an unattended cron
job. For a production cron, consider pointing `GROQ_MODEL` at a
non-preview/production model instead once you've confirmed script quality.

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

This app is a single batch job (generate one video, upload it, exit) — on
Railway that maps to **one service of type Cron**, not a long-running web
service. `railway.toml` already has the right `[build]`/`[deploy]` block
(`Dockerfile` builder, `startCommand = "python main.py --upload"`,
`restartPolicyType = "NEVER"` since a cron run should finish and stop, not
restart in a crash loop).

1. `railway login`, then `railway init` in this directory (or connect the
   GitHub repo from the Railway dashboard instead — either works, since
   `railway.toml` carries the config).
2. In the Railway dashboard, open the service's **Settings → Cron Schedule**
   and set it, e.g. `0 15 * * *` for once daily at 15:00 UTC. (Minimum
   granularity is 1 minute; don't go more than a few times a day — see the
   content-policy note below.)
3. **Project → Variables**: paste in every value from your local `.env`
   (`GROQ_API_KEY`, `YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN`, `PIXABAY_API_KEY`
   if you have one, `CHANNEL_NICHE`, etc). Set `AUTO_UPLOAD=true` only once
   you've reviewed a few local renders and trust the output.
4. Attach a **volume** mounted at `/app/data` to the service. Topic-dedup
   history (`data/posted_topics.json`) is written inside the container,
   which is otherwise wiped between cron runs — without the volume, the
   script writer won't remember what topics it already covered, and you'll
   get more repeats.
5. Trigger one manual run from the dashboard ("Deploy" / "Run now") before
   trusting the schedule, and check Railway's logs for the `Uploaded
   successfully: https://youtu.be/...` line.

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
- `UPLOAD_PRIVACY_STATUS=public` publishes uploads immediately; set it to
  `private` instead if you want to review outputs before they go live.

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
