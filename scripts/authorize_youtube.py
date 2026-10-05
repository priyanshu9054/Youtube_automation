"""
One-time local setup: exchanges your Google Cloud OAuth client for a refresh
token you can paste into .env / Railway variables. Run this on your own
machine (not on Railway) because it needs to open a browser.

Usage:
    python scripts/authorize_youtube.py path/to/client_secret.json
"""

import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/authorize_youtube.py path/to/client_secret.json")
        sys.exit(1)

    client_secret_path = sys.argv[1]
    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(client_secret_path) as f:
        client_info = json.load(f)
        installed = client_info.get("installed") or client_info.get("web")
        client_id = installed["client_id"]
        client_secret = installed["client_secret"]

    print("\nAuthorization complete. Add these to your .env (and Railway variables):\n")
    print(f"YOUTUBE_CLIENT_ID={client_id}")
    print(f"YOUTUBE_CLIENT_SECRET={client_secret}")
    print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
