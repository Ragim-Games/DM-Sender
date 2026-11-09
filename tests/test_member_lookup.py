import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from member_lookup import choose_channel, describe_error, load_members


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.member = SimpleNamespace(guild_permissions=SimpleNamespace(
            kick_members=False, ban_members=False, manage_roles=False))
        self.role = object()
        self.channels = []
        self.guild = SimpleNamespace(me=self.member, default_role=self.role, text_channels=self.channels,
                                     get_channel=lambda channel_id: next((c for c in self.channels if c.id == channel_id), None))

    def channel(self, channel_id, visible=True, public=False):
        channel = SimpleNamespace(id=channel_id, permissions_for=lambda subject: SimpleNamespace(
            view_channel=visible if subject is self.member else public))
        self.channels.append(channel)
        return channel

    def test_role_visible_channel_works_without_public_channels(self):
        hidden = self.channel(1, visible=False)
        visible = self.channel(2)
        self.assertIs(choose_channel(self.guild), visible)
        self.assertIsNot(choose_channel(self.guild), hidden)

    def test_public_and_visible_channel_is_preferred(self):
        self.channel(1)
        public = self.channel(2, public=True)
        self.assertIs(choose_channel(self.guild), public)

    def test_explicit_channel_is_used(self):
        self.channel(1, public=True)
        selected = self.channel(2)
        self.assertIs(choose_channel(self.guild, 2), selected)

    def test_hidden_or_other_server_channel_is_rejected(self):
        self.channel(1, visible=False)
        for channel_id in (1, 999):
            with self.subTest(channel_id=channel_id), self.assertRaises(ValueError):
                choose_channel(self.guild, channel_id)

    def test_no_visible_channels_has_specific_error(self):
        self.channel(1, visible=False)
        with self.assertRaisesRegex(ValueError, "No visible text channels"):
            choose_channel(self.guild)


class LookupTests(unittest.IsolatedAsyncioTestCase):
    @patch("builtins.print")
    async def test_explicit_channel_avoids_library_auto_selection(self, output):
        member = SimpleNamespace(guild_permissions=SimpleNamespace(
            kick_members=False, ban_members=False, manage_roles=False))
        channel = SimpleNamespace(id=123, permissions_for=lambda _: SimpleNamespace(view_channel=True))
        guild = SimpleNamespace(me=member, text_channels=[channel], default_role=object(),
                                 get_channel=lambda _: channel, fetch_members=AsyncMock(return_value=["member"]))
        self.assertEqual(await load_members(guild, 123), ["member"])
        guild.fetch_members.assert_awaited_once_with(channels=[channel], force_scraping=True)

    @patch("builtins.print")
    async def test_lookup_timeout_reports_stage(self, output):
        guild = SimpleNamespace(me=SimpleNamespace(guild_permissions=SimpleNamespace(kick_members=True)),
                                 fetch_members=AsyncMock(side_effect=TimeoutError))
        with self.assertRaisesRegex(ValueError, "Member lookup timed out"):
            await load_members(guild)


class DiagnosticTests(unittest.TestCase):
    def test_http_error_reports_codes_not_response_body(self):
        error = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"),
                                  {"code": 50001, "message": "private-token-and-body"})
        message = describe_error(error)
        self.assertIn("HTTP 403, code 50001", message)
        self.assertNotIn("private-token-and-body", message)

    def test_client_exception_has_actionable_hint_without_raw_details(self):
        message = describe_error(discord.ClientException("private-details"))
        self.assertIn("ClientException", message)
        self.assertIn("--member-channel", message)
        self.assertNotIn("private-details", message)

    def test_unexpected_exception_reports_type_without_details(self):
        message = describe_error(RuntimeError("private-details"))
        self.assertIn("RuntimeError", message)
        self.assertNotIn("private-details", message)
