from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

from src.config import Config

# Single scope only — this is the one permission Google's consent screen will
# ask for. Dedup of topics is handled locally (see src/history.py) so we don't
# need the youtube.readonly scope at all.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

TOKEN_URI = "https://oauth2.googleapis.com/token"


def build_youtube_client(cfg: Config) -> Resource:
    creds = Credentials(
        token=None,
        refresh_token=cfg.youtube_refresh_token,
        token_uri=TOKEN_URI,
        client_id=cfg.youtube_client_id,
        client_secret=cfg.youtube_client_secret,
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds)
