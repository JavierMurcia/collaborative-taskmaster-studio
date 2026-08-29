"""Run deterministic framework generation in a credential-free subprocess."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from studio.domain.errors import DomainError
from studio.domain.models import TaskmasterSpecification
from studio.ports.construction import BuilderRuntime, ConstructionProgress
from studio.ports.generator import GeneratedBundle, GeneratedFile, GeneratorAdapter


class IsolatedControlledConstructionOrchestrator:
    """Generate a project outside the web process with no inherited credentials."""

    runtime_id: BuilderRuntime = "isolated_controlled_builder"

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
        del generator
        root = destination.resolve().parent
        requests = root / ".studio-build-requests"
        requests.mkdir(parents=True, exist_ok=True)
        request_path = requests / f"{destination.name}.json"
        request_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "root": str(root),
                    "destination": str(destination.resolve()),
                    "specification": specification.model_dump(mode="json", by_alias=True),
                    "contract_sha256": str(contract.get("sha256", "")),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        progress(
            "generation",
            "Entregando el contrato a un trabajador aislado sin credenciales…",
            "running",
        )
        try:
            result = self._runner(
                [self._python, "-m", "adapters.controlled.worker", str(request_path)],
                Path(__file__).resolve().parents[2],
            )
        finally:
            request_path.unlink(missing_ok=True)
        if result.returncode != 0:
            raise DomainError(
                "ISOLATED_BUILDER_FAILED",
                "El trabajador aislado detuvo la generación de forma segura.",
                context={"return_code": result.returncode},
            )
        evidence = destination / ".studio" / "isolated-builder.json"
        if not evidence.is_file():
            raise DomainError(
                "ISOLATED_BUILDER_EVIDENCE_MISSING",
                "El trabajador no devolvió evidencia verificable.",
            )
        progress(
            "generation",
            "Proyecto generado por el trabajador aislado y devuelto para verificación.",
            "passed",
        )
        return _bundle_from_directory(destination)


def _run_worker(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
        env=_credential_free_environment(),
    )


def _credential_free_environment() -> dict[str, str]:
    allowed = {
        "LANG",
        "LC_ALL",
        "PATH",
        "PYTHONPATH",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment["STUDIO_ISOLATED_BUILD_WORKER"] = "true"
    return environment


def _bundle_from_directory(root: Path) -> GeneratedBundle:
    manifest_path = root / "taskmaster.manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        template_version = str(manifest["template_version"])
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
        raise DomainError("ISOLATED_BUILDER_MANIFEST_INVALID", "El manifiesto no es válido.") from error
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
    return GeneratedBundle(
        output_directory=root,
        manifest_path=manifest_path,
        template_version=template_version,
        files=tuple(files),
    )
