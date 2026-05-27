"""Deterministic harness profile evaluation helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..agent.harness_inventory import SENSOR_IDS_BY_TASK_TYPE
from ..agent.harness_policy import HarnessPolicyService
from ..agent.harness_profile import HarnessProfileService
from ..agent.task_contract import TaskContractService
from ..agent.task_intent import TaskIntentService


HARNESS_PROFILE_EVAL_CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "chat_question",
        "label": "Plain chat question",
        "prompt": "Why does an agent harness improve reliability?",
        "expected_profile": "chat",
        "expected_profile_task_type": "question",
        "expected_policy": "chat_read_policy",
        "expected_verification_policy": "none",
        "expected_continuation_policy": "minimal",
        "expected_contract_task_type": "pure_answer",
        "expected_profile_tool_groups": (),
        "expected_profile_evidence": (),
        "expected_approval_risks": (),
        "expected_contract_requirement_kinds": (),
        "expected_contract_tool_groups": (),
        "expected_contract_acceptance_kinds": (),
        "expected_scenario_check": "chat_allows_no_tool_final",
    },
    {
        "id": "research_sources",
        "label": "Source-grounded web research",
        "prompt": "Search the web for the latest OpenSprite release and cite sources.",
        "expected_profile": "research",
        "expected_profile_task_type": "web_research",
        "expected_policy": "research_source_policy",
        "expected_verification_policy": "source_grounded",
        "expected_continuation_policy": "bounded_with_source_fetch",
        "expected_contract_task_type": "web_research",
        "expected_profile_tool_groups": ("web_research",),
        "expected_profile_evidence": ("web_source", "source_reference"),
        "expected_approval_risks": ("external_side_effect",),
        "expected_contract_requirement_kinds": ("tool_group",),
        "expected_contract_tool_groups": ("web_research",),
        "expected_contract_acceptance_kinds": ("source_artifact", "source_detail", "source_reference"),
        "expected_scenario_check": "research_requires_source_grounding",
    },
    {
        "id": "coding_change",
        "label": "Workspace code change",
        "prompt": "Please fix the failing pytest in src/opensprite/agent/task_intent.py and run tests.",
        "expected_profile": "coding",
        "expected_profile_task_type": "workspace_change",
        "expected_policy": "workspace_change_policy",
        "expected_verification_policy": "focused_if_possible",
        "expected_continuation_policy": "bounded_with_verification",
        "expected_contract_task_type": "code_change",
        "expected_profile_tool_groups": ("workspace_read", "workspace_write"),
        "expected_profile_evidence": ("file_change",),
        "expected_approval_risks": ("external_side_effect", "configuration"),
        "expected_contract_requirement_kinds": ("tool_group", "file_change", "verification"),
        "expected_contract_tool_groups": ("workspace_read", "verification"),
        "expected_contract_acceptance_kinds": ("verification_or_gap", "substantive_final_answer"),
        "expected_scenario_check": "coding_change_requires_verification_or_gap",
    },
    {
        "id": "media_artifact",
        "label": "Media artifact extraction",
        "prompt": "Please OCR this screenshot and summarize the text.",
        "images": ("workspace/images/screenshot.png",),
        "expected_profile": "media",
        "expected_profile_task_type": "media_extraction",
        "expected_policy": "media_artifact_policy",
        "expected_verification_policy": "artifact_required",
        "expected_continuation_policy": "bounded",
        "expected_contract_task_type": "media_extraction",
        "expected_profile_tool_groups": ("media",),
        "expected_profile_evidence": ("media_artifact",),
        "expected_approval_risks": (),
        "expected_contract_requirement_kinds": ("resource_coverage",),
        "expected_contract_tool_groups": ("image_text",),
        "expected_contract_acceptance_kinds": ("media_artifact", "substantive_final_answer"),
        "expected_scenario_check": "media_requires_artifact",
    },
    {
        "id": "operations_approval",
        "label": "Approval-bounded operations",
        "prompt": "Update the MCP server configuration and restart the service after approval.",
        "expected_profile": "ops",
        "expected_profile_task_type": "operations",
        "expected_policy": "operations_approval_policy",
        "expected_verification_policy": "validate_or_report",
        "expected_continuation_policy": "approval_bounded",
        "expected_contract_task_type": "operations",
        "expected_profile_tool_groups": ("workspace_read",),
        "expected_profile_evidence": ("audit_trace",),
        "expected_approval_risks": ("external_side_effect", "configuration", "mcp"),
        "expected_contract_requirement_kinds": (),
        "expected_contract_tool_groups": (),
        "expected_contract_acceptance_kinds": ("operation_report", "substantive_final_answer"),
        "expected_scenario_check": "ops_requires_audit_or_remaining_risk",
    },
)


def run_harness_profile_eval(cases: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Run deterministic harness profile cases without calling an LLM."""
    selected_cases = HARNESS_PROFILE_EVAL_CASES if cases is None else cases
    evaluated = [evaluate_harness_profile_case(case) for case in selected_cases]
    return _summarize_cases(evaluated)


