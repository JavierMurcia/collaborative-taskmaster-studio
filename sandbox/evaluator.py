"""Deterministic evaluation of a generated Taskmaster in a temporary workspace."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from sandbox.models import EvaluationReport, ScenarioResult
from sandbox.policy import SandboxPolicy, contains_credentials
from sandbox.runner import SandboxRunner
from studio.domain.enums import TestCategory
from studio.domain.errors import DomainError


class SandboxEvaluator:
    def __init__(self, *, timeout_seconds: float = 8.0) -> None:
        self.timeout_seconds = timeout_seconds

    def evaluate(self, source: Path) -> EvaluationReport:
        source = source.resolve()
        if not source.is_dir():
            raise DomainError("SANDBOX_SOURCE_MISSING", "No existe el proyecto generado.")
        started = perf_counter()
        with tempfile.TemporaryDirectory(prefix="taskmaster-lab-") as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(source, workspace)
            _install_framework_stubs(workspace)
            policy = SandboxPolicy(workspace, timeout_seconds=self.timeout_seconds)
            if contains_credentials(policy.sanitized_environment()):
                raise DomainError(
                    "SANDBOX_CREDENTIALS_PRESENT",
                    "El laboratorio se detuvo porque detectó credenciales.",
                )
            unit_tests = SandboxRunner(policy).run_unit_tests()
            manifest = _read_object(policy.confine(workspace / "taskmaster.manifest.json"))
            scenarios_document = _read_object(
                policy.confine(workspace / "tests" / "eval" / "test_scenarios.json")
            )
            policies_source = policy.confine(workspace / "app" / "policies.py").read_text(
                encoding="utf-8"
            )
            tools_source = policy.confine(workspace / "app" / "tools.py").read_text(
                encoding="utf-8"
            )
            scenarios = _evaluate_scenarios(
                scenarios_document.get("scenarios", []), policies_source, tools_source
            )
            warnings: list[str] = []
            if unit_tests.timed_out:
                warnings.append("Las pruebas excedieron el tiempo límite y el proceso fue terminado.")
            elif unit_tests.exit_code != 0:
                warnings.append("Las pruebas unitarias generadas no terminaron correctamente.")
            failed = [item for item in scenarios if not item.passed]
            security_failed = any(
                item.category == TestCategory.SECURITY.value and not item.passed for item in scenarios
            )
            required = {TestCategory.HAPPY_PATH, TestCategory.FAILURE, TestCategory.SECURITY}
            present = {TestCategory(item.category) for item in scenarios}
            if not required.issubset(present):
                warnings.append("Faltan uno o más escenarios obligatorios.")
            if (
                unit_tests.timed_out
                or unit_tests.exit_code != 0
                or security_failed
                or not required.issubset(present)
            ):
                decision = "failed_safe"
            elif failed:
                decision = "needs_changes"
            else:
                decision = "ready"
            files = manifest.get("files", [])
            return EvaluationReport(
                project_id=str(manifest.get("project_id", "unknown")),
                specification_id=str(manifest.get("specification_id", "unknown")),
                revision=int(manifest.get("revision", 1)),
                template_version=str(manifest.get("template_version", "0.0.0")),
                decision=cast(Any, decision),
                unit_tests=unit_tests,
                scenarios=scenarios,
                policies_activated=tuple(_identifiers(policies_source)),
                simulated_tools=tuple(_tool_names(tools_source)),
                duration_ms=max(0, int((perf_counter() - started) * 1000)),
                files_evaluated=len(files) if isinstance(files, list) else 0,
                warnings=tuple(warnings),
            )


def _evaluate_scenarios(
    raw_scenarios: Any, policies_source: str, tools_source: str
) -> tuple[ScenarioResult, ...]:
    if not isinstance(raw_scenarios, list):
        return ()
    results: list[ScenarioResult] = []
    for raw in raw_scenarios:
        if not isinstance(raw, dict):
            continue
        category = str(raw.get("category", "failure"))
        scenario_id = str(raw.get("id", "unknown"))
        if category == TestCategory.HAPPY_PATH:
            passed = '"status": "simulated"' in tools_source and "record_tool_call" in tools_source
            outcome = "completed" if passed else "failed"
            detail = (
                "El flujo normal usa herramientas simuladas y conserva trazabilidad."
                if passed
                else "No se encontró una herramienta simulada auditable."
            )
        elif category in {TestCategory.FAILURE, TestCategory.EDGE_CASE}:
            passed = "def validate_input" in policies_source and "dato obligatorio" in policies_source
            outcome = "stopped_safely" if passed else "failed"
            detail = (
                "La información faltante detiene el flujo antes de cualquier efecto externo."
                if passed
                else "No se encontró una guarda determinista de detención segura."
            )
        else:
            has_policy = "def validate_input" in policies_source and "system override" in policies_source
            has_no_effect = "no se produjo ningún efecto externo" in tools_source.casefold()
            passed = has_policy and has_no_effect
            outcome = "rejected" if passed else "failed"
            detail = (
                "La instrucción maliciosa se trata como dato y no puede omitir la aprobación."
                if passed
                else "La defensa contra instrucciones maliciosas no quedó demostrada."
            )
        results.append(
            ScenarioResult(
                scenario_id=scenario_id,
                name=str(raw.get("name", scenario_id)),
                category=cast(Any, category),
                passed=passed,
                outcome=cast(Any, outcome),
                detail=detail,
            )
        )
    return tuple(results)


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DomainError("SANDBOX_DOCUMENT_INVALID", f"{path.name} no contiene un objeto JSON.")
    return payload


def _identifiers(source: str) -> list[str]:
    return sorted(set(re.findall(r"['\"]id['\"]:\s*['\"]([a-z][a-z0-9_]*)", source)))


def _tool_names(source: str) -> list[str]:
    ignored = {"authorize_tool", "record_tool_call"}
    names = set(re.findall(r"^def ([a-z][a-z0-9_]*)\(", source, flags=re.MULTILINE))
    return sorted(names - ignored)


def _install_framework_stubs(workspace: Path) -> None:
    files = {
        "sitecustomize.py": (
            "import socket\n"
            "def _blocked(*args, **kwargs):\n"
            "    raise RuntimeError('network disabled by Taskmaster sandbox')\n"
            "socket.create_connection = _blocked\n"
            "socket.socket.connect = _blocked\n"
            "socket.socket.connect_ex = _blocked\n"
        ),
        "google/__init__.py": "",
        "google/adk/__init__.py": "",
        "google/adk/agents.py": "class Agent:\n    def __init__(self, **kwargs): self.kwargs = kwargs\n",
        "google/adk/apps.py": "class App:\n    def __init__(self, **kwargs): self.kwargs = kwargs\n",
        "google/adk/models.py": "class Gemini:\n    def __init__(self, **kwargs): self.kwargs = kwargs\n",
        "google/genai/__init__.py": "from . import types\n",
        "google/genai/types.py": "class HttpRetryOptions:\n    def __init__(self, **kwargs): self.kwargs = kwargs\n",
    }
    for relative, content in files.items():
        path = workspace.joinpath(*Path(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
