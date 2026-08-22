"""Validate and summarize the official fictional demonstration data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from studio.application.demo_fixture import (
    FINAL_SPECIFICATION_PATH,
    FIXTURE_PATH,
    load_final_demo_specification,
    load_official_demo_fixture,
)
from studio.domain.validation import validate_specification


def prepare_demo_data() -> dict[str, object]:
    fixture = load_official_demo_fixture()
    specification = load_final_demo_specification()
    metadata = specification.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("source_project_id") != (
        fixture.project.project_id
    ):
        raise RuntimeError("La especificación final no corresponde al proyecto oficial.")
    validation = validate_specification(specification)
    if not validation.valid or validation.capabilities is None:
        errors = [error.code for error in validation.errors]
        raise RuntimeError(f"La especificación oficial no es válida: {errors}")
    if not validation.capabilities.can_generate:
        raise RuntimeError("La especificación oficial no está lista para generar.")

    return {
        "schema_version": fixture.schema_version,
        "status": "ready",
        "fixture_id": fixture.fixture_id,
        "fictional_data": fixture.fictional_data,
        "project_id": fixture.project.project_id,
        "interview_turns": len(fixture.interview_turns),
        "academic_requirements": len(fixture.academic_requirements.items),
        "available_minutes": fixture.academic_requirements.available_minutes,
        "approved_revision": fixture.expected.approved_revision,
        "target_framework": fixture.generation.target_framework,
        "required_scenarios": [item.value for item in fixture.evaluation.required_categories],
        "expected_decision": fixture.evaluation.expected_decision,
        "external_actions_allowed": fixture.privacy.allows_external_actions,
        "contains_personal_data": fixture.privacy.contains_personal_data,
        "contains_secrets": fixture.privacy.contains_secrets,
        "fixture_sha256": _sha256(FIXTURE_PATH),
        "specification_sha256": _sha256(FINAL_SPECIFICATION_PATH),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    print(json.dumps(prepare_demo_data(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
