"""Small durable inventory database; no production fixtures or school secrets."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS cabinets (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    group_name TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS gateways (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    provider_gateway_id TEXT UNIQUE,
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS locks (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    provider_lock_id TEXT UNIQUE,
    cabinet_id INTEGER REFERENCES cabinets(id) ON DELETE RESTRICT,
    gateway_id INTEGER REFERENCES gateways(id) ON DELETE RESTRICT,
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tools (
    id INTEGER PRIMARY KEY,
    cabinet_id INTEGER NOT NULL REFERENCES cabinets(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    quantity INTEGER NOT NULL CHECK(quantity >= 0),
    icon TEXT NOT NULL DEFAULT 'toolbox',
    icon_data TEXT,
    archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1))
);
CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY,
    user_name TEXT NOT NULL,
    school_code TEXT NOT NULL,
    cabinet_id INTEGER NOT NULL REFERENCES cabinets(id) ON DELETE RESTRICT,
    cabinet_name TEXT NOT NULL,
    cabinet_code TEXT NOT NULL,
    tool_id INTEGER NOT NULL REFERENCES tools(id) ON DELETE RESTRICT,
    tool_name TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    borrowed_at TEXT NOT NULL,
    returned_at TEXT
);
CREATE INDEX IF NOT EXISTS loans_active_tool ON loans(tool_id, returned_at);
CREATE INDEX IF NOT EXISTS loans_cabinet ON loans(cabinet_id);
"""


class Database:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or os.environ.get("DATABASE_PATH", "runtime/tongtongsuo.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, *, write=False):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
