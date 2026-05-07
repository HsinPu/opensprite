"""Prompt logging and compact diagnostic formatting helpers."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable

from ..config import LogConfig
from ..utils import sanitize_assistant_visible_text, strip_assistant_internal_scaffolding
from ..utils.log import logger


LOG_WHITESPACE_RE = re.compile(r"\s+")
_SENSITIVE_QUERY_PARAMS = frozenset(
    {
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "api_key",
        "apikey",
        "client_secret",
        "password",
        "auth",
        "jwt",
        "session",
        "secret",
        "key",
        "code",
        "signature",
        "x-amz-signature",
    }
)
_SECRET_ENV_NAMES = r"(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)"
_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(sk-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|AIza[A-Za-z0-9_-]{30,}|pplx-[A-Za-z0-9]{10,}|"
    r"hf_[A-Za-z0-9]{10,}|gsk_[A-Za-z0-9]{10,}|pypi-[A-Za-z0-9_-]{10,})"
    r"(?![A-Za-z0-9_-])"
)
_ENV_ASSIGN_RE = re.compile(
    rf"([A-Z0-9_]{{0,50}}{_SECRET_ENV_NAMES}[A-Z0-9_]{{0,50}})\s*=\s*(['\"]?)(\S+)\2"
)
_JSON_FIELD_RE = re.compile(
    r'("(?:api_?key|token|secret|password|access_token|refresh_token|authorization|key)")\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)
_AUTH_HEADER_RE = re.compile(r"(Authorization:\s*Bearer\s+)(\S+)", re.IGNORECASE)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----")
_DB_CONNSTR_RE = re.compile(r"((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:]+:)([^@]+)(@)", re.IGNORECASE)
_URL_WITH_QUERY_RE = re.compile(r"(https?|wss?|ftp)://([^\s/?#]+)([^\s?#]*)\?([^\s#]+)(#\S*)?")


def _mask_secret(value: str) -> str:
    if not value:
        return "***"
    if len(value) < 18:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def _redact_query_string(query: str) -> str:
    parts = []
    for pair in query.split("&"):
        if "=" not in pair:
            parts.append(pair)
            continue
        key, _, value = pair.partition("=")
        parts.append(f"{key}=***" if key.lower() in _SENSITIVE_QUERY_PARAMS else pair)
    return "&".join(parts)


def redact_log_preview(text: str) -> str:
    """Redact common secret shapes before text reaches diagnostic logs."""
    if not text:
        return text

    text = _ENV_ASSIGN_RE.sub(lambda match: f"{match.group(1)}={match.group(2)}{_mask_secret(match.group(3))}{match.group(2)}", text)
    text = _JSON_FIELD_RE.sub(lambda match: f'{match.group(1)}: "{_mask_secret(match.group(2))}"', text)
    text = _AUTH_HEADER_RE.sub(lambda match: match.group(1) + _mask_secret(match.group(2)), text)
    text = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", text)
    text = _DB_CONNSTR_RE.sub(lambda match: f"{match.group(1)}***{match.group(3)}", text)
    text = _URL_WITH_QUERY_RE.sub(
        lambda match: f"{match.group(1)}://{match.group(2)}{match.group(3)}?{_redact_query_string(match.group(4))}{match.group(5) or ''}",
        text,
    )
    return _PREFIX_RE.sub(lambda match: _mask_secret(match.group(1)), text)


class PromptLoggingService:
    """Handles prompt log files and compact log previews for agent diagnostics."""

    def __init__(self, *, log_config: LogConfig, app_home_getter: Callable[[], Path | None]):
        self.log_config = log_config
        self._app_home_getter = app_home_getter

    @staticmethod
    def sanitize_log_filename(value: str) -> str:
        """Sanitize a string for use in per-prompt log filenames."""
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
        return cleaned[:80] or "prompt"

    def get_system_prompt_log_path(self, log_id: str) -> Path:
        """Return a unique file path for one full system prompt log entry."""
        logs_root = (self._app_home_getter() or Path.home() / ".opensprite") / "logs" / "system-prompts"
        if ":subagent:" in log_id:
            logs_root = logs_root / "subagents"
        dated_root = logs_root / time.strftime("%Y-%m-%d")
        dated_root.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%H-%M-%S")
        suffix = str(time.time_ns())[-6:]
        safe_log_id = self.sanitize_log_filename(log_id)
        filename = f"{timestamp}_{safe_log_id}_{suffix}.md"
        return dated_root / filename

    def write_full_system_prompt_log(self, log_id: str, content: str) -> None:
        """Write the full system prompt to a dedicated per-prompt log file."""
        try:
            log_path = self.get_system_prompt_log_path(log_id)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            entry = (
                f"[{timestamp}] [{log_id}] prompt.system.begin\n"
                f"{content}\n"
                f"[{timestamp}] [{log_id}] prompt.system.end\n"
            )
            with log_path.open("w", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            logger.error(f"[{log_id}] prompt.file.error | error={e}")

    @staticmethod
    def sanitize_response_content(content: str) -> str:
        """Remove provider-internal control blocks from visible replies."""
        return sanitize_assistant_visible_text(content)

    @staticmethod
    def format_log_preview(content: str | list[dict[str, Any]] | None, max_chars: int = 160) -> str:
        """Build a compact, single-line preview for logs."""
        if isinstance(content, list):
            text_parts: list[str] = []
            image_count = 0
            other_items = 0
            for item in content:
                if not isinstance(item, dict):
                    other_items += 1
                    continue
                item_type = item.get("type")
                if item_type == "text":
                    text_parts.append(str(item.get("text", "")))
                elif item_type == "image_url":
                    image_count += 1
                else:
                    other_items += 1

            text = " ".join(part for part in text_parts if part)
            text = strip_assistant_internal_scaffolding(text)
            text = LOG_WHITESPACE_RE.sub(" ", text).strip() or "<multimodal>"
            suffix_parts = []
            if image_count:
                suffix_parts.append(f"images={image_count}")
            if other_items:
                suffix_parts.append(f"items={other_items}")
            if suffix_parts:
                text = f"{text} [{' '.join(suffix_parts)}]"
        else:
            text = strip_assistant_internal_scaffolding(str(content or ""))
            text = LOG_WHITESPACE_RE.sub(" ", text).strip()

        if not text:
            return "<empty>"
        text = redact_log_preview(text)
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."

    @staticmethod
    def summarize_messages(messages: list[Any], tail: int = 4) -> str:
        """Build a compact summary of the trailing chat messages for diagnostics."""
        summary = []
        for msg in messages[-tail:]:
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                content_kind = f"list[{len(content)}]"
            else:
                content_kind = f"str[{len(content or '')}]"
            tool_id = "y" if getattr(msg, "tool_call_id", None) else "n"
            tool_calls = len(getattr(msg, "tool_calls", None) or [])
            summary.append(
                f"{getattr(msg, 'role', '?')}({content_kind},tool_id={tool_id},tool_calls={tool_calls})"
            )
        return ", ".join(summary) if summary else "<empty>"

    @staticmethod
    def extract_available_subagents(system_prompt: str) -> list[str]:
        """Parse the Available Subagents section from a rendered system prompt."""
        in_section = False
        subagents: list[str] = []

        for raw_line in system_prompt.splitlines():
            line = raw_line.strip()
            if not in_section:
                if line in {"# Available Subagents", "## Available Subagents"}:
                    in_section = True
                continue

            if not line:
                continue
            if line == "---" or line.startswith("#"):
                break
            if not line.startswith("- `"):
                continue

            end_tick = line.find("`", 3)
            if end_tick <= 3:
                continue
            subagents.append(line[3:end_tick])

        return subagents

    def log_prepared_messages(self, log_id: str, messages: list[dict[str, Any]]) -> None:
        """Log prepared prompt/messages when prompt logging is enabled."""
        if not self.log_config.log_system_prompt:
            return

        try:
            system_msg = next((m for m in messages if m.get("role") == "system"), None)
            if system_msg:
                system_prompt = str(system_msg.get("content", ""))
                self.write_full_system_prompt_log(log_id, system_prompt)
                max_chars = 240
                if self.log_config.log_system_prompt_lines > 0:
                    max_chars = max(120, self.log_config.log_system_prompt_lines * 120)
                logger.info(
                    f"[{log_id}] prompt.system | {self.format_log_preview(system_prompt, max_chars=max_chars)}"
                )
                if ":subagent:" not in log_id:
                    available_subagents = self.extract_available_subagents(system_prompt)
                    names = ", ".join(available_subagents) if available_subagents else "<none>"
                    logger.info(
                        f"[{log_id}] prompt.subagents | count={len(available_subagents)} names={names}"
                    )

            for index, msg in enumerate(messages):
                role = msg.get("role", "unknown")
                if role == "system":
                    continue
                preview = self.format_log_preview(msg.get("content", ""))
                logger.info(
                    f"[{log_id}] prompt.message[{index}] | role={role} preview={preview}"
                )
        except Exception as e:
            logger.error(f"[{log_id}] prompt.log.error | error={e}")
