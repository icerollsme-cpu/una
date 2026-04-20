import sqlite3
import os
import uuid
import json
from datetime import datetime
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "orders.db"))


def _ensure_db_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def _conn():
    _ensure_db_dir()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                phone        TEXT PRIMARY KEY,
                name         TEXT,
                state        TEXT NOT NULL DEFAULT 'WELCOME',
                order_ref    TEXT,
                context      TEXT NOT NULL DEFAULT '{}',
                updated_at   TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                order_ref    TEXT PRIMARY KEY,
                phone        TEXT NOT NULL,
                name         TEXT,
                print_type   TEXT,
                paper_size   TEXT,
                pages        INTEGER,
                copies       INTEGER,
                binding      INTEGER DEFAULT 0,
                spiral       INTEGER DEFAULT 0,
                lamination   INTEGER DEFAULT 0,
                total        REAL,
                file_path    TEXT,
                status       TEXT NOT NULL DEFAULT 'pending_payment',
                payment_ref  TEXT,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );
        """)


def _now() -> str:
    return datetime.utcnow().isoformat()


# ── Session helpers ──────────────────────────────────────────────────────────

def get_session(phone: str) -> dict:
    with _conn() as con:
        row = con.execute("SELECT * FROM sessions WHERE phone=?", (phone,)).fetchone()
    if row:
        d = dict(row)
        d["context"] = json.loads(d["context"])
        return d
    return {"phone": phone, "name": "", "state": "WELCOME", "order_ref": None, "context": {}}


def save_session(phone: str, name: str, state: str, order_ref: str | None, context: dict):
    with _conn() as con:
        con.execute("""
            INSERT INTO sessions (phone, name, state, order_ref, context, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                name=excluded.name,
                state=excluded.state,
                order_ref=excluded.order_ref,
                context=excluded.context,
                updated_at=excluded.updated_at
        """, (phone, name, state, order_ref, json.dumps(context), _now()))


def reset_session(phone: str):
    with _conn() as con:
        con.execute("DELETE FROM sessions WHERE phone=?", (phone,))


# ── Order helpers ────────────────────────────────────────────────────────────

def new_order_ref() -> str:
    return "UNA-" + uuid.uuid4().hex[:6].upper()


def create_order(phone: str, name: str) -> str:
    ref = new_order_ref()
    with _conn() as con:
        con.execute("""
            INSERT INTO orders (order_ref, phone, name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (ref, phone, name, _now(), _now()))
    return ref


def update_order(order_ref: str, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [_now(), order_ref]
    with _conn() as con:
        con.execute(f"UPDATE orders SET {sets}, updated_at=? WHERE order_ref=?", vals)


def get_order(order_ref: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM orders WHERE order_ref=?", (order_ref,)).fetchone()
    return dict(row) if row else None


def list_pending_orders() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM orders WHERE status IN ('pending_payment','payment_received') ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]
