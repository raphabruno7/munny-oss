import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL CHECK (source = 'enable_banking'),
    aspsp_key       TEXT NOT NULL,
    stable_key      TEXT NOT NULL,
    provider_ref    TEXT,
    currency        TEXT NOT NULL,
    display_name    TEXT,
    balance_amount  REAL,
    balance_updated_at TEXT,
    last_synced_at  TEXT,
    UNIQUE (source, aspsp_key, stable_key)
);

CREATE TABLE IF NOT EXISTS bank_connections (
    id                  INTEGER PRIMARY KEY,
    source              TEXT NOT NULL,
    aspsp_key           TEXT NOT NULL,
    session_id          TEXT,
    valid_until         TEXT,
    unattended_sync_count_today TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (source, aspsp_key)
);

CREATE TABLE IF NOT EXISTS transactions (
    id                  INTEGER PRIMARY KEY,
    account_id          INTEGER NOT NULL REFERENCES accounts(id),
    source              TEXT NOT NULL,
    external_id         TEXT NOT NULL,
    booking_date        TEXT NOT NULL,
    amount              REAL NOT NULL,
    currency            TEXT NOT NULL,
    description         TEXT,
    category             TEXT,
    category_source      TEXT,
    category_rationale   TEXT,
    raw_json             TEXT,
    UNIQUE (source, external_id)
);
"""

# Migrações idempotentes para BDs criadas antes de uma coluna existir (SQLite não
# adiciona colunas via CREATE TABLE IF NOT EXISTS).
_MIGRATIONS = [
    "ALTER TABLE transactions ADD COLUMN category_rationale TEXT",
]


def init_db(db_path: str | None = None) -> None:
    path = db_path or DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # coluna já existe


@contextmanager
def get_conn(db_path: str | None = None):
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_account(conn, *, source: str, aspsp_key: str, stable_key: str, provider_ref: str | None,
                    currency: str, display_name: str | None,
                    balance_amount: float | None = None, balance_updated_at: str | None = None) -> int:
    conn.execute(
        """
        INSERT INTO accounts (source, aspsp_key, stable_key, provider_ref, currency, display_name,
                               balance_amount, balance_updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source, aspsp_key, stable_key) DO UPDATE SET
            provider_ref = excluded.provider_ref,
            currency = excluded.currency,
            display_name = excluded.display_name,
            balance_amount = COALESCE(excluded.balance_amount, accounts.balance_amount),
            balance_updated_at = COALESCE(excluded.balance_updated_at, accounts.balance_updated_at)
        """,
        (source, aspsp_key, stable_key, provider_ref, currency, display_name, balance_amount, balance_updated_at),
    )
    row = conn.execute(
        "SELECT id FROM accounts WHERE source = ? AND aspsp_key = ? AND stable_key = ?",
        (source, aspsp_key, stable_key),
    ).fetchone()
    return row["id"]


def update_watermark(conn, account_id: int, synced_at: str) -> None:
    conn.execute("UPDATE accounts SET last_synced_at = ? WHERE id = ?", (synced_at, account_id))


def update_balance(conn, account_id: int, amount: float, updated_at: str) -> None:
    conn.execute(
        "UPDATE accounts SET balance_amount = ?, balance_updated_at = ? WHERE id = ?",
        (amount, updated_at, account_id),
    )


def insert_transactions_idempotent(conn, account_id: int, source: str, transactions: list[dict]) -> int:
    """transactions: dicts com external_id, booking_date, amount, currency, description, raw.
    Devolve o número de linhas novas inseridas (duplicados são ignorados)."""
    inserted = 0
    for txn in transactions:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO transactions
                (account_id, source, external_id, booking_date, amount, currency, description, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id, source, txn["external_id"], txn["booking_date"], txn["amount"],
                txn["currency"], txn.get("description"), json.dumps(txn.get("raw", txn)),
            ),
        )
        inserted += cur.rowcount
    return inserted


def uncategorized_transactions(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM transactions WHERE category IS NULL").fetchall()


def set_category(conn, transaction_id: int, category: str, category_source: str,
                 rationale: str | None = None) -> None:
    conn.execute(
        "UPDATE transactions SET category = ?, category_source = ?, category_rationale = ? WHERE id = ?",
        (category, category_source, rationale, transaction_id),
    )


def reset_auto_categories(conn) -> int:
    """Limpa a categoria de tudo o que não foi editado à mão. Devolve quantas linhas."""
    cur = conn.execute(
        "UPDATE transactions SET category = NULL, category_source = NULL, category_rationale = NULL "
        "WHERE category_source IS NULL OR category_source != 'manual'"
    )
    return cur.rowcount


def list_accounts(conn, source: str | None = None, aspsp_key: str | None = None) -> list[sqlite3.Row]:
    query = "SELECT * FROM accounts WHERE 1=1"
    params: list = []
    if source:
        query += " AND source = ?"
        params.append(source)
    if aspsp_key:
        query += " AND aspsp_key = ?"
        params.append(aspsp_key)
    return conn.execute(query, params).fetchall()


def query_transactions(conn, date_from: str, date_to: str, source: str | None = None,
                        category: str | None = None, aspsp_key: str | None = None) -> list[sqlite3.Row]:
    query = (
        "SELECT t.*, a.aspsp_key AS aspsp_key FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id "
        "WHERE t.booking_date >= ? AND t.booking_date <= ?"
    )
    params: list = [date_from, date_to]
    if source:
        query += " AND t.source = ?"
        params.append(source)
    if category:
        query += " AND t.category = ?"
        params.append(category)
    if aspsp_key:
        query += " AND a.aspsp_key = ?"
        params.append(aspsp_key)
    query += " ORDER BY t.booking_date"
    return conn.execute(query, params).fetchall()


def upsert_bank_connection(conn, *, source: str, aspsp_key: str, session_id: str | None,
                            valid_until: str | None, status: str = "active") -> None:
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO bank_connections (source, aspsp_key, session_id, valid_until, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source, aspsp_key) DO UPDATE SET
            session_id = excluded.session_id,
            valid_until = excluded.valid_until,
            status = excluded.status,
            updated_at = excluded.updated_at
        """,
        (source, aspsp_key, session_id, valid_until, status, now, now),
    )


