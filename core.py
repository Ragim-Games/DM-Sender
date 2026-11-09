"""Message validation and a durable, token-free delivery journal."""

import hashlib
import sqlite3
from pathlib import Path


def snowflake(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**64:
        raise ValueError("Enter a valid numeric Guild ID.")
    return int(value)


def render_message(template: str, username: str, display_name: str, server: str) -> str:
    # Substitute in one pass so user-controlled names cannot introduce placeholders.
    import re

    values = {"username": username, "display_name": display_name, "server": server}
    message = re.sub(r"\{(username|display_name|server)\}", lambda m: values[m[1]], template)
    if not message.strip():
        raise ValueError("The message cannot be empty.")
    if len(message.encode("utf-16-le")) // 2 > 2000:
        raise ValueError("The personalized message exceeds the 2,000-character limit.")
    return message


def campaign_key(account_id: int, guild_id: int, template: str) -> str:
    return hashlib.sha256(f"{account_id}:{guild_id}:{template}".encode()).hexdigest()


class Journal:
    """Reserve before sending; never automatically retry an uncertain delivery."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS deliveries ("
            "campaign TEXT NOT NULL, user_id TEXT NOT NULL, status TEXT NOT NULL, "
            "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY(campaign, user_id))"
        )
        self.db.commit()

    def seen(self, campaign: str, user_id: int) -> bool:
        return self.db.execute(
            "SELECT 1 FROM deliveries WHERE campaign=? AND user_id=?",
            (campaign, str(user_id)),
        ).fetchone() is not None

    def reserve(self, campaign: str, user_id: int) -> bool:
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO deliveries(campaign,user_id,status) VALUES(?,?, 'pending')",
            (campaign, str(user_id)),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def finish(self, campaign: str, user_id: int, status: str) -> None:
        self.db.execute(
            "UPDATE deliveries SET status=?,updated_at=CURRENT_TIMESTAMP "
            "WHERE campaign=? AND user_id=?", (status, campaign, str(user_id)),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()
