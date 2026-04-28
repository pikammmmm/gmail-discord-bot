# Full Email Rendering — Design

**Date:** 2026-04-28
**Status:** Approved (pending review)
**Affected file:** `gmail_to_discord.py`

## Goal

Today the bot posts only the Gmail-generated short snippet, with no images and no clickable email actions. Upgrade the Discord embed so each forwarded email shows:

- The full email body (best-effort HTML → markdown), truncated only if it exceeds Discord limits.
- Any inline / attached images, attached to the Discord message.
- Any action buttons in the email (e.g. "Verify email", "View order"), as real Discord link-style buttons next to the existing **📨 Reply** button.
- The full original HTML attached as a `.html` file so nothing is ever lost to truncation.

The existing 📨 Reply flow (modal → threaded Gmail send) must keep working unchanged.

## Non-goals

- No filtering of "junk" images. The user explicitly opted into showing every image (capped only by Discord's hard 10-attachment limit).
- No attempt to perfectly render arbitrary email HTML in Discord — markdown approximation + the attached `.html` is the contract.
- No new Gmail scopes (still `gmail.readonly` + `gmail.send`).

## Architecture

### 1. Gmail fetch

Replace `get_message_details(service, msg_id)`'s metadata-only fetch with `format="full"`, and walk `payload` recursively to extract:

```
EmailContent {
  headers: subject, from, to, date, message_id_header, thread_id
  text_body:  str | None      # text/plain part, decoded
  html_body:  str | None      # text/html part, decoded
  inline_images: dict[cid -> ImageBlob]   # parts with Content-ID, downloaded via attachments.get
  attached_images: list[ImageBlob]        # image/* parts without a CID
  other_attachments: list[FileBlob]       # non-image attachments (kept for future, not posted yet)
}

ImageBlob { filename: str, mime: str, data: bytes }
```

The existing reply path (`send_gmail_reply`) needs only headers, so it keeps using a lightweight metadata fetch — refactor `get_message_details` so that path is preserved (e.g. an `EmailHeaders` helper that both code paths share).

### 2. HTML processing pipeline

New module-level helpers, only invoked when `html_body` exists:

- `extract_action_buttons(html: str) -> list[(label, href)]`
  - Parse with BeautifulSoup.
  - An `<a>` qualifies as an "action button" if **either**:
    - its inline `style` contains `background-color` AND `padding` (the classic email-button pattern), or
    - its visible text matches a small whitelist of action phrases — case-insensitive, exact match on trimmed link text. Whitelist: `verify`, `verify email`, `confirm`, `confirm email`, `view order`, `view invoice`, `view receipt`, `reset password`, `sign in`, `log in`, `accept`, `decline`, `activate`, `get started`, `download`, `open`. (Deliberately excludes `unsubscribe` and `view in browser` — those are usually footer-only and rarely worth a button.)
  - De-duplicate by `href`.
  - Cap at 10 (Discord allows 25 components total; we keep budget for the Reply button and visual breathing room).
  - Each `label` truncated to 80 chars (Discord button label limit).

- `render_body_markdown(html: str, plain: str | None) -> str`
  - If `html` is non-empty: convert with `html2text` configured for Discord (no wrapping, keep links as `[text](url)`, no images — they're attached separately).
  - Strip CID `<img src="cid:...">` before conversion (they become Discord attachments).
  - Otherwise fall back to `plain` (or `(no body)`).

### 3. Image collection

`collect_images(email_content) -> list[ImageBlob]`:

1. Inline images (CID parts) — already downloaded during MIME walk.
2. Image attachments — already downloaded during MIME walk.
3. Remote `<img src="https://...">` URLs from the HTML body — fetched via `requests.get(url, timeout=5, stream=True)`. Failures (timeout, non-200, non-image content-type) are silently skipped. Each downloaded blob is capped at 8 MB (Discord free tier). The URL list is itself capped at 20 fetch attempts to keep poll latency bounded.

Order: inline → attached → remote, preserving discovery order. Cap final list at **9** so one attachment slot stays free for the `.html` file.

### 4. Discord message construction (`_post_email`)

For each new email, post **one** Discord message containing:

- **Embed**
  - `title`: subject (truncated to 256)
  - `description`: rendered markdown body, truncated to 4000 chars; if truncated, append `\n\n*[…full email attached as .html below]*`
  - field `From`: sender (truncated to 1024)
  - footer: date
  - `set_image(url=f"attachment://{first_image.filename}")` if any images were collected
- **Files** (`discord.File`, max 10):
  - `email-<msg_id>.html` containing the original HTML body (or wrapped plain text if no HTML)
  - Up to 9 images, in collection order
- **View** (`discord.ui.View(timeout=None)`):
  - Existing **📨 Reply** button (kept unchanged, still `custom_id=f"reply:{msg_id}"`)
  - Up to 10 link-style buttons (`discord.ui.Button(style=ButtonStyle.link, url=href, label=label)`) for the action buttons.
- If buttons or images were dropped due to caps, append a final line to the embed description: `\n\n_+N more links / +M more images in attached HTML_`

### 5. Edge cases

| Case | Behavior |
|---|---|
| Plain-text-only email | Skip HTML pipeline. Use `text_body` directly as description. No action buttons. `.html` attachment is a `<pre>`-wrapped copy of the text body. |
| No body at all (rare) | Description = `(no body)`. Still attach headers as `.html`. |
| Body fits well under 4000 chars | No truncation marker. |
| Remote image fetch fails | Skipped. No error in embed. |
| Email with > 9 images | First 9 attached. Footer line notes the overflow count. |
| Email with > 10 action buttons | First 10 used. Footer line notes the overflow count. |
| Attached file > 8 MB (image or HTML) | Skipped with a footer note. |

### 6. Dependencies

Add to `requirements.txt`:

```
beautifulsoup4>=4.12.0
html2text>=2024.2.26
```

(`requests` already pinned.)

### 7. Reply path (unchanged)

`send_gmail_reply` continues to fetch metadata only and reuses the original Gmail thread ID + Message-ID for proper threading. No behavior change.

### 8. Files touched

- `gmail_to_discord.py` — all logic changes.
- `requirements.txt` — two new pins.
- `README.md` — short note in "Ideas for later" → moved to "What you get".
- No new files at runtime; the `.html` attachments are sent directly to Discord, not written to disk.

## Testing strategy

Manual end-to-end (no unit-test suite exists in the repo):

1. Send self a plain-text email → Discord embed shows the text body, no buttons except Reply, `.html` attachment present.
2. Send self an HTML email with an inline `<img src="cid:...">` and a styled `<a style="background-color:#1a73e8;padding:12px">Verify</a>` → embed shows body, image attached + visible in embed, "Verify" button appears next to Reply, click opens correct URL.
3. Forward a real-world newsletter (e.g. GitHub or Stripe receipt) → multiple action buttons render, multiple images attached, body is readable, `.html` opens cleanly in a browser.
4. Receive a long email (>4000 chars) → description is truncated with the `[…full email attached]` marker; `.html` contains everything.
5. Click 📨 Reply on each → confirm the reply still threads correctly in Gmail.

## Rollback

Single-file change. `git revert <commit>` restores prior behavior. `seen_ids.json` is unaffected (same IDs, same key shape).
