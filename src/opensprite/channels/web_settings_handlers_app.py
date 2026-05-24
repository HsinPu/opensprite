"""Application-level settings HTTP handlers for the web adapter."""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from ..cli import update as update_cli
from ..config import Config
from ..ops import OperationAuditRecord


async def handle_settings_llm(adapter: Any, request: web.Request) -> web.Response:
    config = Config.load(adapter._get_config_path())
    return web.json_response({"llm": adapter._llm_payload(config)})


async def handle_settings_llm_update(adapter: Any, request: web.Request) -> web.Response:
    body = await adapter._read_json_body(request)
    config_path = adapter._get_config_path()
    config = Config.load(config_path)
    decoding_mode = adapter._coerce_optional_text(body.get("decoding_mode"))
    if decoding_mode:
        if decoding_mode == "custom":
            decoding = body.get("decoding")
            if decoding is not None and not isinstance(decoding, dict):
                raise web.HTTPBadRequest(text="decoding must be a JSON object")
            adapter._apply_custom_llm_decoding(config, decoding or {})
        else:
            adapter._apply_llm_decoding_preset(config, decoding_mode)
    elif "pass_decoding_params" in body:
        config.llm.pass_decoding_params = adapter._coerce_bool(
            body.get("pass_decoding_params"),
            field="pass_decoding_params",
            default=config.llm.pass_decoding_params,
        )
    if "semantic_contract_classifier_enabled" in body:
        config.agent.semantic_contract_classifier_enabled = adapter._coerce_bool(
            body.get("semantic_contract_classifier_enabled"),
            field="semantic_contract_classifier_enabled",
            default=config.agent.semantic_contract_classifier_enabled,
        )
    if "semantic_contract_classifier_confidence_threshold" in body:
        config.agent.semantic_contract_classifier_confidence_threshold = adapter._coerce_float_range(
            body.get("semantic_contract_classifier_confidence_threshold"),
            field="semantic_contract_classifier_confidence_threshold",
            default=config.agent.semantic_contract_classifier_confidence_threshold,
            minimum=0.0,
            maximum=1.0,
        )
    config.save(config_path)
    payload = {"llm": adapter._llm_payload(config), "restart_required": True}
    payload = adapter._reload_agent_llm_from_config(payload, force=True)
    agent = adapter._get_agent()
    if agent is not None:
        agent.config = config.agent
        llm_calls = getattr(agent, "llm_calls", None)
        if llm_calls is not None:
            llm_calls.config = config.agent
    return web.json_response(payload)


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


async def handle_settings_permissions(adapter: Any, request: web.Request) -> web.Response:
    config = Config.load(adapter._get_config_path())
    return web.json_response({"permissions": adapter._permissions_payload(config)})


async def handle_settings_harness_policy_preview(adapter: Any, request: web.Request) -> web.Response:
    config = Config.load(adapter._get_config_path())
    return web.json_response({"harness_policy_preview": adapter._harness_policy_preview_payload(config)})


async def handle_settings_permissions_update(adapter: Any, request: web.Request) -> web.Response:
    body = await adapter._read_json_body(request)
    config_path = adapter._get_config_path()
    config = Config.load(config_path)
    permissions = config.tools.permissions
    before_permissions = adapter._permissions_payload(config)
    permissions.enabled = adapter._coerce_bool(body.get("enabled"), field="enabled", default=permissions.enabled)
    permissions.approval_mode = adapter._coerce_approval_mode(body.get("approval_mode", permissions.approval_mode))
    permissions.approval_timeout_seconds = adapter._coerce_float_range(
        body.get("approval_timeout_seconds"),
        field="approval_timeout_seconds",
        default=permissions.approval_timeout_seconds,
        minimum=1.0,
        maximum=86400.0,
    )
    permissions.allowed_tools = adapter._coerce_text_list(body.get("allowed_tools"), field="allowed_tools", default=permissions.allowed_tools)
    if not permissions.allowed_tools:
        permissions.allowed_tools = ["*"]
    permissions.denied_tools = adapter._coerce_text_list(body.get("denied_tools"), field="denied_tools", default=permissions.denied_tools)
    permissions.allowed_risk_levels = adapter._coerce_risk_level_list(body.get("allowed_risk_levels"), field="allowed_risk_levels", default=permissions.allowed_risk_levels)
    permissions.denied_risk_levels = adapter._coerce_risk_level_list(body.get("denied_risk_levels"), field="denied_risk_levels", default=permissions.denied_risk_levels)
    permissions.approval_required_tools = adapter._coerce_text_list(body.get("approval_required_tools"), field="approval_required_tools", default=permissions.approval_required_tools)
    permissions.approval_required_risk_levels = adapter._coerce_risk_level_list(body.get("approval_required_risk_levels"), field="approval_required_risk_levels", default=permissions.approval_required_risk_levels)
    permissions.profile_overrides = adapter._coerce_permission_profile_overrides(body.get("profile_overrides"), default=permissions.profile_overrides)
    config.save(config_path)
    payload = {"permissions": adapter._permissions_payload(config), "restart_required": True}
    payload = adapter._reload_permissions_from_config(payload)
    payload["operation_audit"] = OperationAuditRecord(
        operation_type="settings.permissions.update",
        target="tools.permissions",
        before=before_permissions,
        after=payload["permissions"],
        validation={"runtime_reloaded": bool(payload.get("runtime_reloaded")), "restart_required": bool(payload.get("restart_required"))},
        rollback_available=True,
    ).to_metadata()
    return web.json_response(payload)
