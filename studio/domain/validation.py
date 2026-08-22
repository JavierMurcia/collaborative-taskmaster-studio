"""Structural and semantic validation for TaskmasterSpecification."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from studio.domain.enums import ApprovalStatus, ErrorSeverity, RiskLevel, TestCategory
from studio.domain.errors import ContractIssue
from studio.domain.models import TaskmasterSpecification

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schemas" / "taskmaster-specification-1.0.0.json"
)
SUPPORTED_ADAPTERS = {
    ("google_adk", "python"),
    ("genai_sdk", "python"),
    ("antigravity", "python"),
    ("genkit", "typescript"),
}
IDENTIFIER_KEYS = {
    "id",
    "source_project_id",
    "initial_state",
    "terminal_states",
    "actor_id",
    "tool_ids",
    "input_ids",
    "output_ids",
    "approval_policy_id",
    "verified_by",
    "from",
    "to",
}


@dataclass(frozen=True, slots=True)
class ValidationCapabilities:
    can_generate: bool
    can_simulate: bool
    supported_adapter: str | None

    def as_dict(self) -> dict[str, bool | str | None]:
        return {
            "can_generate": self.can_generate,
            "can_simulate": self.can_simulate,
            "supported_adapter": self.supported_adapter,
        }


@dataclass(frozen=True, slots=True)
class ContractValidationResult:
    valid: bool
    errors: tuple[ContractIssue, ...] = ()
    warnings: tuple[ContractIssue, ...] = ()
    schema_version: str | None = None
    revision: int | None = None
    specification_id: str | None = None
    capabilities: ValidationCapabilities = field(
        default_factory=lambda: ValidationCapabilities(False, False, None)
    )

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "valid": self.valid,
            "errors": [issue.as_dict() for issue in self.errors],
            "warnings": [issue.as_dict() for issue in self.warnings],
            "capabilities": self.capabilities.as_dict(),
        }
        if self.schema_version is not None:
            payload["schema_version"] = self.schema_version
        if self.revision is not None:
            payload["revision"] = self.revision
        if self.specification_id is not None:
            payload["specification_id"] = self.specification_id
        return payload


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        schema: dict[str, Any] = json.load(source)
    Draft202012Validator.check_schema(schema)
    return schema


SCHEMA = load_schema()
SCHEMA_VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def normalize_identifier(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return re.sub(r"_+", "_", normalized)


def normalize_specification(value: Any, *, key: str | None = None) -> Any:
    """Normalize presentation-only details without changing semantic decisions."""
    if isinstance(value, str):
        stripped = value.strip()
        return normalize_identifier(stripped) if key in IDENTIFIER_KEYS else stripped
    if isinstance(value, list):
        normalized = [normalize_specification(item, key=key) for item in value]
        if key in {"tags", "terminal_states", "tool_ids", "input_ids", "output_ids"}:
            return sorted(dict.fromkeys(normalized))
        return normalized
    if isinstance(value, dict):
        return {
            item_key: normalize_specification(item, key=item_key)
            for item_key, item in value.items()
        }
    return value


def validate_specification(
    payload: Mapping[str, Any],
    *,
    active_project_id: str | None = None,
    declared_secret_names: Iterable[str] = (),
) -> ContractValidationResult:
    normalized = normalize_specification(dict(payload))
    structural = _structural_issues(normalized)
    if structural:
        return ContractValidationResult(valid=False, errors=tuple(structural))

    try:
        specification = TaskmasterSpecification.model_validate(normalized)
    except ValidationError as error:
        issues = [
            ContractIssue(
                code="SCHEMA_VALIDATION_FAILED",
                path=_pydantic_path(item["loc"]),
                message=item["msg"],
                suggestion="Corrija el campo indicado según el contrato 1.0.0.",
            )
            for item in error.errors()
        ]
        return ContractValidationResult(valid=False, errors=tuple(issues))

    issues = _semantic_issues(
        specification,
        active_project_id=active_project_id,
        declared_secret_names=set(declared_secret_names),
    )
    errors = tuple(issue for issue in issues if issue.severity is ErrorSeverity.ERROR)
    warnings = tuple(issue for issue in issues if issue.severity is ErrorSeverity.WARNING)
    adapter_supported = (
        specification.generation.target_framework,
        specification.generation.language,
    ) in SUPPORTED_ADAPTERS
    approved = specification.approval.status is ApprovalStatus.APPROVED
    capabilities = ValidationCapabilities(
        can_generate=not errors and adapter_supported and approved,
        can_simulate=not errors,
        supported_adapter=(
            specification.generation.target_framework if adapter_supported else None
        ),
    )
    return ContractValidationResult(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        schema_version=specification.schema_version,
        revision=specification.revision,
        specification_id=specification.metadata.id,
        capabilities=capabilities,
    )


def _structural_issues(payload: Mapping[str, Any]) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for error in sorted(SCHEMA_VALIDATOR.iter_errors(payload), key=lambda item: list(item.path)):
        path = "/" + "/".join(str(part) for part in error.absolute_path)
        secret_value_detected = "required_secret_refs" in path and isinstance(error.instance, str)
        issues.append(
            ContractIssue(
                code=(
                    "SECRET_VALUE_DETECTED" if secret_value_detected else "SCHEMA_VALIDATION_FAILED"
                ),
                path=path or "/",
                message=(
                    "Se detectó un posible valor secreto; declare únicamente el nombre de la variable."
                    if secret_value_detected
                    else error.message
                ),
                suggestion=(
                    "Mueva el valor a un almacén de secretos y conserve sólo su referencia."
                    if secret_value_detected
                    else "Ajuste el valor al JSON Schema TaskmasterSpecification 1.0.0."
                ),
            )
        )
    return issues


def _semantic_issues(
    specification: TaskmasterSpecification,
    *,
    active_project_id: str | None,
    declared_secret_names: set[str],
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    metadata = specification.metadata
    if active_project_id and (
        metadata.source_project_id != active_project_id or metadata.id != active_project_id
    ):
        issues.append(
            _issue(
                "PROJECT_ID_MISMATCH",
                "/metadata",
                "La especificación no corresponde al proyecto activo.",
            )
        )
    if metadata.updated_at < metadata.created_at:
        issues.append(
            _issue(
                "INVALID_TIMESTAMP_ORDER",
                "/metadata/updated_at",
                "updated_at no puede ser anterior a created_at.",
            )
        )

    collections: list[tuple[str, list[Any]]] = [
        ("actors", specification.actors),
        ("inputs", specification.inputs),
        ("outputs", specification.outputs),
        ("workflow/steps", specification.workflow.steps),
        ("tools", specification.tools),
        ("policies", specification.policies),
        ("verification/criteria", specification.verification.criteria),
        ("test_scenarios", specification.test_scenarios),
    ]
    for path, items in collections:
        issues.extend(_duplicate_issues(path, [item.id for item in items]))

    actors = {actor.id for actor in specification.actors}
    tools = {tool.id for tool in specification.tools}
    io_ids = {item.id for item in specification.inputs + specification.outputs}
    policies = {policy.id: policy for policy in specification.policies}
    steps = {step.id: step for step in specification.workflow.steps}
    workflow = specification.workflow

    if workflow.initial_state not in steps:
        issues.append(
            _issue("UNREACHABLE_STATE", "/workflow/initial_state", "El estado inicial no existe.")
        )
    for index, terminal in enumerate(workflow.terminal_states):
        if terminal not in steps:
            issues.append(
                _issue(
                    "UNREACHABLE_STATE",
                    f"/workflow/terminal_states/{index}",
                    f"El estado terminal {terminal} no existe.",
                )
            )
    for index, transition in enumerate(workflow.transitions):
        if transition.from_state not in steps:
            issues.append(
                _issue(
                    "UNREACHABLE_STATE",
                    f"/workflow/transitions/{index}/from",
                    f"El estado {transition.from_state} no existe.",
                )
            )
        if transition.to not in steps:
            issues.append(
                _issue(
                    "UNREACHABLE_STATE",
                    f"/workflow/transitions/{index}/to",
                    f"El estado {transition.to} no existe.",
                )
            )

    issues.extend(_graph_issues(specification))
    for index, step in enumerate(specification.workflow.steps):
        base = f"/workflow/steps/{index}"
        if step.actor_id not in actors:
            issues.append(
                _issue(
                    "UNKNOWN_ACTOR_REFERENCE",
                    f"{base}/actor_id",
                    f"El actor {step.actor_id} no existe.",
                )
            )
        for tool_index, tool_id in enumerate(step.tool_ids):
            if tool_id not in tools:
                issues.append(
                    _issue(
                        "UNKNOWN_TOOL_REFERENCE",
                        f"{base}/tool_ids/{tool_index}",
                        f"La herramienta {tool_id} no existe.",
                    )
                )
        for field_name, references in (
            ("input_ids", step.input_ids),
            ("output_ids", step.output_ids),
        ):
            for ref_index, item_id in enumerate(references):
                if item_id not in io_ids:
                    issues.append(
                        _issue(
                            "UNKNOWN_IO_REFERENCE",
                            f"{base}/{field_name}/{ref_index}",
                            f"La entrada o salida {item_id} no existe.",
                        )
                    )
        if step.action_type == "tool" and not step.tool_ids:
            issues.append(
                _issue(
                    "UNKNOWN_TOOL_REFERENCE",
                    f"{base}/tool_ids",
                    "Un paso de herramienta debe declarar al menos una herramienta.",
                )
            )
        if step.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            policy = policies.get(step.approval_policy_id or "")
            if policy is None or policy.type != "require_approval":
                issues.append(
                    _issue(
                        "MISSING_APPROVAL_POLICY",
                        f"{base}/approval_policy_id",
                        "Una acción de alto riesgo requiere una política de aprobación válida.",
                    )
                )

    for index, tool in enumerate(specification.tools):
        if tool.mode == "write" and tool.risk is RiskLevel.LOW:
            issues.append(
                _issue(
                    "INVALID_RISK_CLASSIFICATION",
                    f"/tools/{index}/risk",
                    "Una herramienta de escritura no puede clasificarse como riesgo bajo.",
                )
            )
        for secret_index, secret in enumerate(tool.required_secret_refs):
            if secret not in declared_secret_names:
                issues.append(
                    _issue(
                        "UNKNOWN_SECRET_REFERENCE",
                        f"/tools/{index}/required_secret_refs/{secret_index}",
                        f"El secreto {secret} no está declarado en .env.example.",
                    )
                )

    if specification.verification.verified_by not in actors:
        issues.append(
            _issue(
                "UNKNOWN_ACTOR_REFERENCE",
                "/verification/verified_by",
                "El actor verificador no existe.",
            )
        )
    elif specification.verification.strategy != "human":
        for terminal in workflow.terminal_states:
            terminal_step = steps.get(terminal)
            if terminal_step and terminal_step.actor_id == specification.verification.verified_by:
                issues.append(
                    _issue(
                        "VERIFIER_NOT_INDEPENDENT",
                        "/verification/verified_by",
                        "El verificador debe ser distinto del actor de la acción final.",
                    )
                )
                break

    categories = {scenario.category for scenario in specification.test_scenarios}
    for category in (TestCategory.HAPPY_PATH, TestCategory.FAILURE, TestCategory.SECURITY):
        if category not in categories:
            issues.append(
                _issue(
                    "MISSING_REQUIRED_TEST_CATEGORY",
                    "/test_scenarios",
                    f"Falta un escenario de categoría {category.value}.",
                )
            )
    if specification.deployment.max_instances < specification.deployment.min_instances:
        issues.append(
            _issue(
                "INVALID_INSTANCE_RANGE",
                "/deployment/max_instances",
                "max_instances debe ser mayor o igual que min_instances.",
            )
        )
    adapter = (specification.generation.target_framework, specification.generation.language)
    if adapter not in SUPPORTED_ADAPTERS:
        issues.append(
            _issue(
                "INCOMPATIBLE_FRAMEWORK_LANGUAGE",
                "/generation",
                "La combinación de framework y lenguaje no dispone de un adaptador instalado.",
            )
        )
    if specification.approval.status is not ApprovalStatus.APPROVED:
        issues.append(
            ContractIssue(
                code="SPECIFICATION_NOT_APPROVED",
                path="/approval/status",
                message="La especificación todavía no está aprobada para generar.",
                severity=ErrorSeverity.WARNING,
                suggestion="Obtenga una aprobación humana explícita.",
            )
        )
    return issues


def _duplicate_issues(path: str, identifiers: list[str]) -> list[ContractIssue]:
    seen: set[str] = set()
    issues: list[ContractIssue] = []
    for index, identifier in enumerate(identifiers):
        if identifier in seen:
            issues.append(
                _issue(
                    "DUPLICATE_IDENTIFIER",
                    f"/{path}/{index}/id",
                    f"El identificador {identifier} está duplicado.",
                )
            )
        seen.add(identifier)
    return issues


def _graph_issues(specification: TaskmasterSpecification) -> list[ContractIssue]:
    workflow = specification.workflow
    known = {step.id for step in workflow.steps}
    adjacency: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)
    for transition in workflow.transitions:
        if transition.from_state in known and transition.to in known:
            adjacency[transition.from_state].add(transition.to)
            reverse[transition.to].add(transition.from_state)
    reachable = (
        _walk(workflow.initial_state, adjacency) if workflow.initial_state in known else set()
    )
    terminal_reachable = set().union(
        *(_walk(item, reverse) for item in workflow.terminal_states if item in known)
    )
    issues: list[ContractIssue] = []
    for step_id in sorted(known - reachable):
        issues.append(
            _issue(
                "UNREACHABLE_STATE",
                "/workflow/steps",
                f"El estado {step_id} no es alcanzable desde el inicio.",
            )
        )
    for step_id in sorted(known - terminal_reachable):
        issues.append(
            _issue(
                "NO_TERMINAL_PATH",
                "/workflow/steps",
                f"El estado {step_id} no tiene camino a un estado terminal.",
            )
        )
    return issues


def _walk(start: str, graph: Mapping[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    pending = deque([start])
    while pending:
        current = pending.popleft()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.get(current, set()) - visited)
    return visited


def _issue(code: str, path: str, message: str) -> ContractIssue:
    return ContractIssue(
        code=code,
        path=path,
        message=message,
        suggestion="Revise el diseño antes de aprobar o generar.",
    )


def _pydantic_path(location: tuple[int | str, ...]) -> str:
    return "/" + "/".join(str(item) for item in location)
