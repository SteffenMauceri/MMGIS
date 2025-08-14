import os
from typing import Any, Dict, Optional

from LLM.memory_store import MemoryStore


# Durable memory store
_db_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "memory", "agent_memory.sqlite3")
)
os.makedirs(os.path.dirname(_db_path), exist_ok=True)
_memory_store: MemoryStore = MemoryStore(_db_path)

# Current thread context shared across modules
_CURRENT_THREAD: Dict[str, Optional[str]] = {}


def get_memory_store() -> MemoryStore:
    return _memory_store


def set_current_thread(thread: Dict[str, Optional[str]]) -> None:
    global _CURRENT_THREAD
    _CURRENT_THREAD = dict(thread or {})


def get_current_thread() -> Dict[str, Optional[str]]:
    return _CURRENT_THREAD


