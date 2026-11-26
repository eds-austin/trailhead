"""SQLite connection, PRAGMAs and migrations."""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import platformdirs

APP_DIR_NAME = "Trailhead"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{3})_.*\.sql$")

sqlite3.register_adapter(Decimal, str)
sqlite3.register_adapter(date, lambda d: d.isoformat())
sqlite3.register_adapter(datetime, lambda d: d.isoformat(timespec="seconds"))


def default_data_dir() -> Path:
    return Path(platformdirs.user_data_dir(APP_DIR_NAME, appauthor=False))


def default_db_path() -> Path:
    return default_data_dir() / "portfolio.db"


def migrations() -> list[tuple[int, Path]]:
    out = []
    for f in MIGRATIONS_DIR.glob("*.sql"):
        m = _MIGRATION_RE.match(f.name)
        if m:
            out.append((int(m.group(1)), f))
    return sorted(out)


LATEST_VERSION = migrations()[-1][0]


def connect(path: Path | str, *, migrate_db: bool = True) -> sqlite3.Connection:
    """Open (and by default migrate) the database. Use ':memory:' for tests."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    if migrate_db:
        migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()
    if row is None:
        return 0
    v = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    return int(v or 0)


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations in order, each in its own transaction. Returns the new version."""
    current = schema_version(conn)
    if current > LATEST_VERSION:
        raise RuntimeError(f"Database schema v{current} is newer than this app (v{LATEST_VERSION})")
    for version, path in migrations():
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        if version > 1:
            sql += f"\nUPDATE schema_version SET version = {version};"
        conn.executescript(f"BEGIN;\n{sql}\nCOMMIT;")
        current = version
    return current


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


EXPECTED_TABLES = {"schema_version", "portfolios", "trades", "stop_changes", "cash_events", "instruments",
                   "quotes", "price_history", "dividends", "alerts_log", "settings"}
