# Gmail → Discord Forwarder

A tiny Python script that watches your Gmail inbox and posts new messages to a Discord channel as rich embeds. Uses a Discord **webhook** (not a full bot), so there's no bot hosting to manage — just a URL that receives messages.

```
📧 Gmail inbox ──(poll every 60s)──▶  Python script  ──(POST embed)──▶  💬 Discord channel
```

---

## What you'll get

For every new email, a nicely formatted Discord message:

- **Title:** email subject
- **Description:** preview snippet
- **From:** sender name + address
- **Footer:** date

The script marks your existing inbox as "seen" on first run, so you won't get spammed with 500 old emails when you start it.

---

## Setup (≈ 10 minutes, all free)

### 1. Install Python dependencies

From this folder, in a terminal:

```bash
python -m venv .venv
# Windows (Git Bash / WSL):
source .venv/Scripts/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 2. Create a Discord webhook

1. Open Discord and go to the server where you want emails to land.
   (If you don't have one, create a private server — takes 10 seconds.)
2. **Server Settings → Integrations → Webhooks → New Webhook**
3. Pick the channel, give it a name like "Gmail", and click **Copy Webhook URL**.
4. Keep that URL — you'll paste it into `.env` in step 4.

> Want emails sent as a **DM** to yourself instead? Discord webhooks only post to channels, so make a personal server with one "Gmail" channel — that's the standard trick.

### 3. Get Gmail API credentials

1. Go to <https://console.cloud.google.com/>
2. Create a new project (top bar → "New Project"). Name it anything.
3. Enable the Gmail API: **APIs & Services → Library** → search "Gmail API" → **Enable**
4. Configure the OAuth consent screen: **APIs & Services → OAuth consent screen**
   - User type: **External**
   - App name: anything (e.g., "Gmail Discord Forwarder")
   - Fill in required email fields with your own email
   - On **Scopes**: you can skip adding scopes here, the script declares what it needs
   - On **Test users**: add your own Gmail address — this is important!
5. Create credentials: **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name: anything
   - Click **Create**, then **Download JSON**
6. Rename the downloaded file to `credentials.json` and place it in this folder (next to `gmail_to_discord.py`).

### 4. Create your `.env` file

Copy the template and fill in the webhook URL:

```bash
cp .env.example .env
```

Open `.env` in a text editor and paste your webhook URL into `DISCORD_WEBHOOK_URL=`.

### 5. Run it

```bash
python gmail_to_discord.py
```

- The **first run** will open a browser window asking you to sign into Google and approve read-only access to Gmail. Because the app is in "testing" mode, you'll see a "Google hasn't verified this app" warning — click **Advanced → Go to [app name] (unsafe)**. It's your own app, so it's fine.
- After approval, a `token.json` file is saved so you won't have to sign in again.
- The script will mark your current inbox as "seen" and start polling. Send yourself a test email to verify.

Press **Ctrl+C** to stop.

---

## Files in this repo

| File | Purpose |
|---|---|
| `gmail_to_discord.py` | The main script |
| `requirements.txt` | Python dependencies |
| `.env.example` | Template for secrets (real `.env` is gitignored) |
| `.gitignore` | Keeps secrets and state files out of git |
| `README.md` | This file |

These files are created at runtime and are **not** committed:

| File | Purpose |
|---|---|
| `.env` | Your Discord webhook URL |
| `credentials.json` | Your Google OAuth client (download from Cloud Console) |
| `token.json` | Saved OAuth token after first sign-in |
| `seen_ids.json` | Message IDs already forwarded, so you don't get duplicates |

---

## Config

All config lives in `.env`:

- `DISCORD_WEBHOOK_URL` — required
- `POLL_INTERVAL_SECONDS` — how often to check Gmail (default `60`, minimum `10`)

---

## Troubleshooting

**"credentials.json not found"** — You didn't complete step 3. Download the OAuth client JSON from Google Cloud Console and name it `credentials.json`.

**"Access blocked: [app] has not completed the Google verification process"** — You're trying to sign in with a Gmail account that isn't listed in the OAuth consent screen's **Test users** list. Add it there.

**Browser opens but sign-in fails / page hangs** — Some corporate Google accounts block OAuth for unverified apps. Use a personal Gmail instead.

**Forwarded message is missing the snippet** — Gmail doesn't generate a preview for every message (e.g., purely HTML emails with no plain-text part). The script shows "*(no preview available)*" in that case.

**Stop forwarding temporarily** — Just Ctrl+C. Your `seen_ids.json` remembers what's been sent, so when you restart you won't get duplicates.

**Reset the bot** — Delete `token.json` (to re-auth) and/or `seen_ids.json` (to re-prime the seen set on next run).

---

## Security notes

- `credentials.json`, `token.json`, and `.env` contain sensitive info and are gitignored.
- The OAuth scope requested is `gmail.readonly` — the script **cannot** send, delete, or modify your mail.
- Your Discord webhook URL is also a secret — anyone with it can post to your channel.
- If you ever accidentally commit any of these files, revoke them immediately:
  - Discord webhook: delete it in Server Settings → Integrations → Webhooks
  - Google OAuth token: revoke at <https://myaccount.google.com/permissions>

---

## Ideas for later

- Filter by sender/subject (only forward important stuff)
- Forward the full email body instead of the snippet
- Deploy to a Raspberry Pi, Fly.io, or Oracle free tier so it runs 24/7
- Replace polling with Gmail Push notifications via Google Pub/Sub (instant delivery)
