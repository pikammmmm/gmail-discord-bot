"""Gmail -> Discord bot with Reply button.

Polls a Gmail inbox and posts new messages to a Discord channel as embeds
with a 'Reply' button. Clicking the button opens a modal where you can type
a reply; submitting it sends a properly threaded reply via the Gmail API.
"""

import asyncio
import base64
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import discord
from discord.ext import tasks
from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ----- Config -----

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

SCRIPT_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = SCRIPT_DIR / "credentials.json"
TOKEN_FILE = SCRIPT_DIR / "token.json"
SEEN_FILE = SCRIPT_DIR / "seen_ids.json"
LOG_FILE = SCRIPT_DIR / "bot.log"

GMAIL_BLUE = 0x4285F4
MAX_SEEN_IDS = 500
MAX_LOG_BYTES = 500_000
INBOX_FETCH_COUNT = 25
MAX_IMAGES_PER_EMAIL = 9          # Discord limit is 10 attachments; one slot reserved for the .html
MAX_BUTTONS_PER_EMAIL = 10        # Discord allows 25 components; we keep budget for the Reply button
MAX_REMOTE_IMAGE_FETCHES = 20     # cap to keep poll latency bounded
REMOTE_IMAGE_TIMEOUT = 5          # seconds
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024  # Discord free-tier per-file limit
EMBED_DESCRIPTION_LIMIT = 4000    # safe under Discord's 4096 limit
BODY_TRUNCATION_MARKER = "\n\n*[…full email attached as .html below]*"


@dataclass
class ImageBlob:
    filename: str
    mime: str
    data: bytes


@dataclass
class EmailContent:
    msg_id: str
    thread_id: str
    subject: str
    from_addr: str
    to: str
    date: str
    message_id_header: str
    text_body: str = ""
    html_body: str = ""
    inline_images: list[ImageBlob] = field(default_factory=list)
    attached_images: list[ImageBlob] = field(default_factory=list)


# ----- Logging -----

def log(msg: str) -> None:
    """Write a timestamped message to stdout (if available) and bot.log."""
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except Exception:
        pass  # pythonw.exe may have no usable stdout
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            LOG_FILE.write_text("")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # never let logging crash the bot


# ----- Seen-IDs persistence -----

def load_seen() -> set[str]:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except json.JSONDecodeError:
            log("[warn] seen_ids.json was corrupt, starting fresh")
    return set()


def save_seen(seen: set[str]) -> None:
    trimmed = list(seen)[-MAX_SEEN_IDS:]
    SEEN_FILE.write_text(json.dumps(trimmed))


# ----- Gmail API -----

def get_gmail_service():
    """Authenticate with Gmail and return an API service client."""
    if not CREDENTIALS_FILE.exists():
        sys.exit(
            f"[error] {CREDENTIALS_FILE.name} not found. Download it from "
            "Google Cloud Console (see README) and put it next to this script."
        )

    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception as e:
            log(f"[warn] could not load token.json ({e}) - re-authenticating")
            creds = None

    # If the existing token was issued for fewer scopes than we need now,
    # force a re-auth so the user grants send permission too.
    if creds and creds.scopes is not None:
        if not set(SCOPES).issubset(set(creds.scopes)):
            log("[warn] token scopes are outdated - re-authenticating for gmail.send")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                log("[warn] token refresh failed - re-authenticating")
                creds = None
        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def get_message_details(service, msg_id: str) -> dict:
    """Fetch the headers + snippet we need to display and reply to a message."""
    msg = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=msg_id,
            format="metadata",
            metadataHeaders=["Subject", "From", "To", "Date", "Message-ID"],
        )
        .execute()
    )
    headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
    return {
        "id": msg_id,
        "thread_id": msg.get("threadId", ""),
        "subject": headers.get("subject", "(no subject)"),
        "from": headers.get("from", "(unknown sender)"),
        "to": headers.get("to", ""),
        "date": headers.get("date", ""),
        "message_id_header": headers.get("message-id", ""),
        "snippet": msg.get("snippet", ""),
    }


def _decode_part_body(part: dict) -> bytes:
    """Decode the inline body data of a single MIME part. Returns b'' if absent."""
    body = part.get("body") or {}
    data = body.get("data")
    if not data:
        return b""
    return base64.urlsafe_b64decode(data.encode("ascii"))


def _fetch_attachment_bytes(service, msg_id: str, attachment_id: str) -> bytes:
    """Fetch a Gmail attachment by ID and return its raw bytes."""
    att = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=msg_id, id=attachment_id)
        .execute()
    )
    return base64.urlsafe_b64decode(att["data"].encode("ascii"))


