"""Tools for OpenSprite agent."""

from .base import Tool
from .approval import PermissionRequest, PermissionRequestManager
from .registry import ToolRegistry
from .active_task import TaskUpdateTool
from .audio import TranscribeAudioTool
from .batch import BatchTool
from .video import AnalyzeVideoTool
from .filesystem import (
    ApplyPatchTool,
    EditFileTool,
    GlobFilesTool,
    GrepFilesTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from .skill import ReadSkillTool
from .skill_config import ConfigureSkillTool
from .process import ProcessTool
from .shell import ExecTool
from .verify import VerifyTool
from .search import SearchHistoryTool, SearchKnowledgeTool
from .web_search import WebSearchTool
from .web_fetch import WebFetchTool
from .mcp import MCPToolWrapper, connect_mcp_servers
from .mcp_config import ConfigureMCPTool
from .subagent_config import ConfigureSubagentTool
from .cron import CronTool
from .image import AnalyzeImageTool, OCRImageTool
from .outbound_media import SendMediaTool
from .run_trace import ListRunFileChangesTool, PreviewRunFileChangeRevertTool
from .code_navigation import CodeNavigationTool
from .delegate_many import DelegateManyTool

__all__ = [
    "Tool",
    "PermissionRequest",
    "PermissionRequestManager",
    "ToolRegistry",
    "TaskUpdateTool",
    "BatchTool",
    "TranscribeAudioTool",
    "AnalyzeVideoTool",
    "ReadFileTool",
    "GlobFilesTool",
    "GrepFilesTool",
    "ApplyPatchTool",
    "WriteFileTool",
    "ListDirTool",
    "EditFileTool",
    "ReadSkillTool",
    "ConfigureSkillTool",
    "ProcessTool",
    "ExecTool",
    "VerifyTool",
    "SearchHistoryTool",
    "SearchKnowledgeTool",
    "WebSearchTool",
    "WebFetchTool",
    "MCPToolWrapper",
    "connect_mcp_servers",
    "ConfigureMCPTool",
    "ConfigureSubagentTool",
    "CronTool",
    "AnalyzeImageTool",
    "OCRImageTool",
    "SendMediaTool",
    "ListRunFileChangesTool",
    "PreviewRunFileChangeRevertTool",
    "CodeNavigationTool",
    "DelegateManyTool",
]
