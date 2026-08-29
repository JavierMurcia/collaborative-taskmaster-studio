"""Cloud Storage directory replication without archive formats."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path, PurePosixPath
from typing import Any

from infrastructure.storage.config import CloudStorageSettings
from studio.domain.errors import DomainError
from studio.ports.project_storage import StoredProject

_MANIFEST = "_studio/project-manifest.json"
_LOGGER = logging.getLogger(__name__)


class CloudProjectArtifactStore:
    def __init__(self, client: Any, settings: CloudStorageSettings) -> None:
        if not settings.enabled or not settings.bucket:
            raise ValueError("Cloud Storage must be enabled")
        self._client = client
        self._settings = settings
        self._bucket = client.bucket(settings.bucket)

    def persist_directory(
        self,
        *,
        owner_session_id: str,
        project_id: str,
        build_id: str,
        directory: Path,
    ) -> StoredProject:
        root = directory.resolve()
        files = _files(root)
        total = sum(path.stat().st_size for path in files)
        if len(files) > self._settings.max_files or total > self._settings.max_total_bytes:
            raise DomainError(
                "PROJECT_STORAGE_LIMIT_EXCEEDED",
                "El proyecto supera los límites permitidos para almacenamiento durable.",
                context={"files": len(files), "bytes": total},
            )
        owner_hash = hashlib.sha256(owner_session_id.encode("utf-8")).hexdigest()
        prefix = f"{self._settings.prefix}/users/{owner_hash}/projects/{_segment(project_id)}/builds/{_segment(build_id)}"
        manifest_files: list[dict[str, object]] = []
        aggregate = hashlib.sha256()
        try:
            for path in files:
                relative = path.relative_to(root).as_posix()
                content = path.read_bytes()
                checksum = hashlib.sha256(content).hexdigest()
                aggregate.update(relative.encode("utf-8"))
                aggregate.update(b"\0")
                aggregate.update(bytes.fromhex(checksum))
                self._bucket.blob(f"{prefix}/{relative}").upload_from_string(content)
                manifest_files.append({"path": relative, "sha256": checksum, "bytes": len(content)})
            digest = aggregate.hexdigest()
            manifest = {
                "schema_version": "1.0.0",
                "owner_hash": owner_hash,
                "project_id": project_id,
                "build_id": build_id,
                "digest": digest,
                "file_count": len(files),
                "total_bytes": total,
                "files": manifest_files,
            }
            self._bucket.blob(f"{prefix}/{_MANIFEST}").upload_from_string(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                content_type="application/json",
            )
        except DomainError:
            raise
        except Exception as error:
            _LOGGER.exception(
                "Cloud Storage failed while persisting project %s for build %s",
                project_id,
                build_id,
            )
            raise DomainError(
                "PROJECT_STORAGE_UNAVAILABLE",
                "No fue posible guardar el proyecto en Cloud Storage.",
                context=_storage_error_context(error),
            ) from error
        return StoredProject(f"gs://{self._settings.bucket}/{prefix}", digest, len(files), total)

    def restore_directory(
        self,
        *,
        owner_session_id: str,
        uri: str,
        directory: Path,
        expected_digest: str,
    ) -> None:
        bucket_name, prefix = _parse_uri(uri)
        if bucket_name != self._settings.bucket:
            raise DomainError("PROJECT_STORAGE_SCOPE_INVALID", "El proyecto pertenece a otro almacenamiento.")
        owner_hash = hashlib.sha256(owner_session_id.encode("utf-8")).hexdigest()
        if f"/users/{owner_hash}/" not in f"/{prefix}/":
            raise DomainError("PROJECT_ACCESS_DENIED", "El proyecto no pertenece al usuario autenticado.")
        try:
            manifest = json.loads(self._bucket.blob(f"{prefix}/{_MANIFEST}").download_as_bytes())
            if manifest.get("digest") != expected_digest or manifest.get("owner_hash") != owner_hash:
                raise DomainError("PROJECT_ARTIFACT_INVALID", "La copia durable del proyecto no coincide con su catálogo.")
            target = directory.resolve()
            target.mkdir(parents=True, exist_ok=True)
            aggregate = hashlib.sha256()
            for item in manifest.get("files", []):
                relative = _relative(str(item.get("path", "")))
                content = self._bucket.blob(f"{prefix}/{relative.as_posix()}").download_as_bytes()
                checksum = hashlib.sha256(content).hexdigest()
                if checksum != item.get("sha256"):
                    raise DomainError("PROJECT_ARTIFACT_INVALID", "Un archivo durable no superó la verificación.")
                aggregate.update(relative.as_posix().encode("utf-8"))
                aggregate.update(b"\0")
                aggregate.update(bytes.fromhex(checksum))
                output = (target / Path(*relative.parts)).resolve()
                output.relative_to(target)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(content)
            if aggregate.hexdigest() != expected_digest:
                raise DomainError("PROJECT_ARTIFACT_INVALID", "El proyecto restaurado no superó la verificación.")
            runtime_blob = self._bucket.blob(f"{prefix}/_studio/runtime-state.json")
            if runtime_blob.exists():
                (target / "runtime-state.json").write_bytes(runtime_blob.download_as_bytes())
        except DomainError:
            raise
        except Exception as error:
            _LOGGER.exception("Cloud Storage failed while restoring %s", uri)
            raise DomainError(
                "PROJECT_STORAGE_UNAVAILABLE",
                "No fue posible restaurar el proyecto durable.",
                context=_storage_error_context(error),
            ) from error

    def persist_file(
        self,
        *,
        owner_session_id: str,
        uri: str,
        relative_path: str,
        source: Path,
    ) -> None:
        bucket_name, prefix = _parse_uri(uri)
        owner_hash = hashlib.sha256(owner_session_id.encode("utf-8")).hexdigest()
        if bucket_name != self._settings.bucket or f"/users/{owner_hash}/" not in f"/{prefix}/":
            raise DomainError("PROJECT_ACCESS_DENIED", "El proyecto no pertenece al usuario autenticado.")
        relative = _relative(relative_path)
        try:
            object_name = (
                f"{prefix}/_studio/runtime-state.json"
                if relative.as_posix() == "runtime-state.json"
                else f"{prefix}/{relative.as_posix()}"
            )
            self._bucket.blob(object_name).upload_from_filename(str(source))
        except Exception as error:
            _LOGGER.exception("Cloud Storage failed while persisting runtime state for %s", uri)
            raise DomainError(
                "PROJECT_STORAGE_UNAVAILABLE",
                "No fue posible guardar el estado del agente.",
                context=_storage_error_context(error),
            ) from error


def _storage_error_context(error: Exception) -> dict[str, object]:
    context: dict[str, object] = {"exception_type": type(error).__name__}
    code = getattr(error, "code", None)
    if callable(code):
        code = code()
    if code is not None:
        context["status_code"] = str(code)
    reason = str(error).replace("\r", " ").replace("\n", " ").strip()
    if reason:
        context["reason"] = reason[:500]
    return context


def _files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise DomainError("PROJECT_DIRECTORY_MISSING", "La carpeta del proyecto no existe.")
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise DomainError("PROJECT_SYMLINK_FORBIDDEN", "Los proyectos no pueden contener enlaces simbólicos.")
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _segment(value: str) -> str:
    if not value or not value.replace("-", "").replace("_", "").isalnum():
        raise DomainError("PROJECT_STORAGE_ID_INVALID", "El identificador del proyecto no es válido.")
    return value


def _parse_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise DomainError("PROJECT_STORAGE_URI_INVALID", "La ubicación durable no es válida.")
    bucket, separator, prefix = uri[5:].partition("/")
    if not separator or not bucket or not prefix:
        raise DomainError("PROJECT_STORAGE_URI_INVALID", "La ubicación durable no es válida.")
    return bucket, prefix.rstrip("/")


def _relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise DomainError("PROJECT_ARTIFACT_PATH_INVALID", "La copia durable contiene una ruta insegura.")
    return path
