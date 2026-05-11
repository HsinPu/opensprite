from opensprite.agent.follow_up_intent import FollowUpIntentResolver


def test_follow_up_inherits_web_research_from_stock_context():
    result = FollowUpIntentResolver.resolve(
        current_message="那00981t呢",
        history=[
            {"role": "user", "content": "幫我查 00980A 這檔 ETF 的股價和基本資料"},
            {"role": "assistant", "content": "我查到 00980A 的公開資訊來源。"},
        ],
    )

    assert result.is_follow_up is True
    assert result.inherited_task_type == "web_research"
    assert result.inherited_tool_group == "web_research"
    assert result.confidence > 0.5


def test_follow_up_inherits_web_research_from_generic_search_context():
    result = FollowUpIntentResolver.resolve(
        current_message="那這個呢",
        history=[
            {"role": "user", "content": "幫我搜尋 Reddit 上有哪些開源搜尋工具"},
            {"role": "assistant", "content": "我找到幾個來源。"},
        ],
    )

    assert result.is_follow_up is True
    assert result.inherited_tool_group == "web_research"


def test_follow_up_does_not_inherit_for_acknowledgement():
    result = FollowUpIntentResolver.resolve(
        current_message="謝啦",
        history=[{"role": "user", "content": "幫我搜尋 Reddit 上有哪些開源搜尋工具"}],
    )

    assert result.is_follow_up is False
    assert result.inherited_tool_group is None


def test_follow_up_inherits_media_context():
    result = FollowUpIntentResolver.resolve(
        current_message="這張呢",
        history=[
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/a.jpg"},
            {"role": "user", "content": "幫我讀取這張圖片的文字"},
        ],
    )

    assert result.is_follow_up is True
    assert result.inherited_task_type == "media_extraction"
    assert result.inherited_tool_group == "image_text"


def test_follow_up_inherits_workspace_context():
    result = FollowUpIntentResolver.resolve(
        current_message="那這個檔案呢",
        history=[
            {"role": "user", "content": "幫我檢查 src/opensprite/agent/task_contract.py 的邏輯"},
            {"role": "assistant", "content": "我看了這個檔案的 task contract 建立流程。"},
        ],
    )

    assert result.is_follow_up is True
    assert result.inherited_task_type == "workspace_read"
    assert result.inherited_tool_group == "workspace_read"


def test_follow_up_prefers_workspace_context_after_repo_search():
    result = FollowUpIntentResolver.resolve(
        current_message="那這個呢",
        history=[
            {"role": "user", "content": "幫我搜尋目前專案裡的 TODO"},
            {"role": "assistant", "content": "我在 repo 裡找到幾個 TODO 註記。"},
        ],
    )

    assert result.is_follow_up is True
    assert result.inherited_task_type == "workspace_read"
    assert result.inherited_tool_group == "workspace_read"
