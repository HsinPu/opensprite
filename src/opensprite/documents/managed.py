"""Helpers for markdown files with auto-managed sections."""

from __future__ import annotations

from pathlib import Path


class ManagedMarkdownDocument:
    """Read and update a delimited auto-managed markdown block."""

    def __init__(
        self,
        file_path: Path,
        *,
        start_marker: str,
        end_marker: str,
        default_content: str,
        heading: str,
        intro: str,
        anchor_heading: str | None = None,
        bootstrap_text: str = "",
    ):
        self.file_path = Path(file_path).expanduser()
        self.start_marker = start_marker
        self.end_marker = end_marker
        self.default_content = default_content
        self.heading = heading
        self.intro = intro
        self.anchor_heading = anchor_heading
        self.bootstrap_text = bootstrap_text
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_file()

    def _render_managed_section(self, content: str) -> str:
        managed = content.strip() or self.default_content
        return (
            f"{self.heading}\n\n"
            f"{self.intro}\n\n"
            f"{self.start_marker}\n"
            f"{managed}\n"
            f"{self.end_marker}\n"
        )

    def _ensure_markers(self, text: str) -> str:
        if self.start_marker in text and self.end_marker in text:
            return text

        managed_section = self._render_managed_section(self.default_content)
        if self.anchor_heading and self.anchor_heading in text:
            return text.replace(self.anchor_heading, f"{managed_section}\n{self.anchor_heading}", 1)

        text = text.rstrip()
        if text:
            return f"{text}\n\n{managed_section}"
        return self.bootstrap_text or managed_section

    def _ensure_file(self) -> None:
        if self.file_path.exists():
            content = self.file_path.read_text(encoding="utf-8")
        else:
            content = self.bootstrap_text

        normalized = self._ensure_markers(content)
        if normalized != content or not self.file_path.exists():
            self.file_path.write_text(normalized, encoding="utf-8")

    def read_text(self) -> str:
        """Read the full markdown document."""
        self._ensure_file()
        return self.file_path.read_text(encoding="utf-8")

    def read_managed_block(self) -> str:
        """Read the content between the managed markers."""
        text = self.read_text()
        start = text.find(self.start_marker)
        end = text.find(self.end_marker)
        if start == -1 or end == -1 or end <= start:
            return self.default_content
        start += len(self.start_marker)
        content = text[start:end].strip()
        return content or self.default_content

    def write_managed_block(self, content: str) -> None:
        """Replace only the content between the managed markers."""
        text = self._ensure_markers(self.read_text())
        start = text.find(self.start_marker)
        end = text.find(self.end_marker)
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"Managed markers missing in {self.file_path}")

        managed = content.strip() or self.default_content
        start += len(self.start_marker)
        updated = text[:start] + "\n" + managed + "\n" + text[end:]
        self.file_path.write_text(updated, encoding="utf-8")