def evaluate_harness_profile_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one deterministic harness profile case."""
    prompt = _string(case.get("prompt"))
    images = _string_sequence(case.get("images"))
    audios = _string_sequence(case.get("audios"))
    videos = _string_sequence(case.get("videos"))

    intent = TaskIntentService().classify(prompt, images=images, audios=audios, videos=videos)
    profile = HarnessProfileService().select(intent)
    policy = HarnessPolicyService().select(profile)
    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=prompt,
        current_image_files=list(images),
        current_audio_files=list(audios),
        current_video_files=list(videos),
        harness_profile=profile,
    )

    checks = [
        _expect_equal("profile", "Harness profile matches", profile.name, case.get("expected_profile")),
        _expect_equal(
            "profile_task_type",
            "Harness profile task type matches",
            profile.task_type,
            case.get("expected_profile_task_type"),
        ),
        _expect_equal("policy", "Harness policy matches", policy.name, case.get("expected_policy")),
        _expect_equal(
            "verification_policy",
            "Verification policy matches",
            profile.verification_policy,
            case.get("expected_verification_policy"),
        ),
        _expect_equal(
            "continuation_policy",
            "Continuation policy matches",
            profile.continuation_policy,
            case.get("expected_continuation_policy"),
        ),
        _expect_equal(
            "contract_task_type",
            "Task contract type matches",
            contract.task_type,
            case.get("expected_contract_task_type"),
        ),
        _expect_contains_all(
            "profile_tool_groups",
            "Harness profile declares expected tool groups",
            profile.required_tool_groups,
            case.get("expected_profile_tool_groups"),
        ),
        _expect_contains_all(
            "profile_evidence",
            "Harness profile declares expected evidence",
            profile.required_evidence,
            case.get("expected_profile_evidence"),
        ),
        _expect_contains_all(
            "approval_risks",
            "Harness profile declares expected approval risks",
            profile.approval_required_risk_levels,
            case.get("expected_approval_risks"),
        ),
        _expect_contains_all(
            "contract_requirement_kinds",
            "Task contract contains expected requirement kinds",
            [item.kind for item in contract.requirements],
            case.get("expected_contract_requirement_kinds"),
        ),
        _expect_contains_all(
            "contract_tool_groups",
            "Task contract contains expected tool groups",
            [item.tool_group for item in contract.requirements if item.tool_group],
            case.get("expected_contract_tool_groups"),
        ),
        _expect_contains_all(
            "contract_acceptance_kinds",
            "Task contract contains expected acceptance criteria",
            [item.kind for item in contract.acceptance_criteria],
            case.get("expected_contract_acceptance_kinds"),
        ),
        _expect_scenario(case.get("expected_scenario_check"), profile, policy, contract),
        _expect_non_empty(
            "expected_sensors",
            "Harness inventory declares expected sensors",
            SENSOR_IDS_BY_TASK_TYPE.get(profile.task_type, ()),
        ),
    ]

    return {
        "id": _string(case.get("id"), default="case"),
        "label": _string(case.get("label")),
        "prompt": prompt,
        "ok": all(check["ok"] for check in checks),
        "score": _score(checks),
        "checks": checks,
        "intent": intent.to_metadata(),
        "profile": profile.to_metadata(),
        "policy": policy.to_metadata(),
        "contract": contract.to_metadata(),
        "expected_sensor_ids": list(SENSOR_IDS_BY_TASK_TYPE.get(profile.task_type, ())),
    }


def _summarize_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    total_checks = sum(len(case["checks"]) for case in cases)
    passed_checks = sum(1 for case in cases for check in case["checks"] if check["ok"])
    return {
        "ok": all(case["ok"] for case in cases),
        "live": False,
        "kind": "harness_profiles",
        "cases": cases,
        "summary": {
            "passed_cases": sum(1 for case in cases if case["ok"]),
            "total_cases": len(cases),
            "passed_checks": passed_checks,
            "total_checks": total_checks,
        },
    }


def _expect_equal(id_: str, description: str, observed: Any, expected: Any) -> dict[str, Any]:
    observed_text = _string(observed)
    expected_text = _string(expected)
    ok = observed_text == expected_text
    return _check(
        id_,
        description,
        ok,
        f"Expected {expected_text or '-'}, observed {observed_text or '-'}." if not ok else f"Observed {observed_text or '-'}.",
    )


def _expect_contains_all(id_: str, description: str, observed: Any, expected: Any) -> dict[str, Any]:
    observed_values = set(_string_sequence(observed))
    expected_values = set(_string_sequence(expected))
    missing = sorted(expected_values - observed_values)
    return _check(
        id_,
        description,
        not missing,
        f"Missing {', '.join(missing)}." if missing else f"Observed {', '.join(sorted(observed_values)) or '-'}.",
    )


def _expect_non_empty(id_: str, description: str, observed: Any) -> dict[str, Any]:
    observed_values = _string_sequence(observed)
    return _check(
        id_,
        description,
        bool(observed_values),
        f"Observed {', '.join(observed_values) or '-'}.",
    )


def _check(id_: str, description: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"id": id_, "description": description, "ok": bool(ok), "detail": detail}


def _expect_scenario(expected: Any, profile: Any, policy: Any, contract: Any) -> dict[str, Any]:
    scenario = _string(expected, default="scenario")
    acceptance_kinds = {item.kind for item in contract.acceptance_criteria}
    requirement_kinds = {item.kind for item in contract.requirements}
    policy_approval = set(getattr(policy, "approval_required_risk_levels", ()) or ())
    scenario_results = {
        "chat_allows_no_tool_final": contract.allow_no_tool_final is True and profile.name == "chat",
        "research_requires_source_grounding": {"source_artifact", "source_detail", "source_reference"}.issubset(acceptance_kinds),
        "coding_change_requires_verification_or_gap": "file_change" in requirement_kinds and "verification_or_gap" in acceptance_kinds,
        "media_requires_artifact": "media_artifact" in acceptance_kinds and bool(contract.selected_resources),
        "ops_requires_audit_or_remaining_risk": "operation_report" in acceptance_kinds and {"configuration", "mcp"}.issubset(policy_approval),
    }
    ok = bool(scenario_results.get(scenario, False))
    return _check(
        "scenario",
        "Scenario-level harness behavior matches",
        ok,
        f"Scenario {scenario} {'passed' if ok else 'failed'}.",
    )


def _score(checks: list[dict[str, Any]]) -> dict[str, int]:
    return {"passed": sum(1 for check in checks if check["ok"]), "total": len(checks)}


def _string(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _string_sequence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (_string(value),) if _string(value) else ()
    if isinstance(value, Sequence):
        return tuple(item for item in (_string(item) for item in value) if item)
    return (_string(value),) if _string(value) else ()
