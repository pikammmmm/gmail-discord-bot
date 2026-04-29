# Gmail → Discord Bot (with Reply button)

A Python Discord bot that watches your Gmail inbox and posts new messages to a Discord channel as rich embeds. Each forwarded email includes a **Reply** button — click it, type your response in the popup, and the bot sends a properly threaded reply straight from your Gmail account.

```
📧 Gmail inbox ──(poll every 60s)──▶  Python bot  ──(embed + Reply button)──▶  💬 Discord channel
                                         ▲                                            │
                                         │                                            │ click Reply
                                         │                                            ▼
                                         │                                      📝 modal popup
                                         │                                            │
                                         └──────── send threaded reply ◀──────────────┘
```

---

## What you get

- New emails appear in Discord within ~60 seconds as blue embeds
- The **full email body** is rendered (HTML → markdown), with the original HTML attached as a `.html` file you can open in your browser
- Inline images, attachments, and remote `<img>` images are downloaded and attached to the Discord message
- Email action buttons (e.g. "Verify email", "View order") appear as real Discord link buttons next to **📨 Reply**
- Clicking Reply opens a Discord popup with a single body field — the subject is auto-set to `Re: [original]`
- Sending posts the reply into the **same Gmail thread**, so the other person sees a real reply, not a new email
- First run marks your current inbox as "seen", so it won't spam you with historical mail
- Everything is logged to `bot.log` so you can check what happened even when running headless

---

## Setup (about 15 minutes, all free)

### 1. Install Python dependencies

```bash
git clone https://github.com/<your-username>/gmail-discord-bot.git
cd gmail-discord-bot
python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell / CMD
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

### 2. Create your Discord bot (new!)

Unlike webhooks, buttons require a real bot with a token.

1. Go to <https://discord.com/developers/applications>
2. **New Application** → give it a name like `Gmail Bot` → **Create**
3. Left sidebar → **Bot**
4. Under **Privileged Gateway Intents**, leave all toggles OFF — we don't need any privileged intents
5. Under the bot username, click **Reset Token** → **Yes, do it** → **Copy**
   - ⚠️ Treat this like a password. Anyone with it can control your bot.
6. Left sidebar → **OAuth2 → URL Generator**
7. **Scopes:** check `bot` and `applications.commands`
8. **Bot Permissions:** check `Send Messages`, `Embed Links`, `Read Message History`
9. Copy the **Generated URL** at the bottom of the page → paste it in your browser
10. Pick the server where you want the bot → **Authorize**

The bot should now appear (offline) in your server's member list.

### 3. Decide where the bot should send emails

You have two options — pick one (or set both; DM mode wins if both are set).

**Option A — DM mode (recommended).** The bot slides every email into your personal Discord DMs.
1. In Discord: **User Settings → Advanced → Developer Mode** (turn it ON).
2. Right-click your **own profile** in any server → **Copy User ID**.
3. You'll paste this as `DM_USER_ID` in step 5.
4. Make sure you share at least one server with the bot (you already do from step 2) **and** that your DM privacy settings allow messages from server members.

**Option B — Channel mode.** The bot posts into a server channel.
1. Turn on Developer Mode as above.
2. Right-click the target channel → **Copy Channel ID**.
3. You'll paste this as `DISCORD_CHANNEL_ID` in step 5.

### 4. Get Gmail API credentials

1. <https://console.cloud.google.com/> → create or select a project.
2. **APIs & Services → Library** → search **Gmail API** → **Enable**.
3. **APIs & Services → OAuth consent screen** → **External** → fill in the required fields → add your own Gmail address under **Test users**.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID** → application type **Desktop app** → **Create** → **Download JSON**.
5. Rename the downloaded file to `credentials.json` and put it next to `gmail_to_discord.py`.

### 5. Fill in `.env`

Copy the template:

```bash
cp .env.example .env
```

Open `.env` in any text editor and fill in the values you collected:

```env
DISCORD_BOT_TOKEN=the token you copied in step 2

# Pick one (or both — DM_USER_ID wins if both are set)
DM_USER_ID=your own Discord user ID (Option A)
DISCORD_CHANNEL_ID=the channel ID (Option B)

