import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from core import Journal, campaign_key, render_message, snowflake
from main import deliver, read_interval, read_message, run_session


class MessageTests(unittest.TestCase):
    def test_utf16_limit(self):
        self.assertEqual(len(render_message("😀" * 1000, "", "", "")), 1000)
        with self.assertRaises(ValueError):
            render_message("😀" * 1001, "", "", "")
        with self.assertRaises(ValueError):
            render_message(" \n", "", "", "")

    def test_substitution_does_not_interpret_names(self):
        self.assertEqual(render_message("Hi {username} in {server}: {other}", "{server}", "", "Community"),
                         "Hi {server} in Community: {other}")

    def test_guild_id(self):
        self.assertEqual(snowflake("123456789012345678"), 123456789012345678)
        for value in ("0", "-1", "1.5", "abc", "１２３", str(2**64)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                snowflake(value)

    def test_campaign_scope(self):
        keys = {campaign_key(1, 2, "Hi"), campaign_key(2, 2, "Hi"),
                campaign_key(1, 3, "Hi"), campaign_key(1, 2, "Hello")}
        self.assertEqual(len(keys), 4)

    @patch("builtins.print")
    def test_multiline_input(self, _print):
        with patch("builtins.input", side_effect=["1", "First line", "", "Last line", ".end"]):
            self.assertEqual(read_message(), "First line\n\nLast line")

    @patch("builtins.print")
    def test_interval_rejects_nonfinite_and_out_of_range(self, _print):
        with patch("builtins.input", side_effect=["nan", "inf", "-1", "3601", "0"]):
            self.assertEqual(read_interval(), 0)
        with patch("builtins.input", return_value=""):
            self.assertEqual(read_interval(), 0)
        with patch("builtins.input", return_value="0.1"):
            self.assertEqual(read_interval(), 0.1)


class JournalTests(unittest.TestCase):
    def test_reservations_survive_restart_and_prevent_concurrent_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            first = Journal(path)
            second = Journal(path)
            try:
                self.assertTrue(first.reserve("campaign", 42))
                self.assertFalse(second.reserve("campaign", 42))
            finally:
                first.close()
                second.close()
            restarted = Journal(path)
            try:
                self.assertTrue(restarted.seen("campaign", 42))
                self.assertFalse(restarted.reserve("campaign", 42))
                self.assertTrue(restarted.reserve("another", 42))
            finally:
                restarted.close()


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.journal = Journal(Path(self.directory.name) / "journal.sqlite3")
        self.members = [SimpleNamespace(id=n, name=f"user{n}", display_name=f"Member {n}", send=AsyncMock())
                        for n in (1, 2, 3)]

    def tearDown(self):
        self.journal.close()
        self.directory.cleanup()

    @patch("builtins.print")
    async def test_single_pass_and_restart(self, _print):
        sleep = AsyncMock()
        self.assertTrue(await deliver(self.members, "Hi {username}", "Server", "key", self.journal, 0, sleep=sleep))
        sleep.assert_not_awaited()
        self.assertTrue(await deliver(self.members, "Hi {username}", "Server", "key", self.journal, 0, sleep=sleep))
        for member in self.members:
            member.send.assert_awaited_once()
            self.assertFalse(member.send.call_args.kwargs["allowed_mentions"].everyone)

    @patch("builtins.print")
    async def test_forbidden_stops_remaining_recipients(self, _print):
        self.members[0].send.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), {"code": 50007, "message": "Cannot send"})
        self.assertFalse(await deliver(self.members, "Hello", "Server", "key", self.journal, 10, sleep=AsyncMock()))
        self.members[1].send.assert_not_awaited()
        self.members[2].send.assert_not_awaited()
        self.assertTrue(self.journal.seen("key", 1))
        self.assertFalse(self.journal.seen("key", 2))

    @patch("builtins.print")
    async def test_unknown_delivery_is_not_retried(self, _print):
        self.members[0].send.side_effect = ConnectionError("Uncertain transport outcome")
        self.assertFalse(await deliver(self.members, "Hello", "Server", "key", self.journal, 10, sleep=AsyncMock()))
        self.assertTrue(self.journal.seen("key", 1))
        self.assertEqual(self.journal.db.execute("SELECT status FROM deliveries").fetchone()[0], "pending")
        self.members[0].send.reset_mock()
        await deliver(self.members[:1], "Hello", "Server", "key", self.journal, 10, sleep=AsyncMock())
        self.members[0].send.assert_not_awaited()

    @patch("builtins.print")
    async def test_rate_limit_stops(self, _print):
        self.members[0].send.side_effect = discord.RateLimited(60)
        self.assertFalse(await deliver(self.members, "Hello", "Server", "key", self.journal, 10, sleep=AsyncMock()))
        self.members[1].send.assert_not_awaited()


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.person = SimpleNamespace(id=2, name="user", display_name="User", bot=False, send=AsyncMock())
        self.guild = SimpleNamespace(name="Community", member_count=1,
                                     me=SimpleNamespace(guild_permissions=SimpleNamespace(
                                         kick_members=True, ban_members=False, manage_roles=False)),
                                     fetch_members=AsyncMock(return_value=[self.person]))
        self.channel = SimpleNamespace(id=456, permissions_for=lambda member: SimpleNamespace(view_channel=True))
        self.guild.text_channels = [self.channel]
        self.guild.default_role = object()
        self.guild.get_channel = lambda channel_id: self.channel if channel_id == 456 else None
        guild = self.guild

        class FakeClient:
            user = SimpleNamespace(id=1, name="Sender")

            async def __aenter__(self):
                self.closed = asyncio.Event()
                return self

            async def __aexit__(self, *args):
                self.closed.set()

            async def start(self, *args, **kwargs):
                await self.closed.wait()

            def is_ready(self):
                return True

            def get_guild(self, guild_id):
                return guild

        self.client = FakeClient()
        self.client_patch = patch("discord.Client", return_value=self.client)
        self.journal_patch = patch("main.Journal", side_effect=lambda _: Journal(Path(self.directory.name) / "journal.sqlite3"))
        self.client_patch.start()
        self.journal_patch.start()

    def tearDown(self):
        self.client_patch.stop()
        self.journal_patch.stop()
        self.directory.cleanup()

    @patch("builtins.print")
    async def test_preview_never_sends(self, _print):
        on_authenticated = Mock()
        self.assertTrue(await run_session("fake-token", 123, "Hello", 10, True,
                                          on_authenticated=on_authenticated))
        on_authenticated.assert_called_once_with()
        self.person.send.assert_not_awaited()

    @patch("builtins.print")
    async def test_failed_login_never_saves_token(self, _print):
        self.client.start = AsyncMock(side_effect=discord.LoginFailure("Invalid token"))
        on_authenticated = Mock()
        with self.assertRaises(discord.LoginFailure):
            await run_session("fake-token", 123, "Hello", 10, True, on_authenticated=on_authenticated)
        on_authenticated.assert_not_called()
        self.person.send.assert_not_awaited()

    @patch("builtins.print")
    async def test_incomplete_member_list_can_send_after_confirmation(self, output):
        self.guild.member_count = 5
        with patch("main.prompt", return_value="SEND 1") as prompt:
            self.assertTrue(await run_session("fake-token", 123, "Hello", 10, False))
        self.person.send.assert_awaited_once()
        self.assertIn("SEND 1", prompt.call_args.args[0])
        self.assertIn("may be incomplete", str(output.call_args_list))

    @patch("builtins.print")
    async def test_sidebar_members_can_send_without_moderation_permissions(self, output):
        permissions = self.guild.me.guild_permissions
        permissions.kick_members = permissions.ban_members = permissions.manage_roles = False
        with patch("main.prompt", return_value="SEND 1"):
            self.assertTrue(await run_session("fake-token", 123, "Hello", 0, False))
        self.guild.fetch_members.assert_awaited_once()
        self.person.send.assert_awaited_once()
        self.guild.fetch_members.assert_awaited_once_with(channels=[self.channel], force_scraping=True)
        self.assertIn("channel's visible members", str(output.call_args_list))

    @patch("builtins.print")
    async def test_partial_preview_does_not_send(self, output):
        self.guild.me.guild_permissions.kick_members = False
        self.guild.member_count = None
        self.assertTrue(await run_session("fake-token", 123, "Hello", 0, True))
        self.person.send.assert_not_awaited()
        self.assertIn("may be incomplete", str(output.call_args_list))

    @patch("builtins.print")
    async def test_empty_member_list_stops_without_confirmation(self, output):
        self.guild.fetch_members.return_value = []
        with patch("main.prompt") as prompt:
            with self.assertRaisesRegex(ValueError, "returned no members"):
                await run_session("fake-token", 123, "Hello", 0, False)
        prompt.assert_not_called()
        self.person.send.assert_not_awaited()

    @patch("builtins.print")
    async def test_confirmation_must_match_count(self, _print):
        with patch("main.prompt", return_value="SEND 99"):
            self.assertTrue(await run_session("fake-token", 123, "Hello", 10, False))
        self.person.send.assert_not_awaited()

    @patch("builtins.print")
    async def test_confirmed_send(self, _print):
        with patch("main.prompt", return_value="SEND 1"):
            self.assertTrue(await run_session("fake-token", 123, "Hello", 10, False))
        self.person.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
