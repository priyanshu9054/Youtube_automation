import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
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
    sentence_count: int
    upload_privacy_status: str
    auto_upload: bool

    @classmethod
    def load(cls) -> "Config":
        return cls(
            groq_api_key=_require("GROQ_API_KEY"),
            pixabay_api_key=_require("PIXABAY_API_KEY"),
            youtube_client_id=_require("YOUTUBE_CLIENT_ID"),
            youtube_client_secret=_require("YOUTUBE_CLIENT_SECRET"),
            youtube_refresh_token=_require("YOUTUBE_REFRESH_TOKEN"),
            channel_niche=os.environ.get(
                "CHANNEL_NICHE", "interesting bite-sized facts"
            ),
            tts_voice=os.environ.get("TTS_VOICE", "en-US-AriaNeural"),
            sentence_count=int(os.environ.get("SENTENCE_COUNT", "8")),
            upload_privacy_status=os.environ.get("UPLOAD_PRIVACY_STATUS", "private"),
            auto_upload=os.environ.get("AUTO_UPLOAD", "false").lower() == "true",
        )
