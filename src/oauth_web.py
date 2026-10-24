"""Browser re-authorization, served by the deployed app itself.

Google expires the refresh token every 7 days while the OAuth consent screen
sits in Testing mode, and nothing in this codebase can prevent that: a token
Google has revoked cannot be renewed, only replaced. What it can do is make the
replacement cost one click instead of a local script run, a copy-paste into the
Railway dashboard and a redeploy. Opening /auth/start and approving the Google
prompt writes the new token into the SQLite store (src/token_store.py) while
the service keeps running.

These are public URLs on a public domain, so they are gated on a shared secret
(AUTH_SETUP_TOKEN). Without that gate a stranger could walk the consent flow
with their own Google account and quietly repoint this uploader at their own
channel -- the app only ever asks for youtube.upload, so whoever approves last
owns the destination.
"""

import logging
import os
import secrets
import time
from typing import Optional

from flask import Blueprint, Response, jsonify, redirect, request

from src.config import Config
from src.token_store import TokenStore
from src.youtube_client import AUTH_URI, SCOPES, TOKEN_URI

log = logging.getLogger("oauth_web")

# Google echoes the granted scope back in a different order (and occasionally
# with an extra alias) than it was requested in. oauthlib treats any difference
# as a scope change and raises, which would fail an otherwise perfect
# authorization. We request exactly one scope, so there is nothing to widen.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

STATE_TTL_SECONDS = 600


