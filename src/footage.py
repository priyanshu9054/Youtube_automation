import logging
import os
import random
import re
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

import requests

log = logging.getLogger("footage")

PEXELS_VIDEOS_URL = "https://api.pexels.com/videos/search"
PIXABAY_VIDEOS_URL = "https://pixabay.com/api/videos/"
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"
OPENVERSE_IMAGES_URL = "https://api.openverse.org/v1/images/"

# Shorts are 9:16, so this is the aspect ratio a source image should be closest
# to before it gets cropped.
TARGET_RATIO = 1920 / 1080

# Appended to every generated image so six separate shots still look like one
# video. "no text" matters: stock-style prompts otherwise come back with
# invented captions burned into the frame.
AI_STYLE_SUFFIX = (
    "cinematic still, moody dramatic lighting, shallow depth of field, "
    "muted earthy tones, film grain, vertical composition, no text, no words"
)

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


def _ai_image(query: str, out_path: Path, token: str = "") -> Path:
    """Generate an image for `query` with Pollinations. No API key required.

    This is the only source that is relevant by construction: the picture is
    made from the sentence's own keywords rather than searched for among clips
    that happen to exist. Anonymous requests are watermarked and rate limited to
    roughly one every 15 seconds; POLLINATIONS_TOKEN (a free account, no
    payment) removes both.
    """
    prompt = urllib.parse.quote(f"{query}, {AI_STYLE_SUFFIX}")
    # "sana" is the free model. The default and "flux" both answer 402 Payment
    # Required on the anonymous tier, so the model is named explicitly.
    params = {
        "width": 1080,
        "height": 1920,
        "nologo": "true",
        "model": os.environ.get("POLLINATIONS_MODEL", "sana"),
    }
    if token:
        params["token"] = token

    last_error = None
    for attempt in range(4):
        try:
            resp = requests.get(
                f"{POLLINATIONS_URL}{prompt}",
                params=params,
                timeout=180,
                headers={"User-Agent": "youtube-automation/1.0"},
            )
            if resp.status_code == 429:
                # Anonymous tier allows ~1 request per 15s; back off rather than
                # dropping the sentence to an unrelated clip.
                wait = 15 * (attempt + 1)
                log.info("Pollinations rate limited; waiting %ss", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            if not resp.content or not resp.headers.get("content-type", "").startswith("image/"):
                raise RuntimeError(f"unexpected content-type {resp.headers.get('content-type')!r}")
            out_path.write_bytes(resp.content)
            return out_path
        except Exception as e:
            last_error = e
            log.warning("Pollinations attempt %d failed for %r: %s", attempt + 1, query, e)
            time.sleep(3)

    raise RuntimeError(f"Could not generate an image for {query!r}: {last_error}")


def _ken_burns(image_path: Path, duration: float, out_path: Path, index: int = 0) -> Path:
    """Turn a still into a slow push-in so the frame is never static.

    Scaled up before zoompan because zoompan samples from the source frame --
    zooming a 1080-wide still directly produces visible stepping as the crop
    window moves by whole pixels.
    """
    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    frames = max(1, int(duration * 30))
    # Alternate push-in and pull-out so consecutive shots don't feel identical.
    if index % 2 == 0:
        zoom_expr = "min(zoom+0.0008,1.18)"
    else:
        zoom_expr = "if(lte(zoom,1.0),1.18,max(1.001,zoom-0.0008))"

    result = subprocess.run(
        [
            ffmpeg_bin, "-y", "-loop", "1", "-i", str(image_path), "-t", f"{duration:.3f}",
            "-vf",
            "scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,"
            f"zoompan=z='{zoom_expr}':d={frames}:"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            str(out_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"ffmpeg ken-burns failed: {(result.stderr or '')[-400:]}")
    return out_path


def acquire_clip(
    query: str,
    out_path: Path,
    duration: float,
    *,
    pexels_api_key: str = "",
    pixabay_api_key: str = "",
    pollinations_token: str = "",
    prefer_ai: bool = False,
    index: int = 0,
) -> str:
    """Put a clip for `query` at `out_path`. Returns a label for the source used.

    Order: real stock video when a key exists, then an AI-generated image given
    motion, then an arbitrary clip, then a plain colour card. prefer_ai moves
    generation to the front, which trades real footage for exact relevance.
    """
    work_dir = out_path.parent

    def _stock() -> str | None:
        if pexels_api_key:
            link = _fetch_pexels_clip(query, pexels_api_key, duration)
            if link:
                download_clip(link, out_path)
                return "pexels"
        if pixabay_api_key:
            link = _fetch_pixabay_clip(query, pixabay_api_key, duration)
            if link:
                download_clip(link, out_path)
                return "pixabay"
        return None

    def _ai() -> str | None:
        try:
            img = _ai_image(query, work_dir / f"ai_{index:03d}.jpg", pollinations_token)
            _ken_burns(img, duration, out_path, index)
            return "pollinations"
        except Exception as e:
            log.warning("AI image generation failed for %r: %s", query, e)
            return None

    def _openverse() -> str | None:
        try:
            link = _fetch_openverse_image(query)
            if not link:
                return None
            img = _download_image(link, work_dir / f"ov_{index:03d}.jpg")
            _ken_burns(img, duration, out_path, index)
            return "openverse-cc0"
        except Exception as e:
            log.warning("Openverse clip build failed for %r: %s", query, e)
            return None

    # Openverse needs no key at all, so it sits ahead of generation: it is the
    # only source that works out of the box on a fresh deploy.
    order = [_ai, _stock, _openverse] if prefer_ai else [_stock, _openverse, _ai]
    for step in order:
        source = step()
        if source:
            log.info("Footage for %r from %s", query, source)
            return source

    try:
        log.warning("No relevant footage for %r; using an arbitrary clip.", query)
        download_clip(_fetch_unrelated_mixkit_clip(), out_path)
        return "mixkit-arbitrary"
    except Exception as e:
        log.warning("Arbitrary clip failed too (%s); using a colour card.", e)
        generate_fallback_clip(duration, out_path, index)
        return "colour-card"


# Enough to fill 1080x1920 with headroom for the zoom, without hauling a
# 5644x10328 original across the network and through ffmpeg. Oversized sources
# cost download time and peak memory on a small Railway instance for no visible
# gain, since the output is 1080 wide either way.
IDEAL_SOURCE_HEIGHT = 2400

# Below this the upscale to 1920 tall is visible, so the source is rejected.
MIN_SOURCE_HEIGHT = 1200


def _score_openverse(result: dict) -> tuple:
    """Rank by how little cropping is needed, then by closeness to a sane size."""
    width = result.get("width") or 0
    height = result.get("height") or 0
    if not width or not height:
        return (-99.0, -99.0)
    ratio = height / width
    if height < 1920:
        # Upscaling shows; treat anything under the output height as a last pick.
        size_penalty = -(1920 - height) / 1920 - 1.0
    else:
        size_penalty = -abs(height - IDEAL_SOURCE_HEIGHT) / IDEAL_SOURCE_HEIGHT
    return (-abs(ratio - TARGET_RATIO), size_penalty)


def _fetch_openverse_image(query: str) -> str | None:
    """Find a public-domain image for `query`. No API key, no account.

    Restricted to cc0 and pdm because those two carry no attribution
    requirement -- every other CC licence would oblige the video to credit each
    photographer on screen.

    One broad request per query variant, ranked locally, rather than a grid of
    narrow server-side filters: asking for aspect_ratio=tall and size=large cut
    "hourglass sand" from 91 results to 13, and walking those filter
    combinations cost about a dozen round trips per sentence.
    """
    best_overall = None
    best_overall_score = None

    for variant in _query_variants(query)[:3]:
        try:
            resp = requests.get(
                OPENVERSE_IMAGES_URL,
                params={
                    "q": variant,
                    "license": "cc0,pdm",
                    "page_size": 20,
                    "mature": "false",
                },
                headers={"User-Agent": "youtube-automation/1.0"},
                timeout=20,
            )
            resp.raise_for_status()
            results = [
                r for r in resp.json().get("results", [])
                if r.get("url") and r.get("width") and r.get("height")
                # Skip pathological originals: a 5644x10328 scan is ~20MB to
                # fetch and decode for a 1080-wide output, and peak memory
                # matters on a small instance.
                and r["width"] * r["height"] <= 24_000_000
            ]
            if not results:
                continue

            results.sort(key=_score_openverse, reverse=True)
            best = results[0]
            score = _score_openverse(best)
            if best_overall_score is None or score > best_overall_score:
                best_overall, best_overall_score = best, score

            # Good enough to stop looking: roughly portrait and big enough that
            # the zoom will not show softness.
            if best["height"] / best["width"] >= 1.3 and best["height"] >= 1920:
                log.info(
                    "Openverse matched %r via %r (%sx%s, %s)",
                    query, variant, best["width"], best["height"], best.get("license"),
                )
                return best["url"]
        except Exception as e:
            log.warning("Openverse lookup failed for %r: %s", variant, e)

    # A 369x500 original blown up to 1080x1920 looks worse than a generated
    # image or even unrelated-but-sharp footage, so reject it and let the chain
    # fall through rather than shipping a soft frame.
    if best_overall and best_overall["height"] >= MIN_SOURCE_HEIGHT:
        log.info(
            "Openverse best-effort for %r (%sx%s, %s)",
            query, best_overall["width"], best_overall["height"], best_overall.get("license"),
        )
        return best_overall["url"]
    if best_overall:
        log.info(
            "Openverse only had %sx%s for %r; too small to upscale cleanly.",
            best_overall["width"], best_overall["height"], query,
        )
    return None


def _download_image(url: str, out_path: Path) -> Path:
    resp = requests.get(
        url, headers={"User-Agent": "youtube-automation/1.0"}, timeout=60, stream=True
    )
    resp.raise_for_status()
    with open(out_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            fh.write(chunk)
    if out_path.stat().st_size < 1024:
        raise RuntimeError(f"image at {url} was empty")
    return out_path
