"""Harness profile selection for one agent turn."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .task_intent import TaskIntent


_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_RESEARCH_MARKERS = (
    "web",
    "internet",
    "online",
    "search",
    "source",
    "sources",
    "citation",
    "cite",
    "news",
    "current",
    "latest",
    "url",
    "link",
    "website",
    "site",
    "上網",
    "網路",
    "搜尋",
    "查資料",
    "資料來源",
    "來源",
    "引用",
    "新聞",
    "最新",
    "目前",
    "網站",
    "連結",
)
_NO_WEB_RE = re.compile(
    r"\b(?:do not|don't|dont|without|no)\s+(?:use\s+)?(?:the\s+)?(?:web|internet|online|search|sources?)\b"
    r"|(?:不要|不用|不需要|別)(?:上網|搜尋|搜索|查資料|查網路|使用\s*web)",
    re.IGNORECASE,
)
_NO_WEB_EN_RE = re.compile(
    r"\b(?:do not|don't|dont|without|no)\s+(?:use\s+)?(?:the\s+)?(?:web|internet|online|search|browse|sources?)\b"
    r"|\b(?:do not|don't|dont)\b[^.?!\n]{0,80}\b(?:use\s+)?(?:the\s+)?(?:web|internet|online|search|browse|sources?)\b"
    r"|\b(?:do not|don't|dont)\s+(?:search|browse|look\s+up|google)\b"
    r"|\b(?:offline|no\s+internet|no\s+web|no\s+search)\b",
    re.IGNORECASE,
)
_NO_WEB_ZH_RE = re.compile(
    r"(?:不要|不用|別)[^。！？\n]{0,40}(?:上網|搜尋|查網路|查網頁|web_search|web_research)",
    re.IGNORECASE,
)
_NO_WORKSPACE_EN_RE = re.compile(
    r"\b(?:do not|don't|dont|without|no)\s+(?:read|inspect|access|open|use)?\s*(?:files?|workspace|repo|repository|codebase)\b"
    r"|\b(?:do not|don't|dont)\s+(?:read|inspect|access|open)\s+(?:files?|workspace|repo|repository|codebase)\b"
    r"|\b(?:no\s+file\s+access|no\s+workspace\s+access)\b",
    re.IGNORECASE,
)
_NO_WEB_LITERAL_PHRASES = (
    "\u4e0d\u8981\u4e0a\u7db2",
    "\u4e0d\u7528\u4e0a\u7db2",
    "\u5225\u4e0a\u7db2",
    "\u4e0d\u8981\u67e5\u7db2\u8def",
    "\u4e0d\u7528\u67e5\u7db2\u8def",
    "\u4e0d\u8981\u641c\u5c0b",
    "\u4e0d\u7528\u641c\u5c0b",
    "\u4e0d\u8981\u5916\u90e8\u4f86\u6e90",
    "\u4e0d\u7528\u5916\u90e8\u4f86\u6e90",
    "\u4e0d\u8981\u7528 web",
    "\u4e0d\u8981\u7528web",
    "\u4e0d\u8981 web_search",
    "\u4e0d\u8981 web_research",
)
_NO_WORKSPACE_LITERAL_PHRASES = (
    "\u4e0d\u8981\u8b80\u6a94",
    "\u4e0d\u7528\u8b80\u6a94",
    "\u4e0d\u8981\u8b80\u53d6\u6a94\u6848",
    "\u4e0d\u7528\u8b80\u53d6\u6a94\u6848",
    "\u4e0d\u8981\u8b80\u6587\u4ef6",
    "\u4e0d\u8981\u770b\u6a94\u6848",
    "\u4e0d\u8981\u770b\u5c08\u6848",
    "\u4e0d\u8981\u67e5 workspace",
    "\u4e0d\u8981\u8b80 AGENTS.md",
)
_WEB_DENIED_TOOLS = (
    "web_search",
    "web_fetch",
    "web_research",
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_press",
    "browser_scroll",
    "browser_back",
    "browser_console",
)
_WORKSPACE_DENIED_TOOLS = (
    "read_file",
    "list_dir",
    "glob_files",
    "grep_files",
    "code_navigation",
    "read_skill",
    "list_run_file_changes",
    "preview_run_file_change_revert",
)
_LOCAL_RUNTIME_RE = re.compile(
    r"\b(?:channel|session id|current time|trace metrics?|cli chat)\b|(?:目前時間|現在時間|對話|工作階段|執行階段)",
    re.IGNORECASE,
)
_PURE_ANSWER_RE = re.compile(
    r"\b(?:translate|translation|calculate|compute)\b|(?:翻譯|翻成|計算|算出)",
    re.IGNORECASE,
)
_CODING_MARKERS = (
    "repo",
    "repository",
    "codebase",
    "code",
    "file",
    "files",
    "function",
    "class",
    "method",
    "tests",
    "pytest",
    "build",
    "compile",
    "traceback",
    "stack trace",
    "src/",
    "tests/",
    "apps/",
    "程式",
    "程式碼",
    "專案",
    "檔案",
    "函式",
    "類別",
    "測試",
    "建置",
    "編譯",
    "修正",
    "修改",
    "重構",
)
_CODE_PATH_RE = re.compile(
    r"(?:^|\s)(?:[\w.-]+[\\/])+[\w.-]+|"
    r"(?:^|\s)[\w.-]+\.(?:py|js|ts|tsx|jsx|vue|json|toml|yaml|yml|md|css|html|java|go|rs|sql)(?:\s|$)",
    re.IGNORECASE,
)
_MEDIA_MARKERS = (
    "image",
    "images",
    "photo",
    "picture",
    "screenshot",
    "audio",
    "voice",
    "speech",
    "transcribe",
    "video",
    "clip",
    "ocr",
    "圖片",
    "照片",
    "截圖",
    "音訊",
    "語音",
    "轉錄",
    "影片",
    "辨識",
)
_OPS_MARKERS = (
    "credential",
    "credentials",
    "token",
    "secret",
    "provider",
    "schedule",
    "cron",
    "deploy",
    "restart",
    "service",
    "settings",
    "configuration",
    "configure",
    "mcp server",
    "憑證",
    "金鑰",
    "權杖",
    "供應商",
    "設定",
    "配置",
    "排程",
    "部署",
    "重啟",
    "服務",
)
_PROFILE_PRIORITY_ORDER = ("ops", "media", "coding", "research", "chat")


@dataclass(frozen=True)
class HarnessProfile:
    """Selected harness strategy for one task."""

    name: str
    task_type: str
    required_tool_groups: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    verification_policy: str = "none"
    continuation_policy: str = "bounded"
    approval_required_risk_levels: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ()
    reason: str = ""
    selection_signals: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe run event payload."""
        return {
            "schema_version": 1,
            "name": self.name,
            "task_type": self.task_type,
            "required_tool_groups": list(self.required_tool_groups),
            "required_evidence": list(self.required_evidence),
            "verification_policy": self.verification_policy,
            "continuation_policy": self.continuation_policy,
            "approval_required_risk_levels": list(self.approval_required_risk_levels),
            "denied_tools": list(self.denied_tools),
            "reason": self.reason,
            "selection": {
                "priority_order": list(_PROFILE_PRIORITY_ORDER),
                "matched_signals": list(self.selection_signals),
                "selected_by": self.reason,
            },
        }


