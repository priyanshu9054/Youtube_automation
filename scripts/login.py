#!/usr/bin/env python3
"""One command: sign in to Google, get the value to paste into Railway.

    python scripts/login.py

Opens a browser, asks for one permission (youtube.upload), and prints the
single line to paste into the Railway variables. It also updates .env and the
local token store so a local run works straight away.

Changing YOUTUBE_REFRESH_TOKEN in Railway is enough on its own -- the app
notices the variable changed and adopts it, overriding whatever is in its
token store (see TokenStore.sync_from_env).

Note this is only needed weekly while the OAuth consent screen is in Testing
mode. Publish the app to Production and the token stops expiring, making this
script a once-ever step rather than a chore.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def find_client_secret() -> Path:
    for pattern in ("client_secret*.json", "client_secret.json"):
        matches = sorted(REPO.glob(pattern))
        if matches:
            return matches[0]
    sys.exit(
        "No client_secret*.json found in the repo root.\n"
        "Download it from Google Cloud Console -> Credentials -> your OAuth client."
    )


def set_env_key(text: str, key: str, value: str) -> str:
    line = f"{key}={value}"
    if re.search(rf"^{key}=.*$", text, flags=re.MULTILINE):
        return re.sub(rf"^{key}=.*$", line, text, flags=re.MULTILINE)
    return text.rstrip() + f"\n{line}\n"


def copy_to_clipboard(text: str) -> bool:
    for cmd in (["pbcopy"], ["xclip", "-selection", "clipboard"], ["wl-copy"]):
        try:
            subprocess.run(cmd, input=text.encode(), check=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return False


def main() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret_path = find_client_secret()
    info = json.loads(secret_path.read_text())
    client = info.get("installed") or info.get("web")
    if not client:
        sys.exit(f"{secret_path.name} has neither an 'installed' nor a 'web' client.")

    print(f"Using {secret_path.name}")
    print("Opening your browser. Approve the one permission it asks for...\n")

    # A loopback redirect on a random port. Google always allows this for a
    # desktop client, so nothing has to be registered as a redirect URI, and
    # it works even if the deployed callback URL is not set up.
    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
    creds = flow.run_local_server(
        port=0,
        prompt="consent",          # force a refresh token every time
        access_type="offline",     # without this there is no refresh token at all
        authorization_prompt_message="If no browser opened, visit:\n{url}\n",
        success_message="Done. You can close this tab and return to the terminal.",
    )

    token = creds.refresh_token
    if not token:
        sys.exit(
            "Google returned no refresh token.\n"
            "Revoke this app at https://myaccount.google.com/permissions and retry."
        )

    # .env, for local runs.
    env_path = REPO / ".env"
    text = env_path.read_text() if env_path.exists() else ""
    text = set_env_key(text, "YOUTUBE_CLIENT_ID", client["client_id"])
    text = set_env_key(text, "YOUTUBE_CLIENT_SECRET", client["client_secret"])
    text = set_env_key(text, "YOUTUBE_REFRESH_TOKEN", token)
    env_path.write_text(text)

    # The local token store, so `python main.py --upload` works immediately.
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
    print("Signed in. Paste this ONE line into the Railway variables:\n")
    print(f"  {line}\n")
    print("=" * 72)
    print(f".env updated, {store_note}.")
    if copied:
        print("Copied to your clipboard.")
    print(
        "\nRailway -> Youtube_automation -> Variables -> edit YOUTUBE_REFRESH_TOKEN.\n"
        "Saving it redeploys the service, which is all that is needed: on the next\n"
        "run the app sees the variable changed and adopts it over its stored token."
    )


if __name__ == "__main__":
    main()
