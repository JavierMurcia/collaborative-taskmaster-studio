from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FINAL_ARCHITECTURE = ROOT / "docs" / "12_DIAGRAMA_ARQUITECTURA_FINAL.md"
README = ROOT / "README.md"


def test_final_architecture_has_five_balanced_mermaid_views() -> None:
    content = FINAL_ARCHITECTURE.read_text(encoding="utf-8")

    assert content.count("```mermaid") == 5
    assert content.count("```") == 10
    assert "Vista consolidada vigente" in content
    assert "Sistema desplegado" in content
    assert "Recorrido completo del producto" in content
    assert "Fronteras de confianza y autoridad" in content
    assert "Construcción, identidad y operación" in content


def test_final_architecture_records_deployed_components_and_limits() -> None:
    content = FINAL_ARCHITECTURE.read_text(encoding="utf-8")

    required_claims = (
        "Gemini 3.7 Flash",
        "VertexModelGateway",
        "Firestore",
        "Google ADK",
        "Cloud Tasks",
        "Antigravity",
        "Documentos y datasets",
        "Laboratorio aislado",
        "Aprobación humana",
        "min 0 · max 1 · concurrencia 1",
        "collaborative-taskmaster-studio-00004-fqp",
        "no inicia un Runner ni una sesión ADK",
    )
    for claim in required_claims:
        assert claim in content


def test_architecture_markdown_links_resolve_and_readme_points_to_final_view() -> None:
    content = FINAL_ARCHITECTURE.read_text(encoding="utf-8")
    links = re.findall(r"\[[^]]+\]\(([^)]+)\)", content)

    assert links
    for target in links:
        assert not target.startswith(("http://", "https://"))
        assert (FINAL_ARCHITECTURE.parent / target).resolve().is_file()

    readme = README.read_text(encoding="utf-8")
    assert "docs/12_DIAGRAMA_ARQUITECTURA_FINAL.md" in readme


def test_readme_indexes_current_product_documentation() -> None:
    readme = README.read_text(encoding="utf-8")

    for number in range(23, 29):
        match = re.search(rf"\]\(docs/({number}_[^)]+\.md)\)", readme)
        assert match, f"README does not index document {number}"
        assert (ROOT / "docs" / match.group(1)).is_file()
