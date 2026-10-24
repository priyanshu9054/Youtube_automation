import logging

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

from src.config import Config
from src.token_store import TokenStore

log = logging.getLogger("youtube_client")

# Single scope only — this is the one permission Google's consent screen will
# ask for. Dedup of topics is handled locally (see src/history.py) so we don't
# need the youtube.readonly scope at all.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class TokenUnavailable(RuntimeError):
    """Nothing to authorize with: the app has never been given a refresh token."""


class TokenRejected(RuntimeError):
    """Google refused the stored refresh token — expired, revoked or mismatched."""


def reauth_hint(cfg: Config) -> str:
    """How to get a working token again, phrased for whoever reads /status.

    Deliberately carries no secret: this string ends up in `last_error`, which
    /status serves unauthenticated, so the setup key is described rather than
    included.
    """
    base = "YouTube rejected the refresh token (invalid_grant: expired or revoked)."
    if cfg.public_base_url:
        return (
            f"{base} Re-authorize by opening {cfg.public_base_url}/auth/start"
            "?key=$AUTH_SETUP_TOKEN in a browser and approving the Google "
            "prompt. That writes the new token straight into the token store — "
            "no local script, no Railway variable to edit, no redeploy."
        )
    return (
        f"{base} Set PUBLIC_BASE_URL and AUTH_SETUP_TOKEN to enable browser "
        "re-authorization at /auth/start, or run scripts/authorize_youtube.py "
        "locally and copy the token into the store."
    )


def authorize(cfg: Config, store: TokenStore) -> Credentials:
    """Return credentials that have already been refreshed once, here and now.

    The refresh is the point. Called before rendering starts, it turns a dead
    token into a failure that costs one HTTP request instead of one wasted
    five-minute render — the pipeline used to discover this only at upload
    time, after the video existed, and then threw the video away.
    """
    stored = store.read()
    refresh_token = stored.refresh_token if stored else None

    if not refresh_token and cfg.youtube_refresh_token:
        # First boot after the move to the store, or a store that was lost with
        # its volume: adopt the env var once, then never prefer it again.
        if store.seed(cfg.youtube_refresh_token):
            log.info("Token store was empty; seeded it from YOUTUBE_REFRESH_TOKEN.")
        refresh_token = cfg.youtube_refresh_token

    if not refresh_token:
        raise TokenUnavailable(
            "No YouTube refresh token in the store and YOUTUBE_REFRESH_TOKEN is "
            f"unset. {reauth_hint(cfg)}"
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=cfg.youtube_client_id,
        client_secret=cfg.youtube_client_secret,
        scopes=SCOPES,
    )

    try:
        creds.refresh(Request())
    except RefreshError as e:
        store.mark_error(f"refresh rejected by Google: {e}")
        raise TokenRejected(reauth_hint(cfg)) from e

    if creds.refresh_token and creds.refresh_token != refresh_token:
        # Google hands back a replacement on some refreshes. Persisting it is
        # the whole reason the store exists: once it has done this, the token
        # we started the request with may already be dead.
        log.info("Google issued a replacement refresh token; saving it.")
        store.save(creds.refresh_token)
    else:
        store.mark_refresh_ok()

    return creds


def build_youtube_client(cfg: Config, store: TokenStore) -> Resource:
    return build("youtube", "v3", credentials=authorize(cfg, store))
