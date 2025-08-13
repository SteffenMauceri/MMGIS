import os
import sqlite3
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple


class MemoryStore:
    """
    Lightweight durable store for per-thread conversational state, facts, brief summaries,
    and last observed UI state. Uses a single SQLite file; safe for single-process access.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            c = conn.cursor()
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS threads (
                  id INTEGER PRIMARY KEY,
                  user_id TEXT,
                  session_id TEXT,
                  mission_id TEXT,
                  UNIQUE(user_id, session_id, mission_id)
                );
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                  id INTEGER PRIMARY KEY,
                  thread_id INTEGER NOT NULL,
                  ts INTEGER NOT NULL,
                  role TEXT NOT NULL,
                  content TEXT NOT NULL,
                  run_id TEXT,
                  FOREIGN KEY(thread_id) REFERENCES threads(id)
                );
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                  thread_id INTEGER NOT NULL,
                  key TEXT NOT NULL,
                  value TEXT,
                  PRIMARY KEY(thread_id, key),
                  FOREIGN KEY(thread_id) REFERENCES threads(id)
                );
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS briefs (
                  thread_id INTEGER PRIMARY KEY,
                  summary TEXT,
                  updated_ts INTEGER,
                  FOREIGN KEY(thread_id) REFERENCES threads(id)
                );
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS ui_state (
                  thread_id INTEGER PRIMARY KEY,
                  state_json TEXT,
                  updated_ts INTEGER,
                  FOREIGN KEY(thread_id) REFERENCES threads(id)
                );
                """
            )
            conn.commit()

    def _get_or_create_thread_id(self, user_id: str, session_id: str, mission_id: Optional[str]) -> int:
        with self._connect() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT id FROM threads WHERE user_id=? AND session_id=? AND mission_id IS ?",
                (user_id, session_id, mission_id),
            )
            row = c.fetchone()
            if row:
                return int(row[0])
            c.execute(
                "INSERT INTO threads(user_id, session_id, mission_id) VALUES (?, ?, ?)",
                (user_id, session_id, mission_id),
            )
            conn.commit()
            return int(c.lastrowid)

    def ensure_thread(self, thread: Dict[str, Optional[str]]) -> int:
        return self._get_or_create_thread_id(
            thread.get("user_id") or "anon",
            thread.get("session_id") or "default",
            thread.get("mission_id"),
        )

    def append_messages(self, thread: Dict[str, Optional[str]], messages: List[Tuple[str, str]], run_id: Optional[str]) -> None:
        thread_id = self.ensure_thread(thread)
        now = int(time.time() * 1000)
        with self._connect() as conn:
            c = conn.cursor()
            for role, content in messages:
                c.execute(
                    "INSERT INTO messages(thread_id, ts, role, content, run_id) VALUES (?, ?, ?, ?, ?)",
                    (thread_id, now, role, content, run_id),
                )
            conn.commit()

    def load_recent_messages(self, thread: Dict[str, Optional[str]], limit: int = 50) -> List[Tuple[str, str]]:
        thread_id = self.ensure_thread(thread)
        with self._connect() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT role, content FROM messages WHERE thread_id=? ORDER BY id DESC LIMIT ?",
                (thread_id, limit),
            )
            rows = c.fetchall()
        rows.reverse()
        return [(str(r[0]), str(r[1])) for r in rows]

    def get_facts(self, thread: Dict[str, Optional[str]]) -> Dict[str, Any]:
        thread_id = self.ensure_thread(thread)
        with self._connect() as conn:
            c = conn.cursor()
            c.execute("SELECT key, value FROM facts WHERE thread_id=?", (thread_id,))
            rows = c.fetchall()
        out: Dict[str, Any] = {}
        for k, v in rows:
            try:
                out[str(k)] = json.loads(v) if v is not None else None
            except Exception:
                out[str(k)] = v
        return out

    def upsert_fact(self, thread: Dict[str, Optional[str]], key: str, value: Any) -> None:
        thread_id = self.ensure_thread(thread)
        with self._connect() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO facts(thread_id, key, value) VALUES (?, ?, ?)\n                 ON CONFLICT(thread_id, key) DO UPDATE SET value=excluded.value",
                (thread_id, key, json.dumps(value)),
            )
            conn.commit()

    def get_brief(self, thread: Dict[str, Optional[str]]) -> Optional[str]:
        thread_id = self.ensure_thread(thread)
        with self._connect() as conn:
            c = conn.cursor()
            c.execute("SELECT summary FROM briefs WHERE thread_id=?", (thread_id,))
            row = c.fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def set_brief(self, thread: Dict[str, Optional[str]], summary: str) -> None:
        thread_id = self.ensure_thread(thread)
        now = int(time.time() * 1000)
        with self._connect() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO briefs(thread_id, summary, updated_ts) VALUES (?, ?, ?)\n                 ON CONFLICT(thread_id) DO UPDATE SET summary=excluded.summary, updated_ts=excluded.updated_ts",
                (thread_id, summary, now),
            )
            conn.commit()

    def get_last_ui_state(self, thread: Dict[str, Optional[str]]) -> Optional[Dict[str, Any]]:
        thread_id = self.ensure_thread(thread)
        with self._connect() as conn:
            c = conn.cursor()
            c.execute("SELECT state_json FROM ui_state WHERE thread_id=?", (thread_id,))
            row = c.fetchone()
        if not row or row[0] is None:
            return None
        try:
            return json.loads(str(row[0]))
        except Exception:
            return None

    def set_last_ui_state(self, thread: Dict[str, Optional[str]], state: Dict[str, Any]) -> None:
        thread_id = self.ensure_thread(thread)
        now = int(time.time() * 1000)
        with self._connect() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO ui_state(thread_id, state_json, updated_ts) VALUES (?, ?, ?)\n                 ON CONFLICT(thread_id) DO UPDATE SET state_json=excluded.state_json, updated_ts=excluded.updated_ts",
                (thread_id, json.dumps(state), now),
            )
            conn.commit()

    @staticmethod
    def new_run_id() -> str:
        return str(uuid.uuid4())


