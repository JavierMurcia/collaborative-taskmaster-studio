"""Confined orchestration of agent projects through Google Antigravity SDK."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from studio.domain.errors import DomainError
from studio.domain.models import TaskmasterSpecification
from studio.ports.construction import BuilderRuntime, ConstructionProgress
from studio.ports.generator import GeneratedBundle, GeneratedFile, GeneratorAdapter

_MAX_WRITES = 32
_MAX_FILE_BYTES = 256_000
_MAX_PROJECT_FILES = 96
_SECRET_PATTERN = re.compile(
    r"(-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"AIza[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z]{20,})"
)


@dataclass(frozen=True)
class _SdkBindings:
    agent: type[Any]
    config: type[Any]
    deny_all: Callable[[], object]
    allow: Callable[[str], object]


def _load_sdk() -> _SdkBindings:
    try:
        from google.antigravity import Agent, LocalAgentConfig
        from google.antigravity.hooks.policy import allow, deny_all
    except (ImportError, ModuleNotFoundError) as error:
        raise DomainError(
            "ANTIGRAVITY_SDK_UNAVAILABLE",
            "El SDK de Antigravity no está instalado en el entorno de construcción.",
        ) from error
    return _SdkBindings(
        agent=Agent,
        config=LocalAgentConfig,
        deny_all=deny_all,
        allow=allow,
    )


def sdk_version() -> str:
    """Return the installed SDK version for auditable construction evidence."""

    try:
        return importlib.metadata.version("google-antigravity")
    except importlib.metadata.PackageNotFoundError as error:
        raise DomainError(
            "ANTIGRAVITY_SDK_UNAVAILABLE",
            "El SDK de Antigravity no está instalado en el entorno de construcción.",
        ) from error


class _ConfinedWorkspace:
    """Expose the minimum text-file API Antigravity needs to refine a scaffold."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.operations: list[dict[str, object]] = []
        self._writes = 0

    def list_project_files(self) -> list[str]:
        """List regular project files relative to the confined workspace."""
        files = [
            path.relative_to(self.root).as_posix()
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        ]
        self.operations.append({"tool": "list_project_files", "count": len(files)})
        return files[:_MAX_PROJECT_FILES]

    def read_project_file(self, relative_path: str) -> str:
        """Read one UTF-8 text file from the confined workspace."""
        path = self._resolve(relative_path)
        if not path.is_file() or path.is_symlink():
            raise ValueError("The requested project file does not exist.")
        payload = path.read_bytes()
        if len(payload) > _MAX_FILE_BYTES:
            raise ValueError("The requested project file is too large.")
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Only UTF-8 text project files may be read.") from error
        self.operations.append({"tool": "read_project_file", "path": relative_path})
        return content

    def write_project_file(self, relative_path: str, content: str) -> str:
        """Create or replace one UTF-8 text file inside the confined workspace."""
        if self._writes >= _MAX_WRITES:
            raise ValueError("The construction write limit has been reached.")
        path = self._resolve(relative_path)
        if path.name == "taskmaster.manifest.json" or ".studio" in path.parts:
            raise ValueError("Managed Studio artifacts cannot be changed by the agent.")
        payload = content.encode("utf-8")
        if len(payload) > _MAX_FILE_BYTES:
            raise ValueError("The project file exceeds the construction size limit.")
        if _SECRET_PATTERN.search(content):
            raise ValueError("Potential secret material is not allowed in generated files.")
        current_files = sum(1 for item in self.root.rglob("*") if item.is_file())
        if not path.exists() and current_files >= _MAX_PROJECT_FILES:
            raise ValueError("The construction file-count limit has been reached.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        self._writes += 1
        self.operations.append(
            {"tool": "write_project_file", "path": relative_path, "size_bytes": len(payload)}
        )
        return f"Wrote {relative_path} ({len(payload)} bytes)."

    def _resolve(self, relative_path: str) -> Path:
        candidate = PurePosixPath(relative_path)
        if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
            raise ValueError("Only relative paths inside the project are allowed.")
        resolved = self.root.joinpath(*candidate.parts).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as error:
            raise ValueError("The requested path escapes the project workspace.") from error
        return resolved


class AntigravitySdkOrchestrator:
    """Run Antigravity in a separate Python environment with a confined workspace."""

    runtime_id: BuilderRuntime = "antigravity_sdk"

    def __init__(
        self,
        python_executable: str,
        *,
        runner: Callable[[list[str], Path], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._python = python_executable
        self._runner = runner or _run_worker

    def construct(
        self,
        specification: TaskmasterSpecification,
        destination: Path,
        *,
        generator: GeneratorAdapter,
        contract: Mapping[str, object],
        progress: ConstructionProgress,
    ) -> GeneratedBundle:
        progress("generation", "Creando una base reproducible para Antigravity…", "running")
        seed = generator.generate(specification, destination)
        progress("orchestration", "Antigravity está inspeccionando el proyecto confinado…", "running")
        studio_directory = seed.output_directory / ".studio"
        studio_directory.mkdir(parents=True, exist_ok=True)
        request_path = studio_directory / "antigravity-request.json"
        evidence_path = studio_directory / "antigravity-orchestration.json"
        request_path.write_text(
            json.dumps(
                {
                    "workspace": str(seed.output_directory),
                    "specification": specification.model_dump(mode="json"),
                    "contract": dict(contract),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            result = self._runner(
                [self._python, "-m", "adapters.antigravity.worker", str(request_path)],
                Path(__file__).resolve().parents[2],
            )
        finally:
            request_path.unlink(missing_ok=True)
        if result.returncode != 0:
            raise DomainError(
                "ANTIGRAVITY_WORKER_FAILED",
                "El trabajador aislado de Antigravity se detuvo de forma segura.",
                context={"return_code": result.returncode},
            )
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            operations = evidence["operations"]
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise DomainError(
                "ANTIGRAVITY_EVIDENCE_INVALID",
                "El trabajador de Antigravity no devolvió evidencia válida.",
            ) from error
        if not isinstance(operations, list) or not operations:
            raise DomainError(
                "ANTIGRAVITY_NO_OBSERVABLE_WORK",
                "Antigravity no produjo ninguna operación observable sobre el proyecto.",
            )
        progress(
            "orchestration",
            f"Antigravity completó {len(operations)} operaciones auditables.",
            "passed",
        )
        return _reindex_bundle(seed)


async def orchestrate_workspace(
    workspace: _ConfinedWorkspace,
    specification: Mapping[str, object],
    contract: Mapping[str, object],
) -> str:
    sdk = _load_sdk()
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    location = os.getenv("STUDIO_ANTIGRAVITY_VERTEX_LOCATION", "us-central1").strip()
    if not project or not location:
        raise DomainError(
            "ANTIGRAVITY_VERTEX_CONFIGURATION_REQUIRED",
            "Antigravity requiere un proyecto y una región de Vertex AI explícitos.",
        )
    policies = [
        sdk.deny_all(),
        sdk.allow("list_project_files"),
        sdk.allow("read_project_file"),
        sdk.allow("write_project_file"),
    ]
    instructions = (
        "You are the Taskmaster Studio agent engineer. Work only through the three provided "
        "project tools. Never use a browser, network, shell, credentials, secrets, or paths "
        "outside the confined workspace. Inspect the scaffold before changing it. Preserve "
        "human approval boundaries and produce a minimal, runnable, tested-by-design agent "
        "project. Do not modify managed Studio artifacts."
    )
    config = sdk.config(
        vertex=True,
        project=project,
        location=location,
        system_instructions=instructions,
        tools=[
            workspace.list_project_files,
            workspace.read_project_file,
            workspace.write_project_file,
        ],
        policies=policies,
    )
    prompt = (
        "Inspect the generated scaffold with list_project_files and read_project_file. "
        "Improve only files that are necessary to satisfy the approved specification. "
        "Do not run tests or commands; the Studio will request separate human approval first. "
        "Finish with a concise summary of observable changes.\n\n"
        f"APPROVED SPECIFICATION:\n{json.dumps(dict(specification), ensure_ascii=False, indent=2)}\n\n"
        f"IMMUTABLE CONTRACT:\n{json.dumps(dict(contract), ensure_ascii=False, indent=2)}"
    )
    async with sdk.agent(config) as agent:
        response = await agent.chat(prompt)
        return str(await response.text())


def _run_worker(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _reindex_bundle(seed: GeneratedBundle) -> GeneratedBundle:
    root = seed.output_directory
    manifest_path = root / "taskmaster.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files: list[GeneratedFile] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path == manifest_path or path.is_symlink():
            continue
        payload = path.read_bytes()
        files.append(
            GeneratedFile(
                relative_path=path.relative_to(root).as_posix(),
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
        )
    manifest["files"] = [item.model_dump(mode="json") for item in files]
    manifest["orchestrator"] = "antigravity_sdk"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return GeneratedBundle(
        output_directory=root,
        manifest_path=manifest_path,
        template_version=seed.template_version,
        files=tuple(files),
    )
