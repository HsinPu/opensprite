"""Optional sqlite-vec integration helpers."""

from __future__ import annotations

import importlib
import sqlite3
from types import ModuleType


def import_sqlite_vec() -> ModuleType | None:
    """Import the optional sqlite_vec Python package if it exists."""
    try:
        return importlib.import_module("sqlite_vec")
    except Exception:
        return None


def load_sqlite_vec_extension(conn: sqlite3.Connection) -> tuple[bool, str | None]:
    """Load sqlite-vec SQL functions into an existing SQLite connection."""
    module = import_sqlite_vec()
    if module is None:
        return False, "sqlite_vec package is not installed"

    try:
        conn.enable_load_extension(True)
        module.load(conn)
        conn.enable_load_extension(False)
        return True, None
    except Exception as exc:
        try:
            conn.enable_load_extension(False)
        except Exception:
            pass
        return False, str(exc)
