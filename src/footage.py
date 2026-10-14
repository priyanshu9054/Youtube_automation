import logging
import random
import re
import shutil
import subprocess
from pathlib import Path

import requests

log = logging.getLogger("footage")

PEXELS_VIDEOS_URL = "https://api.pexels.com/videos/search"
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

# Dropped when shortening a query: they carry no visual meaning, and stock
# search engines match on concrete nouns.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "into", "on", "at", "to", "from",
    "with", "through", "across", "over", "under", "by", "as", "its", "that",
    "this", "while", "being", "very",
}

# Generic modifiers that describe nothing searchable on their own. Without these
# removed, shortening "long shadow stretching across gravel path" produced the
# query "long", and "hand gently holding a small warm stone" produced "hand" --
# dropping the actual subject of the shot.
_WEAK_MODIFIERS = {
    "old", "new", "long", "short", "small", "large", "big", "tiny", "warm",
    "cold", "single", "lone", "gentle", "soft", "slow", "quick", "bright",
    "dark", "deep", "high", "low", "beautiful", "perfect", "simple",
    # particles and directions: "walking away from city lights" should search
    # for the city lights, not for "away"
    "away", "back", "down", "out", "around", "together", "toward", "towards",
}


def _is_weak(word: str) -> bool:
    """True for words that make a stock-footage query worse, not better."""
    return (
        word in _WEAK_MODIFIERS
        or word.endswith("ly")          # adverbs: gently, slowly, quietly
        or word.endswith("ing")         # gerunds: fading, blooming, stretching
    )


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


def _query_variants(query: str) -> list[str]:
    """Progressively shorter versions of a visual-keyword phrase.

    A phrase like "long shadow stretching across gravel path at sunset" matches
    nothing verbatim, but "shadow gravel path" does. Narrowing beats widening:
    the previous implementation appended unrelated stock queries instead, which
    is why footage had nothing to do with the script.
    """
    variants = [query.strip()]
    words = [w for w in re.findall(r"\w+", query.lower()) if w not in _STOPWORDS and len(w) > 2]

    # Prefer the concrete nouns. Keep the weaker words only if stripping them
    # leaves too little to search for.
    core = [w for w in words if not _is_weak(w)]
    if len(core) < 2:
        core = words

    for n in (3, 2, 1):
        if len(core) >= n:
            candidate = " ".join(core[:n])
            if candidate not in variants:
                variants.append(candidate)
    return [v for v in variants if v]


def _score_pexels_video(video: dict, min_duration: float) -> tuple:
    """Rank a Pexels hit: portrait first, then long enough, then biggest."""
    width = video.get("width") or 0
    height = video.get("height") or 0
    is_portrait = height > width
    long_enough = (video.get("duration") or 0) >= min_duration
    return (is_portrait, long_enough, height)


def _best_pexels_file(video: dict) -> str | None:
    """Pick the largest file that is still sane to download for a 1080x1920 render."""
    files = [f for f in video.get("video_files", []) if f.get("link")]
    if not files:
        return None
    # Prefer <=1920 tall so we aren't pulling 4K to downscale it anyway.
    usable = [f for f in files if (f.get("height") or 0) <= 1920] or files
    usable.sort(key=lambda f: (f.get("height") or 0), reverse=True)
    return usable[0]["link"]


def _fetch_pexels_clip(query: str, api_key: str, min_duration: float) -> str | None:
    """Search Pexels for a vertical clip matching `query`. None if nothing matches."""
    for variant in _query_variants(query):
        try:
            resp = requests.get(
                PEXELS_VIDEOS_URL,
                headers={"Authorization": api_key},
                params={
                    "query": variant,
                    # Shorts are 1080x1920; asking for portrait avoids cropping
                    # the subject out of a landscape clip.
                    "orientation": "portrait",
                    "per_page": 15,
                },
                timeout=30,
            )
            resp.raise_for_status()
            videos = resp.json().get("videos", [])
            if not videos:
                log.debug("Pexels: no hits for %r", variant)
                continue
            videos.sort(key=lambda v: _score_pexels_video(v, min_duration), reverse=True)
            link = _best_pexels_file(videos[0])
            if link:
                log.info("Pexels matched %r via query %r", query, variant)
                return link
        except Exception as e:
            log.warning("Pexels lookup failed for %r (%s)", variant, e)
    return None


def _fetch_pixabay_clip(query: str, api_key: str, min_duration: float) -> str | None:
    """Search Pixabay for a clip matching `query`. None if nothing matches."""
    for variant in _query_variants(query):
        try:
            resp = requests.get(
                PIXABAY_VIDEOS_URL,
                params={"key": api_key, "q": variant, "per_page": 10, "safesearch": "true"},
                timeout=30,
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            if not hits:
                continue
            hits.sort(key=lambda h: abs((h.get("duration") or 0) - min_duration))
            for hit in hits:
                variants = hit.get("videos", {})
                for size in ("large", "medium", "small", "tiny"):
                    if size in variants and variants[size].get("url"):
                        log.info("Pixabay matched %r via query %r", query, variant)
                        return variants[size]["url"]
        except Exception as e:
            log.warning("Pixabay lookup failed for %r (%s)", variant, e)
    return None


def _fetch_unrelated_mixkit_clip() -> str:
    """Last-resort background clip. NOT a search -- it cannot be.

    Mixkit renders its search results client-side, so the server returns the
    same static page for every `?q=`: a specific query and a nonsense query both
    yield the identical ~30 embedded video ids. The old code scraped that page
    and took random.choice of those ids, which is exactly why footage was
    unrelated to the script no matter how good the keywords were.

    Kept only so a single unmatched sentence gets moving footage rather than a
    flat colour card, and it logs loudly because the result is arbitrary.
    """
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    resp = requests.get("https://mixkit.co/free-stock-video/", headers=headers, timeout=15)
    resp.raise_for_status()
    ids = sorted(set(re.findall(r"https://assets\.mixkit\.co/videos/(\d+)/\1-360\.mp4", resp.text)))
    if not ids:
        raise RuntimeError("No Mixkit fallback clips found")
    chosen = random.choice(ids)
    hd_url = f"https://assets.mixkit.co/videos/{chosen}/{chosen}-720.mp4"
    try:
        if requests.head(hd_url, headers=headers, timeout=5).status_code == 200:
            return hd_url
    except Exception:
        pass
    return f"https://assets.mixkit.co/videos/{chosen}/{chosen}-360.mp4"


def find_clip_url(
    query: str,
    api_key: str,
    min_duration: float,
    pexels_api_key: str = "",
) -> str:
    """Return a download URL for stock video matching `query`.

    Order: Pexels (portrait, real keyword search), then Pixabay, then an
    arbitrary Mixkit background. `api_key` is the Pixabay key, kept in this
    position for backwards compatibility with existing callers.
    """
    if pexels_api_key:
        link = _fetch_pexels_clip(query, pexels_api_key, min_duration)
        if link:
            return link

    if api_key:
        link = _fetch_pixabay_clip(query, api_key, min_duration)
        if link:
            return link

    if not pexels_api_key and not api_key:
        log.warning(
            "No PEXELS_API_KEY or PIXABAY_API_KEY configured, so footage cannot be "
            "matched to the script at all; falling back to arbitrary stock clips. "
            "Set PEXELS_API_KEY (free, pexels.com/api) for topic-relevant footage."
        )
    else:
        log.warning("No stock match found for %r; falling back to an arbitrary clip.", query)

    return _fetch_unrelated_mixkit_clip()


def download_clip(url: str, out_path: Path) -> Path:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    resp = requests.get(url, headers=headers, stream=True, timeout=60)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    return out_path
