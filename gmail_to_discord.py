"""Forward new Gmail messages to a Discord channel via webhook."""

import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

SCRIPT_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = SCRIPT_DIR / "credentials.json"
TOKEN_FILE = SCRIPT_DIR / "token.json"
SEEN_FILE = SCRIPT_DIR / "seen_ids.json"

GMAIL_BLUE = 0x4285F4
MAX_SEEN_IDS = 500
INBOX_FETCH_COUNT = 25


def load_seen() -> set[str]:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except json.JSONDecodeError:
            print("[warn] seen_ids.json was corrupt, starting fresh")
    return set()


def save_seen(seen: set[str]) -> None:
    # Trim to the most recent MAX_SEEN_IDS to keep the file bounded
    trimmed = list(seen)[-MAX_SEEN_IDS:]
    SEEN_FILE.write_text(json.dumps(trimmed))


def get_gmail_service():
    if not CREDENTIALS_FILE.exists():
        sys.exit(
            f"[error] {CREDENTIALS_FILE.name} not found. Download it from the Google "
            "Cloud Console (see README) and put it next to this script."
        )

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                print("[warn] token refresh failed, re-authenticating...")
                creds = None
        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def get_message_details(service, msg_id: str) -> dict:
    msg = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=msg_id,
            format="metadata",
            metadataHeaders=["Subject", "From", "Date"],
        )
        .execute()
    )
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    return {
        "id": msg_id,
        "subject": headers.get("Subject", "(no subject)"),
        "from": headers.get("From", "(unknown sender)"),
        "date": headers.get("Date", ""),
        "snippet": msg.get("snippet", ""),
    }


def send_to_discord(webhook_url: str, email: dict) -> None:
    subject = email["subject"][:256] or "(no subject)"
    snippet = email["snippet"][:2000] or "*(no preview available)*"

    embed = {
        "title": subject,
        "description": snippet,
        "color": GMAIL_BLUE,
        "fields": [
            {"name": "From", "value": email["from"][:1024], "inline": False},
        ],
        "footer": {"text": email["date"][:2048]} if email["date"] else None,
    }
    # Strip any None fields Discord would reject
    embed = {k: v for k, v in embed.items() if v is not None}

    payload = {
        "username": "Gmail",
        "avatar_url": "https://ssl.gstatic.com/ui/v1/icons/mail/rfr/gmail.ico",
        "embeds": [embed],
    }
    response = requests.post(webhook_url, json=payload, timeout=15)
    response.raise_for_status()


def check_and_forward(service, webhook_url: str, seen: set[str]) -> int:
    result = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX"], maxResults=INBOX_FETCH_COUNT)
        .execute()
    )
    messages = result.get("messages", [])
    new_messages = [m for m in messages if m["id"] not in seen]

    # Process oldest first so the Discord channel reads chronologically
    sent = 0
    for m in reversed(new_messages):
        try:
            email = get_message_details(service, m["id"])
            send_to_discord(webhook_url, email)
            seen.add(m["id"])
            sent += 1
            print(f"[sent] {email['from']} - {email['subject']}")
        except HttpError as e:
            print(f"[error] Gmail API error for {m['id']}: {e}")
        except requests.RequestException as e:
            print(f"[error] Discord webhook error for {m['id']}: {e}")

    if sent:
        save_seen(seen)
    return sent


def prime_seen_on_first_run(service, seen: set[str]) -> None:
    """On first run, mark the current inbox as seen so we don't spam old mail."""
    if seen:
        return
    print("[init] First run detected - marking current inbox as 'seen'")
    print("[init] (existing emails will NOT be forwarded; only new ones from now on)")
    result = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX"], maxResults=100)
        .execute()
    )
    for m in result.get("messages", []):
        seen.add(m["id"])
    save_seen(seen)
    print(f"[init] Marked {len(seen)} existing messages as seen")


def main() -> None:
    load_dotenv(SCRIPT_DIR / ".env")

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        sys.exit(
            "[error] DISCORD_WEBHOOK_URL not set. Copy .env.example to .env "
            "and paste your webhook URL."
        )

    try:
        poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
    except ValueError:
        sys.exit("[error] POLL_INTERVAL_SECONDS must be an integer")
    if poll_interval < 10:
        print("[warn] poll interval < 10s is aggressive, clamping to 10s")
        poll_interval = 10

    print("[start] Gmail -> Discord forwarder")
    service = get_gmail_service()
    seen = load_seen()
    prime_seen_on_first_run(service, seen)

    print(f"[start] Polling every {poll_interval}s. Ctrl+C to stop.")
    while True:
        try:
            sent = check_and_forward(service, webhook_url, seen)
            if sent:
                print(f"[tick] forwarded {sent} message(s)")
        except HttpError as e:
            print(f"[error] Gmail API error: {e}")
        except Exception as e:  # noqa: BLE001 - keep the loop alive
            print(f"[error] unexpected: {e}")
        time.sleep(poll_interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[stop] bye!")