class HarnessProfileService:
    """Choose the narrowest harness profile that fits the current task."""

    def select(self, task_intent: TaskIntent) -> HarnessProfile:
        """Select a profile from deterministic intent and objective hints."""
        text = task_intent.objective or ""
        lowered = text.lower()
        denied_tools = denied_tools_for_constraints(text)
        if _looks_like_direct_chat(task_intent, lowered, text):
            direct_signals = _direct_chat_signals(text, denied_tools)
            return HarnessProfile(
                name="chat",
                task_type=task_intent.kind,
                verification_policy="none",
                continuation_policy="minimal",
                denied_tools=denied_tools,
                reason="request is a direct answer or explicitly avoids external lookup",
                selection_signals=direct_signals,
            )
        if _looks_like_ops(lowered):
            return HarnessProfile(
                name="ops",
                task_type="operations",
                required_tool_groups=("workspace_read",),
                required_evidence=("audit_trace",),
                verification_policy="validate_or_report",
                continuation_policy="approval_bounded",
                approval_required_risk_levels=("external_side_effect", "configuration", "mcp"),
                reason="objective references configuration, credentials, scheduling, services, or external side effects",
                selection_signals=_selection_signals("ops", lowered, _OPS_MARKERS),
            )
        if _looks_like_media(task_intent, lowered):
            return HarnessProfile(
                name="media",
                task_type="media_extraction",
                required_tool_groups=("media",),
                required_evidence=("media_artifact",),
                verification_policy="artifact_required",
                continuation_policy="bounded",
                reason="objective references media analysis or an attachment-only media turn",
                selection_signals=_selection_signals("media", lowered, _MEDIA_MARKERS, extra=(task_intent.kind,)),
            )
        if _looks_like_coding(task_intent, lowered, text):
            return HarnessProfile(
                name="coding",
                task_type="workspace_change" if task_intent.expects_code_change else "workspace_analysis",
                required_tool_groups=("workspace_read", "workspace_write") if task_intent.expects_code_change else ("workspace_read",),
                required_evidence=("file_change",) if task_intent.expects_code_change else ("workspace_evidence",),
                verification_policy="focused_if_possible",
                continuation_policy="bounded_with_verification",
                approval_required_risk_levels=("external_side_effect", "configuration"),
                reason="objective references code, files, tests, or a repository task",
                selection_signals=_coding_selection_signals(task_intent, lowered, text),
            )
        if _looks_like_research(lowered):
            return HarnessProfile(
                name="research",
                task_type="web_research",
                required_tool_groups=("web_research",),
                required_evidence=("web_source", "source_reference"),
                verification_policy="source_grounded",
                continuation_policy="bounded_with_source_fetch",
                approval_required_risk_levels=("external_side_effect",),
                reason="objective references web research, URLs, sources, or current information",
                selection_signals=_selection_signals("research", lowered, _RESEARCH_MARKERS, extra=("url" if _URL_RE.search(lowered) else "",)),
            )
        return HarnessProfile(
            name="chat",
            task_type=task_intent.kind,
            verification_policy="none",
            continuation_policy="minimal",
            denied_tools=denied_tools,
            reason="no tool-backed harness profile matched",
            selection_signals=_constraint_signals(denied_tools) + ("fallback:chat",),
        )


