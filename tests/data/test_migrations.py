from trailhead.data.db import EXPECTED_TABLES, LATEST_VERSION, connect, migrate, schema_version, table_names


def test_migrate_from_empty(tmp_path):
    conn = connect(tmp_path / "a.db", migrate_db=False)
    assert schema_version(conn) == 0
    assert migrate(conn) == LATEST_VERSION
    assert EXPECTED_TABLES <= table_names(conn)
    assert "dividend_reviews" in table_names(conn)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    # idempotent
    assert migrate(conn) == LATEST_VERSION
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1


def test_005_backfills_stop_outs_from_note(tmp_path):
    import sqlite3

    from trailhead.data.db import MIGRATIONS_DIR

    conn = connect(tmp_path / "b.db", migrate_db=False)
    for f in sorted(MIGRATIONS_DIR.glob("00[1-4]_*.sql")):
        sql = f.read_text()
        if not f.name.startswith("001"):
            sql += f"\nUPDATE schema_version SET version = {int(f.name[:3])};"
        conn.executescript(f"BEGIN;\n{sql}\nCOMMIT;")
    conn.execute("INSERT INTO portfolios (id, name, max_amount, created_at) VALUES (1, 'P', 1, 'x')")
    conn.execute("INSERT INTO trades (id, portfolio_id, symbol, side, trade_date, shares, price, initial_stop,"
                 " created_at, updated_at) VALUES (1, 1, 'A', 'BUY', '2026-01-01', 10, 10, 9, 'x', 'x')")
    conn.execute("INSERT INTO trades (id, portfolio_id, symbol, side, trade_date, shares, price, lot_id, notes,"
                 " created_at, updated_at) VALUES (2, 1, 'A', 'SELL', '2026-01-02', 10, 9, 1, 'Stopped out', 'x', 'x')")
    conn.commit()
    migrate(conn)
    row = conn.execute("SELECT exit_reason, notes FROM trades WHERE id = 2").fetchone()
    assert tuple(row) == ("STOP", None)
    assert isinstance(conn, sqlite3.Connection)
