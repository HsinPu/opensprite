"""
opensprite/channels/telegram.py - Telegram 訊息 Adapter

把 Telegram 的原始訊息轉換成「統一訊息格式」並走 MessageQueue。

流程：
1. 收到 Telegram Update
2. 用 TelegramAdapter.to_user_message() 轉成 UserMessage
3. 丟到 MessageQueue（enqueue）
4. Queue 處理完後，會依 channel route 把回覆發送到 Telegram

"""

import asyncio
import base64
import binascii
import html
import io
import re
from typing import Any

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, filters

from ..config import MessagesConfig
from ..bus.message import MessageAdapter, UserMessage, AssistantMessage
from ..utils.log import logger


class TelegramAdapter(MessageAdapter):
    """
    Telegram 訊息轉接器
    
    實作 MessageAdapter 介面，把 Telegram 的訊息轉換成統一格式。
    透過 MessageQueue 處理訊息，支援並行處理。
    """
    
    DEFAULT_CONFIG = {
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30,
        "pool_timeout": 30,
        "get_updates_connect_timeout": 10,
        "get_updates_read_timeout": 30,
        "get_updates_write_timeout": 30,
        "get_updates_pool_timeout": 30,
        "poll_timeout": 10,
        "bootstrap_retries": 3,
        "drop_pending_updates": False,
        "typing_action_interval": 4,
    }
    OUTBOUND_MEDIA_EXTENSIONS = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
        "audio/ogg": "ogg",
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/wav": "wav",
        "video/mp4": "mp4",
        "video/webm": "webm",
    }

    def __init__(self, bot_token: str, mq=None, config: dict[str, Any] | None = None):
        """
        初始化 Telegram Adapter
        
        參數：
            bot_token: Telegram Bot Token（從 @BotFather 取得）
            mq: MessageQueue 實例（可選，不給則用舊方式直接叫 agent）
        """
        self.bot_token = bot_token
        self.app = None
        self.mq = mq
        self.messages = getattr(mq, "messages", None) or MessagesConfig()
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        self._typing_tasks: dict[str, asyncio.Task] = {}

    def _get_int(self, key: str) -> int:
        """Read an integer config value with sane defaults."""
        return int(self.config.get(key, self.DEFAULT_CONFIG[key]))

    def _get_bool(self, key: str) -> bool:
        """Read a boolean config value with sane defaults."""
        return bool(self.config.get(key, self.DEFAULT_CONFIG[key]))

    def _mask_token(self) -> str:
        """Return a safe token preview for logs."""
        if not self.bot_token:
            return "<empty>"
        if len(self.bot_token) <= 10:
            return f"{self.bot_token[:2]}***"
        return f"{self.bot_token[:6]}...{self.bot_token[-4:]}"

    def _describe_startup_config(self) -> str:
        """Build a concise Telegram startup config summary for logs."""
        parts = [
            f"token={self._mask_token()}",
            f"token_length={len(self.bot_token)}",
            f"has_mq={self.mq is not None}",
            f"connect_timeout={self._get_int('connect_timeout')}",
            f"read_timeout={self._get_int('read_timeout')}",
            f"write_timeout={self._get_int('write_timeout')}",
            f"pool_timeout={self._get_int('pool_timeout')}",
            f"get_updates_connect_timeout={self._get_int('get_updates_connect_timeout')}",
            f"get_updates_read_timeout={self._get_int('get_updates_read_timeout')}",
            f"get_updates_write_timeout={self._get_int('get_updates_write_timeout')}",
            f"get_updates_pool_timeout={self._get_int('get_updates_pool_timeout')}",
            f"poll_timeout={self._get_int('poll_timeout')}",
            f"bootstrap_retries={self._get_int('bootstrap_retries')}",
            f"drop_pending_updates={self._get_bool('drop_pending_updates')}",
            f"typing_action_interval={self._get_int('typing_action_interval')}",
        ]
        return ", ".join(parts)

    async def _send_typing_action(self, chat_id: str) -> None:
        """Send one Telegram typing action if the app is available."""
        if self.app is None:
            return
        try:
            await self.app.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception as exc:
            logger.debug("Telegram typing action failed for chat {}: {}", chat_id, exc)

    def _start_typing_indicator(self, session_chat_id: str | None, chat_id: str | None) -> None:
        """Start a periodic typing indicator for an active session."""
        if self.app is None or not session_chat_id or not chat_id:
            return
        if session_chat_id in self._typing_tasks:
            return

        interval = max(1, self._get_int("typing_action_interval"))

        async def run_typing() -> None:
            try:
                while True:
                    await self._send_typing_action(chat_id)
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

        self._typing_tasks[session_chat_id] = asyncio.create_task(run_typing())

    async def _stop_typing_indicator(self, session_chat_id: str | None) -> None:
        """Stop the periodic typing indicator for a session."""
        if not session_chat_id:
            return
        task = self._typing_tasks.pop(session_chat_id, None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _log_request_timeouts(self) -> None:
        """Log the applied Bot API request timeout values if available."""
        if self.app is None:
            return

        try:
            request_objects = getattr(self.app.bot, "_request", ())
            if not isinstance(request_objects, tuple) or len(request_objects) != 2:
                logger.info("Telegram internal request objects unavailable for timeout logging")
                return

            labels = ("get_updates", "bot_api")
            for label, request_object in zip(labels, request_objects):
                timeout = request_object._client_kwargs.get("timeout")
                logger.info(
                    "Telegram {} timeout config: connect={}, read={}, write={}, pool={}",
                    label,
                    getattr(timeout, "connect", None),
                    getattr(timeout, "read", None),
                    getattr(timeout, "write", None),
                    getattr(timeout, "pool", None),
                )
        except Exception as exc:
            logger.warning("Failed to inspect Telegram timeout config: {}", exc)

    def _build_application(self) -> Application:
        """Build the Telegram application with configured request timeouts."""
        builder = Application.builder().token(self.bot_token)
        timeout_keys = [
            "connect_timeout",
            "read_timeout",
            "write_timeout",
            "pool_timeout",
            "get_updates_connect_timeout",
            "get_updates_read_timeout",
            "get_updates_write_timeout",
            "get_updates_pool_timeout",
        ]
        for key in timeout_keys:
            method = getattr(builder, key, None)
            if callable(method):
                builder = method(self._get_int(key))
        return builder.build()

    @staticmethod
    def _supported_message_filters():
        """Return the Telegram message types handled by this adapter."""
        return (
            filters.TEXT
            | filters.PHOTO
            | filters.VOICE
            | filters.AUDIO
            | filters.VIDEO
            | filters.VIDEO_NOTE
            | filters.ANIMATION
        ) & ~filters.COMMAND

    async def _shutdown_app(self) -> None:
        """Best-effort cleanup for partially started Telegram applications."""
        if self.app is None:
            return

        updater = getattr(self.app, "updater", None)
        if updater is not None and getattr(updater, "running", False):
            try:
                await updater.stop()
            except Exception:
                pass

        if getattr(self.app, "running", False):
            try:
                await self.app.stop()
            except Exception:
                pass

        try:
            await self.app.shutdown()
        except Exception:
            pass
    
    @staticmethod
    def _resolve_update_bot(raw_update: Any, explicit_bot: Any = None) -> Any:
        """Resolve the Telegram bot object across real PTB updates and tests."""
        if explicit_bot is not None:
            return explicit_bot

        bot = getattr(raw_update, "bot", None)
        if bot is not None:
            return bot

        get_bot = getattr(raw_update, "get_bot", None)
        if callable(get_bot):
            try:
                bot = get_bot()
            except Exception:
                bot = None
            if bot is not None:
                return bot

        return getattr(raw_update, "_bot", None)

    async def to_user_message(self, raw_update: Update, bot: Any = None) -> UserMessage:
        """
        把 Telegram Update 轉成統一的 UserMessage
        
        實作 MessageAdapter 介面。
        
        參數：
            raw_update: telegram.Update 物件
        
        回傳：
            UserMessage: 統一格式的訊息
        """
        # 取出訊息內容
        message = raw_update.message
        if message is None:
            return UserMessage(
                text="",
                channel="telegram",
                metadata={"update_id": raw_update.update_id},
                raw=raw_update,
            )
        
        text = message.text or message.caption or ""
        
        # 取出發送者資訊
        sender_id = None
        sender_name = None
        if message.from_user:
            sender_id = str(message.from_user.id)
            sender_name = (
                message.from_user.username
                or getattr(message.from_user, "full_name", None)
                or sender_id
            )
        
        # 取出聊天室 ID
        chat_id = str(message.chat.id) if message.chat else None
        session_chat_id = f"telegram:{chat_id}" if chat_id else None
        
        telegram_bot = self._resolve_update_bot(raw_update, bot)

        # 處理圖片
        images = []
        photos = getattr(message, "photo", None)
        if photos and telegram_bot is not None:
            images = await self._download_images(photos, telegram_bot)

        # 處理音訊 / 語音
        audios = []
        voice = getattr(message, "voice", None)
        if voice and telegram_bot is not None:
            audio = await self._download_audio(voice, telegram_bot, default_mime_type="audio/ogg")
            if audio:
                audios.append(audio)
        audio_message = getattr(message, "audio", None)
        if audio_message and telegram_bot is not None:
            audio = await self._download_audio(audio_message, telegram_bot, default_mime_type="audio/mpeg")
            if audio:
                audios.append(audio)

        # 處理影片
        videos = []
        video_message = getattr(message, "video", None)
        if video_message and telegram_bot is not None:
            video = await self._download_media_blob(video_message, telegram_bot, default_mime_type="video/mp4")
            if video:
                videos.append(video)
        video_note = getattr(message, "video_note", None)
        if video_note and telegram_bot is not None:
            video = await self._download_media_blob(video_note, telegram_bot, default_mime_type="video/mp4")
            if video:
                videos.append(video)
        animation = getattr(message, "animation", None)
        if animation and telegram_bot is not None:
            video = await self._download_media_blob(animation, telegram_bot, default_mime_type="video/mp4")
            if video:
                videos.append(video)
        if telegram_bot is None and any((photos, voice, audio_message, video_message, video_note, animation)):
            logger.warning("Telegram media update has no bot available; skipping media download")

        metadata = {
            "update_id": raw_update.update_id,
            "message_id": message.message_id,
            "chat_type": getattr(message.chat, "type", None),
        }
        if message.from_user is not None:
            metadata["username"] = message.from_user.username

        return UserMessage(
            text=text,
            channel="telegram",
            chat_id=chat_id,
            session_chat_id=session_chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            images=images if images else None,
            audios=audios if audios else None,
            videos=videos if videos else None,
            metadata=metadata,
            raw=raw_update
        )
    
    async def _download_images(self, photos, bot) -> list[str]:
        """
        下載圖片並轉成 base64
        
        參數：
            photos: telegram photo 清單
            bot: telegram bot 實例
        
        回傳：
            list: base64 編碼的圖片清單
        """
        images = []
        
        # 取最大張的圖片
        photo = photos[-1]
        
        try:
            # 取得檔案
            file = await bot.get_file(photo.file_id)
            
            # 下載到記憶體
            file_content = await file.download_as_bytearray()
            
            # 轉 base64
            b64 = base64.b64encode(bytes(file_content)).decode('utf-8')
            
            # 偵測 MIME type
            mime_type = getattr(photo, "mime_type", None) or "image/jpeg"
            
            images.append(f"data:{mime_type};base64,{b64}")
            
        except Exception as e:
            logger.warning(f"下載圖片失敗: {e}")
        
        return images

    async def _download_audio(self, audio_obj, bot, default_mime_type: str) -> str | None:
        """Download one Telegram audio-like object and return a base64 data URL."""
        return await self._download_media_blob(audio_obj, bot, default_mime_type=default_mime_type)

    async def _download_media_blob(self, media_obj, bot, default_mime_type: str) -> str | None:
        """Download one Telegram media object and return a base64 data URL."""
        try:
            file = await bot.get_file(media_obj.file_id)
            file_content = await file.download_as_bytearray()
            b64 = base64.b64encode(bytes(file_content)).decode("utf-8")
            mime_type = getattr(media_obj, "mime_type", None) or default_mime_type
            return f"data:{mime_type};base64,{b64}"
        except Exception as e:
            logger.warning(f"下載媒體失敗: {e}")
            return None

    @staticmethod
    def _has_outbound_media(message: AssistantMessage) -> bool:
        """Return whether an assistant message carries outbound media."""
        return bool(message.images or message.voices or message.audios or message.videos)

    @classmethod
    def _outbound_media_extension(cls, mime_type: str, fallback: str) -> str:
        return cls.OUTBOUND_MEDIA_EXTENSIONS.get(mime_type.lower(), fallback)

    @classmethod
    def _coerce_outbound_media(
        cls,
        payload: str,
        *,
        default_mime_type: str,
        fallback_extension: str,
        filename_stem: str,
    ):
        """Convert an outbound media payload into a Telegram-compatible value."""
        value = str(payload or "").strip()
        if not value:
            raise ValueError("outbound media payload is empty")
        if not value.startswith("data:"):
            return value

        header, separator, encoded = value.partition(",")
        if not separator:
            raise ValueError("outbound media data URL is missing payload")
        if ";base64" not in header.lower():
            raise ValueError("outbound media data URL must be base64 encoded")

        mime_type = default_mime_type
        declared_mime_type = header[5:].split(";", 1)[0].strip()
        if declared_mime_type:
            mime_type = declared_mime_type

        try:
            media_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("outbound media data URL has invalid base64 payload") from exc

        media_file = io.BytesIO(media_bytes)
        extension = cls._outbound_media_extension(mime_type, fallback_extension)
        media_file.name = f"{filename_stem}.{extension}"
        return media_file

    async def _send_outbound_media(self, message: AssistantMessage) -> None:
        """Send media attachments carried by an assistant message."""
        assert self.app is not None

        for index, image in enumerate(message.images or [], start=1):
            payload = self._coerce_outbound_media(
                image,
                default_mime_type="image/jpeg",
                fallback_extension="jpg",
                filename_stem=f"image-{index}",
            )
            await self.app.bot.send_photo(chat_id=message.chat_id, photo=payload)

        for index, voice in enumerate(message.voices or [], start=1):
            payload = self._coerce_outbound_media(
                voice,
                default_mime_type="audio/ogg",
                fallback_extension="ogg",
                filename_stem=f"voice-{index}",
            )
            await self.app.bot.send_voice(chat_id=message.chat_id, voice=payload)

        for index, audio in enumerate(message.audios or [], start=1):
            payload = self._coerce_outbound_media(
                audio,
                default_mime_type="audio/mpeg",
                fallback_extension="mp3",
                filename_stem=f"audio-{index}",
            )
            await self.app.bot.send_audio(chat_id=message.chat_id, audio=payload)

        for index, video in enumerate(message.videos or [], start=1):
            payload = self._coerce_outbound_media(
                video,
                default_mime_type="video/mp4",
                fallback_extension="mp4",
                filename_stem=f"video-{index}",
            )
            await self.app.bot.send_video(chat_id=message.chat_id, video=payload)
    
    async def send(self, message: AssistantMessage) -> None:
        """
        把助理回覆發送到 Telegram
        
        實作 MessageAdapter 介面。
        
        參數：
            message: AssistantMessage 統一格式的回覆
        """
        if self.app is None:
            raise RuntimeError("TelegramAdapter 未啟動，請先呼叫 run()")
        
        if message.chat_id is None:
            raise ValueError("AssistantMessage 缺少 chat_id，無法發送")
        
        has_media = self._has_outbound_media(message)
        text = message.text or ""
        if not text.strip() and not has_media:
            logger.warning(
                "Telegram reply text is empty for chat {} session {}; sending fallback notice",
                message.chat_id,
                message.session_chat_id,
            )
            text = self.messages.telegram.empty_message_fallback
        max_length = 4000
        
        if text.strip():
            # 截斷過長的訊息
            if len(text) > max_length:
                original_length = len(message.text or "")
                text = text[:max_length] + f"\n\n... (訊息太長，已截斷，共 {original_length} 字)"
            
            # 轉換為 Telegram 官方支援的 HTML 子集
            html_text = self._render_telegram_html(text)
            if not html_text.strip():
                logger.warning(
                    "Telegram HTML renderer produced empty output for chat {} session {}; using escaped plain text",
                    message.chat_id,
                    message.session_chat_id,
                )
                html_text = html.escape(text)
            
            # 嘗試發送 HTML，失敗則用純文字
            try:
                await self.app.bot.send_message(
                    chat_id=message.chat_id,
                    text=html_text,
                    parse_mode="HTML"
                )
            except Exception as exc:
                logger.warning("Telegram HTML send failed, falling back to plain text: {}", exc)
                # Fallback 到純文字
                await self.app.bot.send_message(
                    chat_id=message.chat_id,
                    text=text
                )

        if has_media:
            await self._send_outbound_media(message)
    
    def _format_inline_telegram_html(self, text: str) -> str:
        """Convert inline reply markup into Telegram-safe HTML."""
        placeholders: dict[str, str] = {}

        def stash(value: str) -> str:
            key = f"@@TGPLACEHOLDER{len(placeholders)}@@"
            placeholders[key] = value
            return key

        text = re.sub(
            r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
            lambda m: stash(
                f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1))}</a>'
            ),
            text,
        )
        text = re.sub(
            r"`([^`\n]+)`",
            lambda m: stash(f"<code>{html.escape(m.group(1))}</code>"),
            text,
        )

        escaped = html.escape(text)
        patterns = [
            (r"\*\*(.+?)\*\*", r"<b>\1</b>"),
            (r"__(.+?)__", r"<b>\1</b>"),
            (r"~~(.+?)~~", r"<s>\1</s>"),
            (r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>"),
            (r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<i>\1</i>"),
        ]
        for pattern, replacement in patterns:
            escaped = re.sub(pattern, replacement, escaped)

        for key, value in placeholders.items():
            escaped = escaped.replace(key, value)

        return escaped

    def _format_inline_plain_text(self, text: str) -> str:
        """Convert inline reply markup into plain text for code blocks."""
        text = re.sub(
            r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
            lambda m: f"{m.group(1)} ({m.group(2)})",
            text,
        )
        replacements = [
            (r"`([^`\n]+)`", r"\1"),
            (r"\*\*(.+?)\*\*", r"\1"),
            (r"__(.+?)__", r"\1"),
            (r"~~(.+?)~~", r"\1"),
            (r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1"),
            (r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"\1"),
        ]
        for pattern, replacement in replacements:
            text = re.sub(pattern, replacement, text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _split_markdown_table_row(line: str) -> list[str] | None:
        """Split a Markdown table row into cells."""
        stripped = line.strip()
        if "|" not in stripped:
            return None

        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]

        parts = [part.replace("\\|", "|").strip() for part in re.split(r"(?<!\\)\|", stripped)]
        if len(parts) < 2:
            return None
        return parts

    def _is_markdown_table_separator(self, line: str, expected_columns: int) -> bool:
        """Return True when the line is a Markdown table separator row."""
        cells = self._split_markdown_table_row(line)
        if not cells or len(cells) != expected_columns:
            return False
        return all(re.match(r"^:?-{3,}:?$", cell.replace(" ", "")) for cell in cells)

    def _render_markdown_table(self, rows: list[list[str]]) -> str:
        """Render a Markdown table as a Telegram-safe preformatted block."""
        plain_rows = [
            [self._format_inline_plain_text(cell) for cell in row]
            for row in rows
        ]
        widths = [
            max(len(row[index]) for row in plain_rows)
            for index in range(len(plain_rows[0]))
        ]

        def render_row(row: list[str]) -> str:
            return " | ".join(cell.ljust(widths[index]) for index, cell in enumerate(row))

        header = render_row(plain_rows[0])
        separator = "-+-".join("-" * width for width in widths)
        body = [render_row(row) for row in plain_rows[1:]]
        table_text = "\n".join([header, separator, *body])
        return f"<pre><code>{html.escape(table_text)}</code></pre>"

    def _render_telegram_html(self, text: str) -> str:
        """Render replies into Telegram's supported HTML subset."""
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        rendered: list[str] = []
        paragraph: list[str] = []
        code_lines: list[str] = []
        in_code_block = False

        def flush_paragraph() -> None:
            if not paragraph:
                return
            rendered.append(self._format_inline_telegram_html(" ".join(part.strip() for part in paragraph if part.strip())))
            paragraph.clear()

        def flush_code_block() -> None:
            if not code_lines:
                rendered.append("<pre><code></code></pre>")
            else:
                rendered.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
            code_lines.clear()

        index = 0
        while index < len(lines):
            raw_line = lines[index]
            stripped = raw_line.strip()

            if stripped.startswith("```"):
                flush_paragraph()
                if in_code_block:
                    flush_code_block()
                    in_code_block = False
                else:
                    in_code_block = True
                index += 1
                continue

            if in_code_block:
                code_lines.append(raw_line)
                index += 1
                continue

            if not stripped:
                flush_paragraph()
                if rendered and rendered[-1] != "":
                    rendered.append("")
                index += 1
                continue

            header_cells = self._split_markdown_table_row(raw_line)
            if (
                header_cells
                and index + 1 < len(lines)
                and self._is_markdown_table_separator(lines[index + 1], len(header_cells))
            ):
                flush_paragraph()
                table_rows = [header_cells]
                index += 2
                while index < len(lines):
                    row_cells = self._split_markdown_table_row(lines[index])
                    if not row_cells or len(row_cells) != len(header_cells):
                        break
                    table_rows.append(row_cells)
                    index += 1
                rendered.append(self._render_markdown_table(table_rows))
                continue

            heading_match = re.match(r"^#{1,6}\s+(.*)$", stripped)
            if heading_match:
                flush_paragraph()
                rendered.append(f"<b>{self._format_inline_telegram_html(heading_match.group(1).strip())}</b>")
                index += 1
                continue

            bullet_match = re.match(r"^[-*]\s+(.*)$", stripped)
            if bullet_match:
                flush_paragraph()
                rendered.append(f"• {self._format_inline_telegram_html(bullet_match.group(1).strip())}")
                index += 1
                continue

            number_match = re.match(r"^(\d+)\.\s+(.*)$", stripped)
            if number_match:
                flush_paragraph()
                rendered.append(f"{number_match.group(1)}. {self._format_inline_telegram_html(number_match.group(2).strip())}")
                index += 1
                continue

            paragraph.append(raw_line)
            index += 1

        flush_paragraph()
        if in_code_block:
            flush_code_block()

        while rendered and rendered[-1] == "":
            rendered.pop()

        return "\n".join(rendered)
    
    async def _on_response(self, response: AssistantMessage, channel: str, chat_id: str | None) -> None:
        """
        Telegram channel 的 outbound handler。
        """
        meta = response.metadata or {}
        if not meta.get("interim"):
            await self._stop_typing_indicator(response.session_chat_id)
        await self.send(response)

    async def _on_error(self, session_chat_id: str, error: str) -> None:
        """Stop typing when queued processing fails."""
        await self._stop_typing_indicator(session_chat_id)
    
    async def run(self):
        """
        啟動 Telegram Bot
        
        使用 MessageQueue 時：
        - 訊息會丟到 Queue 處理
        - 回覆會透過 telegram channel handler 發送
        """
        logger.info("Preparing Telegram adapter startup: {}", self._describe_startup_config())
        if not self.bot_token:
            logger.warning("Telegram token is empty; skipping Telegram adapter startup")
            return
        elif ":" not in self.bot_token:
            logger.warning("Telegram token format looks unusual: {}", self._mask_token())

        self.app = self._build_application()
        self._log_request_timeouts()
        
        # 註冊訊息 handler
        async def handle_update(update: Update, context):
            # 轉換成統一格式
            user_msg = await self.to_user_message(update, bot=getattr(context, "bot", None))
            
            # 檢查是否是空訊息
            if not user_msg.text and not user_msg.images and not user_msg.audios and not user_msg.videos:
                return
            
            if self.mq:
                # === 新方式：走 MessageQueue ===
                self._start_typing_indicator(user_msg.session_chat_id, user_msg.chat_id)
                await self.mq.enqueue(user_msg)
            else:
                # === 舊方式：直接叫 agent（向後相容）===
                # 這裡需要傳入 agent，暫時不支援
                raise RuntimeError("請传入 mq (MessageQueue) 來啟動")
        
        from telegram.ext import MessageHandler

        self.app.add_handler(MessageHandler(self._supported_message_filters(), handle_update))

        if self.mq is None:
            raise RuntimeError("請传入 mq (MessageQueue) 來啟動")
        self.mq.register_response_handler("telegram", self._on_response)
        self.mq.on_error = self._on_error
        
        # 初始化並啟動
        stage = "initialize"
        try:
            logger.info("Initializing Telegram app (Bot API getMe)...")
            await self.app.initialize()
            bot_name = getattr(self.app.bot, "username", None) or getattr(self.app.bot, "first_name", None) or "<unknown>"
            logger.info("Telegram app initialized successfully for bot {}", bot_name)

            stage = "start"
            logger.info("Starting Telegram app runtime...")
            await self.app.start()
            logger.info("Telegram app runtime started")

            stage = "start_polling"
            logger.info(
                "Starting Telegram polling with poll_timeout={}, bootstrap_retries={}, drop_pending_updates={}",
                self._get_int("poll_timeout"),
                self._get_int("bootstrap_retries"),
                self._get_bool("drop_pending_updates"),
            )
            if self.app.updater is None:
                raise RuntimeError("Telegram updater is unavailable")
            await self.app.updater.start_polling(
                timeout=self._get_int("poll_timeout"),
                bootstrap_retries=self._get_int("bootstrap_retries"),
                drop_pending_updates=self._get_bool("drop_pending_updates"),
            )
            logger.info("Telegram polling started!")
        except (TimedOut, NetworkError) as exc:
            logger.error(
                "Telegram startup failed during {} with {}: {} | config={}",
                stage,
                type(exc).__name__,
                exc,
                self._describe_startup_config(),
            )
            await self._shutdown_app()
            raise RuntimeError(
                "Telegram startup timed out while contacting the Bot API. "
                "Check network access to api.telegram.org or increase channels.telegram timeouts in ~/.opensprite/opensprite.json."
            ) from exc
        except Exception:
            logger.exception(
                "Telegram startup failed during {} with config={}",
                stage,
                self._describe_startup_config(),
            )
            await self._shutdown_app()
            raise
        
        # 保持執行
        await asyncio.Event().wait()


# ============================================
# 使用範例
# ============================================

"""
使用方式（Queue 版）：

```python
import asyncio
from ..agent import AgentLoop
from ..config import Config
from ..llms import OpenAILLM
from ..storage import MemoryStorage
from ..bus.dispatcher import MessageQueue
from .telegram import TelegramAdapter

async def main():
    # 1. 建立 LLM
    llm = OpenAILLM(api_key="your-openai-key")
    
    # 2. 建立 Agent 設定
    config = Config.load_agent_template_config()
    
    # 3. 建立 Storage
    storage = MemoryStorage()
    
    # 4. 建立 Agent
    agent = AgentLoop(config, llm, storage)
    
    # 5. 建立 MessageQueue
    mq = MessageQueue(agent)
    
    # 6. 建立 Telegram Adapter（傳入 mq）
    telegram = TelegramAdapter(bot_token="your-telegram-token", mq=mq)
    
    # 7. 啟動（不需要callback，Queue 會自動處理回覆）
    await telegram.run()

asyncio.run(main())
```

這樣：
- 收到 Telegram 訊息 → 丟到 Queue
- Agent 處理完 → Queue 會依 `telegram` channel handler 發送回 Telegram
- 支援多訊息並行處理
"""
