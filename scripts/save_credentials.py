#!/usr/bin/env python3
"""Get a YouTube refresh token, save it everywhere, and prove it works.

    python scripts/save_credentials.py            sign in with Google, save, verify
    python scripts/save_credentials.py --token X  save a token you already have, verify
    python scripts/save_credentials.py --check    verify what is saved, change nothing

Saves to BOTH places that matter:
  .env                  so a local run picks it up
  $DATA_DIR/tokens.db   the store the app actually reads at runtime

Then does a real refresh against Google, so you find out here -- not on the next
scheduled run -- whether the credential is good.

Only needed weekly while the OAuth consent screen is in Testing mode. Publish
the app to Production and the token stops expiring, making this a once-ever step.
"""

import argparse
import json
import os
import re
import select
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_URI = "https://oauth2.googleapis.com/token"

OK, BAD, DIM = "\033[32m", "\033[31m", "\033[2m"
OFF = "\033[0m"


def say(msg, colour=""):
    print(f"{colour}{msg}{OFF}" if sys.stdout.isatty() else msg)


def client_secret_file() -> Path:
    matches = sorted(REPO.glob("client_secret*.json"))
    if not matches:
        sys.exit(
            "No client_secret*.json in the repo root.\n"
            "Google Cloud Console -> Credentials -> your OAuth client -> Download JSON."
        )
    return matches[0]


def load_client() -> tuple[Path, dict, bool]:
    path = client_secret_file()
    info = json.loads(path.read_text())
    client = info.get("installed") or info.get("web")
    if not client:
        sys.exit(f"{path.name} has neither an 'installed' nor a 'web' client.")
    return path, client, "installed" in info


# --------------------------------------------------------------------------
# saving
# --------------------------------------------------------------------------

def write_env(client: dict, token: str) -> None:
    path = REPO / ".env"
    text = path.read_text() if path.exists() else ""
    for key, value in (
        ("YOUTUBE_CLIENT_ID", client["client_id"]),
        ("YOUTUBE_CLIENT_SECRET", client["client_secret"]),
        ("YOUTUBE_REFRESH_TOKEN", token),
    ):
        if re.search(rf"^{key}=.*$", text, flags=re.MULTILINE):
            text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.MULTILINE)
        else:
            text = text.rstrip() + f"\n{key}={value}\n"
    path.write_text(text)


def write_store(token: str) -> str:
    from src.token_store import TokenStore

    db = REPO / os.environ.get("DATA_DIR", "data") / "tokens.db"
    # env_seed is set to the same value so the app does not later mistake the
    # matching .env entry for a human override and re-adopt it needlessly.
    TokenStore(db).save(token, env_seed=token)
    return str(db.relative_to(REPO))


