"""
One-time local setup: exchanges your Google Cloud OAuth client for a refresh
token and updates your .env automatically.

Supports:
- Desktop App OAuth clients (type: "installed")
- Web Application OAuth clients (type: "web", e.g. with https://localhost:8000/oauth/callback)
- Automatic browser callback interception AND manual paste fallback
- Automatic .env file updating

Usage:
    python scripts/authorize_youtube.py [path/to/client_secret.json]
"""

import glob
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
from http.server import BaseHTTPRequestHandler, HTTPServer
import webbrowser

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def find_client_secret(arg_path: str = None) -> str:
    if arg_path and os.path.exists(arg_path):
        return arg_path

    # Try looking for client_secret*.json in current directory
    matches = glob.glob("client_secret*.json")
    if matches:
        return matches[0]

    if os.path.exists("client_secret.json"):
        return "client_secret.json"

    return None


def create_self_signed_cert() -> tuple[str, str]:
    cert_dir = tempfile.mkdtemp()
    cert_file = os.path.join(cert_dir, "cert.pem")
    key_file = os.path.join(cert_dir, "key.pem")
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            key_file,
            "-out",
            cert_file,
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return cert_file, key_file


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    auth_code = None
    auth_error = None
    done_event = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            OAuthCallbackHandler.auth_code = params["code"][0]
            if OAuthCallbackHandler.done_event:
                OAuthCallbackHandler.done_event.set()

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = """
            <!DOCTYPE html>
            <html>
            <head><title>Authorization Successful</title></head>
            <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #0f172a; color: #f8fafc;">
                <div style="background: #1e293b; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); text-align: center; max-width: 480px; border: 1px solid #334155;">
                    <div style="font-size: 48px; margin-bottom: 16px;">✅</div>
                    <h2 style="color: #4ade80; margin-top: 0;">Authorization Successful!</h2>
                    <p style="color: #94a3b8; font-size: 16px; line-height: 1.5;">Your YouTube authorization code has been received. You can safely close this browser window and return to the terminal.</p>
                </div>
            </body>
            </html>
            """
            self.wfile.write(html.encode("utf-8"))
        elif "error" in params:
            OAuthCallbackHandler.auth_error = params["error"][0]
            if OAuthCallbackHandler.done_event:
                OAuthCallbackHandler.done_event.set()

            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                f"Authorization error: {OAuthCallbackHandler.auth_error}".encode("utf-8")
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress noisy HTTP access logs
        return


def update_env_file(
    client_id: str, client_secret: str, refresh_token: str, env_path: str = ".env"
) -> None:
    if not os.path.exists(env_path):
        if os.path.exists(".env.example"):
            with open(".env.example", "r") as f:
                content = f.read()
        else:
            content = ""
    else:
        with open(env_path, "r") as f:
            content = f.read()

    def set_key(text: str, key: str, val: str) -> str:
        pattern = rf"^{key}=.*$"
        replacement = f"{key}={val}"
        if re.search(pattern, text, flags=re.MULTILINE):
            return re.sub(pattern, replacement, text, flags=re.MULTILINE)
        else:
            return text.rstrip() + f"\n{replacement}\n"

    content = set_key(content, "YOUTUBE_CLIENT_ID", client_id)
    content = set_key(content, "YOUTUBE_CLIENT_SECRET", client_secret)
    content = set_key(content, "YOUTUBE_REFRESH_TOKEN", refresh_token)

    with open(env_path, "w") as f:
        f.write(content)


