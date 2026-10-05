from pathlib import Path

import requests

PIXABAY_VIDEOS_URL = "https://pixabay.com/api/videos/"


def find_clip_url(query: str, api_key: str, min_duration: float) -> str:
    """Return a download URL for a Pixabay clip matching `query`, preferring
    ones at least `min_duration` seconds long. Pixabay videos are typically
    landscape; render.py scales+center-crops to vertical, so that's fine."""
    resp = requests.get(
        PIXABAY_VIDEOS_URL,
        params={"key": api_key, "q": query, "per_page": 10, "safesearch": "true"},
        timeout=30,
    )
    resp.raise_for_status()
    hits = resp.json().get("hits", [])
    if not hits:
        raise RuntimeError(f"No Pixabay results for query: {query!r}")

    hits.sort(key=lambda h: abs(h.get("duration", 0) - min_duration))

    for hit in hits:
        variants = hit.get("videos", {})
        for size in ("large", "medium", "small", "tiny"):
            if size in variants:
                return variants[size]["url"]

    raise RuntimeError(f"No usable video file for query: {query!r}")


def download_clip(url: str, out_path: Path) -> Path:
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    return out_path
