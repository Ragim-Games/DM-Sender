"""Interactive, single-pass Discord announcement sender."""

import argparse
import asyncio
import getpass
import logging
import math
import sys
import warnings
from pathlib import Path

from core import Journal, campaign_key, render_message, snowflake
from member_lookup import describe_error, load_members
from token_store import TokenStorageError, TokenStore

ROOT = Path(__file__).resolve().parent


def prompt(message: str) -> str:
    return input(message).strip()


def read_token() -> str:
    if not sys.stdin.isatty():
        raise ValueError("Run this program in an interactive terminal to enter your token securely.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            token = getpass.getpass("Account token (hidden): ").strip()
        except getpass.GetPassWarning:
            raise ValueError("Hidden input is unavailable. Use Windows Terminal or another terminal.") from None
    if not token or any(character.isspace() for character in token):
        raise ValueError("Enter a non-empty account token without whitespace.")
    return token


def select_token(store: TokenStore, replace: bool = False):
    can_save = True
    if not replace:
        try:
            saved = store.load()
        except TokenStorageError:
            print("Credential storage is unavailable. The token will be used for this session only.")
            can_save = False
        else:
            if saved and not any(character.isspace() for character in saved):
                print("Using saved account token. Use --replace-token to change it or --forget-token to delete it.")
                return saved, False
            if saved is not None:
                print("The saved token is invalid. Enter a replacement.")
    token = read_token()
    if not can_save:
        return token, False
    while True:
        answer = prompt("Remember this token in OS credential storage after successful login? [Y/n]: ").lower()
        if answer in ("", "y", "yes"):
            return token, True
        if answer in ("n", "no"):
            print("This token will be used for this session only. Any previously saved token is unchanged.")
            return token, False
        print("Enter yes or no.")


def remember_token(store: TokenStore, token: str) -> None:
    try:
        store.save(token)
    except TokenStorageError:
        print("Could not save the token. Continuing with this session only; any prior saved token may remain.")
    else:
        print("Token saved in OS credential storage for future runs.")


def read_message() -> str:
    while True:
        mode = prompt("Message source: [1] Type text  [2] UTF-8 text file: ")
        if mode == "1":
            print("Enter your message. Finish with a line containing only .end")
            lines = []
            while True:
                line = input()
                if line == ".end":
                    break
                lines.append(line)
            value = "\n".join(lines)
        elif mode == "2":
            path = Path(prompt("Message file path: ").strip('"')).expanduser()
            try:
                if path.stat().st_size > 64_000:
                    print("The message file is too large (maximum 64 KB).")
                    continue
                value = path.read_text(encoding="utf-8-sig").rstrip("\r\n")
            except (OSError, UnicodeError):
                print("Could not read that UTF-8 text file.")
                continue
        else:
            print("Choose 1 or 2.")
            continue
        try:
            render_message(value, "ExampleUser", "Example Member", "Example Server")
        except ValueError as error:
            print(error)
            continue
        return value


def read_interval() -> float:
    while True:
        try:
            interval = float(prompt("Seconds between recipients [0; no added delay]: ") or "0")
            if math.isfinite(interval) and 0 <= interval <= 3600:
                return interval
        except ValueError:
            pass
        print("Enter a number between 0 and 3600.")


async def deliver(recipients, template, server, campaign, journal, interval, *, sleep=asyncio.sleep):
    """Skip rejected deliveries and CAPTCHA challenges; stop on other failures."""
    import discord

    sent = skipped = failed = 0
    try:
        for index, member in enumerate(recipients):
            message = render_message(template, member.name, member.display_name, server)
            if not journal.reserve(campaign, member.id):
                skipped += 1
                continue
            try:
                await member.send(message, allowed_mentions=discord.AllowedMentions.none())
            except asyncio.CancelledError:
                failed += 1
                print("Stopped during a request. Its delivery status is uncertain.")
                raise
            except discord.CaptchaRequired:
                journal.finish(campaign, member.id, "captcha_required")
                failed += 1
                print(f"Skipped user {member.id}: Discord requires CAPTCHA verification (HTTP 400, code -1). "
                      "Continuing with the next recipient.")
            except discord.HTTPException as error:
                # Never print the response body: it can contain request details.
                journal.finish(campaign, member.id, f"failed:{error.status}:{error.code}")
                failed += 1
                reason = {(403, 50007): "cannot send messages to this user",
                          (403, 50278): "Discord reports no mutual guilds with this user",
                          (404, 10013): "user no longer available"}.get((error.status, error.code))
                if reason:
                    print(f"Skipped user {member.id}: {reason} (code {error.code}). Continuing with the next recipient.")
                else:
                    print(f"Stopped: Discord {type(error).__name__} (HTTP {error.status}, code {error.code}).")
                    print("This error is not confirmed to be recipient-specific. No further recipients will be attempted.")
                    break
            except discord.RateLimited:
                journal.finish(campaign, member.id, "rate_limited")
                failed += 1
                print("Stopped: Discord requested an extended rate-limit pause.")
                break
            except Exception:
                failed += 1
                print("Stopped: delivery could not be confirmed. This recipient will not be retried automatically.")
                break
            else:
                sent += 1
                journal.finish(campaign, member.id, "sent")
                print(f"Sent {sent}/{len(recipients)} | User ID: {member.id}")
            if interval > 0 and index + 1 < len(recipients):
                await sleep(interval)
    finally:
        print(f"Results: sent={sent}, skipped={skipped}, failed/uncertain={failed}, "
              f"not attempted={len(recipients) - sent - skipped - failed}")
    return failed == 0


async def run_session(token: str, guild_id: int, template: str, interval: float, preview: bool,
                      on_authenticated=None, member_channel_id=None) -> bool:
    import discord

    # No CAPTCHA handler, proxy rotation, or application-level retry loop.
    client = discord.Client(chunk_guilds_at_startup=False, max_ratelimit_timeout=30.0)
    connection = None
    ready = None
    try:
        async with client:
            connection = asyncio.create_task(client.start(token, reconnect=False))
            ready = asyncio.create_task(wait_ready(client))
            done, _ = await asyncio.wait({connection, ready}, timeout=90, return_when=asyncio.FIRST_COMPLETED)
            if connection in done:
                await connection
                raise ValueError("The connection closed before the account was ready.")
            if ready not in done:
                raise ValueError("Login timed out. No messages were sent.")
            await ready
            if on_authenticated is not None:
                on_authenticated()
            guild = client.get_guild(guild_id)
            if guild is None:
                raise ValueError("This account cannot access that Guild ID.")
            print(f"Connected as: {client.user.name} | Server: {guild.name}")
            members = await load_members(guild, member_channel_id)
            unique = {member.id: member for member in members}
            if not unique:
                raise ValueError("Discord returned no members for this account. No messages were sent.")
            print(f"Retrieved members: {len(unique)} | Reported server size: {guild.member_count or 'unknown'}")
            if not guild.member_count or len(unique) < guild.member_count:
                print("This list may be incomplete. Only retrieved members will be included in the recipient count.")
            recipients = sorted((member for member in unique.values()
                                 if not member.bot and member.id != client.user.id), key=lambda member: member.id)
            # Validate every personalized message before any send.
            for member in recipients:
                render_message(template, member.name, member.display_name, guild.name)
            campaign = campaign_key(client.user.id, guild_id, template)
            journal = Journal(ROOT / "data" / "deliveries.sqlite3")
            try:
                eligible = [member for member in recipients if not journal.seen(campaign, member.id)]
                print(f"Retrieved human recipients: {len(recipients)} | Previously attempted: {len(recipients)-len(eligible)} | Pending: {len(eligible)}")
                if not eligible:
                    print("No new recipients. Nothing to send.")
                    return True
                print("\n--- Message preview (first pending recipient) ---")
                print(render_message(template, eligible[0].name, eligible[0].display_name, guild.name))
                print("--- End preview ---\n")
                print(f"Interval: {interval:g} seconds. Delivery is not guaranteed for blocked or closed DMs.")
                if preview:
                    print("Preview complete. No DMs were sent.")
                    return True
                confirmation = await asyncio.to_thread(prompt, f"Type SEND {len(eligible)} to send one DM per pending recipient: ")
                if confirmation != f"SEND {len(eligible)}":
                    print("Cancelled. No DMs were sent.")
                    return True
                return await deliver(eligible, template, guild.name, campaign, journal, interval)
            finally:
                journal.close()
    finally:
        for task in (ready, connection):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (ready, connection) if task is not None), return_exceptions=True)


