"""Durable Copilot interview sessions and displayed turn records."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from backend.config import settings

DB_PATH = settings.db_path


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS copilot_sessions (
            session_id TEXT PRIMARY KEY, prep_id TEXT NOT NULL, user_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active', knowledge_version INTEGER NOT NULL DEFAULT 1,
            started_at TEXT NOT NULL, ended_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS copilot_turns (
            turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, prep_id TEXT NOT NULL,
            user_id TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL,
            text TEXT NOT NULL, answer TEXT DEFAULT '', route TEXT DEFAULT '',
            node_id TEXT, anchor_id TEXT, answer_id TEXT, score REAL,
            sources TEXT NOT NULL DEFAULT '[]', latency TEXT NOT NULL DEFAULT '{}',
            fallback_reason TEXT DEFAULT '', created_at TEXT NOT NULL,
            UNIQUE(session_id, seq, role)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_copilot_sessions_user_prep ON copilot_sessions(user_id, prep_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_copilot_turns_session_seq ON copilot_turns(session_id, seq)")
    conn.commit()
    return conn


def start_session(session_id: str, prep_id: str, user_id: str, knowledge_version: int) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO copilot_sessions "
        "(session_id, prep_id, user_id, status, knowledge_version, started_at, ended_at) "
        "VALUES (?, ?, ?, 'active', ?, ?, NULL)",
        (session_id, prep_id, user_id, knowledge_version, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def end_session(session_id: str, user_id: str) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE copilot_sessions SET status='ended', ended_at=? WHERE session_id=? AND user_id=?",
        (datetime.now().isoformat(), session_id, user_id),
    )
    conn.commit()
    conn.close()


def create_turn(
    turn_id: str, session_id: str, prep_id: str, user_id: str,
    seq: int, role: str, text: str,
) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO copilot_turns "
        "(turn_id, session_id, prep_id, user_id, seq, role, text, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (turn_id, session_id, prep_id, user_id, seq, role, text, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def complete_turn(turn_id: str, user_id: str, **values) -> None:
    allowed = {
        "answer", "route", "node_id", "anchor_id", "answer_id", "score",
        "sources", "latency", "fallback_reason",
    }
    fields = [key for key in values if key in allowed]
    if not fields:
        return
    encoded = {
        key: json.dumps(values[key], ensure_ascii=False) if key in {"sources", "latency"} else values[key]
        for key in fields
    }
    conn = _get_conn()
    conn.execute(
        f"UPDATE copilot_turns SET {', '.join(f'{key}=?' for key in fields)} "
        "WHERE turn_id=? AND user_id=?",
        (*[encoded[key] for key in fields], turn_id, user_id),
    )
    conn.commit()
    conn.close()


def list_sessions(user_id: str, prep_id: str | None = None) -> list[dict]:
    conn = _get_conn()
    sql = "SELECT * FROM copilot_sessions WHERE user_id=?"
    params: list[object] = [user_id]
    if prep_id:
        sql += " AND prep_id=?"
        params.append(prep_id)
    rows = conn.execute(sql + " ORDER BY started_at DESC", params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_session(session_id: str, user_id: str) -> dict | None:
    conn = _get_conn()
    session = conn.execute(
        "SELECT * FROM copilot_sessions WHERE session_id=? AND user_id=?",
        (session_id, user_id),
    ).fetchone()
    if not session:
        conn.close()
        return None
    turns = conn.execute(
        "SELECT * FROM copilot_turns WHERE session_id=? AND user_id=? ORDER BY seq, created_at",
        (session_id, user_id),
    ).fetchall()
    conn.close()
    result = dict(session)
    result["turns"] = []
    for row in turns:
        item = dict(row)
        item["sources"] = json.loads(item.get("sources") or "[]")
        item["latency"] = json.loads(item.get("latency") or "{}")
        result["turns"].append(item)
    return result


def delete_for_prep(prep_id: str, user_id: str, conn: sqlite3.Connection | None = None) -> None:
    owns_conn = conn is None
    conn = conn or _get_conn()
    conn.execute("DELETE FROM copilot_turns WHERE prep_id=? AND user_id=?", (prep_id, user_id))
    conn.execute("DELETE FROM copilot_sessions WHERE prep_id=? AND user_id=?", (prep_id, user_id))
    if owns_conn:
        conn.commit()
        conn.close()
