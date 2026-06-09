"""Core settings HTTP handlers for the web adapter."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from ..auth.credentials import CredentialStoreError, add_credential, list_credentials, remove_credential, set_capability_default, set_provider_default
from ..config.channel_settings import ChannelSettingsError
from ..config.provider_settings import ProviderSettingsError


async def handle_settings_providers(adapter: Any, request: web.Request) -> web.Response:
    try:
        payload = adapter._get_provider_settings().list_providers()
    except ProviderSettingsError as exc:
        adapter._raise_provider_settings_error(exc)
    return web.json_response(payload)


async def handle_settings_codex_auth_status(adapter: Any, request: web.Request) -> web.Response:
    from ..auth.codex import CodexAuthError, get_codex_status

    try:
        status = get_codex_status(adapter._get_app_home())
    except CodexAuthError as exc:
        return web.json_response({"provider": "openai-codex", "configured": False, "error": str(exc)}, status=400)
    return web.json_response(
        {
            "provider": "openai-codex",
            "configured": status.configured,
            "path": str(status.path),
            "expires_at": status.expires_at,
            "expired": status.expired,
            "account_id": status.account_id,
        }
    )


async def handle_settings_codex_auth_login(adapter: Any, request: web.Request) -> web.Response:
    from ..auth.codex import CodexAuthError, codex_start_device_auth

    try:
        device_auth = codex_start_device_auth()
    except CodexAuthError as exc:
        return web.json_response({"ok": False, "provider": "openai-codex", "error": str(exc)}, status=502)
    return web.json_response(
        {
            "ok": True,
            "provider": "openai-codex",
            "mode": "web_device_code",
            "verification_uri": device_auth.verification_uri,
            "user_code": device_auth.user_code,
            "device_auth_id": device_auth.device_auth_id,
            "interval": device_auth.poll_interval,
            "expires_in": device_auth.expires_in,
            "message": "Open the verification URL and enter the code to complete OpenAI Codex login.",
        }
    )


async def handle_settings_codex_auth_poll(adapter: Any, request: web.Request) -> web.Response:
    from ..auth.codex import CodexAuthError, codex_poll_device_auth, get_codex_status

    body = await adapter._read_json_body(request)
    try:
        result = codex_poll_device_auth(
            adapter._coerce_optional_text(body.get("device_auth_id")),
            adapter._coerce_optional_text(body.get("user_code")),
            app_home=adapter._get_app_home(),
        )
        status = get_codex_status(adapter._get_app_home()) if result.status == "authorized" else None
    except CodexAuthError as exc:
        return web.json_response({"ok": False, "provider": "openai-codex", "error": str(exc)}, status=400)
    payload: dict[str, Any] = {"ok": True, "provider": "openai-codex", "status": result.status}
    if status is not None:
        payload["auth"] = {
            "configured": status.configured,
            "path": str(status.path),
            "expires_at": status.expires_at,
            "expired": status.expired,
            "account_id": status.account_id,
        }
        payload = adapter._reload_agent_llm_from_config(payload, force=True)
    return web.json_response(payload)


async def handle_settings_codex_auth_logout(adapter: Any, request: web.Request) -> web.Response:
    from ..auth.codex import codex_auth_path, delete_codex_token

    app_home = adapter._get_app_home()
    path = codex_auth_path(app_home)
    removed = delete_codex_token(app_home)
    return web.json_response({"ok": True, "provider": "openai-codex", "removed": removed, "path": str(path)})


async def handle_settings_copilot_auth_status(adapter: Any, request: web.Request) -> web.Response:
    from ..auth.copilot import CopilotAuthError, get_copilot_status

    try:
        status = get_copilot_status(adapter._get_app_home())
    except CopilotAuthError as exc:
        return web.json_response({"provider": "copilot", "configured": False, "error": str(exc)}, status=400)
    return web.json_response({"provider": "copilot", "configured": status.configured, "path": str(status.path)})


async def handle_settings_copilot_auth_login(adapter: Any, request: web.Request) -> web.Response:
    from ..auth.copilot import CopilotAuthError, copilot_start_device_auth

    try:
        device_auth = copilot_start_device_auth()
    except CopilotAuthError as exc:
        return web.json_response({"ok": False, "provider": "copilot", "error": str(exc)}, status=502)
    return web.json_response(
        {
            "ok": True,
            "provider": "copilot",
            "mode": "web_device_code",
            "verification_uri": device_auth.verification_uri,
            "user_code": device_auth.user_code,
            "device_code": device_auth.device_code,
            "interval": device_auth.poll_interval,
            "expires_in": device_auth.expires_in,
        }
    )


async def handle_settings_copilot_auth_poll(adapter: Any, request: web.Request) -> web.Response:
    from ..auth.copilot import CopilotAuthError, copilot_poll_device_auth, get_copilot_status

    body = await adapter._read_json_body(request)
    try:
        result = copilot_poll_device_auth(adapter._coerce_optional_text(body.get("device_code")), app_home=adapter._get_app_home())
        status = get_copilot_status(adapter._get_app_home()) if result.status == "authorized" else None
    except CopilotAuthError as exc:
        return web.json_response({"ok": False, "provider": "copilot", "error": str(exc)}, status=400)
    payload: dict[str, Any] = {"ok": True, "provider": "copilot", "status": result.status}
    if status is not None:
        payload["auth"] = {"configured": status.configured, "path": str(status.path)}
        payload = adapter._reload_agent_llm_from_config(payload, force=True)
    return web.json_response(payload)


async def handle_settings_copilot_auth_logout(adapter: Any, request: web.Request) -> web.Response:
    from ..auth.copilot import copilot_auth_path, delete_copilot_token

    app_home = adapter._get_app_home()
    path = copilot_auth_path(app_home)
    removed = delete_copilot_token(app_home)
    return web.json_response({"ok": True, "provider": "copilot", "removed": removed, "path": str(path)})


async def handle_settings_channels(adapter: Any, request: web.Request) -> web.Response:
    try:
        payload = adapter._get_channel_settings().list_channels()
    except ChannelSettingsError as exc:
        adapter._raise_channel_settings_error(exc)
    payload = await adapter._reload_channels_from_config(payload)
    return web.json_response(payload)


async def handle_settings_channel_create(adapter: Any, request: web.Request) -> web.Response:
    body = await adapter._read_json_body(request)
    channel_type = adapter._coerce_optional_text(body.get("type"))
    if channel_type is None:
        raise web.HTTPBadRequest(text="type is required")
    try:
        payload = adapter._get_channel_settings().create_channel(
            channel_type,
            name=adapter._coerce_optional_text(body.get("name")),
            token=adapter._coerce_optional_text(body.get("token")),
        )
    except ChannelSettingsError as exc:
        adapter._raise_channel_settings_error(exc)
    payload = await adapter._reload_channels_from_config(payload)
    return web.json_response(payload)


async def handle_settings_channel_update(adapter: Any, request: web.Request) -> web.Response:
    channel_id = adapter._coerce_optional_text(request.match_info.get("channel_id"))
    if channel_id is None:
        raise web.HTTPBadRequest(text="channel_id is required")
    body = await adapter._read_json_body(request)
    try:
        payload = adapter._get_channel_settings().update_channel(
            channel_id,
            enabled=body.get("enabled") if "enabled" in body else None,
            settings=body.get("settings", {}),
        )
    except ChannelSettingsError as exc:
        adapter._raise_channel_settings_error(exc)
    payload = await adapter._reload_channels_from_config(payload)
    return web.json_response(payload)


async def handle_settings_channel_connect(adapter: Any, request: web.Request) -> web.Response:
    channel_id = adapter._coerce_optional_text(request.match_info.get("channel_id"))
    if channel_id is None:
        raise web.HTTPBadRequest(text="channel_id is required")
    body = await adapter._read_json_body(request)
    try:
        payload = adapter._get_channel_settings().connect_channel(
            channel_id,
            token=adapter._coerce_optional_text(body.get("token")),
            name=adapter._coerce_optional_text(body.get("name")),
        )
    except ChannelSettingsError as exc:
        adapter._raise_channel_settings_error(exc)
    payload = await adapter._reload_channels_from_config(payload)
    return web.json_response(payload)


async def handle_settings_channel_disconnect(adapter: Any, request: web.Request) -> web.Response:
    channel_id = adapter._coerce_optional_text(request.match_info.get("channel_id"))
    if channel_id is None:
        raise web.HTTPBadRequest(text="channel_id is required")
    try:
        payload = adapter._get_channel_settings().disconnect_channel(channel_id)
    except ChannelSettingsError as exc:
        adapter._raise_channel_settings_error(exc)
    payload = await adapter._reload_channels_from_config(payload)
    return web.json_response(payload)


async def handle_settings_provider_connect(adapter: Any, request: web.Request) -> web.Response:
    provider_id = adapter._coerce_optional_text(request.match_info.get("provider_id"))
    if provider_id is None:
        raise web.HTTPBadRequest(text="provider_id is required")
    body = await adapter._read_json_body(request)
    try:
        payload = adapter._get_provider_settings().connect_provider(
            provider_id,
            api_key=adapter._coerce_optional_text(body.get("api_key")),
            base_url=adapter._coerce_optional_text(body.get("base_url")),
            name=adapter._coerce_optional_text(body.get("name")),
        )
    except ProviderSettingsError as exc:
        adapter._raise_provider_settings_error(exc)
    return web.json_response(payload)


async def handle_settings_provider_disconnect(adapter: Any, request: web.Request) -> web.Response:
    provider_id = adapter._coerce_optional_text(request.match_info.get("provider_id"))
    if provider_id is None:
        raise web.HTTPBadRequest(text="provider_id is required")
    try:
        payload = adapter._get_provider_settings().disconnect_provider(provider_id)
    except ProviderSettingsError as exc:
        adapter._raise_provider_settings_error(exc)
    payload = adapter._reload_agent_llm_from_config(payload, force=True)
    return web.json_response(payload)


async def handle_settings_credentials(adapter: Any, request: web.Request) -> web.Response:
    provider = adapter._coerce_optional_text(request.query.get("provider"))
    try:
        credentials = list_credentials(provider, app_home=adapter._get_config_path().parent)
    except CredentialStoreError as exc:
        adapter._raise_credential_store_error(exc)
    return web.json_response({"credentials": credentials})


async def handle_settings_credential_create(adapter: Any, request: web.Request) -> web.Response:
    body = await adapter._read_json_body(request)
    provider = adapter._coerce_optional_text(body.get("provider"))
    secret = adapter._coerce_optional_text(body.get("secret")) or adapter._coerce_optional_text(body.get("api_key"))
    if provider is None or secret is None:
        raise web.HTTPBadRequest(text="provider and secret are required")
    scopes = body.get("scopes")
    if not isinstance(scopes, list):
        scopes = None
    try:
        credential = add_credential(
            provider,
            secret,
            label=adapter._coerce_optional_text(body.get("label")),
            auth_type=adapter._coerce_optional_text(body.get("auth_type"), default="api_key") or "api_key",
            base_url=adapter._coerce_optional_text(body.get("base_url")),
            scopes=scopes,
            app_home=adapter._get_config_path().parent,
        )
    except CredentialStoreError as exc:
        adapter._raise_credential_store_error(exc)
    return web.json_response({"ok": True, "credential": credential})


async def handle_settings_credential_delete(adapter: Any, request: web.Request) -> web.Response:
    provider = adapter._coerce_optional_text(request.match_info.get("provider"))
    credential_id = adapter._coerce_optional_text(request.match_info.get("credential_id"))
    if provider is None or credential_id is None:
        raise web.HTTPBadRequest(text="provider and credential_id are required")
    try:
        payload = remove_credential(provider, credential_id, app_home=adapter._get_config_path().parent)
        cleanup = adapter._get_provider_settings().remove_credential_references(provider, credential_id)
        payload.update(cleanup)
    except CredentialStoreError as exc:
        adapter._raise_credential_store_error(exc)
    except ProviderSettingsError as exc:
        adapter._raise_provider_settings_error(exc)
    payload = adapter._reload_agent_llm_from_config(payload, force=bool(payload.get("restart_required")))
    return web.json_response(payload)


async def handle_settings_credential_default(adapter: Any, request: web.Request) -> web.Response:
    body = await adapter._read_json_body(request)
    provider = adapter._coerce_optional_text(body.get("provider"))
    capability = adapter._coerce_optional_text(body.get("capability"))
    credential_id = adapter._coerce_optional_text(body.get("credential_id"))
    if credential_id is None or (provider is None and capability is None):
        raise web.HTTPBadRequest(text="credential_id plus provider or capability is required")
    try:
        if provider is not None:
            credential = set_provider_default(provider, credential_id, app_home=adapter._get_config_path().parent)
        else:
            credential = set_capability_default(capability or "", credential_id, app_home=adapter._get_config_path().parent)
    except CredentialStoreError as exc:
        adapter._raise_credential_store_error(exc)
    return web.json_response({"ok": True, "credential": credential})


async def handle_settings_provider_credential(adapter: Any, request: web.Request) -> web.Response:
    provider_id = adapter._coerce_optional_text(request.match_info.get("provider_id"))
    if provider_id is None:
        raise web.HTTPBadRequest(text="provider_id is required")
    body = await adapter._read_json_body(request)
    credential_id = adapter._coerce_optional_text(body.get("credential_id"))
    if credential_id is None:
        raise web.HTTPBadRequest(text="credential_id is required")
    try:
        payload = adapter._get_provider_settings().set_provider_credential(provider_id, credential_id)
    except ProviderSettingsError as exc:
        adapter._raise_provider_settings_error(exc)
    payload = adapter._reload_agent_llm_from_config(payload, force=True)
    return web.json_response(payload)


async def handle_settings_models(adapter: Any, request: web.Request) -> web.Response:
    try:
        payload = adapter._get_provider_settings().list_models()
    except ProviderSettingsError as exc:
        adapter._raise_provider_settings_error(exc)
    return web.json_response(payload)
