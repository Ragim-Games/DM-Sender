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

ROOT = Path(__file__).resolve().parent


def prompt(message: str) -> str:
    return input(message).strip()


def read_token() -> str:
    if not sys.stdin.isatty():
        raise ValueError("Run this program in an interactive terminal to enter your token securely.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            token = getpass.getpass("Account token (hidden; never saved): ").strip()
        except getpass.GetPassWarning:
            raise ValueError("Hidden input is unavailable. Use Windows Terminal or another terminal.") from None
    if not token or any(character.isspace() for character in token):
        raise ValueError("Enter a non-empty account token without whitespace.")
    return token


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
            interval = float(prompt("Seconds between recipients [10; minimum 5]: ") or "10")
            if math.isfinite(interval) and 5 <= interval <= 3600:
                return interval
        except ValueError:
            pass
        print("Enter a number between 5 and 3600.")


async def deliver(recipients, template, server, campaign, journal, interval, *, sleep=asyncio.sleep):
    """Send sequentially. Stop after any API failure; leave ambiguous attempts pending."""
    import discord

    sent = skipped = failed = 0
    try:
        for member in recipients:
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
            except discord.HTTPException as error:
                # Never print the response body: it can contain request details.
                journal.finish(campaign, member.id, f"failed:{error.status}:{error.code}")
                failed += 1
                print(f"Stopped: Discord rejected delivery (HTTP {error.status}, code {error.code}).")
                print("Resolve the restriction in Discord. This program will not bypass it.")
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
            sent += 1
            journal.finish(campaign, member.id, "sent")
            print(f"Sent {sent}/{len(recipients)} | User ID: {member.id}")
            if sent + skipped < len(recipients):
                await sleep(interval)
    finally:
        print(f"Results: sent={sent}, skipped={skipped}, failed/uncertain={failed}, "
              f"not attempted={len(recipients) - sent - skipped - failed}")
    return failed == 0


async def run_session(token: str, guild_id: int, template: str, interval: float, preview: bool) -> bool:
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
            guild = client.get_guild(guild_id)
            if guild is None:
                raise ValueError("This account cannot access that Guild ID.")
            member = guild.me
            permissions = member.guild_permissions if member else None
            if not permissions or not (permissions.kick_members or permissions.ban_members or permissions.manage_roles):
                raise ValueError("A complete member list requires Kick Members, Ban Members, or Manage Roles permission. No messages were sent.")
            print(f"Connected as: {client.user.name} | Server: {guild.name}")
            print("Loading members, including offline members...")
            members = await asyncio.wait_for(guild.fetch_members(), timeout=180)
            unique = {member.id: member for member in members}
            if guild.member_count and len(unique) < guild.member_count:
                raise ValueError("Discord returned an incomplete member list. No messages were sent. Try again later.")
            recipients = sorted((member for member in unique.values()
                                 if not member.bot and member.id != client.user.id), key=lambda member: member.id)
            # Validate every personalized message before any send.
            for member in recipients:
                render_message(template, member.name, member.display_name, guild.name)
            campaign = campaign_key(client.user.id, guild_id, template)
            journal = Journal(ROOT / "data" / "deliveries.sqlite3")
            try:
                eligible = [member for member in recipients if not journal.seen(campaign, member.id)]
                print(f"Human members: {len(recipients)} | Previously attempted: {len(recipients)-len(eligible)} | Pending: {len(eligible)}")
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
    args = parser.parse_args()
    try:
        print((ROOT / "title.txt").read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError):
        print("DM Sender")
    if args.title_only:
        return 0
    print("User-account automation violates Discord's terms and may terminate your account.")
    print("Tokens stay in memory. Never share your token or commit it to GitHub.")
    print("One announcement per recipient; Ctrl+C stops the program.\n")
    logging.getLogger("discord").addHandler(logging.NullHandler())
    logging.getLogger("discord").propagate = False
    try:
        import discord
    except ImportError:
        print("Missing dependency. Run: python -m pip install -r requirements.txt")
        return 1
    try:
        token = read_token()
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
        return 0 if asyncio.run(run_session(token, guild_id, template, interval, preview)) else 1
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Earlier deliveries remain recorded; pending deliveries are not retried automatically.")
        return 130
    except discord.LoginFailure:
        print("Login failed. Check your account token locally.")
    except ValueError as error:
        print(error)
    except (discord.DiscordException, TimeoutError, OSError):
        print("Stopped: a Discord, network, or local storage error occurred. Check your account and connection.")
    except Exception:
        print("Stopped: an unexpected error occurred. No further messages will be sent.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
