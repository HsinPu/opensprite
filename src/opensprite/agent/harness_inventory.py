"""Canonical inventory of harness profiles, policies, and expected sensors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .harness_policy import HarnessPolicyService
from .harness_profile import HarnessProfile, preview_harness_profiles


SENSOR_IDS_BY_TASK_TYPE: dict[str, tuple[str, ...]] = {
    "conversation": ("chat.no_unexpected_tools", "completion.final_answer"),
    "question": ("chat.no_unexpected_tools", "completion.final_answer"),
    "web_research": ("research.source_coverage", "research.freshness", "completion.source_grounding"),
    "workspace_analysis": ("coding.workspace_evidence", "completion.verification_or_gap"),
    "workspace_change": ("coding.file_change", "coding.verification", "completion.change_summary"),
    "media_extraction": ("media.artifact", "completion.media_summary"),
    "operations": ("ops.audit_trace", "ops.approval_boundary", "completion.operation_report"),
}


@dataclass(frozen=True)
class HarnessInventoryItem:
    """One representative harness shape used for scoring, UI, and evals."""

    key: str
    profile: HarnessProfile
    policy_name: str
    expected_sensor_ids: tuple[str, ...]

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe inventory entry."""
        return {
            "key": self.key,
            "profile": self.profile.to_metadata(),
            "policy_name": self.policy_name,
            "expected_sensor_ids": list(self.expected_sensor_ids),
        }


def build_harness_inventory() -> tuple[HarnessInventoryItem, ...]:
    """Return the canonical harness inventory derived from preview profiles."""
    policy_service = HarnessPolicyService()
    items: list[HarnessInventoryItem] = []
    for profile in preview_harness_profiles():
        policy = policy_service.select(profile)
        items.append(
            HarnessInventoryItem(
                key=f"{profile.name}:{profile.task_type}",
                profile=profile,
                policy_name=policy.name,
                expected_sensor_ids=SENSOR_IDS_BY_TASK_TYPE[profile.task_type],
            )
        )
    return tuple(items)


def expected_sensor_ids_for_task_type(task_type: str) -> tuple[str, ...]:
    """Return the expected sensor ids for one harness task type."""
    return SENSOR_IDS_BY_TASK_TYPE.get(task_type, ())


def harness_inventory_payload() -> dict[str, Any]:
    """Return a stable payload for debug exports, evals, and future UI wiring."""
    items = build_harness_inventory()
    return {
        "schema_version": 1,
        "kind": "harness_inventory",
        "items": [item.to_metadata() for item in items],
    }
