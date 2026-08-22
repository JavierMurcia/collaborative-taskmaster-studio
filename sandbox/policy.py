"""Fail-closed sandbox policy for generated projects."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from studio.domain.errors import DomainError

SECRET_MARKERS = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AZURE_CLIENT_SECRET",
)


class SandboxPolicy:
    def __init__(self, workspace: Path, *, timeout_seconds: float = 8.0) -> None:
        self.workspace = workspace.resolve()
        self.timeout_seconds = timeout_seconds

    def confine(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.workspace)
        except ValueError as error:
            raise DomainError(
                "SANDBOX_PATH_ESCAPE",
                "El laboratorio bloqueó un acceso fuera del workspace temporal.",
            ) from error
        return resolved

    def validate_pytest(self, command: tuple[str, ...]) -> None:
        expected = (sys.executable, "-m", "pytest")
        if command[:3] != expected or any(value in command for value in ("--pdb", "--trace")):
            raise DomainError(
                "SANDBOX_COMMAND_BLOCKED",
                "El laboratorio solo permite ejecutar pytest mediante el intérprete aprobado.",
            )

    def sanitized_environment(self) -> dict[str, str]:
        allowed = ("PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "COMSPEC", "WINDIR")
        environment = {key: os.environ[key] for key in allowed if key in os.environ}
        environment.update(
            {
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(self.workspace),
                "TASKMASTER_SANDBOX": "1",
            }
        )
        return environment


def contains_credentials(environment: dict[str, str]) -> bool:
    keys = {key.upper() for key in environment}
    return any(marker in keys for marker in SECRET_MARKERS)
