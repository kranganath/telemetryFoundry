"""Saved mapping drafts (SQLite, stdlib only)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "foundry.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS projects (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             name TEXT NOT NULL,
             standard_id TEXT NOT NULL,
             entity_id TEXT NOT NULL,
             source_json TEXT NOT NULL,
             mappings_json TEXT NOT NULL,
             created_at TEXT NOT NULL,
             updated_at TEXT NOT NULL
           )"""
    )
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "standard_id": row["standard_id"],
        "entity_id": row["entity_id"],
        "source": json.loads(row["source_json"]),
        "mappings": json.loads(row["mappings_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_projects() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [_row_to_dict(r) for r in rows]


def create_project(name: str, standard_id: str, entity_id: str,
                   source: dict, mappings: list) -> dict:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, standard_id, entity_id, source_json,"
            " mappings_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, standard_id, entity_id, json.dumps(source),
             json.dumps(mappings), now, now),
        )
        row = conn.execute("SELECT * FROM projects WHERE id = ?",
                           (cur.lastrowid,)).fetchone()
        return _row_to_dict(row)


def get_project(project_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?",
                           (project_id,)).fetchone()
        return _row_to_dict(row) if row else None


def update_project(project_id: int, name: str, standard_id: str,
                   entity_id: str, source: dict, mappings: list) -> Optional[dict]:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE projects SET name = ?, standard_id = ?, entity_id = ?,"
            " source_json = ?, mappings_json = ?, updated_at = ? WHERE id = ?",
            (name, standard_id, entity_id, json.dumps(source),
             json.dumps(mappings), _now(), project_id),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM projects WHERE id = ?",
                           (project_id,)).fetchone()
        return _row_to_dict(row)


def delete_project(project_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM projects WHERE id = ?",
                           (project_id,))
        return cur.rowcount > 0
