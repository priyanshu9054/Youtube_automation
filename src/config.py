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


def _public_base_url() -> str:
    """Externally reachable origin of this service, with no trailing slash.

    Needed to build the OAuth redirect URI for the browser re-auth flow. On
    Railway this is already in the environment, so the common case needs no
    configuration at all; PUBLIC_BASE_URL overrides it for local tunnels.
    """
    explicit = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    return f"https://{domain}" if domain else ""


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
    pexels_api_key: str = ""
    # Free, no signup. Without a token, generated images carry a small
    # pollinations.ai watermark and are limited to ~1 request per 15s.
    pollinations_token: str = ""
    # Generate imagery from the script instead of searching stock for it.
    prefer_ai_footage: bool = False
    # Silence appended after each sentence. The main lever for a calmer read:
    # sentences otherwise butt straight together, since each one is synthesised
    # and rendered as its own segment.
    tts_sentence_silence: float = 0.4
    # Where run state and the OAuth token store live. On Railway this must be a
    # mounted volume or both are lost on every restart.
    data_dir: Path = Path("data")
    public_base_url: str = ""
    # Shared secret guarding /auth/start. Without it the re-auth endpoints
    # refuse to serve: they are public URLs, and an unguarded one would let a
    # stranger point this uploader at their own YouTube channel.
    auth_setup_token: str = ""

    @classmethod
    def load(cls) -> "Config":
        return cls(
            groq_api_key=_require("GROQ_API_KEY"),
            pixabay_api_key=os.environ.get("PIXABAY_API_KEY", "").strip(),
            youtube_client_id=_require("YOUTUBE_CLIENT_ID"),
            youtube_client_secret=_require("YOUTUBE_CLIENT_SECRET"),
            # Optional on purpose: this only ever seeds an empty token
            # store (see src/token_store.py). Once the app has authorized
            # itself once, the store holds the live token and this is dead
            # weight -- which is the point, since it was the thing that had
            # to be edited by hand every time Google expired a token.
            youtube_refresh_token=os.environ.get("YOUTUBE_REFRESH_TOKEN", "").strip(),
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
            pexels_api_key=os.environ.get("PEXELS_API_KEY", "").strip(),
            pollinations_token=os.environ.get("POLLINATIONS_TOKEN", "").strip(),
            prefer_ai_footage=os.environ.get("PREFER_AI_FOOTAGE", "false").lower() == "true",
            tts_sentence_silence=float(os.environ.get("TTS_SENTENCE_SILENCE", "0.4")),
            data_dir=Path(os.environ.get("DATA_DIR", "data")),
            public_base_url=_public_base_url(),
            auth_setup_token=os.environ.get("AUTH_SETUP_TOKEN", "").strip(),
        )

    @property
    def token_db_path(self) -> Path:
        return self.data_dir / "tokens.db"
