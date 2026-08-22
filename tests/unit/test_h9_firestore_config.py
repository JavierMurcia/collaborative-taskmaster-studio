from __future__ import annotations

import builtins
import importlib
import importlib.util
import json
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from infrastructure.firestore import (
    FirestoreProjectRepository,
    FirestoreReadiness,
    FirestoreRuntime,
    FirestoreSettings,
    FirestoreTransactionExecutor,
    TransactionRetryExhaustedError,
    initialize_firestore,
)
from infrastructure.firestore.check import main as check_main
from studio.domain.errors import DomainError


def firestore_environment(**overrides: str) -> Mapping[str, str]:
    values = {
        "STUDIO_ENABLE_FIRESTORE": "true",
        "GOOGLE_CLOUD_PROJECT": "collaborative-taskmaster-dev",
        "STUDIO_FIRESTORE_DATABASE": "collaborative-taskmaster",
        "STUDIO_FIRESTORE_LOCATION": "us-central1",
    }
    values.update(overrides)
    return values


def optional_module_available(name: str) -> bool:
    """Return false when either the module or one of its parents is absent."""

    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def test_firestore_is_fail_closed_without_loading_credentials_or_client() -> None:
    calls: list[str] = []

    def forbidden_loader(
        *, scopes: tuple[str, ...], quota_project_id: str | None
    ) -> tuple[object, str | None]:
        calls.append("credentials")
        raise AssertionError("ADC must not load in local mode")

    def forbidden_factory(
        *, project: str, database: str, credentials: object
    ) -> object:
        calls.append("client")
        raise AssertionError("Firestore client must not initialize in local mode")

    runtime = initialize_firestore(
        FirestoreSettings.from_environment({}),
        credentials_loader=forbidden_loader,
        client_factory=forbidden_factory,
    )

    assert runtime.readiness.status == "disabled"
    assert runtime.client is None
    assert runtime.readiness.cloud_calls_enabled is False
    assert calls == []


def test_cloud_composition_activates_firestore_repository_without_rpc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main_module = importlib.import_module("app.main")
    settings = FirestoreSettings.from_environment(firestore_environment())
    runtime = FirestoreRuntime(
        settings=settings,
        readiness=FirestoreReadiness(
            status="ready",
            configured=True,
            adc_available=True,
            client_initialized=True,
            database_verified=False,
            repository_active=False,
            cloud_calls_enabled=False,
            project="collaborative-taskmaster-dev",
            database_id="collaborative-taskmaster",
            location="us-central1",
            credentials_source="application_default",
            message="ready",
        ),
        client=object(),
    )
    monkeypatch.setattr(main_module, "initialize_firestore", lambda _: runtime)

    application = main_module.create_app(
        firestore_settings=settings,
        generated_root=tmp_path,
    )

    assert isinstance(application.state.services.repository, FirestoreProjectRepository)
    assert application.state.persistence_ready is True
    assert application.state.firestore_runtime.readiness.repository_active is True


def test_enabled_settings_match_versioned_database_declaration() -> None:
    settings = FirestoreSettings.from_environment(firestore_environment())

    assert settings.enabled is True
    assert settings.project == "collaborative-taskmaster-dev"
    assert settings.database_id == "collaborative-taskmaster"
    assert settings.location == "us-central1"
    assert settings.transaction_max_attempts == 5
    assert settings.demo_retention_days == 7


def test_firestore_transaction_attempts_are_configurable_and_bounded() -> None:
    settings = FirestoreSettings.from_environment(
        firestore_environment(STUDIO_FIRESTORE_TRANSACTION_MAX_ATTEMPTS="7")
    )

    assert settings.transaction_max_attempts == 7
    assert FirestoreTransactionExecutor(
        max_attempts=settings.transaction_max_attempts
    ).max_attempts == 7


def test_demo_retention_is_configurable_and_bounded() -> None:
    settings = FirestoreSettings.from_environment(
        firestore_environment(STUDIO_FIRESTORE_DEMO_RETENTION_DAYS="14")
    )

    assert settings.demo_retention_days == 14


