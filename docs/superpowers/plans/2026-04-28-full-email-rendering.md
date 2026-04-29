# Full Email Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `gmail-discord-bot` so each forwarded email shows the full body (HTML→markdown), all images, and the email's action buttons as native Discord link-style buttons next to the existing 📨 Reply button.

**Architecture:** Single-file Python (`gmail_to_discord.py`). New helpers walk the Gmail MIME tree, render the HTML body to markdown, extract styled action `<a>` tags, and collect inline / attached / remote images. `_post_email` now builds a richer embed + image attachments + a `.html` fallback file + link buttons. The Reply path is untouched.

**Tech Stack:** Python 3.12, discord.py, google-api-python-client, beautifulsoup4 (new), html2text (new), requests (existing).

**Spec:** `docs/superpowers/specs/2026-04-28-full-email-rendering-design.md`

**Testing approach:** Manual end-to-end against the user's own Gmail/Discord, matching the project's existing convention (no pytest infrastructure exists). Each task ends with a quick smoke command (`python -c "import gmail_to_discord"`) to catch syntax / import errors. Task 8 is the full E2E pass.

---

## File map

- `requirements.txt` — add `beautifulsoup4`, `html2text`
- `gmail_to_discord.py` — all logic changes (new helpers + rewritten `_post_email` + updated poll loop)
- `README.md` — promote "full-body" from "Ideas for later" into "What you get"

No new code files. The single-file layout is the existing project convention.

---

### Task 1: Add new dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the two pins to `requirements.txt`**

The file currently ends after the `discord.py` line. Add:

```
beautifulsoup4>=4.12.0
html2text>=2024.2.26
```

Final `requirements.txt`:

```
google-api-python-client>=2.120.0
google-auth-httplib2>=0.2.0
google-auth-oauthlib>=1.2.0
requests>=2.31.0
python-dotenv>=1.0.1
discord.py>=2.3.2,<3
beautifulsoup4>=4.12.0
html2text>=2024.2.26
```

- [ ] **Step 2: Activate venv and install**

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Bash (Git Bash):

```bash
source .venv/Scripts/activate
pip install -r requirements.txt
```

Expected: `Successfully installed beautifulsoup4-... html2text-... soupsieve-...`

- [ ] **Step 3: Smoke-import to confirm versions resolve**

```bash
python -c "import bs4, html2text; print('bs4', bs4.__version__); print('html2text', html2text.__version__)"
```

Expected: two version strings, no traceback.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "Add beautifulsoup4 and html2text deps for email rendering"
```

---

### Task 2: Add `EmailContent` dataclass and full-body fetcher

**Files:**
- Modify: `gmail_to_discord.py` (imports near top; new dataclasses near config block; new helpers in the Gmail API section)

- [ ] **Step 1: Add imports**

In `gmail_to_discord.py`, find the import block (lines 8–24) and add `dataclass`/`field` and `Iterable` if needed. Replace:

```python
import asyncio
import base64
import json
import os
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
```

with:

```python
import asyncio
import base64
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
```

- [ ] **Step 2: Add `ImageBlob` and `EmailContent` dataclasses**

Right after the `INBOX_FETCH_COUNT = 25` line in the Config block, add:

```python
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
```

- [ ] **Step 3: Add `_decode_part_body` helper**

Add this in the "Gmail API" section, just below `get_message_details`:

```python
def _decode_part_body(part: dict) -> bytes:
    """Decode the inline body data of a single MIME part. Returns b'' if absent."""
    body = part.get("body") or {}
    data = body.get("data")
    if not data:
        return b""
    return base64.urlsafe_b64decode(data.encode("ascii"))