def clipboard(text: str) -> bool:
    for cmd in (["pbcopy"], ["xclip", "-selection", "clipboard"], ["wl-copy"]):
        try:
            subprocess.run(cmd, input=text.encode(), check=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return False


# --------------------------------------------------------------------------
# verifying
# --------------------------------------------------------------------------

def verify(token: str, client: dict) -> bool:
    """Exchange the refresh token for an access token, for real."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google.auth.exceptions import RefreshError

    creds = Credentials(
        token=None,
        refresh_token=token,
        token_uri=TOKEN_URI,
        client_id=client["client_id"],
        client_secret=client["client_secret"],
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())
    except RefreshError as e:
        say(f"\nVERIFY FAILED: Google rejected this token.\n  {e}", BAD)
        return False
    say(f"\nVERIFIED: Google accepted it. Access token expires {creds.expiry} UTC.", OK)
    return True


# --------------------------------------------------------------------------
# the OAuth dance
# --------------------------------------------------------------------------

class _Callback(BaseHTTPRequestHandler):
    code = error = done = None

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in q:
            _Callback.code, msg, status = q["code"][0], "Signed in. Close this tab.", 200
        elif "error" in q:
            _Callback.error, msg, status = q["error"][0], f"Failed: {q['error'][0]}", 400
        else:
            self.send_response(404); self.end_headers(); return
        if _Callback.done:
            _Callback.done.set()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            f"<body style='font-family:sans-serif;background:#0f172a;color:#f8fafc;"
            f"display:flex;align-items:center;justify-content:center;height:100vh'>"
            f"<h2>{msg}</h2></body>".encode()
        )

    def log_message(self, *a):
        return


def _cert() -> tuple[str, str]:
    d = tempfile.mkdtemp()
    cert, key = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", key, "-out",
         cert, "-days", "1", "-nodes", "-subj", "/CN=localhost"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return cert, key


def sign_in(path: Path, client: dict, is_desktop: bool) -> str:
    if is_desktop:
        from google_auth_oauthlib.flow import InstalledAppFlow

        say("Desktop OAuth client: using a loopback redirect on a free port.", DIM)
        flow = InstalledAppFlow.from_client_secrets_file(str(path), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
        return creds.refresh_token

    from google_auth_oauthlib.flow import Flow

    uris = client.get("redirect_uris") or []
    if not uris:
        sys.exit(
            "This 'web' OAuth client has no redirect_uris in the JSON.\n"
            "Add http://localhost:8080/ to its Authorized redirect URIs and\n"
            "re-download the JSON -- or create a 'Desktop app' client, which\n"
            "needs no redirect URI management at all."
        )
    # Prefer plain-http loopback when registered: no certificate, no warning.
    uri = next((u for u in uris if u.startswith("http://localhost")), uris[0])
    parsed = urllib.parse.urlparse(uri)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    flow = Flow.from_client_secrets_file(str(path), scopes=SCOPES, redirect_uri=uri)
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    done = threading.Event()
    _Callback.done, _Callback.code, _Callback.error = done, None, None
    server = HTTPServer(("localhost", port), _Callback)
    if parsed.scheme == "https":
        cert, key = _cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    say(f"Web OAuth client: listening on {uri}", DIM)
    if parsed.scheme == "https":
        say("\nYour browser WILL warn about the self-signed localhost certificate.")
        say("  -> Advanced, then 'Proceed to localhost (unsafe)'.")
    say("\nIf the browser does not open, or you would rather not click through the")
    say("warning, open this URL and paste the redirected URL (or just the code) back")
    say("here, then press Enter:\n")
    print(auth_url + "\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    pasted = None
    while not done.is_set():
        if sys.stdin.isatty() and select.select([sys.stdin], [], [], 0.5)[0]:
            line = sys.stdin.readline().strip()
            if line:
                if "code=" in line:
                    q = urllib.parse.parse_qs(urllib.parse.urlparse(line).query)
                    pasted = q.get("code", [None])[0]
                else:
                    pasted = line
                break
        else:
            done.wait(timeout=0.5)
    server.shutdown()

    code = pasted or _Callback.code
    if not code:
        sys.exit(f"No authorization code received. Error: {_Callback.error}")
    flow.fetch_token(code=code)
    return flow.credentials.refresh_token


# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", help="save a refresh token you already have")
    ap.add_argument("--check", action="store_true",
                    help="verify the stored token; change nothing")
    args = ap.parse_args()

    path, client, is_desktop = load_client()
    say(f"OAuth client: {path.name} ({'desktop' if is_desktop else 'web'})", DIM)

    if args.check:
        from src.token_store import TokenStore

        db = REPO / os.environ.get("DATA_DIR", "data") / "tokens.db"
        stored = TokenStore(db).read()
        if not stored:
            sys.exit("Token store is empty. Run this script with no arguments.")
        say(f"Stored token: ...{stored.refresh_token[-10:]}")
        sys.exit(0 if verify(stored.refresh_token, client) else 1)

    token = args.token
    if token:
        say("Saving the token you supplied.", DIM)
    else:
        say("Opening your browser. Approve the one permission it asks for.\n")
        token = sign_in(path, client, is_desktop)

    if not token:
        sys.exit(
            "No refresh token obtained.\n"
            "Revoke this app at https://myaccount.google.com/permissions and retry."
        )

    if not verify(token, client):
        sys.exit("Not saving a token Google already rejects.")

    write_env(client, token)
    try:
        where = write_store(token)
        store_note = f"token store updated ({where})"
    except Exception as e:
        store_note = f"token store NOT updated ({e})"

    line = f"YOUTUBE_REFRESH_TOKEN={token}"
    copied = clipboard(line)

    print("\n" + "=" * 72)
    say("SAVED AND VERIFIED. Local runs will work now.", OK)
    print("=" * 72)
    print(f"  .env updated, {store_note}.")
    if copied:
        print("  Copied the line below to your clipboard.")
    print(f"\n  {line}\n")
    print("Only if you also run this on a server: paste that one line into its")
    print("environment variables. The app notices the variable changed and adopts")
    print("it over whatever is in its own store.")


if __name__ == "__main__":
    main()
