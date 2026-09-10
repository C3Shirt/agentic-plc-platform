from __future__ import annotations

from agentic_plc.contracts.events import DeceptionPlan


class DeceptionPlanValidator:
    """Validates the narrow actions an asynchronous agent may propose."""

    ALLOWED_ACTIONS = frozenset(
        {
            "expose_existing_artifact",
            "publish_maintenance_note",
            "select_existing_fault_scenario",
            "adjust_noncritical_narrative",
        }
    )

    def validate(self, plan: DeceptionPlan) -> None:
        if plan.action not in self.ALLOWED_ACTIONS:
            raise ValueError(f"agent action is not allowed: {plan.action}")
        if not plan.actor_id.strip():
            raise ValueError("actor_id is required")
        if not plan.target.strip():
            raise ValueError("target is required")
        if not 1 <= plan.ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be between 1 and 3600")

