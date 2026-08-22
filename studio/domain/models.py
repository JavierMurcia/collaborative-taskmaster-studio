"""Pydantic domain models for projects and TaskmasterSpecification 1.0.0."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from studio.domain.enums import (
    ApprovalStatus,
    AuditEventType,
    AutonomyLevel,
    ProjectState,
    RiskLevel,
    TestCategory,
)
from studio.domain.errors import RevisionImmutableError

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")]
NonEmpty = Annotated[str, Field(min_length=1, max_length=2000)]


def utc_now() -> datetime:
    return datetime.now(UTC)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class InterviewAnswerRecord(DomainModel):
    operation_id: Annotated[str, Field(min_length=1, max_length=200)]
    question_id: Identifier
    target_fields: Annotated[list[Identifier], Field(min_length=1)]
    answer: Annotated[str, Field(min_length=1, max_length=4000)]
    values: dict[str, Any] = Field(default_factory=dict)
    recorded_at: datetime = Field(default_factory=utc_now)
    correction: bool = False


class Briefing(DomainModel):
    problem: str = ""
    goal: str = ""
    desired_result: str = ""
    actors: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    scope_in: list[str] = Field(default_factory=list)
    scope_out: list[str] = Field(default_factory=list)
    approvals: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    deadline: str = ""
    available_hours: Annotated[int, Field(ge=1, le=168)] | None = None
    input_format: str = ""
    outputs: list[str] = Field(default_factory=list)
    external_actions: Literal["none", "allowed", "requires_clarification"] | None = None
    approval_owner: str = ""
    answer_history: list[InterviewAnswerRecord] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    confirmed: bool = False
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


class Project(DomainModel):
    id: Identifier
    name: Annotated[str, Field(min_length=3, max_length=100)]
    state: ProjectState = ProjectState.IDEA
    owner_session_id: Annotated[str, Field(min_length=1, max_length=128)] = "local_session"
    briefing: Briefing = Field(default_factory=Briefing)
    active_revision: int | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Trigger(DomainModel):
    type: Literal["manual", "schedule", "event", "api"]
    description: NonEmpty


class Metadata(DomainModel):
    id: Identifier
    name: Annotated[str, Field(min_length=3, max_length=100)]
    summary: Annotated[str, Field(min_length=10, max_length=500)]
    language: Annotated[str, Field(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")]
    created_at: datetime
    updated_at: datetime
    created_by: Annotated[str, Field(min_length=1, max_length=100)]
    source_project_id: Identifier
    tags: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=30)]], Field(max_length=10)
    ] = Field(default_factory=list)


class Mission(DomainModel):
    problem: NonEmpty
    goal: NonEmpty
    scope_in: Annotated[list[NonEmpty], Field(min_length=1)]
    scope_out: list[NonEmpty]
    trigger: Trigger
    completion_definition: Annotated[list[NonEmpty], Field(min_length=1)]


class Actor(DomainModel):
    id: Identifier
    type: Literal["human", "agent", "system"]
    name: Annotated[str, Field(min_length=1, max_length=100)]
    responsibilities: Annotated[list[NonEmpty], Field(min_length=1)]


class IOItem(DomainModel):
    id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: NonEmpty
    data_type: Literal["text", "number", "boolean", "date", "object", "array", "file", "url"]
    required: bool
    sensitivity: Literal["public", "internal", "confidential", "restricted"]
    source: Annotated[str, Field(max_length=200)] | None = None


class WorkflowStep(DomainModel):
    id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: NonEmpty
    actor_id: Identifier
    action_type: Literal["reason", "tool", "human", "verify"]
    tool_ids: list[Identifier]
    input_ids: list[Identifier]
    output_ids: list[Identifier]
    risk: RiskLevel
    approval_policy_id: Identifier | None = None
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)]


class Transition(DomainModel):
    from_state: Identifier = Field(alias="from", serialization_alias="from")
    to: Identifier
    condition: NonEmpty
    priority: Annotated[int, Field(ge=1)] = 100


class Workflow(DomainModel):
    initial_state: Identifier
    terminal_states: Annotated[list[Identifier], Field(min_length=1)]
    steps: Annotated[list[WorkflowStep], Field(min_length=1, max_length=30)]
    transitions: Annotated[list[Transition], Field(min_length=1)]


class Tool(DomainModel):
    id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: NonEmpty
    mode: Literal["simulated", "read_only", "write"]
    risk: RiskLevel
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    side_effects: list[NonEmpty]
    required_secret_refs: list[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{2,127}$")]] = Field(
        default_factory=list
    )


class Memory(DomainModel):
    session: bool
    persistent: bool
    provider: Literal["local", "firestore", "memory_bank", "none"] | None = None
    retention_days: Annotated[int, Field(ge=0, le=3650)]
    allowed_fields: list[Identifier]
    forbidden_fields: list[Identifier]


class Autonomy(DomainModel):
    level: AutonomyLevel
    max_steps: Annotated[int, Field(ge=1, le=100)]
    max_tool_calls: Annotated[int, Field(ge=0, le=100)]
    max_runtime_seconds: Annotated[int, Field(ge=1, le=86400)]
    human_interruptible: bool


class Policy(DomainModel):
    id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=100)]
    type: Literal["allow", "deny", "require_approval", "budget", "data"]
    rule: NonEmpty
    effect: NonEmpty


class Criterion(DomainModel):
    id: Identifier
    description: NonEmpty
    measurement: NonEmpty
    expected: NonEmpty


class Verification(DomainModel):
    strategy: Literal["deterministic", "tool_assisted", "human", "hybrid"]
    criteria: Annotated[list[Criterion], Field(min_length=1)]
    verified_by: Identifier


class FailureHandling(DomainModel):
    max_retries: Annotated[int, Field(ge=0, le=10)]
    retry_strategy: Literal["none", "fixed", "exponential"]
    fallback: NonEmpty
    on_exhausted: Literal["fail_safe", "request_human", "pause"]


class TestScenario(DomainModel):
    id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=120)]
    category: TestCategory
    given: NonEmpty
    when: NonEmpty
    then: NonEmpty


class Generation(DomainModel):
    target_framework: Literal["google_adk", "genai_sdk", "genkit", "antigravity"]
    language: Literal["python", "typescript"]
    template_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    required_artifacts: Annotated[
        list[
            Literal[
                "source", "tests", "readme", "env_example", "dockerfile", "architecture", "manifest"
            ]
        ],
        Field(min_length=1),
    ]


class Deployment(DomainModel):
    target: Literal["local", "cloud_run"]
    region: Annotated[str, Field(min_length=1, max_length=50)]
    public_access: bool
    min_instances: Annotated[int, Field(ge=0, le=10)]
    max_instances: Annotated[int, Field(ge=1, le=100)]


class Approval(DomainModel):
    status: ApprovalStatus
    decided_by: Annotated[str, Field(max_length=100)] | None
    decided_at: datetime | None
    note: Annotated[str, Field(max_length=1000)]


class TaskmasterSpecification(DomainModel):
    schema_version: Literal["1.0.0"]
    revision: Annotated[int, Field(ge=1)]
    metadata: Metadata
    mission: Mission
    actors: Annotated[list[Actor], Field(min_length=1)]
    inputs: Annotated[list[IOItem], Field(min_length=1)]
    outputs: Annotated[list[IOItem], Field(min_length=1)]
    workflow: Workflow
    tools: list[Tool]
    memory: Memory
    autonomy: Autonomy
    policies: Annotated[list[Policy], Field(min_length=1)]
    verification: Verification
    failure_handling: FailureHandling
    test_scenarios: Annotated[list[TestScenario], Field(min_length=3)]
    generation: Generation
    deployment: Deployment
    approval: Approval


class Revision(DomainModel):
    project_id: Identifier
    number: Annotated[int, Field(ge=1)]
    specification: TaskmasterSpecification
    created_at: datetime = Field(default_factory=utc_now)

    def replace_specification(self, specification: TaskmasterSpecification) -> Revision:
        if self.specification.approval.status is ApprovalStatus.APPROVED:
            raise RevisionImmutableError(self.number)
        return self.model_copy(update={"specification": specification}, deep=True)


class AuditEvent(DomainModel):
    id: Identifier
    project_id: Identifier
    event_type: AuditEventType
    actor_id: str
    sequence: Annotated[int, Field(ge=1)] | None = None
    summary: Annotated[str, Field(max_length=500)] = ""
    revision: Annotated[int, Field(ge=1)] | None = None
    occurred_at: datetime = Field(default_factory=utc_now)
    details: dict[str, Any] = Field(default_factory=dict)


class ArtifactMetadata(DomainModel):
    id: Identifier
    project_id: Identifier
    revision: Annotated[int, Field(ge=1)]
    relative_path: Annotated[str, Field(min_length=1, max_length=500)]
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    framework: Literal["google_adk", "genai_sdk", "genkit", "antigravity"]
    template_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    validation_status: Literal["pending", "valid", "invalid"] = "pending"


class ApprovalRecord(DomainModel):
    id: Identifier
    project_id: Identifier
    revision: Annotated[int, Field(ge=1)]
    approval: Approval
    created_at: datetime = Field(default_factory=utc_now)


class ProjectSnapshot(DomainModel):
    project: Project
    version: Annotated[int, Field(ge=1)]
    revisions: tuple[Revision, ...] = ()
    approvals: tuple[ApprovalRecord, ...] = ()
    artifacts: tuple[ArtifactMetadata, ...] = ()
