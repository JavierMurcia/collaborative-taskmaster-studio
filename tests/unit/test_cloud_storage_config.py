from __future__ import annotations

import pytest

from infrastructure.storage import CloudStorageSettings, initialize_cloud_storage
from studio.domain.errors import DomainError


def test_cloud_storage_is_disabled_by_default() -> None:
    settings = CloudStorageSettings.from_environment({})
    runtime = initialize_cloud_storage(settings)
    assert runtime.readiness.status == "disabled"
    assert runtime.client is None


def test_enabled_storage_requires_a_bucket() -> None:
    with pytest.raises(DomainError, match="bucket"):
        CloudStorageSettings.from_environment({"STUDIO_ENABLE_CLOUD_STORAGE": "true"})


def test_client_initialization_uses_adc_without_network_calls() -> None:
    credentials = object()
    client = object()
    settings = CloudStorageSettings.from_environment(
        {
            "STUDIO_ENABLE_CLOUD_STORAGE": "true",
            "GOOGLE_CLOUD_PROJECT": "collaborative-taskmaster-dev",
            "STUDIO_PROJECTS_BUCKET": "studio-projects-test",
        }
    )

    runtime = initialize_cloud_storage(
        settings,
        credentials_loader=lambda **kwargs: (credentials, "detected-project"),
        client_factory=lambda **kwargs: client,
    )

    assert runtime.readiness.status == "ready"
    assert runtime.client is client

