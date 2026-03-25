"""Storage helpers for the global USER.md profile."""

from __future__ import annotations

import json
from pathlib import Path


AUTO_PROFILE_HEADER = "## Auto-managed Profile"
START_MARKER = "<!-- OPENSPRITE:USER_PROFILE:START -->"
END_MARKER = "<!-- OPENSPRITE:USER_PROFILE:END -->"
DEFAULT_MANAGED_CONTENT = "- No learned user profile details yet."


class UserProfileStore:
    """Persist the global USER.md profile and its consolidation state."""

    def __init__(self, user_profile_file: Path, state_file: Path):
        self.user_profile_file = Path(user_profile_file).expanduser()
        self.state_file = Path(state_file).expanduser()
        self.user_profile_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_user_profile_file()

    def _ensure_user_profile_file(self) -> None:
        """Ensure USER.md exists and contains the managed block markers."""
        if self.user_profile_file.exists():
            content = self.user_profile_file.read_text(encoding="utf-8")
        else:
            content = "# User Profile\n\n"

        normalized = self._ensure_managed_block(content)
        if normalized != content or not self.user_profile_file.exists():
            self.user_profile_file.write_text(normalized, encoding="utf-8")

    @staticmethod
    def _render_managed_section(content: str) -> str:
        managed = content.strip() or DEFAULT_MANAGED_CONTENT
        return (
            f"{AUTO_PROFILE_HEADER}\n\n"
            "This section is maintained by OpenSprite. Review it and edit the manual sections below if needed.\n\n"
            f"{START_MARKER}\n"
            f"{managed}\n"
            f"{END_MARKER}\n"
        )

    @classmethod
    def _ensure_managed_block(cls, text: str) -> str:
        """Insert the managed block if the file does not already have it."""
        if START_MARKER in text and END_MARKER in text:
            return text

        managed_section = cls._render_managed_section(DEFAULT_MANAGED_CONTENT)
        anchor = "## Identity"
        if anchor in text:
            return text.replace(anchor, f"{managed_section}\n{anchor}", 1)

        text = text.rstrip()
        if text:
            return f"{text}\n\n{managed_section}"
        return f"# User Profile\n\n{managed_section}"

    def read_text(self) -> str:
        """Read the full USER.md file."""
        self._ensure_user_profile_file()
        return self.user_profile_file.read_text(encoding="utf-8")

    def read_managed_block(self) -> str:
        """Read the auto-managed USER.md block."""
        text = self.read_text()
        start = text.find(START_MARKER)
        end = text.find(END_MARKER)
        if start == -1 or end == -1 or end <= start:
            return DEFAULT_MANAGED_CONTENT
        start += len(START_MARKER)
        content = text[start:end].strip()
        return content or DEFAULT_MANAGED_CONTENT

    def write_managed_block(self, content: str) -> None:
        """Replace only the auto-managed USER.md block."""
        text = self._ensure_managed_block(self.read_text())
        start = text.find(START_MARKER)
        end = text.find(END_MARKER)
        if start == -1 or end == -1 or end <= start:
            raise ValueError("USER.md is missing profile markers")

        managed = content.strip() or DEFAULT_MANAGED_CONTENT
        start += len(START_MARKER)
        updated = text[:start] + "\n" + managed + "\n" + text[end:]
        self.user_profile_file.write_text(updated, encoding="utf-8")

    def load_state(self) -> dict[str, int]:
        """Load the per-chat processed message offsets."""
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
        """Persist the per-chat processed message offsets."""
        safe_state = {str(key): max(0, int(value)) for key, value in state.items()}
        self.state_file.write_text(
            json.dumps(safe_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def get_processed_index(self, chat_id: str) -> int:
        """Return the last processed message index for a chat."""
        return self.load_state().get(chat_id, 0)

    def set_processed_index(self, chat_id: str, index: int) -> None:
        """Persist the last processed message index for a chat."""
        state = self.load_state()
        state[chat_id] = max(0, int(index))
        self.save_state(state)
