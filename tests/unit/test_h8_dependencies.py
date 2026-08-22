from __future__ import annotations

import tomllib
from pathlib import Path


def project_metadata() -> dict[str, object]:
    root = Path(__file__).resolve().parents[2]
    with (root / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)


def test_vertex_sdks_are_explicit_optional_dependencies() -> None:
    metadata = project_metadata()
    project = metadata["project"]
    assert isinstance(project, dict)
    base = project["dependencies"]
    extras = project["optional-dependencies"]
    assert isinstance(base, list)
    assert isinstance(extras, dict)
    vertex = extras["vertex"]
    assert isinstance(vertex, list)
    assert "google-adk>=2.7,<3" in vertex
    assert "google-genai>=2.12.1,<3" in vertex
    assert all(not item.startswith(("google-adk", "google-genai")) for item in base)


def test_local_mode_remains_disabled_by_default() -> None:
    root = Path(__file__).resolve().parents[2]
    example = (root / ".env.example").read_text(encoding="utf-8")
    assert "STUDIO_ENABLE_VERTEX=false" in example
