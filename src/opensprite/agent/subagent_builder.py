from pathlib import Path

from ..context.runtime import build_runtime_context
from ..llms import ChatMessage
from ..skills import SkillsLoader
from ..subagent_prompts import load_prompt


class SubagentMessageBuilder:
    """Build prompt/messages for delegated subagent work."""

    def __init__(self, prompt_loader=load_prompt, skills_loader: SkillsLoader | None = None):
        self.prompt_loader = prompt_loader
        self.skills_loader = skills_loader

    def build_system_prompt(
        self,
        prompt_type: str = "writer",
        workspace: str | Path | None = None,
        app_home: Path | None = None,
    ) -> str:
        prompt_body = self.prompt_loader(prompt_type, app_home=app_home)
        runtime_context = build_runtime_context(workspace=workspace)
        workspace_path = Path(workspace) if workspace is not None else None
        skills_summary = ""
        if self.skills_loader is not None:
            personal_skills_dir = workspace_path / "skills" if workspace_path is not None else None
            skills_summary = self.skills_loader.build_skills_summary(personal_skills_dir)

        sections = []
        if prompt_body:
            sections.append(prompt_body)
        else:
            sections.append(
                "## 角色（Role）\n"
                f"你是專注於單一任務的 `{prompt_type}` 助手。\n\n"
                "## 任務（Task）\n"
                "1. 先理解目前任務。\n"
                "2. 根據已提供資訊完成內容。\n"
                "3. 若資訊不足，只提出必要問題。\n\n"
                "## 規範（Constraints）\n"
                "- 聚焦當前任務\n"
                "- 不要虛構事實\n"
                "- 直接輸出可交付內容\n\n"
                "## 輸出（Output）\n"
                "- 若資訊足夠：直接輸出完成內容。\n"
                "- 若資訊不足：列出需要補充的問題。"
            )

        if skills_summary:
            sections.extend([
                "",
                "If a listed skill is relevant, read it before using other non-trivial tools so you can follow its workflow first.",
                "",
                skills_summary,
            ])
        sections.extend(["", runtime_context])
        return "\n".join(sections).strip()

    def build_messages(
        self,
        task: str,
        prompt_type: str = "writer",
        workspace: str | Path | None = None,
        app_home: Path | None = None,
    ) -> list[ChatMessage]:
        system_prompt = self.build_system_prompt(prompt_type, workspace=workspace, app_home=app_home)
        return [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=task),
        ]
