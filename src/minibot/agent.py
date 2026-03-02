"""
minibot/agent.py - 最簡單的 Agent Loop（參考 nanobot 架構）

核心流程：
1. 接收使用者訊息
2. 把訊息丟給 LLM
3. LLM 回覆後回傳給使用者

設計重點：
- 只認得「統一的訊息格式」：UserMessage、AssistantMessage
- 只認得「統一的 LLM Provider 介面」
- 具体的訊息來源（telegram、discord）由外部 Adapter 轉換
- 具体的 LLM 廠商由 Provider 實作

"""

from dataclasses import dataclass  # 用來定義簡單的資料結構
from minibot.message import UserMessage, AssistantMessage  # 統一訊息格式
from minibot.llms import LLMProvider, ChatMessage  # LLM Provider 介面


@dataclass
class AgentConfig:
    """
    Agent 的設定參數集合，相當於「設定檔」。
    
    屬性：
        model: 使用的模型名稱，預設由 Provider 提供
        system_prompt: 系統提示詞，定義 AI 的行為風格
        max_tokens: 最大回覆長度（token 數），預設 2048
        temperature: 隨機性參數，0.0 = 穩定一致，1.0 = 創意自由
    """
    model: str | None = None  # 可讓 Provider 決定預設
    system_prompt: str = "你是個有用的助理。"
    max_tokens: int = 2048
    temperature: float = 0.7


class AgentLoop:
    """
    極簡 Agent Loop
    
    這是整個 Agent 的核心類別，負責：
    - 維護對話歷史（messages）
    - 呼叫 LLM（透過 Provider 介面）
    - 處理使用者輸入並回傳（process）
    
    設計重點：
    - 只認得「統一的訊息格式」：UserMessage、AssistantMessage
    - 只認得「統一的 LLM Provider 介面」
    - 具体的 LLM 廠商由外部注入
    """

    def __init__(self, config: AgentConfig, provider: LLMProvider):
        """
        AgentLoop 的建構函式。
        
        參數：
            config: AgentConfig 物件，包含所有設定
            provider: LLMProvider 物件（OpenAI/Anthropic/其他）
        
        初始化時會：
            1. 儲存設定到 self.config
            2. 儲存 LLM Provider（不直接建 client）
            3. 建立空的對話歷史（self.messages = []）
        """
        self.config = config
        self.provider = provider  # 注入 LLM Provider（可以是 OpenAI/Anthropic/其他）
        self.messages: list[ChatMessage] = []  # 對話歷史，一開始是空的

    def add_user_message(self, content: str) -> None:
        """
        把「使用者傳來的訊息」加入對話歷史。
        
        參數：
            content: 使用者輸入的文字
        
        用途：
            每次使用者說話，都用這個方法記錄下來，
            之後呼叫 LLM 時會把完整歷史傳給它，讓它知道上下文。
        """
        self.messages.append(ChatMessage(role="user", content=content))

    def add_assistant_message(self, content: str) -> None:
        """
        把「AI 的回覆」加入對話歷史。
        
        參數：
            content: AI 回覆的文字
        
        用途：
            每次 AI 回話，都記錄下來，這樣下一次對話時
            AI 就能看到之前的回覆，保持對話連貫性。
        """
        self.messages.append(ChatMessage(role="assistant", content=content))

    async def call_llm(self) -> str:
        """
        呼叫 LLM（大型語言模型）並取得回應。
        
        這次透過 Provider 介面呼叫，不直接寫死 OpenAI。
        以後要換成 Anthropic/DeepSeek/本地模型都不用改這裡。
        
        運作流程：
            1. 把 system prompt 加到訊息最前面
            2. 呼叫 self.provider.chat()（由外部注入的 Provider 處理）
            3. 取出回覆文字並回傳
        
        回傳：
            str: LLM 回覆的內容文字
        """
        # 組合成完整對話（system + 歷史）
        full_messages = [
            ChatMessage(role="system", content=self.config.system_prompt)
        ] + self.messages
        
        # 呼叫 Provider（由外部決定用哪家 LLM）
        response = await self.provider.chat(
            messages=full_messages,
            model=self.config.model,  # 可讓 Provider 用預設模型
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens
        )
        
        # 回傳內容
        return response.content

    async def process(self, user_message: UserMessage) -> AssistantMessage:
        """
        處理使用者訊息的主要入口函式。
        
        這是 AgentLoop 的「主流程」，每次使用者傳訊息來，
        就是呼叫這個函式處理。
        
        參數：
            user_message: UserMessage 統一格式的訊息
        
        回傳：
            AssistantMessage: 統一格式的回覆
        
        運作流程：
            1. 把使用者訊息加入歷史（add_user_message）
            2. 呼叫 LLM 取得回覆（call_llm）
            3. 把 AI 回覆加入歷史（add_assistant_message）
            4. 把回覆包裝成 AssistantMessage 回傳
        """
        # 步驟 1：把使用者訊息加入歷史
        self.add_user_message(user_message.text)

        # 步驟 2：呼叫 LLM（等待回覆）
        response = await self.call_llm()

        # 步驟 3：把 AI 回覆加入歷史
        self.add_assistant_message(response)

        # 步驟 4：包裝成統一格式回傳
        return AssistantMessage(
            text=response,
            chat_id=user_message.chat_id  # 回傳到同一個聊天室
        )

    def reset_history(self) -> None:
        """
        清除所有對話歷史。
        
        用途：
            當使用者想要「重新開始對話」時，
            呼叫這個函式可以清除所有之前的對話記錄，
            讓 AI 忘記之前的上下文。
        """
        self.messages.clear()


# ============================================
# 使用範例
# ============================================

"""
建立 Agent 時，需要傳入一個 Provider：

```python
from minibot.agent import AgentLoop, AgentConfig
from minibot.llms import OpenAILLM

# 1. 建立 Provider（可換成 AnthropicProvider、DeepSeekProvider 等）
provider = OpenAIProvider(
    api_key="your-openai-key",
    base_url=None,  # 或用 OpenRouter: "https://openrouter.ai/v1"
    default_model="gpt-4o-mini"
)

# 2. 建立 Agent（傳入 Provider）
config = AgentConfig(
    system_prompt="你是個有用且簡潔的助理。"
)
agent = AgentLoop(config, provider)

# 3. 使用（不變）
user_msg = UserMessage(text="你好", chat_id="123")
response = await agent.process(user_msg)
print(response.text)
```

之後要換 Provider（例：Anthropic）：

```python
# from minibot.llms import AnthropicLLM  # 未來實作

provider = AnthropicProvider(api_key="your-anthropic-key")
agent = AgentLoop(config, provider)  # Agent 程式碼不用改！
```
"""
