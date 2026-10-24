#!/usr/bin/env python3
"""One command: sign in to Google, get the variable to paste into Railway.

    python scripts/login.py

Prints the single YOUTUBE_REFRESH_TOKEN=... line to paste into the Railway
variables (and copies it to your clipboard). Also updates .env and the local
token store so a local run works straight away.

Changing YOUTUBE_REFRESH_TOKEN in Railway is enough on its own: the app notices
the variable changed and adopts it over whatever is in its token store.

Handles both OAuth client types, because they redirect differently:
  Desktop ("installed") -- loopback on a random port, nothing to register.
  Web                   -- must use a redirect URI already registered on the
                           client. This project's is https://localhost:8000/...,
                           so a throwaway self-signed cert is generated and your
                           browser will warn once; click through it, or paste the
                           redirected URL back into the terminal instead.

Only needed weekly while the OAuth consent screen is in Testing mode. Publish
the app to Production and the token stops expiring, making this a once-ever step.
"""

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


def find_client_secret() -> Path:
    matches = sorted(REPO.glob("client_secret*.json"))
    if matches:
        return matches[0]
    sys.exit(
        "No client_secret*.json in the repo root.\n"
        "Google Cloud Console -> Credentials -> your OAuth client -> Download JSON."
    )


def set_env_key(text: str, key: str, value: str) -> str:
    if re.search(rf"^{key}=.*$", text, flags=re.MULTILINE):
        return re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.MULTILINE)
    return text.rstrip() + f"\n{key}={value}\n"


def copy_to_clipboard(text: str) -> bool:
    for cmd in (["pbcopy"], ["xclip", "-selection", "clipboard"], ["wl-copy"]):
        try:
            subprocess.run(cmd, input=text.encode(), check=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return False


class _Callback(BaseHTTPRequestHandler):
    code = None
    error = None
    done = None

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in params:
            _Callback.code = params["code"][0]
            body = "<h2>Signed in.</h2><p>Close this tab and return to the terminal.</p>"
            status = 200
        elif "error" in params:
            _Callback.error = params["error"][0]
            body = f"<h2>Authorization failed</h2><p>{_Callback.error}</p>"
            status = 400
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
            f"<div>{body}</div></body>".encode()
        )

    def log_message(self, *a):
        return


def _self_signed_cert() -> tuple[str, str]:
    d = tempfile.mkdtemp()
    cert, key = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", key,
         "-out", cert, "-days", "1", "-nodes", "-subj", "/CN=localhost"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return cert, key


def web_flow(secret_path: Path, redirect_uri: str):
    """Authorize a 'web' client against one of its registered redirect URIs."""
    from google_auth_oauthlib.flow import Flow

    parsed = urllib.parse.urlparse(redirect_uri)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    flow = Flow.from_client_secrets_file(
        str(secret_path), scopes=SCOPES, redirect_uri=redirect_uri
    )
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    done = threading.Event()
    _Callback.done, _Callback.code, _Callback.error = done, None, None
    server = HTTPServer(("localhost", port), _Callback)
    if parsed.scheme == "https":
        cert, key = _self_signed_cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"\nListening on {redirect_uri}")
    print("Opening your browser. Approve the one permission it asks for.\n")
    if parsed.scheme == "https":
        print("Your browser WILL warn about the self-signed localhost certificate.")
        print("  -> click Advanced, then 'Proceed to localhost (unsafe)'.")
    print("If that is awkward, paste the full redirected URL (or just the code)")
    print("here and press Enter instead:\n")
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
    return flow.credentials


def main() -> None:
    secret_path = find_client_secret()
    info = json.loads(secret_path.read_text())
    client = info.get("installed") or info.get("web")
    if not client:
        sys.exit(f"{secret_path.name} has neither an 'installed' nor a 'web' client.")

    print(f"Using {secret_path.name}")

    if "installed" in info:
        from google_auth_oauthlib.flow import InstalledAppFlow

        print("Desktop client: using a loopback redirect on a free port.")
        flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    else:
        uris = client.get("redirect_uris") or []
        if not uris:
            sys.exit(
                "This is a 'web' OAuth client with no redirect_uris in the JSON.\n"
                "Either add http://localhost:8080/ to its Authorized redirect URIs\n"
                "and re-download the JSON, or (better) create a 'Desktop app'\n"
                "OAuth client, which needs no redirect URI management at all."
            )
        # Prefer a plain-http loopback if one is registered: no cert, no warning.
        uri = next((u for u in uris if u.startswith("http://localhost")), uris[0])
        print(f"Web client: using registered redirect URI {uri}")
        creds = web_flow(secret_path, uri)

    token = creds.refresh_token
    if not token:
        sys.exit(
            "Google returned no refresh token.\n"
            "Revoke this app at https://myaccount.google.com/permissions and retry."
        )

    env_path = REPO / ".env"
    text = env_path.read_text() if env_path.exists() else ""
    text = set_env_key(text, "YOUTUBE_CLIENT_ID", client["client_id"])
    text = set_env_key(text, "YOUTUBE_CLIENT_SECRET", client["client_secret"])
    text = set_env_key(text, "YOUTUBE_REFRESH_TOKEN", token)
    env_path.write_text(text)

    try:
        from src.token_store import TokenStore

        db = REPO / os.environ.get("DATA_DIR", "data") / "tokens.db"
        TokenStore(db).save(token, env_seed=token)
        store_note = f"local token store updated ({db.relative_to(REPO)})"
    except Exception as e:
        store_note = f"local token store NOT updated ({e})"

    line = f"YOUTUBE_REFRESH_TOKEN={token}"
    copied = copy_to_clipboard(line)

    print("\n" + "=" * 72)
    print("PASTE THIS ONE LINE INTO RAILWAY -> Variables:\n")
    print(f"  {line}\n")
    print("=" * 72)
    print(f".env updated, {store_note}.")
    if copied:
        print("Copied to your clipboard.")
    print(
        "\nRailway -> Youtube_automation -> Variables -> YOUTUBE_REFRESH_TOKEN.\n"
        "Saving it redeploys; on the next run the app sees the variable changed\n"
        "and adopts it over its stored token. Nothing else to do."
    )


if __name__ == "__main__":
    main()
