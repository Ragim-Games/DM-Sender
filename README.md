# DM Sender

An English-language terminal application for a single server announcement. It prints `title.txt`, securely prompts for an account token and Guild ID, then asks for message text, delivery interval, and preview or send mode.

**Discord prohibits user-account automation and may terminate your account.** This is an unofficial client. It cannot guarantee delivery, overcome closed DMs, or guarantee a complete member list. See [Discord's account automation policy](https://support.discord.com/hc/en-us/articles/115002192352-Automated-User-Accounts-Self-Bots).

## Setup

Install Python 3.10 or newer. Use a dedicated virtual environment; do not install `discord.py` and `discord.py-self` in the same environment.

```powershell
git clone https://github.com/Ragim-Games/DM-Sender.git
cd DM-Sender
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py
```

After setup, Windows users can double-click `start.bat`. Use Windows Terminal with a Unicode-capable font for the supplied title artwork.

On macOS/Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

## Workflow

1. On the first run, enter your account token in the hidden terminal prompt and choose whether to remember it (default Yes). It is saved only after successful login and automatically reused next time. Choosing No keeps the new token in memory for this session. Tokens are sent to Discord for authentication. Do not paste them into chat, command-line arguments, source code, or GitHub.
2. Enter a server's numeric Guild ID. Your account must already belong to the server. Enable Developer Mode in Discord, then use the server's Copy Server ID action.
3. Type a multiline message ending with `.end`, or load a UTF-8 text file. Optional placeholders are `{username}`, `{display_name}`, and `{server}`. Other braces are literal. Messages must fit within 2,000 UTF-16 code units after substitution. Files and text are sent as message content, not attachments.
4. Choose a delivery interval (default 0 seconds). Zero adds no delay between sequential deliveries; positive values add the specified wait. Actual throughput depends on Discord response times and rate limits, which the client continues to respect.
5. Choose preview or sending. The program authenticates, checks the server, and loads the members available to your account. Kick Members, Ban Members, and Manage Roles are not required to proceed. With one of these permissions the client can request a broader list including offline members; otherwise it retrieves members visible through the member sidebar. Offline or hidden members may be missing. The program displays the retrieved count and reported server size, warns when the list may be incomplete, and lets you continue with the retrieved members. Bots and your own account are excluded. If no members can be retrieved, it stops without sending.
6. Review the recipient count and sample personalized message. Sending requires the exact confirmation `SEND <count>`. Any other response cancels.
7. Review progress and the final counts. Ctrl+C stops future sends. Already sent messages remain sent, and a request already in flight may still complete.

The pinned client library handles normal Discord rate-limit waits. The application sends sequentially and stops at the first surfaced API error, extended rate limit, CAPTCHA, or unconfirmed delivery. It does not solve CAPTCHAs, rotate accounts or proxies, or bypass blocks. A failed recipient can therefore prevent the rest of the announcement from being delivered in that run.

## Remembering, replacing, and deleting a token

Update dependencies before running an upgraded copy:

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
```

Remembered tokens use Windows Credential Manager, macOS Keychain, or Linux Secret Service through [keyring](https://keyring.readthedocs.io/en/latest/). The application explicitly selects native backends and never falls back to a plaintext token file. If storage is unavailable or locked, you can still enter a token for the current session. On Linux, an unlocked Secret Service keyring and desktop D-Bus session are required for persistence.

One token is stored for the current OS user under service `Ragim-Games.DM-Sender` and account `default-account`. Copies of this app run by the same OS user share that entry. Only save a token under an OS account you trust; other processes running as that user may be able to access the credential vault.

```powershell
.venv\Scripts\python main.py --replace-token
.venv\Scripts\python main.py --forget-token
```

`--replace-token` prompts for a new token and asks whether to remember it. The existing entry is replaced only after the new token logs in successfully and saving succeeds. Declining to save, cancelling, or failing login leaves the previous token unchanged. `--forget-token` deletes the entry and exits without connecting to Discord; it does not revoke the token in Discord or remove delivery history. If a saved token expires, use `--replace-token`. Both flags can also be passed to `start.bat`. `--title-only` always displays only the title and does not access credentials.

## Duplicate protection and local data

`data/deliveries.sqlite3` records the campaign hash, recipient IDs, status, and timestamps. It does not store tokens or message bodies. Treat this file as private server-member data. It is excluded from Git.

The same account, Guild ID, and exact message template identify a campaign. Each recipient is reserved before sending. Subsequent runs skip every recorded attempt, including failed or uncertain attempts, to avoid accidental duplicate delivery. A crash or network loss can leave a `pending` entry even if the DM arrived. Review it manually in Discord; the app never automatically retries it. Changing the text creates a new campaign; deleting or moving the database removes duplicate protection. Keep it when updating the application. Two program instances sharing this database cannot reserve the same recipient for the same campaign.

## Preview and tests

```powershell
.venv\Scripts\python main.py --title-only
.venv\Scripts\python main.py --preview
.venv\Scripts\python -m unittest discover -s tests -v
```

`--title-only` is fully offline. `--preview` connects to Discord and retrieves members, but never sends DMs. The automated tests use simulated recipients and send no real messages.

## Limitations

Discord can change its unofficial account API at any time. Closed DMs, blocks, moderation requirements, account restrictions, and changing server membership can prevent completion. Receipt is not proof of reading. The program does not send repeated reminders, schedule campaigns, or support attachments. There is no hosted service: publishing this repository distributes source code, and you run it locally.
