"""Delegate Tool - 委派子代理執行任務"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN
from ..agent.subagent_session import SUBAGENT_TASK_ID_PATTERN
from ..subagent_prompts import get_all_subagents


def _build_description(subagents: dict[str, str]) -> str:
    """動態生成 delegate tool 的 description"""
    subagent_list = "\n".join([f"- {name}: {desc}" for name, desc in subagents.items()])
    return f"""委派任務給子代理執行。

可用子代理類型（prompt_type 必須是下列其中一個已存在的 id）：
{subagent_list}

子代理會自行載入對應 prompt 並組合執行時 context。每次新委派會建立 child task session 並在結果回傳 `task_id`；後續可帶同一個 `task_id` 續跑。
不同子代理類型會套用不同 runtime tool policy；例如 reviewer 類型預設 read-only，implementer/debugger 類型可寫檔與執行驗證，test-writer 類型只能寫測試路徑。

若使用者要新增或改版子代理：請用主代理的 `configure_subagent`（add／upsert）寫入目前工作階段 workspace 底下的 `subagent_prompts/<id>.md`（同一 id 時覆寫 `~/.opensprite/subagent_prompts/` 的內容）。
預設 id 仍來自 `~/.opensprite/subagent_prompts/`（套件同步）；自訂與覆寫以 `configure_subagent` 為主。
新建前建議先 `read_skill` 讀取 `agent-creator-design`；**全新 id 的 add 必須在參數帶 `user_confirmed: true`（且使用者已同意）**，否則會被拒絕。
完成後此清單會在下次載入工具描述時包含新 id，再呼叫 `delegate`。
不要要求使用者手動改該目錄的 markdown，也不要用 `write_file`／`edit_file` 繞過（與設定檔防護一致時應避免）。
"""


class DelegateTool(Tool):
    """Delegate tool - run or resume a focused child subagent task."""

    name = "delegate"

    def __init__(
        self,
        run_subagent: Callable[[str, str | None, str | None], Awaitable[str]],
        *,
        app_home: Path | None = None,
        workspace_resolver: Callable[[], Path] | None = None,
    ):
        self._run_subagent = run_subagent
        self._app_home = app_home
        self._workspace_resolver = workspace_resolver

    def _subagents_for_current_session(self) -> dict[str, str]:
        session_ws = None
        if self._workspace_resolver is not None:
            session_ws = self._workspace_resolver()
        return get_all_subagents(self._app_home, session_workspace=session_ws)

    @property
    def description(self) -> str:
        return _build_description(self._subagents_for_current_session())

    @property
    def parameters(self) -> dict[str, Any]:
        subs = self._subagents_for_current_session()
        return {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "要委派的工作描述", "pattern": NON_EMPTY_STRING_PATTERN},
                "prompt_type": {
                    "type": "string",
                    "description": (
                        f"子代理 id，必須是目前已存在的類型之一: {list(subs.keys())}。"
                        "若要新增類型，請先用 configure_subagent 建立 prompt，再使用新的 id 呼叫 delegate。"
                        "續跑既有 task_id 時可省略，會沿用原本的子代理 id。"
                    ),
                },
                "task_id": {
                    "type": "string",
                    "description": (
                        "Optional. Resume an existing child subagent task session by id. "
                        "Omit to create a new child session; the tool result will include the new task_id."
                    ),
                    "pattern": SUBAGENT_TASK_ID_PATTERN,
                },
            },
            "required": ["task"],
        }

    async def _execute(
        self,
        task: str,
        prompt_type: str | None = None,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        return await self._run_subagent(task, prompt_type, task_id)