def preview_harness_profiles() -> tuple[HarnessProfile, ...]:
    """Return representative profiles for settings policy previews."""
    return (
        HarnessProfile(
            name="chat",
            task_type="conversation",
            verification_policy="none",
            continuation_policy="minimal",
            reason="preview profile for low-risk chat turns",
        ),
        HarnessProfile(
            name="research",
            task_type="web_research",
            required_tool_groups=("web_research",),
            required_evidence=("web_source", "source_reference"),
            verification_policy="source_grounded",
            continuation_policy="bounded_with_source_fetch",
            approval_required_risk_levels=("external_side_effect",),
            reason="preview profile for source-grounded web research turns",
        ),
        HarnessProfile(
            name="coding",
            task_type="workspace_analysis",
            required_tool_groups=("workspace_read",),
            required_evidence=("workspace_evidence",),
            verification_policy="focused_if_possible",
            continuation_policy="bounded_with_verification",
            approval_required_risk_levels=("external_side_effect", "configuration"),
            reason="preview profile for workspace analysis turns",
        ),
        HarnessProfile(
            name="coding",
            task_type="workspace_change",
            required_tool_groups=("workspace_read", "workspace_write"),
            required_evidence=("file_change",),
            verification_policy="focused_if_possible",
            continuation_policy="bounded_with_verification",
            approval_required_risk_levels=("external_side_effect", "configuration"),
            reason="preview profile for workspace change turns",
        ),
        HarnessProfile(
            name="media",
            task_type="media_extraction",
            required_tool_groups=("media",),
            required_evidence=("media_artifact",),
            verification_policy="artifact_required",
            continuation_policy="bounded",
            reason="preview profile for media extraction turns",
        ),
        HarnessProfile(
            name="ops",
            task_type="operations",
            required_tool_groups=("workspace_read",),
            required_evidence=("audit_trace",),
            verification_policy="validate_or_report",
            continuation_policy="approval_bounded",
            approval_required_risk_levels=("external_side_effect", "configuration", "mcp"),
            reason="preview profile for operations turns",
        ),
    )


def _has_marker(lowered: str, markers: tuple[str, ...]) -> bool:
    for marker in markers:
        if _is_ascii_word_marker(marker):
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(marker)}(?![A-Za-z0-9_])", lowered):
                return True
        elif marker in lowered:
            return True
    return False


def _is_ascii_word_marker(marker: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9_]+", marker))


def _looks_like_research(lowered: str) -> bool:
    if has_no_web_constraint(lowered) or _LOCAL_RUNTIME_RE.search(lowered):
        return False
    return bool(_URL_RE.search(lowered)) or _has_marker(lowered, _RESEARCH_MARKERS)


