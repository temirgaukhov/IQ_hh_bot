import sqlite3
from datetime import datetime
from config import DB_FILE


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                access_key TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS filter_stats (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                access_key   TEXT NOT NULL,
                user_id      INTEGER NOT NULL DEFAULT 0,
                filter_field TEXT NOT NULL,
                filter_value TEXT NOT NULL,
                used_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS download_stats (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                access_key    TEXT NOT NULL,
                user_id       INTEGER NOT NULL DEFAULT 0,
                full_name     TEXT NOT NULL,
                downloaded_at TEXT NOT NULL
            );
        """)
        # Миграция: добавить user_id в существующие таблицы если его нет
        for table in ("filter_stats", "download_stats"):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # колонка уже существует


def get_user(user_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()


def save_user(user_id: int, access_key: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO users (user_id, access_key, created_at) VALUES (?, ?, ?)",
            (user_id, access_key, datetime.now().isoformat()),
        )


def log_filter_usage(access_key: str, user_id: int, filter_field: str, filter_value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO filter_stats (access_key, user_id, filter_field, filter_value, used_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (access_key, user_id, filter_field, filter_value, datetime.now().isoformat()),
        )


def log_download(access_key: str, user_id: int, full_name: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO download_stats (access_key, user_id, full_name, downloaded_at) "
            "VALUES (?, ?, ?, ?)",
            (access_key, user_id, full_name, datetime.now().isoformat()),
        )


def get_filter_stats() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute("""
            SELECT access_key, user_id, filter_field, filter_value, COUNT(*) AS cnt
            FROM filter_stats
            GROUP BY access_key, user_id, filter_field, filter_value
            ORDER BY access_key, user_id, cnt DESC
        """).fetchall()


def get_download_stats() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute("""
            SELECT full_name, COUNT(*) AS cnt, COUNT(DISTINCT user_id) AS unique_users
            FROM download_stats
            GROUP BY full_name
            ORDER BY cnt DESC
        """).fetchall()
