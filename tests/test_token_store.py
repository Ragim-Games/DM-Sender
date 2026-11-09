import unittest
from unittest.mock import Mock, patch

from main import main, remember_token, select_token
from token_store import ACCOUNT, SERVICE, TokenStorageError, TokenStore


class TokenStoreTests(unittest.TestCase):
    def test_native_storage_receives_only_app_scoped_operations(self):
        backend = Mock()
        backend.get_password.return_value = "test-token"
        store = TokenStore(backend)
        store.save("test-token")
        backend.set_password.assert_called_once_with(SERVICE, ACCOUNT, "test-token")
        self.assertEqual(store.load(), "test-token")
        self.assertTrue(store.forget())
        backend.delete_password.assert_called_once_with(SERVICE, ACCOUNT)
        for call in backend.get_password.call_args_list:
            self.assertEqual(call.args, (SERVICE, ACCOUNT))

    def test_missing_entry_does_not_trigger_delete(self):
        backend = Mock()
        backend.get_password.return_value = None
        self.assertFalse(TokenStore(backend).forget())
        backend.delete_password.assert_not_called()

    def test_backend_errors_never_expose_token_details(self):
        for operation, backend_method in (("load", "get_password"), ("save", "set_password"),
                                           ("forget", "get_password")):
            with self.subTest(operation=operation):
                backend = Mock()
                getattr(backend, backend_method).side_effect = RuntimeError("secret-must-not-appear")
                with self.assertRaises(TokenStorageError) as caught:
                    getattr(TokenStore(backend), operation)(*( ["test-token"] if operation == "save" else []))
                self.assertNotIn("secret-must-not-appear", str(caught.exception))

    def test_unavailable_native_backend_has_no_file_fallback(self):
        with patch("token_store.native_backend", side_effect=TokenStorageError):
            with self.assertRaises(TokenStorageError):
                TokenStore().save("test-token")


@patch("builtins.print")
class TokenFlowTests(unittest.TestCase):
    def test_saved_token_reused_without_prompt_or_display(self, output):
        store = Mock()
        store.load.return_value = "saved-test-token"
        with patch("main.read_token") as read, patch("main.prompt") as prompt:
            self.assertEqual(select_token(store), ("saved-test-token", False))
        read.assert_not_called()
        prompt.assert_not_called()
        self.assertNotIn("saved-test-token", str(output.call_args_list))

    def test_save_choice_is_deferred_until_authentication(self, output):
        store = Mock()
        store.load.return_value = None
        with patch("main.read_token", return_value="new-test-token"), patch("main.prompt", return_value=""):
            self.assertEqual(select_token(store), ("new-test-token", True))
        store.save.assert_not_called()

    def test_replacement_decline_preserves_previous_entry(self, output):
        store = Mock()
        with patch("main.read_token", return_value="new-test-token"), patch("main.prompt", return_value="no"):
            self.assertEqual(select_token(store, replace=True), ("new-test-token", False))
        store.load.assert_not_called()
        store.save.assert_not_called()
        store.forget.assert_not_called()

    def test_unavailable_vault_allows_memory_only_input(self, output):
        store = Mock()
        store.load.side_effect = TokenStorageError()
        with patch("main.read_token", return_value="temporary-token"), patch("main.prompt") as prompt:
            self.assertEqual(select_token(store), ("temporary-token", False))
        prompt.assert_not_called()
        store.save.assert_not_called()

    def test_failed_save_is_reported_without_exposing_token(self, output):
        store = Mock()
        store.save.side_effect = TokenStorageError("secret-must-not-appear")
        remember_token(store, "new-test-token")
        printed = str(output.call_args_list)
        self.assertIn("Could not save", printed)
        self.assertNotIn("secret-must-not-appear", printed)
        self.assertNotIn("new-test-token", printed)

    def test_forget_exits_without_connecting(self, output):
        store = Mock()
        store.forget.return_value = True
        with patch("sys.argv", ["main.py", "--forget-token"]), patch("main.TokenStore", return_value=store), \
             patch("main.run_session") as session, patch("main.select_token") as select:
            self.assertEqual(main(), 0)
        store.forget.assert_called_once()
        session.assert_not_called()
        select.assert_not_called()

    def test_title_only_never_accesses_vault(self, output):
        with patch("sys.argv", ["main.py", "--title-only"]), patch("main.TokenStore") as store:
            self.assertEqual(main(), 0)
        store.assert_not_called()