def _page(title: str, heading: str, body: str, colour: str, emoji: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><title>{title}</title><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;background:#0f172a;color:#f8fafc">
  <div style="background:#1e293b;padding:40px;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.5);text-align:center;max-width:480px;border:1px solid #334155">
    <div style="font-size:48px;margin-bottom:16px">{emoji}</div>
    <h2 style="color:{colour};margin-top:0">{heading}</h2>
    <p style="color:#94a3b8;font-size:16px;line-height:1.5">{body}</p>
  </div>
</body>
</html>"""


def build_oauth_blueprint(store: TokenStore) -> Blueprint:
    bp = Blueprint("oauth", __name__)

    # state -> created_at. In-memory is the right lifetime here: a state is
    # valid for one redirect round-trip, and a restart mid-flow should
    # invalidate it rather than honour it.
    pending_states: dict[str, float] = {}

    def _remember(state: str) -> None:
        now = time.time()
        for old, created in list(pending_states.items()):
            if now - created > STATE_TTL_SECONDS:
                pending_states.pop(old, None)
        pending_states[state] = now

    def _consume(state: str) -> bool:
        created = pending_states.pop(state, None)
        return created is not None and (time.time() - created) <= STATE_TTL_SECONDS

    def _redirect_uri(cfg: Config) -> str:
        return f"{cfg.public_base_url}/oauth/callback"

    def _client_config(cfg: Config) -> dict:
        # Built from env vars rather than the client_secret JSON so the file
        # never has to be shipped into the image.
        return {
            "web": {
                "client_id": cfg.youtube_client_id,
                "client_secret": cfg.youtube_client_secret,
                "auth_uri": AUTH_URI,
                "token_uri": TOKEN_URI,
            }
        }

    def _blocked(cfg: Config, *, check_key: bool) -> Optional[tuple]:
        """Reason this request must not proceed, as a ready-to-return response."""
        if not cfg.auth_setup_token:
            return (
                jsonify(
                    status="disabled",
                    message=(
                        "Browser re-authorization is off because AUTH_SETUP_TOKEN "
                        "is unset. Set it to a long random string in the service "
                        "variables, then open /auth/start?key=<that string>."
                    ),
                ),
                503,
            )
        if not cfg.public_base_url:
            return (
                jsonify(
                    status="misconfigured",
                    message=(
                        "Cannot build an OAuth redirect URI: neither "
                        "PUBLIC_BASE_URL nor RAILWAY_PUBLIC_DOMAIN is set."
                    ),
                ),
                503,
            )
        if check_key and not secrets.compare_digest(
            request.args.get("key", ""), cfg.auth_setup_token
        ):
            log.warning("Rejected /auth/start with a missing or wrong ?key=")
            return (
                jsonify(
                    status="forbidden",
                    message="Missing or invalid ?key= (must equal AUTH_SETUP_TOKEN).",
                ),
                403,
            )
        return None

    @bp.get("/auth/start")
    def auth_start():
        cfg = Config.load()
        blocked = _blocked(cfg, check_key=True)
        if blocked:
            return blocked

        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(
            _client_config(cfg), scopes=SCOPES, redirect_uri=_redirect_uri(cfg)
        )
        state = secrets.token_urlsafe(24)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            # Without this Google returns an access token but no refresh token
            # on a repeat authorization, which is exactly the case we are in.
            prompt="consent",
            state=state,
        )
        _remember(state)
        log.info("Starting OAuth re-authorization; redirecting to Google.")
        return redirect(auth_url, code=302)

    @bp.get("/oauth/callback")
    def oauth_callback():
        cfg = Config.load()
        # The key is not checked here: Google only sends back code+state, so
        # `state` is what proves this callback belongs to a flow we started.
        blocked = _blocked(cfg, check_key=False)
        if blocked:
            return blocked

        error = request.args.get("error")
        if error:
            return (
                Response(
                    _page(
                        "Authorization failed",
                        "Authorization failed",
                        f"Google reported: {error}. The stored token is unchanged.",
                        "#f87171",
                        "&#10060;",
                    ),
                    mimetype="text/html",
                ),
                400,
            )

        state = request.args.get("state", "")
        if not _consume(state):
            log.warning("Rejected /oauth/callback with an unknown or expired state.")
            return (
                Response(
                    _page(
                        "Authorization rejected",
                        "Link expired",
                        "This callback did not match a flow started here, or it "
                        "took longer than 10 minutes. Start again at /auth/start.",
                        "#f87171",
                        "&#9888;&#65039;",
                    ),
                    mimetype="text/html",
                ),
                403,
            )

        code = request.args.get("code")
        if not code:
            return (
                Response(
                    _page(
                        "Authorization incomplete",
                        "No authorization code",
                        "Google returned no code. The stored token is unchanged.",
                        "#f87171",
                        "&#10060;",
                    ),
                    mimetype="text/html",
                ),
                400,
            )

        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(
            _client_config(cfg),
            scopes=SCOPES,
            redirect_uri=_redirect_uri(cfg),
            state=state,
        )
        try:
            flow.fetch_token(code=code)
        except Exception as e:
            log.exception("Token exchange failed")
            return (
                Response(
                    _page(
                        "Authorization failed",
                        "Token exchange failed",
                        f"{e}. The stored token is unchanged.",
                        "#f87171",
                        "&#10060;",
                    ),
                    mimetype="text/html",
                ),
                400,
            )

        refresh_token = flow.credentials.refresh_token
        if not refresh_token:
            # Nothing to store, and overwriting with an access-token-only
            # credential would leave the app worse off than before.
            log.error("Authorization returned no refresh token; store left alone.")
            return (
                Response(
                    _page(
                        "Authorization incomplete",
                        "No refresh token returned",
                        "Google granted access but issued no refresh token, so "
                        "nothing was saved. Revoke this app at "
                        "myaccount.google.com/permissions and try again.",
                        "#fbbf24",
                        "&#9888;&#65039;",
                    ),
                    mimetype="text/html",
                ),
                400,
            )

        store.save(refresh_token)
        log.info("Re-authorization complete; new refresh token saved to the store.")
        return Response(
            _page(
                "Authorization successful",
                "Authorization successful",
                "The new refresh token has been saved to the token store. The "
                "next run will upload normally &mdash; no redeploy needed. You "
                "can close this tab.",
                "#4ade80",
                "&#9989;",
            ),
            mimetype="text/html",
        )

    return bp