```

- [ ] **Step 4: Add `_fetch_attachment_bytes` helper**

Append to the same section:

```python
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
```

- [ ] **Step 5: Add `_walk_payload` recursive walker**

Append to the same section:

```python
def _walk_payload(service, msg_id: str, payload: dict, content: EmailContent) -> None:
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
        # Content-ID header (e.g. "<abc123@local>") indicates an inline image.
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
```

- [ ] **Step 6: Add `fetch_full_email_content`**

Append to the same section:

```python
def fetch_full_email_content(service, msg_id: str) -> EmailContent:
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
```

- [ ] **Step 7: Smoke-check the module imports**

```bash
python -c "import gmail_to_discord; print('ok')"
```

Expected: `ok`. If you get a SyntaxError, fix it before committing.

- [ ] **Step 8: Commit**

```bash
git add gmail_to_discord.py
git commit -m "Add EmailContent dataclass and full-body MIME fetcher"
```

---

### Task 3: Add the HTML body → markdown renderer

**Files:**
- Modify: `gmail_to_discord.py` (new helpers in a new "HTML processing" section after the Gmail API section)

- [ ] **Step 1: Add a new section header and `render_body_markdown`**

Just below the Gmail API section (i.e. after `fetch_full_email_content`, above `# ----- Reply sending -----`), insert:

```python
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
    h.body_width = 0          # don't hard-wrap long lines
    h.ignore_images = True    # we strip them anyway, but belt-and-suspenders
    h.ignore_links = False
    h.protect_links = True    # don't break URLs across lines
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
```

- [ ] **Step 2: Quick REPL sanity check**

```bash
python -c "from gmail_to_discord import render_body_markdown; print(render_body_markdown('<p>Hello <b>world</b>! <a href=\"https://example.com\">link</a></p><img src=\"cid:x\">', ''))"
```

Expected (something like):

```
Hello **world**! [link](https://example.com)
```

No `<img>` reference, no traceback.

- [ ] **Step 3: Commit**

```bash
git add gmail_to_discord.py
git commit -m "Add HTML body to markdown renderer for Discord"
```

---

### Task 4: Add the action-button extractor

**Files:**
- Modify: `gmail_to_discord.py` (HTML processing section)

- [ ] **Step 1: Add the whitelist constant**

Below `_make_h2t` and above `render_body_markdown` is fine, or at the end of the HTML section — pick one and stay consistent. Add:

```python
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
```

- [ ] **Step 2: Add `_looks_like_button_style` helper**

```python
def _looks_like_button_style(style: str | None) -> bool:
    """Heuristic: <a> with both background-color and padding is almost always a CTA button."""
    if not style:
        return False
    s = style.lower()
    return "background-color" in s and "padding" in s
```

- [ ] **Step 3: Add `extract_action_buttons`**

```python
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
```

- [ ] **Step 4: REPL check**

```bash
python -c "
from gmail_to_discord import extract_action_buttons
html = '''
<a href=\"https://a.example/verify\" style=\"background-color:#1a73e8;padding:12px;color:white\">Verify email</a>
<a href=\"https://a.example/unsub\">Unsubscribe</a>
<a href=\"https://a.example/order\">View order</a>
'''
print(extract_action_buttons(html))
"
```

Expected: two entries (`Verify email` and `View order`). `Unsubscribe` is excluded — good.

- [ ] **Step 5: Commit**

```bash
git add gmail_to_discord.py
git commit -m "Add action-button extractor for email <a> tags"
```

---

### Task 5: Add the image collector

**Files:**
- Modify: `gmail_to_discord.py` (HTML processing section)

- [ ] **Step 1: Add `requests` import (if not already present at top)**

Check the import block. `requests` is in `requirements.txt` but may not be imported yet. If absent, add near the other top-level imports:

```python
import requests
```

- [ ] **Step 2: Add `_fetch_remote_image`**

In the HTML processing section:

```python
def _fetch_remote_image(url: str) -> ImageBlob | None:
    """Best-effort download of a remote <img src>. Returns None on any failure."""
    try:
        resp = requests.get(url, timeout=REMOTE_IMAGE_TIMEOUT, stream=True)
    except requests.RequestException as e:
        log(f"[warn] remote image fetch failed for {url}: {e}")
        return None
    if resp.status_code != 200:
        return None
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if not ctype.startswith("image/"):
        return None
    data = resp.content
    if not data or len(data) > MAX_ATTACHMENT_BYTES:
        return None
    # Pick a reasonable filename: last path segment or a fallback
    tail = url.rsplit("/", 1)[-1].split("?", 1)[0] or "remote-image"
    if "." not in tail:
        ext = ctype.split("/", 1)[-1].split(";", 1)[0] or "bin"
        tail = f"{tail}.{ext}"
    return ImageBlob(filename=tail, mime=ctype, data=data)
```

- [ ] **Step 3: Add `collect_images`**

```python
def collect_images(content: EmailContent) -> tuple[list[ImageBlob], int]:
    """Return (image list, dropped count). Order: inline -> attached -> remote.

    Inline + attached images already have their bytes. Remote <img src> URLs are fetched
    fresh, capped at MAX_REMOTE_IMAGE_FETCHES attempts. Final list capped at
    MAX_IMAGES_PER_EMAIL; the dropped count is everything that didn't fit.
    """
    images: list[ImageBlob] = []
    images.extend(content.inline_images)
    images.extend(content.attached_images)

    remote_urls: list[str] = []
    if content.html_body.strip():
        soup = BeautifulSoup(content.html_body, "html.parser")
        for img in soup.find_all("img"):
            src = (img.get("src") or "").strip()
            if src.lower().startswith(("http://", "https://")):
                remote_urls.append(src)

    dedup_remote: list[str] = []
    seen: set[str] = set()
    for u in remote_urls:
        if u not in seen:
            seen.add(u)
            dedup_remote.append(u)

    fetched = 0
    for url in dedup_remote:
        if fetched >= MAX_REMOTE_IMAGE_FETCHES:
            break
        fetched += 1
        blob = _fetch_remote_image(url)
        if blob is not None:
            images.append(blob)

    # Drop oversize images (defensive — Discord rejects >8MB on free tier)
    images = [img for img in images if len(img.data) <= MAX_ATTACHMENT_BYTES]

    if len(images) > MAX_IMAGES_PER_EMAIL:
        dropped = len(images) - MAX_IMAGES_PER_EMAIL
        return images[:MAX_IMAGES_PER_EMAIL], dropped
    return images, 0
```

- [ ] **Step 4: REPL check**

```bash
python -c "
from gmail_to_discord import collect_images, EmailContent, ImageBlob
c = EmailContent(msg_id='x', thread_id='', subject='', from_addr='', to='', date='', message_id_header='')
c.inline_images.append(ImageBlob(filename='logo.png', mime='image/png', data=b'\\x89PNG\\r\\n'))
print(collect_images(c))
"
```

Expected: `([ImageBlob(filename='logo.png', mime='image/png', data=b'...')], 0)`

- [ ] **Step 5: Commit**

```bash
git add gmail_to_discord.py
git commit -m "Add image collector (inline + attached + remote)"
```

---

### Task 6: Wire the new pipeline into `_post_email` and `poll_loop`

**Files:**
- Modify: `gmail_to_discord.py` (`poll_loop` and `_post_email`)

- [ ] **Step 1: Switch `poll_loop` to use the full-content fetcher**

Find this block in `poll_loop` (currently around lines 371–384):

```python
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
```

Replace the inner `email = ...` line and the `log(...)` line so it reads:

```python
            for msg_id in reversed(new_ids):
                try:
                    email = await loop.run_in_executor(
                        None, fetch_full_email_content, self.gmail_service, msg_id
                    )
                    await self._post_email(self._destination, email)
                    self.seen.add(msg_id)
                    sent += 1
                    log(f"[sent] {email.from_addr} - {email.subject}")
                except Exception as e:
                    log(f"[error] processing {msg_id}: {e}")
```

(`get_message_details` is still used by the Reply modal — leave it alone.)

- [ ] **Step 2: Replace `_post_email` end-to-end**

Find the existing `async def _post_email(self, destination, email: dict) -> None:` (lines 392–415) and replace the entire method with:

```python
    async def _post_email(self, destination, email: EmailContent) -> None:
        loop = asyncio.get_running_loop()

        body_md = await loop.run_in_executor(
            None, render_body_markdown, email.html_body, email.text_body
        )
        buttons = await loop.run_in_executor(
            None, extract_action_buttons, email.html_body
        )
        images, dropped_images = await loop.run_in_executor(
            None, collect_images, email
        )

        # Truncate body for embed; full HTML always attached.
        truncated = False
        if len(body_md) > EMBED_DESCRIPTION_LIMIT:
            body_md = body_md[: EMBED_DESCRIPTION_LIMIT - len(BODY_TRUNCATION_MARKER)]
            truncated = True

        description = body_md
        if truncated:
            description += BODY_TRUNCATION_MARKER

        overflow_notes: list[str] = []
        if dropped_images:
            overflow_notes.append(f"+{dropped_images} more images")
        # Note: extract_action_buttons already caps; we just mention if we hit the cap
        # by checking if there were more <a> matches than we kept.

        if overflow_notes:
            extra = "_" + " / ".join(overflow_notes) + " in attached HTML_"
            # Make sure we still fit
            if len(description) + len(extra) + 2 <= 4096:
                description += "\n\n" + extra

        embed = discord.Embed(
            title=(email.subject or "(no subject)")[:256],
            description=description or "*(no body)*",
            color=GMAIL_BLUE,
        )
        embed.add_field(name="From", value=email.from_addr[:1024], inline=False)
        if email.date:
            embed.set_footer(text=email.date[:2048])
        if images:
            embed.set_image(url=f"attachment://{images[0].filename}")

        # Build the .html fallback file
        html_payload = email.html_body or f"<pre>{(email.text_body or '(no body)')}</pre>"
        files: list[discord.File] = [
            discord.File(
                fp=io.BytesIO(html_payload.encode("utf-8")),
                filename=f"email-{email.msg_id}.html",
            )
        ]
        for img in images:
            files.append(
                discord.File(fp=io.BytesIO(img.data), filename=img.filename)
            )

        view = discord.ui.View(timeout=None)
        view.add_item(
            discord.ui.Button(
                label="Reply",
                style=discord.ButtonStyle.primary,
                emoji="\U0001F4E8",
                custom_id=f"reply:{email.msg_id}",
            )
        )
        for label, href in buttons:
            view.add_item(
                discord.ui.Button(
                    label=label,
                    style=discord.ButtonStyle.link,
                    url=href,
                )
            )

        await destination.send(embed=embed, view=view, files=files)
```

- [ ] **Step 3: Add `import io` to the imports block**

In the import block at the top, add `import io` alongside the other stdlib imports:

```python
import asyncio
import base64
import io
import json
import os
import sys
```

- [ ] **Step 4: Smoke-check the module imports**

```bash
python -c "import gmail_to_discord; print('ok')"
```

Expected: `ok`. Fix any SyntaxError / ImportError before continuing.

- [ ] **Step 5: Commit**

```bash
git add gmail_to_discord.py
git commit -m "Render full email body, images, and action buttons in Discord"
```

---

### Task 7: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the "What you get" bullet list**

Find this section (around line 17–24) and update the first bullet plus add new bullets:

```markdown
## What you get

- New emails appear in Discord within ~60 seconds as blue embeds
- The **full email body** is rendered (HTML → markdown), with the original HTML attached as a `.html` file you can open in your browser
- Inline images, attachments, and remote `<img>` images are downloaded and attached to the Discord message
- Email action buttons (e.g. "Verify email", "View order") appear as real Discord link buttons next to **📨 Reply**
- Clicking Reply opens a Discord popup — the bot sends a properly threaded reply via your Gmail account
- First run marks your current inbox as "seen", so it won't spam you with historical mail
- Everything is logged to `bot.log` so you can check what happened even when running headless
```

- [ ] **Step 2: Remove the obsolete "Ideas for later" line**

Find:

```markdown
- Full-body email support (currently shows Gmail's short snippet)
```

