"""Small durable paper ledger; no exchange credentials or order endpoints."""
from __future__ import annotations

import json
import sqlite3
import time

from config import DB, INITIAL_EQUITY, MARGIN, MAX_POSITIONS


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=20000")
    return db


def init():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS positions (
          id TEXT PRIMARY KEY, symbol TEXT NOT NULL, strategy TEXT NOT NULL,
          pattern_id TEXT NOT NULL, timeframe TEXT NOT NULL, variant TEXT NOT NULL,
          entry_time INTEGER NOT NULL, entry_price REAL NOT NULL,
          stop REAL NOT NULL, target REAL NOT NULL, expiry_time INTEGER NOT NULL,
          quantity REAL NOT NULL, margin REAL NOT NULL, score REAL NOT NULL,
          status TEXT NOT NULL, exit_time INTEGER, exit_price REAL, exit_reason TEXT,
          funding_usdt REAL NOT NULL DEFAULT 0, net_pnl_usdt REAL,
          mark_price REAL, mark_time INTEGER, last_checked INTEGER,
          created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
        CREATE TABLE IF NOT EXISTS equity_samples (
          timestamp INTEGER PRIMARY KEY, equity REAL NOT NULL, available_cash REAL NOT NULL,
          realized_pnl REAL NOT NULL, unrealized_pnl REAL NOT NULL,
          active_count INTEGER NOT NULL, min_equity REAL NOT NULL, max_drawdown REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL,
          kind TEXT NOT NULL, message TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        db.execute("INSERT OR IGNORE INTO state VALUES ('min_equity', ?)", (str(INITIAL_EQUITY),))
        db.execute("INSERT OR IGNORE INTO state VALUES ('peak_equity', ?)", (str(INITIAL_EQUITY),))
        db.execute("INSERT OR IGNORE INTO state VALUES ('max_drawdown', '0')")


def event(kind: str, message: str, payload: dict | None = None):
    with connect() as db:
        db.execute("INSERT INTO events(timestamp,kind,message,payload) VALUES (?,?,?,?)",
                   (int(time.time() * 1000), kind, message, json.dumps(payload or {}, ensure_ascii=False)))


def positions(status: str | None = None):
    with connect() as db:
        if status:
            return [dict(x) for x in db.execute("SELECT * FROM positions WHERE status=? ORDER BY entry_time DESC", (status,))]
        return [dict(x) for x in db.execute("SELECT * FROM positions ORDER BY entry_time DESC")]


def insert_position(row: dict):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        active = db.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0]
        if active >= MAX_POSITIONS:
            return False
        same_symbol = db.execute("SELECT COUNT(*) FROM positions WHERE status='open' AND symbol=?", (row['symbol'],)).fetchone()[0]
        if same_symbol:
            return False
        realized = db.execute("SELECT COALESCE(SUM(net_pnl_usdt),0) FROM positions WHERE status='closed'").fetchone()[0]
        if INITIAL_EQUITY + realized - active * MARGIN < MARGIN - 1e-9:
            return False
        keys = list(row)
        marks = ",".join("?" for _ in keys)
        before = db.total_changes
        db.execute(f"INSERT OR IGNORE INTO positions ({','.join(keys)}) VALUES ({marks})",
                   [row[k] for k in keys])
        return db.total_changes > before


def update_mark(position_id: str, price: float, minute: int, funding: float):
    with connect() as db:
        db.execute("UPDATE positions SET mark_price=?,mark_time=?,last_checked=?,funding_usdt=? WHERE id=? AND status='open'",
                   (price, minute, minute, funding, position_id))


def close_position(position_id: str, exit_ms: int, price: float, reason: str,
                   funding: float, pnl: float):
    with connect() as db:
        db.execute("UPDATE positions SET status='closed',exit_time=?,exit_price=?,exit_reason=?,funding_usdt=?,net_pnl_usdt=?,mark_price=?,mark_time=?,last_checked=? WHERE id=? AND status='open'",
                   (exit_ms, price, reason, funding, pnl, price, exit_ms, exit_ms, position_id))


def snapshot(now_ms: int, worst_equity: float | None = None):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        open_rows = [dict(x) for x in db.execute("SELECT * FROM positions WHERE status='open'")]
        realized = float(db.execute("SELECT COALESCE(SUM(net_pnl_usdt),0) FROM positions WHERE status='closed'").fetchone()[0])
        unrealized = 0.0
        for p in open_rows:
            if p["mark_price"] is not None:
                # Includes estimated entry + exit fees for open positions.
                unrealized += p["quantity"] * (p["entry_price"] - p["mark_price"])
                unrealized -= .001 * p["quantity"] * (p["entry_price"] + p["mark_price"])
                unrealized += p["funding_usdt"]
        equity = INITIAL_EQUITY + realized + unrealized
        available = INITIAL_EQUITY + realized - len(open_rows) * MARGIN
        state = dict(db.execute("SELECT key,value FROM state").fetchall())
        prior_min = float(state.get("min_equity", INITIAL_EQUITY))
        prior_peak = float(state.get("peak_equity", INITIAL_EQUITY))
        prior_drawdown = float(state.get("max_drawdown", 0))
        observed_low = min(equity, worst_equity if worst_equity is not None else equity)
        minimum = min(prior_min, observed_low)
        peak = max(prior_peak, equity)
        drawdown = max(prior_drawdown, prior_peak - observed_low, peak - equity)
        for key, value in (("min_equity", minimum), ("peak_equity", peak), ("max_drawdown", drawdown)):
            db.execute("INSERT OR REPLACE INTO state(key,value) VALUES (?,?)", (key, str(value)))
        db.execute("INSERT OR REPLACE INTO equity_samples VALUES (?,?,?,?,?,?,?,?)",
                   (now_ms, equity, available, realized, unrealized, len(open_rows), minimum, drawdown))
        return {"timestamp": now_ms, "initial_equity": INITIAL_EQUITY, "equity": equity,
                "available_cash": available, "reserved_margin": len(open_rows) * MARGIN,
                "realized_pnl": realized, "unrealized_pnl": unrealized,
                "net_pnl": realized + unrealized, "active_count": len(open_rows),
                "min_equity": minimum, "peak_equity": peak, "max_drawdown": drawdown}


def recent_samples(limit: int = 3000):
    with connect() as db:
        rows = db.execute("SELECT * FROM equity_samples ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [dict(x) for x in rows][::-1]


def recent_events(limit: int = 80):
    with connect() as db:
        return [dict(x) for x in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]
