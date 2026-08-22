from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from studio.domain.enums import ApprovalStatus, ProjectState
from studio.domain.errors import InvalidTransitionError, RevisionImmutableError
from studio.domain.models import Project, Revision, TaskmasterSpecification
from studio.domain.transitions import transition_project
from studio.domain.validation import normalize_specification, validate_specification

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "academic_delivery_specification.json"


def load_fixture() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))


def error_codes(payload: dict[str, Any]) -> set[str]:
    return {issue.code for issue in validate_specification(payload).errors}


def test_document_02_example_is_valid_and_generatable() -> None:
    result = validate_specification(load_fixture())
    assert result.valid
    assert not result.errors
    assert result.schema_version == "1.0.0"
    assert result.revision == 2
    assert result.specification_id == "academic_delivery_coordinator"
    assert result.capabilities.can_generate
    assert result.capabilities.supported_adapter == "google_adk"


def test_invalid_json_shape_returns_structured_errors() -> None:
    payload = load_fixture()
    del payload["mission"]
    result = validate_specification(payload)
    assert not result.valid
    assert result.errors[0].code == "SCHEMA_VALIDATION_FAILED"
    assert result.errors[0].path == "/"


def test_duplicate_identifiers_are_rejected() -> None:
    payload = load_fixture()
    payload["actors"].append(copy.deepcopy(payload["actors"][0]))
    assert "DUPLICATE_IDENTIFIER" in error_codes(payload)


def test_unknown_references_are_rejected() -> None:
    payload = load_fixture()
    payload["workflow"]["steps"][0]["actor_id"] = "missing_actor"
    payload["workflow"]["steps"][0]["tool_ids"] = ["missing_tool"]
    payload["workflow"]["steps"][0]["input_ids"] = ["missing_input"]
    codes = error_codes(payload)
    assert {"UNKNOWN_ACTOR_REFERENCE", "UNKNOWN_TOOL_REFERENCE", "UNKNOWN_IO_REFERENCE"} <= codes


def test_unreachable_step_and_missing_terminal_path_are_rejected() -> None:
    payload = load_fixture()
    isolated = copy.deepcopy(payload["workflow"]["steps"][0])
    isolated["id"] = "isolated_step"
    isolated["name"] = "Isolated step"
    payload["workflow"]["steps"].append(isolated)
    codes = error_codes(payload)
    assert "UNREACHABLE_STATE" in codes
    assert "NO_TERMINAL_PATH" in codes


def test_high_risk_step_requires_approval_policy() -> None:
    payload = load_fixture()
    step = payload["workflow"]["steps"][0]
    step["risk"] = "high"
    step["approval_policy_id"] = None
    assert "MISSING_APPROVAL_POLICY" in error_codes(payload)


def test_mvp_rejects_unsupported_framework_language() -> None:
    payload = load_fixture()
    payload["generation"]["language"] = "typescript"
    assert "INCOMPATIBLE_FRAMEWORK_LANGUAGE" in error_codes(payload)


def test_required_test_categories_are_enforced() -> None:
    payload = load_fixture()
    payload["test_scenarios"] = [
        scenario for scenario in payload["test_scenarios"] if scenario["category"] != "security"
    ]
    payload["test_scenarios"].append(copy.deepcopy(payload["test_scenarios"][0]))
    payload["test_scenarios"][-1]["id"] = "extra_happy_path"
    assert "MISSING_REQUIRED_TEST_CATEGORY" in error_codes(payload)


def test_project_identity_and_timestamp_order_are_enforced() -> None:
    payload = load_fixture()
    payload["metadata"]["updated_at"] = "2025-01-01T00:00:00Z"
    result = validate_specification(payload, active_project_id="another_project")
    codes = {issue.code for issue in result.errors}
    assert {"PROJECT_ID_MISMATCH", "INVALID_TIMESTAMP_ORDER"} <= codes


