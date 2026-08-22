"""Atomic, confined generator for Google ADK Python projects."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

from adapters.google_adk.capabilities import GoogleAdkCapabilities
from adapters.google_adk.templates import TEMPLATE_VERSION, render_files
from studio.domain.errors import DomainError
from studio.domain.models import TaskmasterSpecification
from studio.ports.generator import GeneratedBundle, GeneratedFile


class GoogleAdkGenerator:
    framework = "google_adk"
    template_version = TEMPLATE_VERSION

    def __init__(self, generated_root: Path) -> None:
        self._root = generated_root.resolve()
        self._capabilities = GoogleAdkCapabilities()

    def validate_capabilities(self, specification: TaskmasterSpecification) -> None:
        self._capabilities.validate(specification)

    def generate(
        self,
        specification: TaskmasterSpecification,
        destination: Path,
    ) -> GeneratedBundle:
        self.validate_capabilities(specification)
        target = self._confined(destination)
        if target.exists():
            raise DomainError(
                "GENERATION_OUTPUT_EXISTS",
                "La carpeta de salida ya existe y no será sobrescrita.",
                context={"relative_path": target.relative_to(self._root).as_posix()},
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".building-", dir=target.parent))
        try:
            files = render_files(specification)
            generated = self._write_files(temporary, files)
            manifest = self._manifest(specification, generated)
            manifest_path = temporary / "taskmaster.manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            self._validate_tree(temporary, generated)
            os.replace(temporary, target)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        final_files = tuple(
            GeneratedFile(
                relative_path=item.relative_path,
                sha256=item.sha256,
                size_bytes=item.size_bytes,
            )
            for item in generated
        )
        return GeneratedBundle(
            output_directory=target,
            manifest_path=target / "taskmaster.manifest.json",
            template_version=self.template_version,
            files=final_files,
        )

    def _confined(self, destination: Path) -> Path:
        target = destination.resolve()
        try:
            target.relative_to(self._root)
        except ValueError as error:
            raise DomainError(
                "GENERATION_PATH_ESCAPE",
                "La salida debe permanecer dentro del directorio generated.",
            ) from error
        return target

    def _write_files(self, root: Path, files: dict[str, str]) -> tuple[GeneratedFile, ...]:
        generated: list[GeneratedFile] = []
        for relative_path, content in sorted(files.items()):
            path = PurePosixPath(relative_path)
            if path.is_absolute() or ".." in path.parts:
                raise DomainError("GENERATION_INVALID_PATH", "Una plantilla produjo una ruta insegura.")
            destination = root.joinpath(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8", newline="\n")
            payload = destination.read_bytes()
            generated.append(
                GeneratedFile(
                    relative_path=path.as_posix(),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    size_bytes=len(payload),
                )
            )
        return tuple(generated)

    def _manifest(
        self,
        specification: TaskmasterSpecification,
        files: tuple[GeneratedFile, ...],
    ) -> dict[str, Any]:
        return {
            "manifest_version": "1.0.0",
            "project_id": specification.metadata.source_project_id,
            "specification_id": specification.metadata.id,
            "revision": specification.revision,
            "framework": self.framework,
            "template_version": self.template_version,
            "generated_at": specification.metadata.updated_at.isoformat(),
            "files": [item.model_dump(mode="json") for item in files],
        }

    def _validate_tree(self, root: Path, files: tuple[GeneratedFile, ...]) -> None:
        required = {
            "app/__init__.py",
            "app/agent.py",
            "app/tools.py",
            "app/policies.py",
            "app/services.py",
            "tests/unit/test_policies.py",
            "tests/eval/test_scenarios.json",
            ".env.example",
            "Dockerfile",
            "pyproject.toml",
            "agents-cli-manifest.yaml",
            "ARCHITECTURE.md",
            "README.md",
        }
        present = {item.relative_path for item in files}
        missing = sorted(required - present)
        if missing:
            raise DomainError(
                "GENERATION_ARTIFACTS_MISSING",
                "La plantilla no produjo todos los artefactos requeridos.",
                context={"paths": missing},
            )
        for item in files:
            path = root.joinpath(*PurePosixPath(item.relative_path).parts)
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != item.sha256:
                raise DomainError("GENERATION_CHECKSUM_MISMATCH", "Falló la verificación SHA-256.")
            if path.suffix in {".py", ".md", ".toml", ".yaml", ".json", ".example"}:
                text = payload.decode("utf-8")
                if re.search(r"\b[A-Z]:(?:/|\\(?![ntrbfv]))", text, flags=re.IGNORECASE):
                    raise DomainError(
                        "GENERATION_ABSOLUTE_PATH",
                        "Un artefacto contiene una ruta absoluta del equipo de desarrollo.",
                        context={"relative_path": item.relative_path},
                    )
                if path.suffix == ".py":
                    try:
                        compile(text, item.relative_path, "exec")
                    except SyntaxError as error:
                        raise DomainError(
                            "GENERATION_PYTHON_INVALID",
                            "Una plantilla produjo código Python inválido.",
                            context={"relative_path": item.relative_path},
                        ) from error
                if path.name == "pyproject.toml":
                    try:
                        tomllib.loads(text)
                    except tomllib.TOMLDecodeError as error:
                        raise DomainError(
                            "GENERATION_TOML_INVALID",
                            "La plantilla produjo un pyproject.toml inválido.",
                        ) from error
