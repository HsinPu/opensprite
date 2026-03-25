"""Shared JSON-backed progress tracking for document consolidators."""

from __future__ import annotations

import json
from pathlib import Path

from .base import IncrementalStateStore


class JsonProgressStore(IncrementalStateStore):
    """Persist per-scope processed message offsets in a JSON file."""

    def __init__(self, state_file: Path):
        self.state_file = Path(state_file).expanduser()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> dict[str, int]:
        """Load the full state mapping."""
        if not self.state_file.exists():
            return {}

        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

        if not isinstance(data, dict):
            return {}

        state: dict[str, int] = {}
        for key, value in data.items():
            try:
                state[str(key)] = max(0, int(value))
            except (TypeError, ValueError):
                continue
        return state

    def save_state(self, state: dict[str, int]) -> None:
        """Persist the full state mapping."""
        safe_state = {str(key): max(0, int(value)) for key, value in state.items()}
        self.state_file.write_text(
            json.dumps(safe_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def get_processed_index(self, scope_id: str) -> int:
        """Return the last processed index for the given scope."""
        return self.load_state().get(scope_id, 0)

    def set_processed_index(self, scope_id: str, index: int) -> None:
        """Persist the last processed index for the given scope."""
        state = self.load_state()
        state[scope_id] = max(0, int(index))
        self.save_state(state)
