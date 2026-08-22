from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.frameworks import AntigravityGenerator, GenAiSdkGenerator, GenkitGenerator
from studio.application.framework_selector import select_framework
from studio.domain.models import TaskmasterSpecification


@pytest.mark.parametrize(
    ("purpose", "workflow", "actions", "expected"),
    [
        ("Editar archivos de un repositorio", ["Inspeccionar código"], ["terminal"], "antigravity"),
        ("Crear una API web para Firebase", ["Recibir petición"], [], "genkit"),
        ("Extraer campos JSON", ["Extraer"], [], "genai_sdk"),
        (
            "Redactar desde documentos locales",
            ["Inspeccionar directorio", "Leer archivos"],
            ["workspace.read"],
            "google_adk",
        ),
        ("Coordinar una aprobación compleja", ["Analizar", "Proponer", "Aprobar"], [], "google_adk"),
    ],
)
def test_selector_chooses_framework_from_project_signals(
    purpose: str,
    workflow: list[str],
    actions: list[str],
    expected: str,
) -> None:
    result = select_framework(purpose=purpose, workflow=workflow, external_actions=actions)

    assert result.framework == expected
    assert result.reason
    assert result.confidence >= 80


@pytest.mark.parametrize(
    ("generator_type", "framework", "language", "required"),
    [
        (GenAiSdkGenerator, "genai_sdk", "python", "requirements.txt"),
        (AntigravityGenerator, "antigravity", "python", "policies/agent-policy.yaml"),
        (GenkitGenerator, "genkit", "typescript", "package.json"),
    ],
)
def test_new_framework_generators_create_verified_projects(
    tmp_path: Path,
    generator_type: type,
    framework: str,
    language: str,
    required: str,
) -> None:
    fixture = Path("studio/application/fixtures/academic_delivery_base.json")
    specification = TaskmasterSpecification.model_validate_json(fixture.read_text(encoding="utf-8"))
    specification = specification.model_copy(
        update={
            "generation": specification.generation.model_copy(
                update={"target_framework": framework, "language": language}
            )
        },
        deep=True,
    )
    root = tmp_path / "generated"
    generator = generator_type(root)

    bundle = generator.generate(specification, root / framework)
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))

    assert manifest["framework"] == framework
    assert (bundle.output_directory / required).is_file()
    assert (bundle.output_directory / "README.md").is_file()