def _walk_payload(service, msg_id: str, payload: dict, content: "EmailContent") -> None:
    """Recursively walk a Gmail MIME payload, populating EmailContent in place."""
    mime_type = (payload.get("mimeType") or "").lower()
    parts = payload.get("parts") or []
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    filename = payload.get("filename") or ""
    body = payload.get("body") or {}
    attachment_id = body.get("attachmentId")

    if parts:
        for part in parts:
            _walk_payload(service, msg_id, part, content)
        return

    # Leaf node from here.
    if mime_type == "text/plain" and not content.text_body:
        content.text_body = _decode_part_body(payload).decode("utf-8", errors="replace")
        return

    if mime_type == "text/html" and not content.html_body:
        content.html_body = _decode_part_body(payload).decode("utf-8", errors="replace")
        return

    if mime_type.startswith("image/"):
        try:
            if attachment_id:
                data = _fetch_attachment_bytes(service, msg_id, attachment_id)
            else:
                data = _decode_part_body(payload)
        except Exception as e:
            log(f"[warn] could not fetch image part for {msg_id}: {e}")
            return
        if not data:
            return
        is_inline = "content-id" in headers or "x-attachment-id" in headers
        blob = ImageBlob(
            filename=filename or f"image-{len(content.inline_images) + len(content.attached_images) + 1}",
            mime=mime_type,
            data=data,
        )
        if is_inline:
            content.inline_images.append(blob)
        else:
            content.attached_images.append(blob)


def fetch_full_email_content(service, msg_id: str) -> "EmailContent":
    """Fetch the full email and return its parsed body + images."""
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=msg_id, format="full")
        .execute()
    )
    payload = msg.get("payload") or {}
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    content = EmailContent(
        msg_id=msg_id,
        thread_id=msg.get("threadId", ""),
        subject=headers.get("subject", "(no subject)"),
        from_addr=headers.get("from", "(unknown sender)"),
        to=headers.get("to", ""),
        date=headers.get("date", ""),
        message_id_header=headers.get("message-id", ""),
    )
    _walk_payload(service, msg_id, payload, content)
    return content


def fetch_new_inbox_ids(service, seen: set[str]) -> list[str]:
    """Return IDs of recent inbox messages we haven't seen yet."""
    result = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX"], maxResults=INBOX_FETCH_COUNT)
        .execute()
    )
    messages = result.get("messages", [])
    return [m["id"] for m in messages if m["id"] not in seen]


def prime_seen_on_first_run(service, seen: set[str]) -> None:
    """On first run, mark current inbox as seen so we don't spam old mail."""
    if seen:
        return
    log("[init] First run detected - marking current inbox as 'seen'")
    log("[init] (existing emails will NOT be forwarded; only new ones from now on)")
    result = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX"], maxResults=100)
        .execute()
    )
    for m in result.get("messages", []):
        seen.add(m["id"])
    save_seen(seen)
    log(f"[init] Marked {len(seen)} existing messages as seen")


# ----- HTML processing -----

import html2text as _html2text  # late import keeps top-of-file tidy
from bs4 import BeautifulSoup


def _strip_cid_and_remote_images(soup: BeautifulSoup) -> None:
    """Remove all <img> tags. Inline (cid:) images become Discord file attachments,
    and remote images are also attached separately, so neither belongs in the body.
    """
    for img in soup.find_all("img"):
        img.decompose()


def _make_h2t() -> _html2text.HTML2Text:
    h = _html2text.HTML2Text()
    h.body_width = 0
    h.ignore_images = True
    h.ignore_links = False
    h.protect_links = True
    h.single_line_break = True
    return h


def render_body_markdown(html: str, plain: str) -> str:
    """Return a Discord-friendly markdown rendering of the email body."""
    if html.strip():
        soup = BeautifulSoup(html, "html.parser")
        _strip_cid_and_remote_images(soup)
        text = _make_h2t().handle(str(soup))
        text = text.strip()
        if text:
            return text
    if plain.strip():
        return plain.strip()
    return "*(no body)*"


BUTTON_TEXT_WHITELIST = {
    "verify", "verify email",
    "confirm", "confirm email",
    "view order", "view invoice", "view receipt",
    "reset password",
    "sign in", "log in", "login",
    "accept", "decline",
    "activate",
    "get started",
    "download",
    "open",
}


def _looks_like_button_style(style: str | None) -> bool:
    """Heuristic: <a> with both background-color and padding is almost always a CTA button."""
    if not style:
        return False
    s = style.lower()
    return "background-color" in s and "padding" in s


