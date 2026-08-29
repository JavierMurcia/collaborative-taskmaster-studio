from __future__ import annotations

import json
from pathlib import Path

import pytest

from infrastructure.storage import CloudProjectArtifactStore, CloudStorageSettings
from studio.domain.errors import DomainError


class FakeBlob:
    def __init__(self, objects: dict[str, bytes], name: str) -> None:
        self._objects = objects
        self.name = name

    def upload_from_string(self, value: str | bytes, **kwargs: object) -> None:
        del kwargs
        self._objects[self.name] = value.encode() if isinstance(value, str) else value

    def upload_from_filename(self, filename: str) -> None:
        self._objects[self.name] = Path(filename).read_bytes()

    def download_as_bytes(self) -> bytes:
        return self._objects[self.name]

    def exists(self) -> bool:
        return self.name in self._objects


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self.objects, name)


class FakeClient:
    def __init__(self) -> None:
        self.bucket_instance = FakeBucket()

    def bucket(self, name: str) -> FakeBucket:
        assert name == "studio-projects-test"
        return self.bucket_instance


class FailingBlob(FakeBlob):
    def upload_from_string(self, value: str | bytes, **kwargs: object) -> None:
        del value, kwargs
        raise PermissionError("storage.objects.create denied")


class FailingBucket(FakeBucket):
    def blob(self, name: str) -> FailingBlob:
        return FailingBlob(self.objects, name)


class FailingClient(FakeClient):
    def __init__(self) -> None:
        self.bucket_instance = FailingBucket()


def _settings(**updates: object) -> CloudStorageSettings:
    return CloudStorageSettings(
        enabled=True,
        project="collaborative-taskmaster-dev",
        bucket="studio-projects-test",
        prefix="taskmaster-projects",
        **updates,
    )


def test_directory_is_uploaded_and_restored_without_archives(tmp_path: Path) -> None:
    client = FakeClient()
    store = CloudProjectArtifactStore(client, _settings())
    source = tmp_path / "projects" / "researcher"
    (source / "app").mkdir(parents=True)
    (source / "taskmaster.specification.json").write_text('{"name":"researcher"}')
    (source / "app" / "agent.py").write_text("agent = True\n")

    stored = store.persist_directory(
        owner_session_id="owner_one",
        project_id="agent_12345678",
        build_id="build_12345678",
        directory=source,
    )

    assert stored.uri.startswith("gs://studio-projects-test/taskmaster-projects/users/")
    assert stored.file_count == 2
    assert all(not name.endswith((".zip", ".rar")) for name in client.bucket_instance.objects)
    manifest_name = next(name for name in client.bucket_instance.objects if name.endswith("project-manifest.json"))
    manifest = json.loads(client.bucket_instance.objects[manifest_name])
    assert manifest["digest"] == stored.digest

    restored = tmp_path / "restored" / "researcher"
    store.restore_directory(
        owner_session_id="owner_one",
        uri=stored.uri,
        directory=restored,
        expected_digest=stored.digest,
    )
    assert (restored / "app" / "agent.py").read_text() == "agent = True\n"


def test_runtime_memory_is_restored_separately_from_immutable_manifest(tmp_path: Path) -> None:
    client = FakeClient()
    store = CloudProjectArtifactStore(client, _settings())
    source = tmp_path / "agent"
    source.mkdir()
    (source / "taskmaster.specification.json").write_text("{}")
    stored = store.persist_directory(
        owner_session_id="owner_one",
        project_id="agent_12345678",
        build_id="build_12345678",
        directory=source,
    )
    state = source / "runtime-state.json"
    state.write_text('{"runs":[{"run_id":"run_1"}]}')
    store.persist_file(
        owner_session_id="owner_one",
        uri=stored.uri,
        relative_path="runtime-state.json",
        source=state,
    )

    restored = tmp_path / "new-instance" / "agent"
    store.restore_directory(
        owner_session_id="owner_one",
        uri=stored.uri,
        directory=restored,
        expected_digest=stored.digest,
    )
    assert "run_1" in (restored / "runtime-state.json").read_text()


def test_restore_rejects_another_owner(tmp_path: Path) -> None:
    client = FakeClient()
    store = CloudProjectArtifactStore(client, _settings())
    source = tmp_path / "agent"
    source.mkdir()
    (source / "taskmaster.specification.json").write_text("{}")
    stored = store.persist_directory(
        owner_session_id="owner_one",
        project_id="agent_12345678",
        build_id="build_12345678",
        directory=source,
    )

    with pytest.raises(DomainError, match="pertenece"):
        store.restore_directory(
            owner_session_id="owner_two",
            uri=stored.uri,
            directory=tmp_path / "stolen",
            expected_digest=stored.digest,
        )


def test_storage_limits_are_enforced_before_upload(tmp_path: Path) -> None:
    client = FakeClient()
    store = CloudProjectArtifactStore(client, _settings(max_files=1))
    source = tmp_path / "agent"
    source.mkdir()
    (source / "one.txt").write_text("one")
    (source / "two.txt").write_text("two")

    with pytest.raises(DomainError, match="límites"):
        store.persist_directory(
            owner_session_id="owner_one",
            project_id="agent_12345678",
            build_id="build_12345678",
            directory=source,
        )
    assert client.bucket_instance.objects == {}


def test_storage_failure_preserves_safe_diagnostics(tmp_path: Path) -> None:
    store = CloudProjectArtifactStore(FailingClient(), _settings())
    source = tmp_path / "agent"
    source.mkdir()
    (source / "agent.py").write_text("agent = True\n")

    with pytest.raises(DomainError) as captured:
        store.persist_directory(
            owner_session_id="owner_one",
            project_id="agent_12345678",
            build_id="build_12345678",
            directory=source,
        )

    assert captured.value.code == "PROJECT_STORAGE_UNAVAILABLE"
    assert captured.value.context == {
        "exception_type": "PermissionError",
        "reason": "storage.objects.create denied",
    }

