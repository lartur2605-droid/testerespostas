"""SQLite simples. Uma conexão por requisição/thread (check_same_thread=False não é necessário)."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS municipalities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ig_account_id TEXT NOT NULL UNIQUE,
    ig_username TEXT NOT NULL DEFAULT '',
    access_token_enc TEXT NOT NULL DEFAULT '',
    token_refreshed_at TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    max_replies_per_hour INTEGER NOT NULL DEFAULT 30,
    ai_enabled INTEGER NOT NULL DEFAULT 0,
    extra_sensitive_keywords TEXT NOT NULL DEFAULT '',
    contact_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    municipality_id INTEGER NOT NULL REFERENCES municipalities(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    keywords TEXT NOT NULL,          -- separadas por vírgula
    responses TEXT NOT NULL,         -- uma variação por linha
    priority INTEGER NOT NULL DEFAULT 100,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    municipality_id INTEGER NOT NULL REFERENCES municipalities(id) ON DELETE CASCADE,
    comment_id TEXT NOT NULL UNIQUE,
    media_id TEXT,
    from_id TEXT,
    from_username TEXT,
    text TEXT,
    status TEXT NOT NULL,            -- respondido | fila | ignorado | limite | erro
    reason TEXT NOT NULL DEFAULT '',
    rule_id INTEGER,
    reply_text TEXT,
    handled_by TEXT,
    received_at TEXT NOT NULL DEFAULT (datetime('now')),
    replied_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_comments_mun_status ON comments(municipality_id, status);
CREATE INDEX IF NOT EXISTS idx_comments_replied ON comments(municipality_id, replied_at);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'prefeitura',     -- admin | prefeitura
    municipality_id INTEGER REFERENCES municipalities(id) ON DELETE CASCADE
);
"""


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
