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
                used_at      TEXT NOT NULL,
                synced       INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS download_stats (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                access_key    TEXT NOT NULL,
                user_id       INTEGER NOT NULL DEFAULT 0,
                full_name     TEXT NOT NULL,
                downloaded_at TEXT NOT NULL
            );

            -- Лог уникальных скачиваний, уже записанных в "Отчет по ФИО"
            CREATE TABLE IF NOT EXISTS fio_report_log (
                user_id     INTEGER NOT NULL,
                full_name   TEXT NOT NULL,
                synced_date TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (user_id, full_name)
            );
        """)
        # Миграция: добавить колонки если их нет
        for table in ("filter_stats", "download_stats"):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
        try:
            conn.execute("ALTER TABLE filter_stats ADD COLUMN synced INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE fio_report_log ADD COLUMN synced_date TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass


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


def get_unsynced_filter_stats() -> list[sqlite3.Row]:
    """Возвращает записи filter_stats ещё не отправленные в Google Sheets."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM filter_stats WHERE synced = 0 ORDER BY used_at"
        ).fetchall()


def mark_filter_stats_synced(ids: list[int]) -> None:
    """Помечает записи как отправленные в Google Sheets."""
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    with _connect() as conn:
        conn.execute(
            f"UPDATE filter_stats SET synced = 1 WHERE id IN ({placeholders})", ids
        )


def get_fio_to_sync() -> list[sqlite3.Row]:
    """Возвращает записи, которые нужно записать или обновить в 'Отчет по ФИО':
    - новые: (user_id, full_name) ещё нет в логе
    - обновлённые: последняя загрузка новее чем synced_date в логе
    Поле action = 'new' | 'update'.
    """
    with _connect() as conn:
        return conn.execute("""
            SELECT ds.user_id,
                   ds.full_name,
                   ds.access_key,
                   MAX(ds.downloaded_at) AS latest_date,
                   CASE WHEN frl.user_id IS NULL THEN 'new' ELSE 'update' END AS action
            FROM download_stats ds
            LEFT JOIN fio_report_log frl
                   ON ds.user_id   = frl.user_id
                  AND ds.full_name = frl.full_name
            GROUP BY ds.user_id, ds.full_name, ds.access_key
            HAVING frl.user_id IS NULL
                OR MAX(ds.downloaded_at) > frl.synced_date
            ORDER BY latest_date
        """).fetchall()


def upsert_fio_report_log(entries: list[tuple[int, str, str]]) -> None:
    """Сохраняет дату синхронизации. entries: [(user_id, full_name, synced_date)]"""
    if not entries:
        return
    with _connect() as conn:
        conn.executemany(
            """INSERT INTO fio_report_log (user_id, full_name, synced_date) VALUES (?, ?, ?)
               ON CONFLICT(user_id, full_name) DO UPDATE SET synced_date = excluded.synced_date""",
            entries,
        )
