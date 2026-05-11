"""Deterministic follow-up intent inheritance for short user turns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .tool_groups import TOOL_GROUP_BY_TOOL_NAME


_URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
_FOLLOW_UP_RE = re.compile(
    r"^(?:那|那個|這個|這張|這段|換|再看|再查|再找|再幫我看|what about|how about)\b"
    r"|(?:呢|勒|咧|了嗎|如何|怎樣|怎麼樣)[？?]*$",
    re.IGNORECASE,
)
_ENTITY_ONLY_RE = re.compile(r"^[\w.:-]{2,32}$", re.IGNORECASE)
_NON_FOLLOW_UP_RE = re.compile(
    r"^(?:ok|okay|thanks|thank you|thx|好|好的|了解|知道了|謝謝|謝啦|感謝|不用|先不用)[。.!！?？]*$",
    re.IGNORECASE,
)
_WEB_KEYWORD_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:web|internet|online|url|link|news|reddit|source|sources|twse|price|pricing)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_WEB_HISTORY_RE = re.compile(
    r"(?:上網|網路|新聞|來源|連結|股價|股票|台股|代碼|基金|外部資料|即時)",
    re.IGNORECASE,
)
_WEB_SEARCH_TERM_RE = re.compile(r"\b(?:search)\b|(?:搜尋)", re.IGNORECASE)
_MEDIA_HISTORY_RE = re.compile(
    r"\b(?:image|images|photo|photos|picture|screenshot|ocr|audio|voice|video|clip|transcribe)\b"
    r"|(?:圖片|照片|截圖|這張|圖|文字辨識|音訊|語音|錄音|影片|視頻)",
    re.IGNORECASE,
)
_WORKSPACE_HISTORY_RE = re.compile(
    r"\b(?:repo|repository|codebase|file|function|class|method|traceback|pytest|src/|tests/|apps/)\b"
    r"|[\w.-]+\.(?:py|js|ts|vue|json|md|yaml|yml|toml)\b"
    r"|(?:程式|程式碼|檔案|函式|類別|專案|錯誤|測試|建置)",
    re.IGNORECASE,
)
_HISTORY_RETRIEVAL_HISTORY_RE = re.compile(
    r"(?:之前|先前|剛剛|上次|剛才|前面|提過|說過)",
    re.IGNORECASE,
)

@dataclass(frozen=True)
class FollowUpIntent:
    """A short turn's inherited task context, if one can be inferred safely."""

    is_follow_up: bool
    inherited_task_type: str | None = None
    inherited_tool_group: str | None = None
    confidence: float = 0.0
    reason: str = ""


class FollowUpIntentResolver:
    """Infer whether a short turn should inherit evidence requirements from recent context."""

    @classmethod
    def resolve(
        cls,
        *,
        current_message: str,
        history: list[dict[str, Any]] | None = None,
    ) -> FollowUpIntent:
        current = _compact(current_message)
        if not _looks_like_follow_up(current):
            return FollowUpIntent(is_follow_up=False, reason="current message is not a short follow-up")

        inherited = _infer_recent_context(history or [])
        if inherited is None:
            return FollowUpIntent(is_follow_up=True, confidence=0.35, reason="follow-up without inheritable recent context")

        task_type, tool_group, reason = inherited
        return FollowUpIntent(
            is_follow_up=True,
            inherited_task_type=task_type,
            inherited_tool_group=tool_group,
            confidence=0.75,
            reason=reason,
        )


def _compact(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _looks_like_follow_up(text: str) -> bool:
    if not text or _NON_FOLLOW_UP_RE.match(text):
        return False
    if len(text) > 80:
        return False
    if _FOLLOW_UP_RE.search(text):
        return True
    words = re.findall(r"[\w\u4e00-\u9fff]+", text)
    return len(words) == 1 and bool(_ENTITY_ONLY_RE.match(text))


def _infer_recent_context(history: list[dict[str, Any]]) -> tuple[str, str, str] | None:
    scores = {"web_research": 0, "media_extraction": 0, "workspace_read": 0, "history_retrieval": 0}
    for message in reversed(history[-12:]):
        content = _compact(str(message.get("content") or ""))
        tool_name = _compact(str(message.get("tool_name") or ""))
        if not content:
            content = ""
        role = str(message.get("role") or "")
        weight = 2 if role == "user" else 1
        if tool_name:
            tool_group = TOOL_GROUP_BY_TOOL_NAME.get(tool_name)
            if tool_group is not None:
                score_key = "media_extraction" if tool_group in {"image_text", "image_understanding", "audio_text", "video_understanding"} else tool_group
                if score_key in scores:
                    scores[score_key] += max(weight, 2)
        if _URL_RE.search(content) or _WEB_KEYWORD_RE.search(content) or _WEB_HISTORY_RE.search(content) or (_WEB_SEARCH_TERM_RE.search(content) and _WEB_KEYWORD_RE.search(content)):
            scores["web_research"] += weight
        if _MEDIA_HISTORY_RE.search(content) or "[Media-only message saved to workspace]" in content:
            scores["media_extraction"] += weight
        if _WORKSPACE_HISTORY_RE.search(content):
            scores["workspace_read"] += weight
        if _HISTORY_RETRIEVAL_HISTORY_RE.search(content):
            scores["history_retrieval"] += weight

    task_type, score = max(scores.items(), key=lambda item: item[1])
    if score <= 0:
        return None
    tool_group = {
        "web_research": "web_research",
        "media_extraction": "image_text",
        "history_retrieval": "history_retrieval",
        "workspace_read": "workspace_read",
    }[task_type]
    return task_type, tool_group, f"inherited {task_type} from recent conversation context"


__all__ = ["FollowUpIntent", "FollowUpIntentResolver"]