def _looks_like_coding(task_intent: TaskIntent, lowered: str, text: str) -> bool:
    if has_no_workspace_constraint(text):
        return False
    if has_no_web_constraint(text) and task_intent.kind == "debug" and not _CODE_PATH_RE.search(text):
        return False
    if task_intent.expects_code_change:
        return True
    if task_intent.kind in {"refactor", "implementation"}:
        return True
    if task_intent.kind == "debug":
        return bool(_CODE_PATH_RE.search(text)) or _has_marker(lowered, _CODING_MARKERS)
    return bool(_CODE_PATH_RE.search(text)) or _has_marker(lowered, _CODING_MARKERS)


def _looks_like_media(task_intent: TaskIntent, lowered: str) -> bool:
    return task_intent.kind == "media_upload" or _has_marker(lowered, _MEDIA_MARKERS)


def _looks_like_ops(lowered: str) -> bool:
    return _has_marker(lowered, _OPS_MARKERS)


def _looks_like_direct_chat(task_intent: TaskIntent, lowered: str, text: str) -> bool:
    if _PURE_ANSWER_RE.search(text) or _LOCAL_RUNTIME_RE.search(text):
        return True
    if has_no_web_constraint(text) and not _CODE_PATH_RE.search(text):
        if task_intent.kind == "debug" or not _has_marker(lowered, _CODING_MARKERS):
            return True
    if has_no_workspace_constraint(text):
        return True
    if _URL_RE.search(lowered) or _has_marker(lowered, _RESEARCH_MARKERS):
        return False
    return task_intent.kind in {"question", "conversation"} and not _CODE_PATH_RE.search(text) and not _has_marker(lowered, _CODING_MARKERS)


def has_no_web_constraint(text: str) -> bool:
    """Return whether the user explicitly forbids web/search evidence."""
    text = text or ""
    lowered = text.lower()
    return bool(
        _NO_WEB_RE.search(text)
        or _NO_WEB_EN_RE.search(text)
        or _NO_WEB_ZH_RE.search(text)
        or any(phrase in lowered for phrase in _NO_WEB_LITERAL_PHRASES)
    )


def has_no_workspace_constraint(text: str) -> bool:
    """Return whether the user explicitly forbids file/workspace inspection."""
    text = text or ""
    lowered = text.lower()
    return bool(_NO_WORKSPACE_EN_RE.search(text) or any(phrase.lower() in lowered for phrase in _NO_WORKSPACE_LITERAL_PHRASES))


def denied_tools_for_constraints(text: str) -> tuple[str, ...]:
    """Return exact tools that must not be exposed for explicit user constraints."""
    denied: list[str] = []
    if has_no_web_constraint(text):
        denied.extend(_WEB_DENIED_TOOLS)
    if has_no_workspace_constraint(text):
        denied.extend(_WORKSPACE_DENIED_TOOLS)
    return tuple(dict.fromkeys(denied))


def _constraint_signals(denied_tools: tuple[str, ...]) -> tuple[str, ...]:
    signals: list[str] = []
    if any(tool in denied_tools for tool in _WEB_DENIED_TOOLS):
        signals.append("constraint:no_web")
    if any(tool in denied_tools for tool in _WORKSPACE_DENIED_TOOLS):
        signals.append("constraint:no_workspace")
    return tuple(signals)


def _direct_chat_signals(text: str, denied_tools: tuple[str, ...]) -> tuple[str, ...]:
    signals = list(_constraint_signals(denied_tools))
    signals.append("fallback:chat")
    if signals[:-1] or _PURE_ANSWER_RE.search(text or "") or _LOCAL_RUNTIME_RE.search(text or ""):
        signals.append("signal:direct_answer")
    return tuple(signals)


def _selection_signals(profile: str, lowered: str, markers: tuple[str, ...], *, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    signals = [f"priority:{profile}"]
    signals.extend(f"marker:{marker}" for marker in markers if marker and _marker_matches(lowered, marker))
    signals.extend(f"signal:{item}" for item in extra if item)
    return tuple(signals)


def _coding_selection_signals(task_intent: TaskIntent, lowered: str, text: str) -> tuple[str, ...]:
    signals = list(_selection_signals("coding", lowered, _CODING_MARKERS))
    if task_intent.expects_code_change:
        signals.append("intent:expects_code_change")
    if task_intent.kind in {"debug", "refactor", "implementation"}:
        signals.append(f"intent:{task_intent.kind}")
    if _CODE_PATH_RE.search(text):
        signals.append("pattern:code_path")
    return tuple(signals)


def _marker_matches(lowered: str, marker: str) -> bool:
    if _is_ascii_word_marker(marker):
        return bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(marker)}(?![A-Za-z0-9_])", lowered))
    return marker in lowered
