"""H11-03 clean local installation verifier."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.verify_clean_install import (
    CLOUD_ENABLE_FLAGS,
    clean_local_environment,
    copy_source_snapshot,
)


def test_clean_environment_disables_cloud_and_removes_credentials(tmp_path: Path) -> None:
    source = {
        "PATH": "retained",
        "PORT": "8080",
        "K_SERVICE": "cloud-run",
        "GOOGLE_APPLICATION_CREDENTIALS": "secret.json",
        "GOOGLE_API_KEY": "secret",
        "GEMINI_API_KEY": "secret",
        **{name: "true" for name in CLOUD_ENABLE_FLAGS},
    }

    result = clean_local_environment(
        source,
        port=43210,
        data_directory=tmp_path / "data",
        generated_root=tmp_path / "generated",
    )

    assert result["PATH"] == "retained"
    assert result["STUDIO_HOST"] == "127.0.0.1"
    assert result["STUDIO_PORT"] == "43210"
    assert all(result[name] == "false" for name in CLOUD_ENABLE_FLAGS)
    for forbidden in (
        "PORT",
        "K_SERVICE",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
    ):
        assert forbidden not in result


def test_source_snapshot_excludes_local_state_and_credentials(tmp_path: Path) -> None:
    source = tmp_path / "repository"
    destination = tmp_path / "snapshot"
    source.mkdir()
    (source / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (source / "app").mkdir()
    (source / "app" / "main.py").write_text("APP = True\n", encoding="utf-8")
    (source / ".env.example").write_text("SAFE=true\n", encoding="utf-8")
    (source / ".env").write_text("SECRET=true\n", encoding="utf-8")
    (source / ".venv").mkdir()
    (source / ".venv" / "secret.txt").write_text("no\n", encoding="utf-8")
    (source / "generated").mkdir()
    (source / "generated" / "artifact.py").write_text("no\n", encoding="utf-8")

    copied = copy_source_snapshot(source, destination)

    assert copied == 3
    assert (destination / "pyproject.toml").is_file()
    assert (destination / "app" / "main.py").is_file()
    assert (destination / ".env.example").is_file()
    assert not (destination / ".env").exists()
    assert not (destination / ".venv").exists()
    assert not (destination / "generated").exists()


def test_source_snapshot_requires_packaging_contract(tmp_path: Path) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    (source / "README.md").write_text("missing package\n", encoding="utf-8")

    try:
        copy_source_snapshot(source, tmp_path / "snapshot")
    except RuntimeError as error:
        assert "pyproject.toml" in str(error)
    else:
        raise AssertionError("El snapshot sin pyproject.toml debió fallar.")


def test_clean_install_evidence_and_readme_command_are_versioned() -> None:
    root = Path(__file__).resolve().parents[2]
    evidence = json.loads(
        (root / "docs" / "evidence" / "h11-03-clean-install.json").read_text(
            encoding="utf-8"
        )
    )
    readme = (root / "README.md").read_text(encoding="utf-8")

    assert evidence["status"] == "passed"
    assert evidence["full_tests"]["passed"] >= 399
    assert evidence["full_tests"]["skipped_optional"] == 5
    assert set(evidence["http"].values()) == {200}
    assert evidence["cloud_integrations_enabled"] is False
    assert evidence["credentials_copied"] is False
    assert "scripts\\verify_clean_install.py" in readme