POLL_INTERVAL_SECONDS=60
```

> If you're upgrading from an older version that only had the `gmail.readonly` scope, delete `token.json` so the next run re-authenticates with the new `gmail.send` scope:
>
> ```powershell
> Remove-Item .\token.json -ErrorAction SilentlyContinue
> ```

### 6. Run it

```bash
python gmail_to_discord.py
```

First run:
- A browser window opens asking you to sign into Google
- You'll see "Google hasn't verified this app" → **Advanced → Go to [app name] (unsafe)** → **Continue**
- You'll be asked to grant **two** permissions: read Gmail **and** send mail as you
- Close the browser tab when done
- The bot connects to Discord — you'll see it come online in your server

Send yourself a test email, wait up to 60 seconds, and the embed should show up in your channel with a **Reply** button.

Press **Ctrl+C** to stop.

---

## Using the Reply button

1. Click **📨 Reply** on any forwarded email
2. A popup appears with a single text area
3. Type your reply → click **Submit**
4. The bot confirms with an ephemeral message (only you can see it)
5. Check your Gmail — the reply is in the same thread as the original email

The reply:
- Is sent **from your Gmail address** as a real email
- Is properly threaded via `In-Reply-To` / `References` headers
- Uses `Re: [original subject]` automatically

---

## Files in this repo

| File | Purpose |
|---|---|
| `gmail_to_discord.py` | The bot (polling + Reply button + modal + Gmail send) |
| `requirements.txt` | Python dependencies |
| `.env.example` | Template for secrets (real `.env` is gitignored) |
| `.gitignore` | Keeps secrets, token, and logs out of git |
| `run_bot.bat` | Double-click launcher (visible window, good for debugging) |
| `README.md` | This file |

Runtime-only (not in git):

| File | Purpose |
|---|---|
| `.env` | Your bot token + DM user ID / channel ID |
| `credentials.json` | OAuth client from Google Cloud |
| `token.json` | Saved OAuth token after first sign-in |
| `seen_ids.json` | Message IDs already forwarded, to prevent duplicates |
| `bot.log` | Runtime log, auto-truncated at ~500 KB |

---

## Running it in the background on startup (Windows)

To make the bot launch silently every time you log into Windows:

1. Press `Win + R`, type `shell:startup`, press Enter — this opens your user Startup folder.
2. Right-click in the folder → **New → Shortcut**.
3. Paste the full path to `pythonw.exe` inside your venv, for example:
   ```
   C:\path\to\gmail-discord-bot\.venv\Scripts\pythonw.exe
   ```
4. Click **Next**, name it `Gmail-Discord Bot`, click **Finish**.
5. Right-click the new shortcut → **Properties**:
   - **Target**: append a space and `gmail_to_discord.py` at the end
   - **Start in**: set this to the project folder (e.g. `C:\path\to\gmail-discord-bot`)
   - Click **OK**
6. Double-click the shortcut once to verify it starts (you won't see a window — that's the point).

Useful commands once it's running in the background:

```powershell
# Is the bot alive?
Get-Process pythonw -ErrorAction SilentlyContinue

# See the latest log entries
Get-Content .\bot.log -Tail 20

# Restart after editing code (run from the project folder)
Stop-Process -Name pythonw -ErrorAction SilentlyContinue
Start-Process ".\.venv\Scripts\pythonw.exe" -ArgumentList "gmail_to_discord.py"
```

> On macOS/Linux, use `launchd` / `systemd --user` / a `cron @reboot` entry instead — the Python script itself is cross-platform.

---

## Troubleshooting

**"DISCORD_BOT_TOKEN not set"** — You haven't filled in `.env`. Do step 5.

**"Channel 12345 not visible to the bot"** — Either the bot isn't invited to the server, or the channel ID is wrong. Redo steps 2 and 3.

**"Access blocked: [app] has not completed the Google verification process"** — Your Gmail address isn't in the OAuth consent screen's **Test users** list. Add it.

**Bot posts emails but the Reply button does nothing** — Check `bot.log` for errors. Most likely cause: the bot token is missing `applications.commands` scope in its invite URL. Re-invite with the correct scopes from step 2.

**"Failed to send reply: insufficient_scope"** — Your `token.json` still has the old readonly-only scopes. Delete `token.json` and run again to re-authenticate.

**Stop the headless bot** — `Stop-Process -Name pythonw`

---

## Security notes

- Your bot token, OAuth credentials, and saved token are all gitignored
- The Gmail scopes used are `gmail.readonly` + `gmail.send` — the bot can read incoming mail and send as you, but **cannot** delete or modify existing messages
- If you ever leak your bot token, immediately **Reset Token** in the Discord Developer Portal — the old token becomes useless instantly
- Same for `credentials.json` / `token.json`: revoke at <https://myaccount.google.com/permissions>

---

## Ideas for later

- Filter by sender/subject (only forward important mail)
- Add an "Archive" button next to Reply
- Deploy to a Raspberry Pi or cloud host so it runs 24/7 without your PC
