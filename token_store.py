"""Store one account token in a native OS credential vault, never a text file."""

import sys

SERVICE = "Ragim-Games.DM-Sender"
ACCOUNT = "default-account"


class TokenStorageError(Exception):
    """A credential operation failed; backend details must not expose secrets."""


def native_backend():
    # Select native backends explicitly: do not load a configured plaintext backend.
    try:
        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring
            backend = WinVaultKeyring()
        elif sys.platform == "darwin":
            from keyring.backends.macOS import Keyring
            backend = Keyring()
        elif sys.platform.startswith("linux"):
            from keyring.backends.SecretService import Keyring
            backend = Keyring()
        else:
            raise TokenStorageError()
        if backend.priority <= 0:
            raise TokenStorageError()
        return backend
    except Exception:
        raise TokenStorageError("Native credential storage is unavailable.") from None


class TokenStore:
    def __init__(self, backend=None):
        self._backend = backend

    @property
    def backend(self):
        if self._backend is None:
            self._backend = native_backend()
        return self._backend

    def load(self):
        try:
            return self.backend.get_password(SERVICE, ACCOUNT)
        except Exception:
            raise TokenStorageError("Could not read the saved token.") from None

    def save(self, token: str) -> None:
        try:
            self.backend.set_password(SERVICE, ACCOUNT, token)
        except Exception:
            raise TokenStorageError("Could not save the token.") from None

    def forget(self) -> bool:
        try:
            if self.backend.get_password(SERVICE, ACCOUNT) is None:
                return False
            self.backend.delete_password(SERVICE, ACCOUNT)
            return True
        except Exception:
            raise TokenStorageError("Could not delete the saved token.") from None
