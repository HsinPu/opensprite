import asyncio

from opensprite.tools.result_status import classify_tool_result_status
from opensprite.tools.search import SearchHistoryTool


class EmptySearchStore:
    async def search_history(self, session_id: str, query: str, limit: int = 5):
        return []


def test_search_history_missing_session_returns_structured_error():
    tool = SearchHistoryTool(EmptySearchStore(), get_session_id=lambda: None, default_limit=5)

    result = asyncio.run(tool.execute(query="prior decision"))
    status = classify_tool_result_status(result)

    assert status.ok is False
    assert status.error_type == "ToolValidationError"
    assert status.category == "session_unavailable"
    assert status.invalid_arguments is True
    assert "session_id is unavailable" in status.error


def test_search_history_uses_current_session_id():
    calls = []

    class Store:
        async def search_history(self, session_id: str, query: str, limit: int = 5):
            calls.append((session_id, query, limit))
            return []

    tool = SearchHistoryTool(Store(), get_session_id=lambda: "chat-1", default_limit=5)

    result = asyncio.run(tool.execute(query="prior decision", limit=2))

    assert calls == [("chat-1", "prior decision", 2)]
    assert result == "No history matches found for 'prior decision' in this session."
