"""Static H10-01 contract for the production container definition."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"


def test_container_uses_separate_builder_and_runtime_stages() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "FROM python:3.13-slim AS builder" in content
    assert "FROM python:3.13-slim AS runtime" in content
    assert "COPY --from=builder /opt/venv /opt/venv" in content
    assert (
        'python -m pip install --no-cache-dir ".[vertex,firestore,laboratory]"'
        in content
    )


def test_runtime_is_non_root_and_has_only_explicit_source_copies() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "USER 10001:10001" in content
    assert "COPY . ." not in content
    assert "--no-create-home" in content
    assert 'CMD ["python", "-m", "app.main"]' in content
    for directory in (
        "app",
        "studio",
        "agents",
        "infrastructure",
        "adapters",
        "sandbox",
        "schemas",
    ):
        assert f"COPY --chown=10001:10001 {directory} ./{directory}" in content


def test_runtime_directories_are_owned_by_the_application_user() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "mkdir -p /app/.studio-data /app/generated" in content
    assert "chown -R 10001:10001 /app" in content


def test_runtime_does_not_override_cloud_run_port() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "STUDIO_PORT=" not in content
    assert "EXPOSE 8080" in content


def test_build_context_excludes_secrets_state_and_development_files() -> None:
    ignored = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {
        ".git/",
        ".env",
        ".env.*",
        "*.pem",
        "*.key",
        "*credentials*.json",
        ".venv/",
        "tests/",
        "docs/",
        ".studio-data/",
        "generated/",
    } <= ignored


def test_required_runtime_resources_exist_in_the_build_context() -> None:
    assert (ROOT / "app" / "static" / "index.html").is_file()
    assert (ROOT / "schemas" / "taskmaster-specification-1.0.0.json").is_file()
    assert (ROOT / "infrastructure" / "firestore" / "database.json").is_file()
    assert (ROOT / "infrastructure" / "firestore" / "indexes.json").is_file()
