"""Structured errors returned by domain operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from studio.domain.enums import ErrorSeverity


@dataclass(frozen=True, slots=True)
class ContractIssue:
    code: str
    path: str
    message: str
    severity: ErrorSeverity = ErrorSeverity.ERROR
    suggestion: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "severity": self.severity.value,
        }
        if self.suggestion:
            result["suggestion"] = self.suggestion
        return result


class DomainError(Exception):
    """Base exception carrying a stable machine-readable code."""

    def __init__(self, code: str, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context or {}


class InvalidTransitionError(DomainError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            "INVALID_STATE_TRANSITION",
            f"No se permite pasar de {current} a {target}.",
            context={"current": current, "target": target},
        )


class RevisionImmutableError(DomainError):
    def __init__(self, revision: int) -> None:
        super().__init__(
            "REVISION_IMMUTABLE",
            f"La revisión aprobada {revision} es inmutable; cree una nueva revisión.",
            context={"revision": revision},
        )


class RepositoryConflictError(DomainError):
    def __init__(self, message: str, **context: Any) -> None:
        super().__init__("REPOSITORY_CONFLICT", message, context=context)


class EntityNotFoundError(DomainError):
    def __init__(self, entity: str, identifier: str) -> None:
        super().__init__(
            "ENTITY_NOT_FOUND",
            f"No existe {entity} con identificador {identifier}.",
            context={"entity": entity, "identifier": identifier},
        )


class IdempotencyConflictError(DomainError):
    def __init__(self, key: str) -> None:
        super().__init__(
            "IDEMPOTENCY_CONFLICT",
            "La clave de idempotencia ya fue usada con un contenido diferente.",
            context={"key": key},
        )


class ProjectAccessDeniedError(DomainError):
    def __init__(self, project_id: str) -> None:
        super().__init__(
            "PROJECT_ACCESS_DENIED",
            "La sesión actual no puede acceder a este proyecto.",
            context={"project_id": project_id},
        )


class BriefingIncompleteError(DomainError):
    def __init__(self, missing_fields: list[str]) -> None:
        super().__init__(
            "BRIEFING_INCOMPLETE",
            "Falta información obligatoria antes de confirmar el briefing.",
            context={"missing_fields": missing_fields},
        )


class InterviewAnswerError(DomainError):
    def __init__(self, message: str, **context: Any) -> None:
        super().__init__("INTERVIEW_ANSWER_INVALID", message, context=context)
