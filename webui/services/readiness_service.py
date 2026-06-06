"""Safe readiness/config view-model builders."""

from __future__ import annotations

import os
from typing import Any, Mapping

from .masking import allowlisted_env_status, mask_text


def get_config_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    items = allowlisted_env_status(env)
    required = {"GCP_PROJECT_ID", "GCP_PUBSUB_SUBSCRIPTION_ID"}
    missing = [item["name"] for item in items if item["name"] in required and not item["configured"]]
    return {"items": items, "missing_required": missing, "status": "ready" if not missing else "missing"}


def get_readiness_summary(*, run_live_check: bool = False) -> dict[str, Any]:
    config = get_config_status(os.environ)
    if not run_live_check:
        return {
            "status": config["status"],
            "message": "Static readiness only; live Pub/Sub check not requested",
            "diagnostics": [],
            "config": config,
        }

    try:
        from pubsub_readiness import check_pubsub_readiness

        result = check_pubsub_readiness()
        return {
            "status": result.status,
            "exit_code": result.exit_code,
            "message": mask_text(result.message, os.environ),
            "diagnostics": [mask_text(item, os.environ) for item in result.diagnostics],
            "config": config,
        }
    except Exception as exc:  # noqa: BLE001 - safe UI diagnostic
        return {
            "status": "indeterminate",
            "message": mask_text(f"Readiness check unavailable: {exc}", os.environ),
            "diagnostics": [],
            "config": config,
        }
