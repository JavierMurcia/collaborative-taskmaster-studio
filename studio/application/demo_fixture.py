"""Validated single source of truth for the official H11 demonstration."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.application.interview_catalog import QUESTION_CATALOG
from studio.domain.enums import AuditEventType, ProjectState, TestCategory

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "official_demo.json"
FINAL_SPECIFICATION_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "academic_delivery_base.json"
)


class DemoModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class DemoProject(DemoModel):
    project_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    owner_session_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    name: str = Field(min_length=3, max_length=100)
    description: str = Field(min_length=10, max_length=800)


class DemoInterviewTurn(DemoModel):
    question_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    question: str = Field(min_length=5)
    answer: str = Field(min_length=5, max_length=4000)


class AcademicRequirement(DemoModel):
    id: str = Field(pattern=r"^req_[a-z0-9_]+$")
    title: str = Field(min_length=3)
    estimated_minutes: int = Field(gt=0, le=360)
    required_evidence: str = Field(min_length=3)


class AcademicRequirements(DemoModel):
    available_minutes: int = Field(gt=0, le=1440)
    items: tuple[AcademicRequirement, ...] = Field(min_length=1)


class DemoFeedback(DemoModel):
    expected_revision: int = Field(ge=1)
    text: str = Field(min_length=20, max_length=4000)


class DemoApproval(DemoModel):
    revision: int = Field(ge=1)
    decision: Literal["approved"]
    note: str = Field(min_length=3, max_length=1000)


class DemoGeneration(DemoModel):
    revision: int = Field(ge=1)
    target_framework: Literal["google_adk"]
    template_version: Literal["1.0.0"]


class DemoEvaluation(DemoModel):
    revision: int = Field(ge=1)
    expected_decision: Literal["ready"]
    required_categories: tuple[TestCategory, ...] = Field(min_length=3)


class DemoAdversarialInput(DemoModel):
    label: str = Field(min_length=3)
    text: str = Field(min_length=20, max_length=1000)


class DemoExpected(DemoModel):
    approved_revision: int = Field(ge=1)
    final_state: ProjectState
    artifact_validation_status: Literal["valid"]
    minimum_audit_events: int = Field(ge=1)
    required_event_types: tuple[AuditEventType, ...] = Field(min_length=1)
    prohibited_actions: tuple[str, ...] = Field(min_length=1)


class DemoPrivacy(DemoModel):
    contains_personal_data: Literal[False]
    contains_secrets: Literal[False]
    allows_external_actions: Literal[False]
    retention_days: Literal[7]


class OfficialDemoFixture(DemoModel):
    schema_version: Literal["1.0.0"]
    fixture_id: Literal["academic_delivery_official_demo"]
    language: Literal["es-CO"]
    fictional_data: Literal[True]
    project: DemoProject
    interview_turns: tuple[DemoInterviewTurn, ...] = Field(min_length=3, max_length=3)
    academic_requirements: AcademicRequirements
    feedback: DemoFeedback
    approval: DemoApproval
    generation: DemoGeneration
    evaluation: DemoEvaluation
    adversarial_input: DemoAdversarialInput
    expected: DemoExpected
    privacy: DemoPrivacy

    @model_validator(mode="after")
    def validate_demo_invariants(self) -> OfficialDemoFixture:
        catalog_ids = tuple(question.id for question in QUESTION_CATALOG)
        turn_ids = tuple(turn.question_id for turn in self.interview_turns)
        if turn_ids != catalog_ids:
            raise ValueError("Los turnos oficiales deben seguir el catálogo completo y ordenado.")
        catalog_prompts = {question.id: question.prompt for question in QUESTION_CATALOG}
        if any(turn.question != catalog_prompts[turn.question_id] for turn in self.interview_turns):
            raise ValueError("Las preguntas oficiales no coinciden con el catálogo local.")
        total_minutes = sum(
            requirement.estimated_minutes for requirement in self.academic_requirements.items
        )
        if total_minutes != self.academic_requirements.available_minutes:
            raise ValueError("Los requisitos oficiales deben ocupar exactamente el tiempo disponible.")
        revision = self.expected.approved_revision
        if not (
            self.feedback.expected_revision + 1
            == self.approval.revision
            == self.generation.revision
            == self.evaluation.revision
            == revision
        ):
            raise ValueError("Feedback, aprobación, generación y evaluación deben converger en v2.")
        required_categories = {
            TestCategory.HAPPY_PATH,
            TestCategory.FAILURE,
            TestCategory.SECURITY,
        }
        if set(self.evaluation.required_categories) != required_categories:
            raise ValueError("La demo debe exigir escenarios normal, fallo y seguridad.")
        return self

    @property
    def answers(self) -> dict[str, str]:
        return {turn.question_id: turn.answer for turn in self.interview_turns}


@lru_cache(maxsize=1)
def load_official_demo_fixture(path: Path = FIXTURE_PATH) -> OfficialDemoFixture:
    """Load and validate the official fixture without calling external services."""

    return OfficialDemoFixture.model_validate_json(path.read_text(encoding="utf-8"))


def load_final_demo_specification(path: Path = FINAL_SPECIFICATION_PATH) -> dict[str, object]:
    """Load the approved deterministic specification referenced by the fixture."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("La especificación final de demo debe ser un objeto JSON.")
    return payload