def main() -> None:
    client_secret_path = sys.argv[1] if len(sys.argv) > 1 else find_client_secret()

    if not client_secret_path or not os.path.exists(client_secret_path):
        print(f"Error: Client secret JSON file not found: {client_secret_path}")
        print("Usage: python scripts/authorize_youtube.py [path/to/client_secret.json]")
        sys.exit(1)

    with open(client_secret_path) as f:
        client_data = json.load(f)

    is_installed = "installed" in client_data
    is_web = "web" in client_data

    if not is_installed and not is_web:
        print("Error: JSON must contain either 'installed' or 'web' client configuration.")
        sys.exit(1)

    client_info = client_data.get("installed") or client_data.get("web")
    client_id = client_info["client_id"]
    client_secret = client_info["client_secret"]

    print(f"Loaded credentials from: {client_secret_path}")
    print(f"Client ID: {client_id}")

    if is_installed:
        # Standard Desktop app flow
        print("\nStarting local authentication server for Desktop Application...")
        flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    else:
        # Web application flow
        redirect_uris = client_info.get("redirect_uris", [])
        if not redirect_uris:
            print("Error: No redirect_uris specified in 'web' client configuration.")
            sys.exit(1)

        redirect_uri = redirect_uris[0]
        parsed_uri = urllib.parse.urlparse(redirect_uri)
        port = parsed_uri.port or (443 if parsed_uri.scheme == "https" else 80)
        is_https = parsed_uri.scheme == "https"

        print(f"\nConfigured redirect URI: {redirect_uri}")
        flow = InstalledAppFlow.from_client_secrets_file(
            client_secret_path,
            scopes=SCOPES,
            redirect_uri=redirect_uri,
        )

        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

        done_event = threading.Event()
        OAuthCallbackHandler.done_event = done_event
        OAuthCallbackHandler.auth_code = None
        OAuthCallbackHandler.auth_error = None

        server = HTTPServer(("localhost", port), OAuthCallbackHandler)

        if is_https:
            cert_file, key_file = create_self_signed_cert()
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(certfile=cert_file, keyfile=key_file)
            server.socket = ssl_context.wrap_socket(server.socket, server_side=True)

        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        print("\n" + "=" * 70)
        print("Please authorize the app in your browser:")
        print(auth_url)
        print("=" * 70 + "\n")

        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

        print(f"Waiting for authorization callback on {redirect_uri}...")
        print("Note: If your browser shows an SSL warning on localhost, you can click")
        print("'Proceed to localhost (unsafe)', OR you can simply paste the redirected")
        print("URL or authorization code from your browser address bar below:")

        pasted_code = None
        while not done_event.is_set():
            if sys.stdin.isatty():
                readable, _, _ = select.select([sys.stdin], [], [], 0.5)
                if readable:
                    line = sys.stdin.readline().strip()
                    if line:
                        if "code=" in line:
                            parsed_in = urllib.parse.urlparse(line)
                            pasted_params = urllib.parse.parse_qs(parsed_in.query)
                            pasted_code = pasted_params.get("code", [None])[0]
                        else:
                            pasted_code = line
                        done_event.set()
                        break
            else:
                done_event.wait(timeout=1.0)

        server.shutdown()

        auth_code = pasted_code or OAuthCallbackHandler.auth_code

        if not auth_code:
            print(f"\nAuthorization failed or cancelled. Error: {OAuthCallbackHandler.auth_error}")
            sys.exit(1)

        print("\nExchanging authorization code for tokens...")
        flow.fetch_token(code=auth_code)
        creds = flow.credentials

    refresh_token = creds.refresh_token

    if not refresh_token:
        print("\nWarning: No refresh token returned. Did you grant offline access?")
        print("Tip: If you previously authorized this app, go to https://myaccount.google.com/permissions,")
        print("revoke access, and run this script again.")
        sys.exit(1)

    update_env_file(client_id, client_secret, refresh_token)

    print("\n" + "=" * 70)
    print("Authorization complete! .env has been successfully updated:")
    print(f"YOUTUBE_CLIENT_ID={client_id}")
    print(f"YOUTUBE_CLIENT_SECRET={client_secret}")
    print(f"YOUTUBE_REFRESH_TOKEN={refresh_token}")
    print("=" * 70)


if __name__ == "__main__":
    main()
