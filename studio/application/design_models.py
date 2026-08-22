"""Read models for deterministic design review and structural differences."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from studio.domain.models import DomainModel, ProjectSnapshot, Revision


class DiffItem(DomainModel):
    category: Literal[
        "scope_in",
        "scope_out",
        "workflow_step",
        "tool",
        "policy",
        "test_scenario",
        "terminal_state",
    ]
    identifier: str
    before: Any | None = None
    after: Any | None = None


class StructuralDiff(DomainModel):
    from_revision: int
    to_revision: int
    added: list[DiffItem] = Field(default_factory=list)
    removed: list[DiffItem] = Field(default_factory=list)
    modified: list[DiffItem] = Field(default_factory=list)


class DesignResult(DomainModel):
    snapshot: ProjectSnapshot
    revision: Revision
    diff: StructuralDiff | None = None


class StepOverview(DomainModel):
    id: str
    name: str
    description: str
    risk: str
    tool_ids: list[str] = Field(default_factory=list)
    approval_required: bool


class ToolOverview(DomainModel):
    id: str
    name: str
    mode: str
    risk: str
    description: str


class DesignOverview(DomainModel):
    revision: int
    goal: str
    steps: list[StepOverview]
    tools: list[ToolOverview]
    policies: list[str]
    verification_criteria: list[str]
    approval_status: str
