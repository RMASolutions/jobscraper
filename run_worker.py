#!/usr/bin/env python
import asyncio
import json
import logging
import os
from uuid import uuid4

from src.core.config import settings
from src.db.connection import init_db
from src.workflows import workflow_registry

# Register example workflows
import src.workflows.examples  # noqa: F401


DEFAULT_WORKFLOWS = [
    "connecting_expertise",
    "pro_unity",
    "bnppf_jobs",
    "elia_jobs",
    "ag_insurance",
]


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_workflow_list() -> list[str]:
    raw = os.getenv("WORKFLOW_LIST", "").strip()
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return DEFAULT_WORKFLOWS


def _load_workflow_inputs() -> dict:
    raw = os.getenv("WORKFLOW_INPUTS", "{}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("WORKFLOW_INPUTS must be valid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("WORKFLOW_INPUTS must be a JSON object")
    return data


async def _run_all() -> int:
    await init_db()

    workflow_inputs = _load_workflow_inputs()
    workflow_names = _parse_workflow_list()
    output_dir = os.getenv("OUTPUT_DIR")
    max_pages = os.getenv("MAX_PAGES")
    fail_fast = _get_bool_env("WORKER_FAIL_FAST", False)

    failures = 0

    for name in workflow_names:
        if name not in workflow_registry.list():
            logging.error("Workflow not registered: %s", name)
            failures += 1
            if fail_fast:
                break
            continue

        input_data = dict(workflow_inputs.get(name, {}))
        if output_dir:
            input_data.setdefault("output_dir", output_dir)
        if max_pages and "max_pages" not in input_data:
            try:
                input_data["max_pages"] = int(max_pages)
            except ValueError:
                logging.warning("Invalid MAX_PAGES value: %s", max_pages)

        execution_id = f"job-{name}-{uuid4()}"
        logging.info("Starting workflow %s (execution_id=%s)", name, execution_id)

        try:
            workflow = workflow_registry.create(name)
            await workflow.run(input_data=input_data, execution_id=execution_id)
            logging.info("Completed workflow %s", name)
        except Exception as exc:
            failures += 1
            logging.exception("Workflow %s failed: %s", name, exc)
            if fail_fast:
                break

    return failures


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    failures = asyncio.run(_run_all())
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