def extract_action_buttons(html: str) -> list[tuple[str, str]]:
    """Return a list of (label, href) tuples for action-button-like <a> tags.

    De-duplicated by href, preserving first-seen order. Capped at MAX_BUTTONS_PER_EMAIL.
    """
    if not html.strip():
        return []
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if not href.lower().startswith(("http://", "https://")):
            continue
        if href in seen:
            continue
        label = a.get_text(strip=True)
        if not label:
            continue
        is_button = _looks_like_button_style(a.get("style"))
        if not is_button and label.lower() not in BUTTON_TEXT_WHITELIST:
            continue
        seen.add(href)
        out.append((label[:80], href))
        if len(out) >= MAX_BUTTONS_PER_EMAIL:
            break
    return out


# ----- Reply sending -----

def _extract_email_address(from_header: str) -> str:
    """Extract 'addr@domain' from 'Name <addr@domain>' or return as-is."""
    if "<" in from_header and ">" in from_header:
        return from_header.split("<", 1)[1].split(">", 1)[0].strip()
    return from_header.strip()


def _prefix_re(subject: str) -> str:
    if subject.lower().lstrip().startswith("re:"):
        return subject
    return f"Re: {subject}"


def send_gmail_reply(service, original_msg_id: str, body_text: str) -> str:
    """Send a threaded reply to the given Gmail message.

    Returns the recipient's email address so we can show a confirmation.
    """
    details = get_message_details(service, original_msg_id)
    to_addr = _extract_email_address(details["from"])
    if not to_addr:
        raise RuntimeError("original email had no usable 'From' address")

    msg = EmailMessage()
    msg["To"] = to_addr
    msg["Subject"] = _prefix_re(details["subject"])
    if details["message_id_header"]:
        msg["In-Reply-To"] = details["message_id_header"]
        msg["References"] = details["message_id_header"]
    msg.set_content(body_text)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    send_body: dict = {"raw": raw}
    if details["thread_id"]:
        send_body["threadId"] = details["thread_id"]

    service.users().messages().send(userId="me", body=send_body).execute()
    return to_addr


# ----- Discord bot -----

class ReplyModal(discord.ui.Modal):
    """Popup form for typing a reply body."""

    body = discord.ui.TextInput(
        label="Your reply",
        style=discord.TextStyle.paragraph,
        placeholder="Type your reply here...",
        required=True,
        max_length=4000,
    )

    def __init__(self, bot: "GmailBot", msg_id: str, original_subject: str):
        super().__init__(title=f"Reply: {original_subject[:40]}"[:45])
        self.bot = bot
        self.msg_id = msg_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Gmail send can take >3s, so defer first to avoid interaction timeout
        await interaction.response.defer(ephemeral=True, thinking=True)
        loop = asyncio.get_running_loop()
        try:
            to_addr = await loop.run_in_executor(
                None,
                send_gmail_reply,
                self.bot.gmail_service,
                self.msg_id,
                str(self.body.value),
            )
            await interaction.followup.send(
                f"✅ Reply sent to **{to_addr}**",
                ephemeral=True,
            )
            log(f"[reply] sent to {to_addr} for msg {self.msg_id}")
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to send reply: `{e}`",
                ephemeral=True,
            )
            log(f"[error] reply send failed for {self.msg_id}: {e}")


