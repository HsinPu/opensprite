import asyncio

from opensprite.config.schema import Config, DocumentLlmConfig
from opensprite.context.paths import sync_templates
from opensprite.documents.user_profile import (
    DEFAULT_MANAGED_CONTENT,
    UserProfileConsolidator,
    create_user_profile_store,
)
from opensprite.llms.base import LLMResponse, ToolCall
from opensprite.storage.base import StoredMessage


class FakeStorage:
    def __init__(self, messages_by_session):
        self.messages_by_session = messages_by_session

    async def get_messages(self, session_id, limit=None):
        return list(self.messages_by_session[session_id])


class FakeProvider:
    def __init__(self):
        self.prompts = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        prompt = messages[1]["content"]
        self.prompts.append(prompt)
        if "dark mode" in prompt:
            profile_update = """### Communication Preferences
- Prefers dark mode.

### Work Context
- No learned work context yet.

### Stable Constraints
- No learned stable constraints yet."""
        else:
            profile_update = """### Communication Preferences
- Prefers light mode.

### Work Context
- No learned work context yet.

### Stable Constraints
- No learned stable constraints yet."""

        return LLMResponse(
            content="",
            model=model or "fake-model",
            tool_calls=[ToolCall(id="call-1", name="save_user_profile", arguments={"profile_update": profile_update})],
        )


def test_create_user_profile_store_resets_managed_block_when_bootstrapping_from_template(tmp_path):
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    bootstrap_user = app_home / "bootstrap" / "USER.md"
    bootstrap_user.write_text(
        "# USER.md - Durable User Context\n\n"
        "Template intro stays.\n\n"
        "## Auto-managed Profile\n\n"
        "This section is maintained by OpenSprite and should stay concise.\n\n"
        "<!-- OPENSPRITE:USER_PROFILE:START -->\n"
        "- Existing global profile detail\n"
        "<!-- OPENSPRITE:USER_PROFILE:END -->\n",
        encoding="utf-8",
    )

    store = create_user_profile_store(app_home, "telegram:user-a")
    profile_text = store.read_text()

    assert "Template intro stays." in profile_text
    assert "- Existing global profile detail" not in profile_text
    assert DEFAULT_MANAGED_CONTENT in profile_text


def test_user_profile_consolidator_writes_separate_profiles_per_session(tmp_path):
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    storage = FakeStorage(
        {
            "telegram:user-a": [StoredMessage(role="user", content="I always use dark mode.", timestamp=1.0)],
            "telegram:user-b": [StoredMessage(role="user", content="I always use light mode.", timestamp=1.0)],
        }
    )
    provider = FakeProvider()
    consolidator = UserProfileConsolidator(
        storage=storage,
        provider=provider,
        model="fake-model",
        profile_store_factory=lambda session_id: create_user_profile_store(app_home, session_id),
        threshold=1,
        lookback_messages=10,
        enabled=True,
        llm=DocumentLlmConfig(**Config.load_template_data()["user_profile"]["llm"]),
    )

    async def scenario():
        await consolidator.maybe_update("telegram:user-a")
        await consolidator.maybe_update("telegram:user-b")

    asyncio.run(scenario())

    profile_a = create_user_profile_store(app_home, "telegram:user-a")
    profile_b = create_user_profile_store(app_home, "telegram:user-b")

    assert "### Communication Preferences" in profile_a.read_managed_block()
    assert "- Prefers dark mode." in profile_a.read_managed_block()
    assert "- Prefers light mode." in profile_b.read_managed_block()
    assert profile_a.user_profile_file != profile_b.user_profile_file
