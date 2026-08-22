"""Immutable contracts for controlled Taskmaster evaluations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SandboxModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProcessResult(SandboxModel):
    label: str
    exit_code: int | None
    timed_out: bool = False
    duration_ms: int = Field(ge=0)
    stdout: str = ""
    stderr: str = ""


class ScenarioResult(SandboxModel):
    scenario_id: str
    name: str
    category: Literal["happy_path", "edge_case", "failure", "security"]
    passed: bool
    outcome: Literal["completed", "stopped_safely", "rejected", "failed"]
    detail: str


class EvaluationReport(SandboxModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    project_id: str
    specification_id: str
    revision: int = Field(ge=1)
    template_version: str
    decision: Literal["ready", "needs_changes", "failed_safe"]
    unit_tests: ProcessResult
    scenarios: tuple[ScenarioResult, ...]
    policies_activated: tuple[str, ...]
    simulated_tools: tuple[str, ...]
    duration_ms: int = Field(ge=0)
    files_evaluated: int = Field(ge=0)
    warnings: tuple[str, ...] = ()