def get_bank_connection(conn, source: str, aspsp_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM bank_connections WHERE source = ? AND aspsp_key = ?", (source, aspsp_key)
    ).fetchone()


def list_bank_connections(conn, source: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM bank_connections WHERE source = ?", (source,)).fetchall()


def query_transactions_by_aspsp(conn, date_from: str, date_to: str, aspsp_key: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT t.* FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.booking_date >= ? AND t.booking_date <= ? AND a.aspsp_key = ?
        ORDER BY t.booking_date
        """,
        (date_from, date_to, aspsp_key),
    ).fetchall()


def check_and_increment_unattended_quota(conn, source: str, aspsp_key: str, daily_limit: int) -> bool:
    """Guarda-costas contra o limite PSD2 de pedidos não-assistidos/dia. Devolve False
    (e não incrementa) se o limite já foi atingido hoje; True caso contrário."""
    today = datetime.now(timezone.utc).date().isoformat()
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO bank_connections (source, aspsp_key, status, created_at, updated_at)
        VALUES (?, ?, 'active', ?, ?)
        ON CONFLICT (source, aspsp_key) DO NOTHING
        """,
        (source, aspsp_key, now, now),
    )
    row = conn.execute(
        "SELECT unattended_sync_count_today FROM bank_connections WHERE source = ? AND aspsp_key = ?",
        (source, aspsp_key),
    ).fetchone()
    raw = row["unattended_sync_count_today"] if row else None
    count = 0
    if raw and raw.startswith(today):
        count = int(raw.split(":", 1)[1])
    if count >= daily_limit:
        return False
    conn.execute(
        "UPDATE bank_connections SET unattended_sync_count_today = ? WHERE source = ? AND aspsp_key = ?",
        (f"{today}:{count + 1}", source, aspsp_key),
    )
    return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