async def wait_ready(client):
    # start() initializes the internal ready event on the next loop iteration.
    while not client.is_ready():
        await asyncio.sleep(0.1)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Interactive single-pass Discord announcement sender.")
    parser.add_argument("--preview", action="store_true", help="Connect and preview recipients without sending any DMs.")
    parser.add_argument("--title-only", action="store_true", help="Display the startup title without connecting.")
    parser.add_argument("--member-channel", type=snowflake, metavar="CHANNEL_ID",
                        help="Read members from a specific visible text channel's sidebar.")
    token_options = parser.add_mutually_exclusive_group()
    token_options.add_argument("--replace-token", action="store_true", help="Enter a new token instead of using the saved one.")
    token_options.add_argument("--forget-token", action="store_true", help="Delete the saved account token and exit without connecting.")
    args = parser.parse_args()
    try:
        print((ROOT / "title.txt").read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError):
        print("DM Sender")
    if args.title_only:
        return 0
    store = TokenStore()
    if args.forget_token:
        try:
            print("Saved token deleted." if store.forget() else "No saved token found.")
            return 0
        except TokenStorageError:
            print("Could not delete the saved token. Unlock or check your OS credential storage and try again.")
            return 1
    print("User-account automation violates Discord's terms and may terminate your account.")
    print("Remembered tokens use OS credential storage. Never share your token or commit it to GitHub.")
    print("One announcement per recipient; Ctrl+C stops the program.\n")
    logging.getLogger("discord").addHandler(logging.NullHandler())
    logging.getLogger("discord").propagate = False
    try:
        import discord
    except ImportError:
        print("Missing dependency. Run: python -m pip install -r requirements.txt")
        return 1
    try:
        token, remember = select_token(store, replace=args.replace_token)
        while True:
            try:
                guild_id = snowflake(prompt("Guild ID: "))
                break
            except ValueError as error:
                print(error)
        print("Optional placeholders: {username}, {display_name}, {server}")
        template = read_message()
        interval = read_interval()
        preview = args.preview
        if not preview:
            while True:
                choice = prompt("Action: [1] Preview only  [2] Send after confirmation: ")
                if choice in ("1", "2"):
                    preview = choice == "1"
                    break
                print("Choose 1 or 2.")
        on_authenticated = (lambda: remember_token(store, token)) if remember else None
        return 0 if asyncio.run(run_session(token, guild_id, template, interval, preview,
                                            on_authenticated=on_authenticated,
                                            member_channel_id=args.member_channel)) else 1
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Earlier deliveries remain recorded; pending deliveries are not retried automatically.")
        return 130
    except discord.LoginFailure:
        print("Login failed. Check your token locally and run with --replace-token to enter a new one.")
    except ValueError as error:
        print(error)
    except Exception as error:
        print(describe_error(error))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
