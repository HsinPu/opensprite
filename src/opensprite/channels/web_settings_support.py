"""Settings service and error helpers for the web adapter."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from ..auth.credentials import CredentialNotFoundError, CredentialStoreError
from ..config.media_settings import MediaSettingsService
from ..config.mcp_settings import MCPSettingsError, MCPSettingsNotFound, MCPSettingsService, MCPSettingsValidationError
from ..config.provider_settings import (
    ProviderSettingsConflict,
    ProviderSettingsError,
    ProviderSettingsNotFound,
    ProviderSettingsService,
    ProviderSettingsValidationError,
)
from ..config.channel_settings import (
    ChannelSettingsError,
    ChannelSettingsNotFound,
    ChannelSettingsService,
    ChannelSettingsValidationError,
)
from ..config.schedule_settings import (
    ScheduleSettingsError,
    ScheduleSettingsNotFound,
    ScheduleSettingsService,
    ScheduleSettingsValidationError,
)


def get_provider_settings(adapter: Any) -> ProviderSettingsService:
    return ProviderSettingsService(adapter._get_config_path())


def get_channel_settings(adapter: Any) -> ChannelSettingsService:
    return ChannelSettingsService(adapter._get_config_path())


def get_schedule_settings(adapter: Any) -> ScheduleSettingsService:
    return ScheduleSettingsService(adapter._get_config_path())


def get_mcp_settings(adapter: Any) -> MCPSettingsService:
    return MCPSettingsService(adapter._get_config_path())


def get_media_settings(adapter: Any) -> MediaSettingsService:
    return MediaSettingsService(adapter._get_config_path())


async def read_json_body(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise web.HTTPBadRequest(text="Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="Request body must be a JSON object")
    return payload


def raise_provider_settings_error(exc: ProviderSettingsError) -> None:
    if isinstance(exc, ProviderSettingsValidationError):
        raise web.HTTPBadRequest(text=str(exc)) from exc
    if isinstance(exc, ProviderSettingsNotFound):
        raise web.HTTPNotFound(text=str(exc)) from exc
    if isinstance(exc, ProviderSettingsConflict):
        raise web.HTTPConflict(text=str(exc)) from exc
    raise web.HTTPServiceUnavailable(text=str(exc)) from exc


def raise_channel_settings_error(exc: ChannelSettingsError) -> None:
    if isinstance(exc, ChannelSettingsValidationError):
        raise web.HTTPBadRequest(text=str(exc)) from exc
    if isinstance(exc, ChannelSettingsNotFound):
        raise web.HTTPNotFound(text=str(exc)) from exc
    raise web.HTTPServiceUnavailable(text=str(exc)) from exc


def raise_credential_store_error(exc: CredentialStoreError) -> None:
    if isinstance(exc, CredentialNotFoundError):
        raise web.HTTPNotFound(text=str(exc)) from exc
    raise web.HTTPBadRequest(text=str(exc)) from exc


def raise_schedule_settings_error(exc: ScheduleSettingsError) -> None:
    if isinstance(exc, ScheduleSettingsValidationError):
        raise web.HTTPBadRequest(text=str(exc)) from exc
    if isinstance(exc, ScheduleSettingsNotFound):
        raise web.HTTPNotFound(text=str(exc)) from exc
    raise web.HTTPServiceUnavailable(text=str(exc)) from exc


def raise_mcp_settings_error(exc: MCPSettingsError) -> None:
    if isinstance(exc, MCPSettingsValidationError):
        raise web.HTTPBadRequest(text=str(exc)) from exc
    if isinstance(exc, MCPSettingsNotFound):
        raise web.HTTPNotFound(text=str(exc)) from exc
    raise web.HTTPServiceUnavailable(text=str(exc)) from exc
