"""H11-04 official fictional demonstration data."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from infrastructure.cloud_run.journey import ANSWERS, DEMO_FIXTURE, FEEDBACK
from scripts.prepare_demo_data import prepare_demo_data
from studio.application.demo_fixture import (
    FIXTURE_PATH,
    OfficialDemoFixture,
    load_final_demo_specification,
    load_official_demo_fixture,
)
from studio.application.interview_catalog import QUESTION_CATALOG
from studio.domain.enums import AuditEventType, ProjectState, TestCategory
from studio.domain.validation import validate_specification


def fixture_payload() -> dict[str, object]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_official_demo_is_fictional_private_and_action_free() -> None:
    fixture = load_official_demo_fixture()

    assert fixture.fictional_data is True
    assert fixture.privacy.contains_personal_data is False
    assert fixture.privacy.contains_secrets is False
    assert fixture.privacy.allows_external_actions is False
    assert fixture.privacy.retention_days == 7


def test_official_turns_match_the_complete_interview_catalog() -> None:
    fixture = load_official_demo_fixture()

    assert [turn.question_id for turn in fixture.interview_turns] == [
        question.id for question in QUESTION_CATALOG
    ]
    assert fixture.answers == {turn.question_id: turn.answer for turn in fixture.interview_turns}
    assert "No puede enviar información" in fixture.answers["ask_autonomy_and_approval"]


def test_academic_requirements_fill_exactly_six_hours() -> None:
    fixture = load_official_demo_fixture()

    assert len(fixture.academic_requirements.items) == 4
    assert sum(
        requirement.estimated_minutes for requirement in fixture.academic_requirements.items
    ) == fixture.academic_requirements.available_minutes == 360


def test_feedback_approval_and_expected_outcome_converge_on_revision_two() -> None:
    fixture = load_official_demo_fixture()

    assert fixture.feedback.expected_revision == 1
    assert fixture.approval.revision == fixture.expected.approved_revision == 2
    assert fixture.generation.revision == fixture.evaluation.revision == 2
    assert fixture.expected.final_state is ProjectState.READY_TO_EXPORT
    assert set(fixture.evaluation.required_categories) == {
        TestCategory.HAPPY_PATH,
        TestCategory.FAILURE,
        TestCategory.SECURITY,
    }
    assert set(fixture.expected.required_event_types) == {
        AuditEventType.BRIEFING_CONFIRMED,
        AuditEventType.REVISION_APPROVED,
        AuditEventType.ARTIFACT_GENERATED,
        AuditEventType.EVALUATION_COMPLETED,
    }


def test_final_specification_is_valid_approved_and_generatable() -> None:
    fixture = load_official_demo_fixture()
    specification = load_final_demo_specification()
    metadata = specification["metadata"]
    assert isinstance(metadata, dict)

    result = validate_specification(specification)

    assert metadata["source_project_id"] == fixture.project.project_id
    assert result.valid is True
    assert result.revision == 2
    assert result.capabilities.can_generate is True
    assert result.capabilities.supported_adapter == "google_adk"


def test_cloud_journey_consumes_the_same_official_payloads() -> None:
    assert DEMO_FIXTURE is load_official_demo_fixture()
    assert DEMO_FIXTURE.answers == ANSWERS
    assert DEMO_FIXTURE.feedback.text == FEEDBACK


def test_preparer_reports_checksums_and_demo_readiness() -> None:
    result = prepare_demo_data()

    assert result["status"] == "ready"
    assert result["interview_turns"] == 3
    assert result["academic_requirements"] == 4
    assert result["available_minutes"] == 360
    assert result["required_scenarios"] == ["happy_path", "failure", "security"]
    assert result["external_actions_allowed"] is False
    assert len(str(result["fixture_sha256"])) == 64
    assert len(str(result["specification_sha256"])) == 64


def test_versioned_evidence_matches_the_validated_sources() -> None:
    root = Path(__file__).resolve().parents[2]
    evidence = json.loads(
        (root / "docs" / "evidence" / "h11-04-demo-data.json").read_text(
            encoding="utf-8"
        )
    )
    result = prepare_demo_data()

    assert evidence["status"] == result["status"]
    assert evidence["fixture_sha256"] == result["fixture_sha256"]
    assert evidence["specification_sha256"] == result["specification_sha256"]
    assert evidence["required_scenarios"] == result["required_scenarios"]
    assert evidence["cloud_calls_performed"] == 0


@pytest.mark.parametrize("mutation", ["catalog", "minutes", "revision", "privacy"])
def test_invalid_official_demo_drift_fails_closed(mutation: str) -> None:
    payload = copy.deepcopy(fixture_payload())
    if mutation == "catalog":
        payload["interview_turns"][0]["question_id"] = "unexpected_question"  # type: ignore[index]
    elif mutation == "minutes":
        payload["academic_requirements"]["available_minutes"] = 359  # type: ignore[index]
    elif mutation == "revision":
        payload["approval"]["revision"] = 3  # type: ignore[index]
    else:
        payload["privacy"]["contains_personal_data"] = True  # type: ignore[index]

    with pytest.raises(ValidationError):
        OfficialDemoFixture.model_validate(payload)


def test_fixture_contains_no_common_secret_markers() -> None:
    content = FIXTURE_PATH.read_text(encoding="utf-8").casefold()
    forbidden = ("private_key", "api_key", "access_token", "password", "bearer ")

    assert not any(marker in content for marker in forbidden)
    assert Path(FIXTURE_PATH).is_file()
