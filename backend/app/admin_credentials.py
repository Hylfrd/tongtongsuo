"""A persisted administrator hash; environment configuration is bootstrap-only."""

import sqlite3
from pathlib import Path

from app.admin_password import parse_password_hash


class AdminCredentials:
    def __init__(self, bootstrap: str, path: Path | None = None):
        self.path = path
        self._memory = (bootstrap, 1) if parse_password_hash(bootstrap) else ("", 0)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path, timeout=10) as connection:
                connection.execute("CREATE TABLE IF NOT EXISTS admin_credentials (id INTEGER PRIMARY KEY CHECK(id=1), password_hash TEXT NOT NULL, revision INTEGER NOT NULL)")
                if self._memory[1]:
                    connection.execute("INSERT OR IGNORE INTO admin_credentials (id,password_hash,revision) VALUES (1,?,1)", (bootstrap,))

    def snapshot(self) -> tuple[str, int]:
        if self.path is None:
            return self._memory
        with sqlite3.connect(self.path, timeout=10) as connection:
            row = connection.execute("SELECT password_hash,revision FROM admin_credentials WHERE id=1").fetchone()
        return tuple(row) if row else ("", 0)

    def replace(self, revision: int, encoded: str) -> bool:
        if parse_password_hash(encoded) is None:
            raise ValueError("Invalid administrator hash format")
        if self.path is None:
            if self._memory[1] != revision:
                return False
            self._memory = (encoded, revision + 1)
            return True
        with sqlite3.connect(self.path, timeout=10) as connection:
            cursor = connection.execute("UPDATE admin_credentials SET password_hash=?,revision=revision+1 WHERE id=1 AND revision=?", (encoded, revision))
            return cursor.rowcount == 1