class GmailBot(discord.Client):
    def __init__(
        self,
        channel_id: int | None,
        dm_user_id: int | None,
        poll_interval: int,
    ):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.channel_id = channel_id
        self.dm_user_id = dm_user_id
        self.poll_interval = max(10, poll_interval)
        self.gmail_service = None
        self.seen: set[str] = set()
        self._priming_done = False
        # Where to post emails: either a discord.User (DM) or a channel.
        # Both support .send(embed=..., view=...).
        self._destination = None

    async def setup_hook(self) -> None:
        log("[init] Authenticating with Gmail...")
        loop = asyncio.get_running_loop()
        self.gmail_service = await loop.run_in_executor(None, get_gmail_service)
        self.seen = load_seen()
        await loop.run_in_executor(
            None, prime_seen_on_first_run, self.gmail_service, self.seen
        )
        self._priming_done = True
        self.poll_loop.change_interval(seconds=self.poll_interval)
        self.poll_loop.start()

    async def on_ready(self) -> None:
        log(f"[ready] Logged in as {self.user} (id={self.user.id})")

        # DM mode takes precedence if DM_USER_ID is set.
        if self.dm_user_id is not None:
            try:
                user = await self.fetch_user(self.dm_user_id)
                self._destination = user
                log(f"[ready] DM mode: will send emails to {user} (id={user.id})")
                return
            except Exception as e:
                log(
                    f"[error] Could not fetch DM user {self.dm_user_id}: {e}. "
                    "Falling back to channel mode if configured."
                )

        # Channel mode fallback.
        if self.channel_id is not None:
            channel = self.get_channel(self.channel_id)
            if channel is None:
                log(
                    f"[error] Channel {self.channel_id} not visible to the bot. "
                    "Is the bot invited to the server? Is the ID correct?"
                )
            else:
                self._destination = channel
                log(f"[ready] Channel mode: posting into #{channel.name} ({channel.id})")
            return

        log("[error] No destination configured (neither DM_USER_ID nor DISCORD_CHANNEL_ID)")

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        data = interaction.data or {}
        custom_id = data.get("custom_id", "") if isinstance(data, dict) else ""
        if not custom_id.startswith("reply:"):
            return
        msg_id = custom_id[len("reply:"):]
        if not msg_id:
            return
        await self._open_reply_modal(interaction, msg_id)

    async def _open_reply_modal(
        self, interaction: discord.Interaction, msg_id: str
    ) -> None:
        loop = asyncio.get_running_loop()
        try:
            details = await loop.run_in_executor(
                None, get_message_details, self.gmail_service, msg_id
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Could not load original email: `{e}`",
                ephemeral=True,
            )
            log(f"[error] loading msg {msg_id} for reply: {e}")
            return
        modal = ReplyModal(self, msg_id, details["subject"])
        await interaction.response.send_modal(modal)

    @tasks.loop(seconds=60)
    async def poll_loop(self) -> None:
        if not self._priming_done:
            return
        try:
            loop = asyncio.get_running_loop()
            new_ids = await loop.run_in_executor(
                None, fetch_new_inbox_ids, self.gmail_service, self.seen
            )
            if not new_ids:
                return
            if self._destination is None:
                log("[error] No destination available; skipping tick")
                return
            # Process oldest first so Discord reads chronologically
            sent = 0
            for msg_id in reversed(new_ids):
                try:
                    email = await loop.run_in_executor(
                        None, get_message_details, self.gmail_service, msg_id
                    )
                    await self._post_email(self._destination, email)
                    self.seen.add(msg_id)
                    sent += 1
                    log(f"[sent] {email['from']} - {email['subject']}")
                except Exception as e:
                    log(f"[error] processing {msg_id}: {e}")
            if sent:
                save_seen(self.seen)
                log(f"[tick] forwarded {sent} message(s)")
        except Exception as e:
            log(f"[error] poll_loop: {e}")

    @poll_loop.before_loop
    async def _before_poll_loop(self) -> None:
        await self.wait_until_ready()

    async def _post_email(self, destination, email: dict) -> None:
        subject = (email["subject"] or "(no subject)")[:256]
        snippet = (email["snippet"] or "*(no preview available)*")[:2000]

        embed = discord.Embed(
            title=subject,
            description=snippet,
            color=GMAIL_BLUE,
        )
        embed.add_field(name="From", value=email["from"][:1024], inline=False)
        if email["date"]:
            embed.set_footer(text=email["date"][:2048])

        view = discord.ui.View(timeout=None)
        view.add_item(
            discord.ui.Button(
                label="Reply",
                style=discord.ButtonStyle.primary,
                emoji="\U0001F4E8",  # 📨
                custom_id=f"reply:{email['id']}",
            )
        )

        await destination.send(embed=embed, view=view)


# ----- Entry point -----

def main() -> None:
    load_dotenv(SCRIPT_DIR / ".env")

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        sys.exit(
            "[error] DISCORD_BOT_TOKEN not set. See README for how to create "
            "a bot and get its token, then put it in .env."
        )

    def _parse_optional_int(name: str) -> int | None:
        value = os.environ.get(name)
        if not value or value.startswith("REPLACE_WITH_"):
            return None
        try:
            return int(value)
        except ValueError:
            sys.exit(f"[error] {name} must be numeric (the raw ID, not a name)")

    channel_id = _parse_optional_int("DISCORD_CHANNEL_ID")
    dm_user_id = _parse_optional_int("DM_USER_ID")

    if channel_id is None and dm_user_id is None:
        sys.exit(
            "[error] Set at least one of DM_USER_ID or DISCORD_CHANNEL_ID in .env. "
            "DM_USER_ID sends emails as DMs to you; DISCORD_CHANNEL_ID posts in a channel."
        )

    try:
        poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
    except ValueError:
        sys.exit("[error] POLL_INTERVAL_SECONDS must be an integer")

    mode = "DM" if dm_user_id is not None else "channel"
    log(f"[start] Gmail -> Discord bot (with Reply button) - {mode} mode")
    bot = GmailBot(
        channel_id=channel_id,
        dm_user_id=dm_user_id,
        poll_interval=poll_interval,
    )
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