and delete that line. Leave the rest of "Ideas for later" intact.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Update README to reflect full email rendering"
```

---

### Task 8: Manual end-to-end smoke test

**Files:** none (runtime verification)

- [ ] **Step 1: Stop any running bot**

```powershell
Stop-Process -Name pythonw -ErrorAction SilentlyContinue
```

- [ ] **Step 2: Start the bot in the foreground**

```bash
python gmail_to_discord.py
```

Watch for `[ready] Logged in as ...`. If you see auth errors, the existing token is fine — no scope changes were made.

- [ ] **Step 3: Plain-text smoke test**

Send yourself a plain-text email (any plain client, or Gmail's "Plain text mode" in compose). Within ~60 s, in Discord:

- Embed should show the body text in the description
- Only the 📨 Reply button should be present (no link buttons)
- An attachment `email-<id>.html` should be present with `<pre>`-wrapped text inside

- [ ] **Step 4: HTML + button + inline image smoke test**

Compose a Gmail draft with rich formatting: a small inline image (drag-drop a PNG into the body) and an HTML link styled as a button. Easiest: use a service that sends one (GitHub email confirmation, Stripe receipt, etc.).

In Discord, verify:

- Embed description has the body
- The inline image shows in the embed (set_image)
- The "Verify email" / "View order" / etc. button appears next to 📨 Reply
- Clicking the link button opens the URL in a browser

- [ ] **Step 5: Real newsletter smoke test**

Forward yourself any newsletter (Substack, GitHub digest, etc.). Verify:

- Multiple buttons render (capped at 10)
- Multiple images attached (capped at 9)
- The `.html` attachment opens cleanly in a browser
- Body markdown is readable (no raw HTML, no `cid:` references)

- [ ] **Step 6: Long-email truncation test**

Forward a long-form email (>4000 chars). Verify:

- Embed description ends with `[…full email attached as .html below]`
- The `.html` attachment contains the full body

- [ ] **Step 7: Reply still works**

Click 📨 Reply on any of the test emails. Type a reply, submit. Verify:

- Ephemeral confirmation in Discord
- The reply lands in the same Gmail thread as the original

- [ ] **Step 8: Stop the bot**

`Ctrl + C` in the terminal running it.

- [ ] **Step 9: If issues found, debug; otherwise the plan is complete**

If anything misbehaves, capture the relevant log lines from `bot.log`, identify the failing step, and fix in a follow-up commit. Otherwise, optionally restart the bot in the background:

```powershell
Start-Process ".\.venv\Scripts\pythonw.exe" -ArgumentList "gmail_to_discord.py"
```

---

## Self-review (filled in by author)

- **Spec coverage:**
  - "Replace `format='metadata'` with `format='full'` and walk MIME" → Task 2
  - "EmailContent dataclass" → Task 2
  - "render_body_markdown" → Task 3
  - "extract_action_buttons" → Task 4
  - "collect_images" → Task 5
  - "Discord embed + 1 .html + ≤9 images + ≤10 link buttons + Reply" → Task 6
  - "Truncation marker, overflow footer line" → Task 6
  - "Plain-text edge case (no buttons, `.html` is `<pre>`-wrapped)" → Task 6 (`html_payload` fallback) + Task 8 step 3
  - "No new Gmail scopes" → no scope changes anywhere; token reuse confirmed
  - "Reply path unchanged" → Task 6 step 1 explicitly leaves `get_message_details` for the Reply modal
  - "Two new deps (beautifulsoup4, html2text)" → Task 1
  - "README update" → Task 7

- **Placeholder scan:** none. Every step has either exact code or an exact command.

- **Type consistency:** `EmailContent` uses `from_addr` (not `from`, which is reserved). Task 6 step 1 uses `email.from_addr`, matching Task 2's dataclass. `ImageBlob.filename`/`.mime`/`.data` are referenced consistently across Tasks 2/5/6.

- **Scope:** single feature, single primary file. Appropriate for one plan.
