import logging
import random
import re
import urllib.parse
from pathlib import Path

import requests

log = logging.getLogger("music")

MIXKIT_MUSIC_BASE = "https://mixkit.co/free-stock-music"

# Mixkit's own mood taxonomy, narrowed to the moods that reliably have a good
# number of tracks. The script writer is asked to pick one of these per video
# so the music matches what the script is actually about instead of a generic
# "trending" track.
ALLOWED_MOODS = [
    "calm", "peaceful", "reflective", "meditative", "soothing", "serene",
    "hopeful", "uplifting", "inspiring", "motivating", "triumphant",
    "epic", "dramatic", "cinematic", "majestic", "powerful", "heroic",
    "mysterious", "mystical", "eerie", "dark", "suspenseful", "tension",
    "happy", "cheerful", "upbeat", "energetic", "playful", "fun",
    "sad", "melancholic", "emotional", "nostalgic", "sentimental",
    "romantic", "warm", "dreamy", "ethereal",
]

_TRACK_RE = re.compile(
    r'data-audio-player-preview-url-value="(https://assets\.mixkit\.co/music/\d+/\d+\.mp3)"'
)


def _scrape_track_urls(page_url: str) -> list[str]:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    resp = requests.get(page_url, headers=headers, timeout=15)
    resp.raise_for_status()
    return list(dict.fromkeys(_TRACK_RE.findall(resp.text)))


def find_music_url(mood: str) -> str:
    """Return a direct MP3 URL for a free, commercially-usable Mixkit track
    matching the given mood. Falls back to a text search, then to a random
    allowed mood, raising only if Mixkit is unreachable entirely."""
    mood = (mood or "").strip().lower()
    candidates = [mood] if mood in ALLOWED_MOODS else []
    candidates.append(random.choice(ALLOWED_MOODS))

    for m in candidates:
        try:
            urls = _scrape_track_urls(f"{MIXKIT_MUSIC_BASE}/mood/{m}/")
            if urls:
                return random.choice(urls[: min(15, len(urls))])
        except Exception as e:
            log.debug("Mixkit mood page failed for %r: %s", m, e)

    # Last resort: a plain text search for the original mood word.
    try:
        urls = _scrape_track_urls(f"{MIXKIT_MUSIC_BASE}/?q={urllib.parse.quote(mood)}")
        if urls:
            return random.choice(urls[: min(15, len(urls))])
    except Exception as e:
        log.debug("Mixkit text search failed for %r: %s", mood, e)

    raise RuntimeError(f"No Mixkit background music found for mood: {mood!r}")


def download_music(url: str, out_path: Path) -> Path:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    resp = requests.get(url, headers=headers, stream=True, timeout=60)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    return out_path
