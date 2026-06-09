"""Application-level settings HTTP handlers for the web adapter."""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from ..cli import update as update_cli
from ..config import Config


async def handle_settings_media(adapter: Any, request: web.Request) -> web.Response:
    try:
        payload = adapter._get_media_settings().list_media()
    except Exception as exc:
        adapter._raise_provider_settings_error(exc)
    return web.json_response(payload)


async def handle_settings_media_update(adapter: Any, request: web.Request) -> web.Response:
    body = await adapter._read_json_body(request)
    category = adapter._coerce_optional_text(body.get("category"))
    if category is None:
        raise web.HTTPBadRequest(text="category is required")
    try:
        payload = adapter._get_media_settings().update_media(
            category,
            enabled=adapter._coerce_bool(body.get("enabled"), field="enabled", default=False),
            provider_id=adapter._coerce_optional_text(body.get("provider_id")),
            model=adapter._coerce_optional_text(body.get("model")),
        )
    except Exception as exc:
        adapter._raise_provider_settings_error(exc)
    payload = adapter._reload_media_from_config(payload)
    return web.json_response(payload)


async def handle_settings_model_select(adapter: Any, request: web.Request) -> web.Response:
    body = await adapter._read_json_body(request)
    provider_id = adapter._coerce_optional_text(body.get("provider_id"))
    model = adapter._coerce_optional_text(body.get("model"))
    if provider_id is None or model is None:
        raise web.HTTPBadRequest(text="provider_id and model are required")
    try:
        payload = adapter._get_provider_settings().select_model(provider_id, model)
    except Exception as exc:
        adapter._raise_provider_settings_error(exc)
    payload = adapter._reload_agent_llm_from_config(payload)
    return web.json_response(payload)


async def handle_settings_update_status(adapter: Any, request: web.Request) -> web.Response:
    payload = await asyncio.to_thread(adapter._build_update_status_payload)
    return web.json_response(payload)


async def handle_settings_update_apply(adapter: Any, request: web.Request) -> web.Response:
    body = await adapter._read_json_body(request)
    restart = adapter._coerce_bool(body.get("restart"), field="restart", default=True)
    try:
        result = await asyncio.to_thread(update_cli.update_checkout, branch="main", install_dev=False)
    except update_cli.UpdateError as exc:
        raise web.HTTPConflict(text=str(exc)) from exc
    except Exception as exc:
        raise web.HTTPServiceUnavailable(text=str(exc)) from exc

    payload = {
        "ok": True,
        "updated": result.updated,
        "before_rev": result.before_rev,
        "before_rev_short": result.before_rev[:7],
        "after_rev": result.after_rev,
        "after_rev_short": result.after_rev[:7],
        "branch": result.branch,
        "project_root": str(result.project_root),
        "python": str(result.python_executable),
        "restart_scheduled": restart,
    }
    if restart:
        config_path = adapter._config.get("config_path") if hasattr(adapter, "_config") else adapter.config.get("config_path")
        resolved_config_path = None
        if config_path:
            from pathlib import Path

            resolved_config_path = Path(config_path).expanduser()
        asyncio.create_task(adapter._restart_gateway_after_response(config_path=resolved_config_path))
    return web.json_response(payload)


async def handle_settings_schedule(adapter: Any, request: web.Request) -> web.Response:
    try:
        payload = adapter._get_schedule_settings().get_schedule()
    except Exception as exc:
        adapter._raise_schedule_settings_error(exc)
    return web.json_response(payload)


async def handle_settings_schedule_update(adapter: Any, request: web.Request) -> web.Response:
    body = await adapter._read_json_body(request)
    try:
        payload = adapter._get_schedule_settings().update_schedule(
            default_timezone=adapter._coerce_optional_text(body.get("default_timezone")),
        )
    except Exception as exc:
        adapter._raise_schedule_settings_error(exc)
    payload = adapter._reload_schedule_from_config(payload)
    return web.json_response(payload)


async def handle_settings_network(adapter: Any, request: web.Request) -> web.Response:
    config = Config.load(adapter._get_config_path())
    return web.json_response({"network": adapter._network_payload(config)})


async def handle_settings_network_update(adapter: Any, request: web.Request) -> web.Response:
    body = await adapter._read_json_body(request)
    config_path = adapter._get_config_path()
    config = Config.load(config_path)
    config.network.http_proxy = adapter._coerce_optional_text(body.get("http_proxy"), default="") or ""
    config.network.https_proxy = adapter._coerce_optional_text(body.get("https_proxy"), default="") or ""
    config.network.no_proxy = adapter._coerce_optional_text(body.get("no_proxy"), default="") or ""
    config.save(config_path)
    adapter._apply_network_environment(config)
    return web.json_response({"network": adapter._network_payload(config), "restart_required": False})
