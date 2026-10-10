import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Ensure virtualenv bin directory is in PATH for ffmpeg/ffprobe subprocess calls
_venv_bin = str(Path(sys.prefix) / "bin")
if _venv_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{_venv_bin}:{os.environ.get('PATH', '')}"


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


VALID_PRIVACY_STATUSES = ("public", "private", "unlisted")


def _privacy_status() -> str:
    """Normalise UPLOAD_PRIVACY_STATUS, falling back to public on anything odd.

    A typo'd value would otherwise be handed straight to the YouTube API, so an
    unrecognised setting fails loudly in the log and still publishes publicly.
    """
    raw = os.environ.get("UPLOAD_PRIVACY_STATUS", "public")
    value = raw.strip().strip("\"'").lower()
    if value not in VALID_PRIVACY_STATUSES:
        logging.getLogger("config").warning(
            "UPLOAD_PRIVACY_STATUS=%r is not one of %s; using 'public'.",
            raw,
            ", ".join(VALID_PRIVACY_STATUSES),
        )
        return "public"
    return value


@dataclass(frozen=True)
class Config:
    groq_api_key: str
    pixabay_api_key: str
    youtube_client_id: str
    youtube_client_secret: str
    youtube_refresh_token: str
    channel_niche: str
    tts_voice: str
    tts_rate: str
    tts_pitch: str
    sentence_count: int
    upload_privacy_status: str
    auto_upload: bool
    groq_model: str
    enable_music: bool
    music_volume: float
    groq_max_tokens: int = 600

    @classmethod
    def load(cls) -> "Config":
        return cls(
            groq_api_key=_require("GROQ_API_KEY"),
            pixabay_api_key=os.environ.get("PIXABAY_API_KEY", "").strip(),
            youtube_client_id=_require("YOUTUBE_CLIENT_ID"),
            youtube_client_secret=_require("YOUTUBE_CLIENT_SECRET"),
            youtube_refresh_token=_require("YOUTUBE_REFRESH_TOKEN"),
            channel_niche=os.environ.get(
                "CHANNEL_NICHE", "interesting bite-sized facts"
            ),
            tts_voice=os.environ.get("TTS_VOICE", "en-US-ChristopherNeural"),
            tts_rate=os.environ.get("TTS_RATE", "-12%"),
            tts_pitch=os.environ.get("TTS_PITCH", "-15Hz"),
            sentence_count=int(os.environ.get("SENTENCE_COUNT", "8")),
            upload_privacy_status=_privacy_status(),
            auto_upload=os.environ.get("AUTO_UPLOAD", "false").lower() == "true",
            groq_model=os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b"),
            enable_music=os.environ.get("ENABLE_MUSIC", "true").lower() == "true",
            music_volume=float(os.environ.get("MUSIC_VOLUME", "0.15")),
            groq_max_tokens=int(os.environ.get("GROQ_MAX_TOKENS", "600")),
        )