def test_transaction_executor_forwards_limit_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firestore = pytest.importorskip("google.cloud.firestore")

    captured: dict[str, object] = {}

    class Client:
        def transaction(self, *, max_attempts: int = 5) -> object:
            captured["max_attempts"] = max_attempts
            return object()

    def transactional(operation: Any) -> Any:
        return lambda transaction: operation(transaction)

    monkeypatch.setattr(firestore, "transactional", transactional)
    result = FirestoreTransactionExecutor(max_attempts=4).execute(
        cast(Any, Client()), lambda _: "completed"
    )

    assert result == "completed"
    assert captured == {"max_attempts": 4}


def test_transaction_executor_identifies_sdk_retry_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firestore = pytest.importorskip("google.cloud.firestore")

    class Aborted(Exception):
        pass

    class Client:
        def transaction(self, *, max_attempts: int = 5) -> object:
            return object()

    def transactional(operation: Any) -> Any:
        def wrapped(transaction: object) -> object:
            try:
                raise Aborted("private commit detail")
            except Aborted as cause:
                raise ValueError("transaction failed") from cause

        return wrapped

    monkeypatch.setattr(firestore, "transactional", transactional)
    with pytest.raises(TransactionRetryExhaustedError) as captured:
        FirestoreTransactionExecutor(max_attempts=2).execute(
            cast(Any, Client()), lambda _: "unused"
        )

    assert "private" not in str(captured.value).casefold()


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"STUDIO_ENABLE_FIRESTORE": "maybe"}, "FIRESTORE_BOOLEAN_INVALID"),
        ({"GOOGLE_CLOUD_PROJECT": "Invalid Project"}, "FIRESTORE_PROJECT_INVALID"),
        (
            {"STUDIO_FIRESTORE_DATABASE": "another-database"},
            "FIRESTORE_DECLARATION_MISMATCH",
        ),
        (
            {"STUDIO_FIRESTORE_LOCATION": "nam5"},
            "FIRESTORE_DECLARATION_MISMATCH",
        ),
        (
            {"STUDIO_FIRESTORE_TRANSACTION_MAX_ATTEMPTS": "zero"},
            "FIRESTORE_TRANSACTION_ATTEMPTS_INVALID",
        ),
        (
            {"STUDIO_FIRESTORE_TRANSACTION_MAX_ATTEMPTS": "11"},
            "FIRESTORE_TRANSACTION_ATTEMPTS_INVALID",
        ),
        (
            {"STUDIO_FIRESTORE_DEMO_RETENTION_DAYS": "0"},
            "FIRESTORE_RETENTION_DAYS_INVALID",
        ),
        (
            {"STUDIO_FIRESTORE_DEMO_RETENTION_DAYS": "forever"},
            "FIRESTORE_RETENTION_DAYS_INVALID",
        ),
    ],
)
def test_firestore_configuration_fails_closed(
    overrides: dict[str, str], code: str
) -> None:
    with pytest.raises(DomainError) as captured:
        FirestoreSettings.from_environment(
            firestore_environment(**overrides)
        )

    assert captured.value.code == code


def test_initialization_uses_adc_and_named_database_without_rpc() -> None:
    captured: dict[str, object] = {}
    credentials = object()
    client = object()

    def loader(
        *, scopes: tuple[str, ...], quota_project_id: str | None
    ) -> tuple[object, str | None]:
        captured["scopes"] = scopes
        captured["quota_project_id"] = quota_project_id
        return credentials, quota_project_id

    def factory(*, project: str, database: str, credentials: object) -> object:
        captured["project"] = project
        captured["database"] = database
        captured["credentials"] = credentials
        return client

    runtime = initialize_firestore(
        FirestoreSettings.from_environment(firestore_environment()),
        credentials_loader=loader,
        client_factory=factory,
    )

    assert runtime.client is client
    assert runtime.readiness.status == "ready"
    assert runtime.readiness.client_initialized is True
    assert runtime.readiness.database_verified is False
    assert runtime.readiness.repository_active is False
    assert runtime.readiness.cloud_calls_enabled is False
    assert captured["project"] == "collaborative-taskmaster-dev"
    assert captured["database"] == "collaborative-taskmaster"
    assert captured["credentials"] is credentials