def test_unknown_states_and_transitions_are_rejected() -> None:
    payload = load_fixture()
    payload["workflow"]["terminal_states"] = ["missing_terminal"]
    payload["workflow"]["transitions"][0]["to"] = "missing_state"
    assert "UNREACHABLE_STATE" in error_codes(payload)


def test_write_tool_cannot_claim_low_risk() -> None:
    payload = load_fixture()
    payload["tools"][0]["mode"] = "write"
    payload["tools"][0]["risk"] = "low"
    assert "INVALID_RISK_CLASSIFICATION" in error_codes(payload)


def test_secret_references_must_be_declared_without_values() -> None:
    payload = load_fixture()
    payload["tools"][0]["required_secret_refs"] = ["CALENDAR_API_KEY"]
    missing = validate_specification(payload)
    assert "UNKNOWN_SECRET_REFERENCE" in {issue.code for issue in missing.errors}

    declared = validate_specification(payload, declared_secret_names={"CALENDAR_API_KEY"})
    assert "UNKNOWN_SECRET_REFERENCE" not in {issue.code for issue in declared.errors}

    payload["tools"][0]["required_secret_refs"] = ["CALENDAR_API_KEY=actual-secret"]
    leaked = validate_specification(payload)
    assert "SECRET_VALUE_DETECTED" in {issue.code for issue in leaked.errors}


def test_verifier_must_be_independent_for_non_human_strategy() -> None:
    payload = load_fixture()
    terminal_id = payload["workflow"]["terminal_states"][0]
    terminal = next(step for step in payload["workflow"]["steps"] if step["id"] == terminal_id)
    payload["verification"]["verified_by"] = terminal["actor_id"]
    payload["verification"]["strategy"] = "deterministic"
    assert "VERIFIER_NOT_INDEPENDENT" in error_codes(payload)


def test_deployment_instance_range_is_consistent() -> None:
    payload = load_fixture()
    payload["deployment"]["min_instances"] = 4
    payload["deployment"]["max_instances"] = 2
    assert "INVALID_INSTANCE_RANGE" in error_codes(payload)


def test_unapproved_contract_is_valid_but_cannot_generate() -> None:
    payload = load_fixture()
    payload["approval"] = {
        "status": "draft",
        "decided_by": None,
        "decided_at": None,
        "note": "",
    }
    result = validate_specification(payload)
    assert result.valid
    assert not result.capabilities.can_generate
    assert {warning.code for warning in result.warnings} == {"SPECIFICATION_NOT_APPROVED"}


def test_approved_revision_is_immutable() -> None:
    specification = TaskmasterSpecification.model_validate(load_fixture())
    revision = Revision(
        project_id="academic_delivery_coordinator",
        number=2,
        specification=specification,
    )
    changed = specification.model_copy(update={"revision": 3}, deep=True)
    assert specification.approval.status is ApprovalStatus.APPROVED
    with pytest.raises(RevisionImmutableError) as captured:
        revision.replace_specification(changed)
    assert captured.value.code == "REVISION_IMMUTABLE"


def test_state_machine_allows_only_declared_transitions() -> None:
    project = Project(id="academic_delivery", name="Academic delivery")
    interviewing = transition_project(project, ProjectState.INTERVIEW)
    assert interviewing.state is ProjectState.INTERVIEW
    assert project.state is ProjectState.IDEA
    with pytest.raises(InvalidTransitionError):
        transition_project(project, ProjectState.DESIGN_APPROVED)


def test_normalization_changes_only_presentation_details() -> None:
    normalized = normalize_specification(
        {
            "metadata": {"id": " Academic Delivery ", "tags": [" beta ", "alpha", "alpha"]},
            "mission": {"goal": "  Preserve semantic wording  "},
        }
    )
    assert normalized["metadata"]["id"] == "academic_delivery"
    assert normalized["metadata"]["tags"] == ["alpha", "beta"]
    assert normalized["mission"]["goal"] == "Preserve semantic wording"
