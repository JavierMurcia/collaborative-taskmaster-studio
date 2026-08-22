from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.local.clock import FrozenClock
from infrastructure.local.repositories import InMemoryRepository
from sandbox import SandboxEvaluator
from sandbox.policy import SandboxPolicy, contains_credentials
from studio.application.evaluation_service import EvaluationService, require_exportable
from studio.application.generation_service import GenerationResult
from studio.domain.enums import ProjectState
from studio.domain.errors import DomainError
from tests.application.test_h6_generation import NOW, OWNER, PROJECT_ID, approved_services


def generated_project(
    tmp_path: Path,
) -> tuple[Path, InMemoryRepository, GenerationResult]:
    root = tmp_path / "generated"
    repository, generation = approved_services(root)
    generated = generation.generate(
        PROJECT_ID,
        revision=2,
        owner_session_id=OWNER,
        idempotency_key="generate-for-h7",
    )
    return root, repository, generated


def test_official_fixture_passes_three_controlled_scenarios(tmp_path: Path) -> None:
    root, repository, _ = generated_project(tmp_path)
    service = EvaluationService(repository, repository, generation_clock(), SandboxEvaluator(), root)

    result = service.evaluate(
        PROJECT_ID,
        revision=2,
        owner_session_id=OWNER,
        idempotency_key="evaluate-h7",
    )

    assert result.report.decision == "ready"
    assert result.snapshot.project.state is ProjectState.READY_TO_EXPORT
    assert result.report.unit_tests.exit_code == 0
    assert not result.report.unit_tests.timed_out
    assert {item.category for item in result.report.scenarios} >= {
        "happy_path",
        "failure",
        "security",
    }
    assert all(item.passed for item in result.report.scenarios)
    assert Path(root / result.report_relative_path).is_file()
    require_exportable(result.snapshot)


def test_timeout_terminates_process_and_fails_safe(tmp_path: Path) -> None:
    root, repository, generated = generated_project(tmp_path)
    output = root / generated.output_relative_path
    (output / "tests/unit/test_timeout.py").write_text(
        "import time\ndef test_wait_forever():\n    time.sleep(2)\n",
        encoding="utf-8",
    )
    service = EvaluationService(
        repository, repository, generation_clock(), SandboxEvaluator(timeout_seconds=0.1), root
    )

    result = service.evaluate(
        PROJECT_ID,
        revision=2,
        owner_session_id=OWNER,
        idempotency_key="evaluate-timeout",
    )

    assert result.report.decision == "failed_safe"
    assert result.report.unit_tests.timed_out
    assert result.snapshot.project.state is ProjectState.DESIGN_IN_REVIEW
    with pytest.raises(DomainError) as captured:
        require_exportable(result.snapshot)
    assert captured.value.code == "EXPORT_BLOCKED_BY_EVALUATION"


def test_sandbox_environment_excludes_cloud_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "secret.json")
    monkeypatch.setenv("GEMINI_API_KEY", "secret")
    environment = SandboxPolicy(tmp_path).sanitized_environment()
    assert not contains_credentials(environment)
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in environment
    assert "GEMINI_API_KEY" not in environment


def test_failed_generated_test_is_never_reported_as_success(tmp_path: Path) -> None:
    root, repository, generated = generated_project(tmp_path)
    output = root / generated.output_relative_path
    (output / "tests/unit/test_failure.py").write_text(
        "def test_generated_failure():\n    assert False\n", encoding="utf-8"
    )
    service = EvaluationService(repository, repository, generation_clock(), SandboxEvaluator(), root)
    result = service.evaluate(
        PROJECT_ID,
        revision=2,
        owner_session_id=OWNER,
        idempotency_key="evaluate-failure",
    )
    assert result.report.unit_tests.exit_code != 0
    assert result.report.decision == "failed_safe"
    assert result.artifact.validation_status == "invalid"


def generation_clock() -> FrozenClock:
    return FrozenClock(NOW)
