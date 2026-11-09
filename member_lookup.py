"""Choose a visible member-list channel instead of requiring public channels."""

import asyncio


def choose_channel(guild, channel_id=None):
    member = guild.me
    if member is None:
        raise ValueError("Your server permissions have not loaded yet. Reconnect and try again.")
    if channel_id is not None:
        channel = guild.get_channel(channel_id)
        if channel is None or channel not in guild.text_channels:
            raise ValueError("The member-list Channel ID must be a text or announcement channel in this server.")
        if not channel.permissions_for(member).view_channel:
            raise ValueError("Your account cannot view the specified member-list channel.")
        return channel
    visible = [channel for channel in guild.text_channels if channel.permissions_for(member).view_channel]
    if not visible:
        raise ValueError("No visible text channels were found. Check the Guild ID and your channel access.")
    # Prefer a broad sidebar when available, then use a channel visible to this account.
    public = [channel for channel in visible if channel.permissions_for(guild.default_role).view_channel]
    return (public or visible)[0]


async def load_members(guild, channel_id=None):
    permissions = guild.me.guild_permissions if guild.me else None
    broad_list = permissions and (permissions.kick_members or permissions.ban_members or permissions.manage_roles)
    if broad_list and channel_id is None:
        print("Requesting the server member list...")
        request = guild.fetch_members()
    else:
        channel = choose_channel(guild, channel_id)
        print(f"Member-list channel ID: {channel.id}")
        print("Loading this channel's visible members. Offline or hidden members may be missing.")
        request = guild.fetch_members(channels=[channel], force_scraping=True)
    try:
        return await asyncio.wait_for(request, timeout=180)
    except TimeoutError:
        raise ValueError("Member lookup timed out after 180 seconds. No DMs were sent. Try --member-channel with a visible text channel's ID.") from None


def describe_error(error):
    """Report types and numeric API codes, never response bodies or credentials."""
    import discord

    name = type(error).__name__
    if isinstance(error, discord.CaptchaRequired):
        return "Stopped: Discord requested CAPTCHA verification. No further requests will be made."
    if isinstance(error, discord.HTTPException):
        return f"Stopped: Discord {name} (HTTP {error.status}, code {error.code}). Check this account's access in Discord."
    if isinstance(error, discord.RateLimited):
        return "Stopped: Discord requested an extended rate-limit pause. Try again later."
    if isinstance(error, discord.ConnectionClosed):
        return f"Stopped: the Discord gateway closed (code {error.code}). Reconnect and try again."
    if isinstance(error, discord.ClientException):
        return f"Stopped: Discord {name} during a client operation. For member lookup, specify --member-channel with a visible text channel's ID."
    if isinstance(error, discord.InvalidData):
        return "Stopped: Discord InvalidData. The member-list response may be missing or unsupported; try a visible channel with --member-channel."
    if isinstance(error, TimeoutError):
        return "Stopped: a network operation timed out. Check the connection and try again."
    if isinstance(error, OSError):
        return f"Stopped: {name} (OS error {error.errno}, Windows error {getattr(error, 'winerror', None)}). Check network access and local file permissions."
    return f"Stopped: {name}. No further messages will be sent. Share this error type when reporting the problem."
