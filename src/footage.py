import logging
import random
import re
import shutil
import subprocess
import urllib.parse
from pathlib import Path

import requests

log = logging.getLogger("footage")

PIXABAY_VIDEOS_URL = "https://pixabay.com/api/videos/"

# Curated sleek background color pairs for vertical shorts when footage is unavailable
FALLBACK_PALETTES = [
    "0x0f172a",  # Slate 900
    "0x1e1b4b",  # Indigo 950
    "0x064e3b",  # Emerald 900
    "0x4a044e",  # Fuchsia 950
    "0x450a0a",  # Red 950
    "0x172554",  # Blue 950
    "0x2e1065",  # Purple 950
    "0x18181b",  # Zinc 900
]

PHILOSOPHICAL_FALLBACK_QUERIES = [
    "deep philosophy",
    "stoic wisdom",
    "man thinking",
    "ancient statue",
    "starry galaxy",
    "deep ocean waves",
    "clouds timelapse",
    "hourglass sand",
    "misty mountain forest",
    "solitary walk",
]


def generate_fallback_clip(duration: float, out_path: Path, index: int = 0) -> Path:
    """Generate a clean vertical 1080x1920 video clip using ffmpeg lavfi when no video is found."""
    color = FALLBACK_PALETTES[index % len(FALLBACK_PALETTES)]
    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=1080x1920:r=30",
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path


def _fetch_mixkit_clip(query: str) -> str:
    """Search Mixkit free stock videos (no API key required) and return a direct MP4 URL."""
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    
    # Try the specific query first, then fallback queries if empty
    candidate_queries = [query]
    # Extract simple words for fallback
    simple_words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 3]
    if simple_words:
        candidate_queries.append(" ".join(simple_words[:2]))
    candidate_queries.extend(random.sample(PHILOSOPHICAL_FALLBACK_QUERIES, 2))

    for q in candidate_queries:
        try:
            encoded_query = urllib.parse.quote(q)
            resp = requests.get(
                f"https://mixkit.co/free-stock-video/?q={encoded_query}",
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                video_ids = list(set(re.findall(r"https://assets\.mixkit\.co/videos/(\d+)/\1-360\.mp4", resp.text)))
                if video_ids:
                    chosen_id = random.choice(video_ids)
                    # Check if crisp 720p HD version exists
                    hd_url = f"https://assets.mixkit.co/videos/{chosen_id}/{chosen_id}-720.mp4"
                    try:
                        head = requests.head(hd_url, headers=headers, timeout=5)
                        if head.status_code == 200:
                            return hd_url
                    except Exception:
                        pass
                    return f"https://assets.mixkit.co/videos/{chosen_id}/{chosen_id}-360.mp4"
        except Exception as e:
            log.debug("Mixkit search attempt failed for %r: %s", q, e)

    raise RuntimeError(f"No Mixkit video clips found for query: {query!r}")


def find_clip_url(query: str, api_key: str, min_duration: float) -> str:
    """Return a download URL for stock video. If Pixabay API key is set, use Pixabay;
    otherwise use free Mixkit stock videos (no API key required)."""
    if api_key:
        try:
            resp = requests.get(
                PIXABAY_VIDEOS_URL,
                params={"key": api_key, "q": query, "per_page": 10, "safesearch": "true"},
                timeout=30,
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            if hits:
                hits.sort(key=lambda h: abs(h.get("duration", 0) - min_duration))
                for hit in hits:
                    variants = hit.get("videos", {})
                    for size in ("large", "medium", "small", "tiny"):
                        if size in variants:
                            return variants[size]["url"]
        except Exception as e:
            log.warning("Pixabay lookup failed (%s); falling back to Mixkit free video", e)

    # Free stock video from Mixkit (no API key needed)
    return _fetch_mixkit_clip(query)


def download_clip(url: str, out_path: Path) -> Path:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    resp = requests.get(url, headers=headers, stream=True, timeout=60)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    return out_path
