# Getting your credentials

Two things need manual setup: Pixabay (2 minutes, instant) and YouTube OAuth
(10-15 minutes, one-time, only one permission). `GROQ_API_KEY` is just a
copy-paste from https://console.groq.com/keys.

---

## 1. `PIXABAY_API_KEY`

Pexels has paused issuing new API keys to new developers as of this writing,
so this app uses Pixabay instead — same idea (free stock video), instant key,
no approval wait.

1. Go to https://pixabay.com/api/docs/ and click **Join** / log in (free
   account).
2. Your API key is shown right on that docs page once logged in (also under
   Account Settings).
3. Paste it into `.env`:
   ```
   PIXABAY_API_KEY=your-key-here
   ```

Free tier: 5,000 requests/hour — effectively unlimited for one channel's
worth of videos. No orientation filter is needed; `src/render.py` already
scales + center-crops whatever clip it gets to 1080x1920.

---

## 2. `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`

### Why this can't be skipped

Every tool that uploads to YouTube *through Google's official API* —
`tokland/youtube-upload`, `porjo/youtubeuploader`, this app, anything — needs
an OAuth client you create, because YouTube has no "just give me an API key"
option for writing to someone's channel. (One CLI tool used to ship a shared
client so people could skip this; Google revoked it.) The consent screen UI
makes it *look* like it wants a pile of permissions, but this app only ever
requests one scope: `youtube.upload`. That's the whole ask.

There's also an unofficial route (`nemo-youtube` and similar) that replays
your logged-in browser session cookie instead of using OAuth — I'd avoid it:
it's automating the Studio web UI rather than the sanctioned API, a leaked
session cookie is worse than a leaked refresh token, and it tends to break
silently whenever Google changes Studio's internals. The OAuth path below is
more work once, but it's the stable, sanctioned one.

### 2a. Create the Google Cloud project + enable the API

1. https://console.cloud.google.com → create a new project (any name).
2. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.

### 2b. Configure the OAuth consent screen

1. **APIs & Services → OAuth consent screen** → User type **External** → Create.
2. Fill in app name + your email for the two contact fields. Logo/domain can
   stay blank for personal use.
3. Scopes step: leave it — the script requests `youtube.upload` directly when
   you run it, nothing to add here.
4. Test users step: add your own Google account (the one that owns the
   channel).
5. Save through to the summary.

**Do this next, or your setup breaks in a week:** while the app stays in
**Testing** status, Google issues refresh tokens that expire after exactly
**7 days**, used or not. Fix it once:

6. Back on the **OAuth consent screen** page, click **Publish App** →
   confirm, moving it to **In production**.
7. `youtube.upload` is a *sensitive* scope (not *restricted*), so this does
   **not** trigger Google's formal verification review for personal/low-volume
   use. You'll just see one "Google hasn't verified this app" warning the
   first time you authorize (next step) — click **Advanced → Go to (app
   name) (unsafe)**. After that, refresh tokens behave normally (they don't
   expire on a fixed schedule, only on revocation/long inactivity).

### 2c. Create the OAuth client

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
2. Application type: **Desktop app**.
3. **Create** → **Download JSON** → save as `client_secret.json` (don't
   commit it — it's in `.gitignore`).

### 2d. Run the authorization script

```
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python scripts/authorize_youtube.py path/to/client_secret.json
```

Browser opens → log in as the channel owner → click through the "unverified
app" warning (expected, see 2b.7) → approve the single `youtube.upload`
scope. It prints:

```
YOUTUBE_CLIENT_ID=...
YOUTUBE_CLIENT_SECRET=...
YOUTUBE_REFRESH_TOKEN=...
```

Paste all three into `.env`, and later into Railway's Variables tab too.

### Troubleshooting

- **"Access blocked: this app's request is invalid"** — consent screen isn't
  fully saved, or you're logging in with an account not on the Test users
  list (only matters before you publish in 2b.6).
- **Refresh token died after about a week** — you skipped 2b.6/2b.7 (app
  still in Testing). Publish it, then re-run the authorization script.
- **`invalid_grant` on Railway** — the token was revoked (password change, or
  you're using an old printed token instead of the newest one). Re-run
  `scripts/authorize_youtube.py` and update the Railway variable.