def test_adc_failure_is_sanitized_and_does_not_create_client() -> None:
    called = False

    def loader(
        *, scopes: tuple[str, ...], quota_project_id: str | None
    ) -> tuple[object, str | None]:
        raise RuntimeError("C:/secret/credential.json")

    def factory(*, project: str, database: str, credentials: object) -> object:
        nonlocal called
        called = True
        return object()

    runtime = initialize_firestore(
        FirestoreSettings.from_environment(firestore_environment()),
        credentials_loader=loader,
        client_factory=factory,
    )

    assert runtime.readiness.status == "adc_unavailable"
    assert runtime.client is None
    assert called is False
    assert "secret" not in runtime.readiness.model_dump_json().casefold()


def test_client_initialization_failure_is_sanitized() -> None:
    def loader(
        *, scopes: tuple[str, ...], quota_project_id: str | None
    ) -> tuple[object, str | None]:
        return object(), quota_project_id

    def factory(*, project: str, database: str, credentials: object) -> object:
        raise RuntimeError("private endpoint details")

    runtime = initialize_firestore(
        FirestoreSettings.from_environment(firestore_environment()),
        credentials_loader=loader,
        client_factory=factory,
    )

    assert runtime.readiness.status == "client_initialization_failed"
    assert runtime.readiness.adc_available is True
    assert runtime.client is None
    assert "private endpoint" not in runtime.readiness.model_dump_json()


def test_missing_firestore_dependency_is_reported_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def blocked_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "google.cloud" and "firestore" in fromlist:
            raise ModuleNotFoundError("google.cloud.firestore")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    def loader(
        *, scopes: tuple[str, ...], quota_project_id: str | None
    ) -> tuple[object, str | None]:
        return object(), quota_project_id

    runtime = initialize_firestore(
        FirestoreSettings.from_environment(firestore_environment()),
        credentials_loader=loader,
    )

    assert runtime.readiness.status == "missing_dependency"
    assert runtime.readiness.client_initialized is False


@pytest.mark.skipif(
    not optional_module_available("google.cloud.firestore"),
    reason="El extra firestore no está instalado.",
)
def test_official_client_initializes_named_database_without_rpc() -> None:
    from google.auth.credentials import AnonymousCredentials

    runtime = initialize_firestore(
        FirestoreSettings.from_environment(firestore_environment()),
        credentials_loader=lambda **_: (AnonymousCredentials(), None),
    )

    assert runtime.readiness.status == "ready"
    assert runtime.client is not None
    assert runtime.client.project == "collaborative-taskmaster-dev"
    assert runtime.client._database == "collaborative-taskmaster"


def test_firestore_sdk_is_an_optional_dependency_and_disabled_by_default() -> None:
    root = Path(__file__).resolve().parents[2]
    with (root / "pyproject.toml").open("rb") as stream:
        metadata = tomllib.load(stream)
    project = metadata["project"]
    assert isinstance(project, dict)
    dependencies = project["dependencies"]
    extras = project["optional-dependencies"]
    assert isinstance(dependencies, list)
    assert isinstance(extras, dict)
    assert extras["firestore"] == ["google-cloud-firestore>=2.28,<3"]
    assert all(not item.startswith("google-cloud-firestore") for item in dependencies)
    environment = (root / ".env.example").read_text(encoding="utf-8")
    assert "STUDIO_ENABLE_FIRESTORE=false" in environment


def test_check_command_reports_disabled_without_importing_firestore(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("STUDIO_ENABLE_FIRESTORE", "false")

    check_main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "disabled"
    assert payload["client_initialized"] is False
    assert payload["cloud_calls_enabled"] is False
