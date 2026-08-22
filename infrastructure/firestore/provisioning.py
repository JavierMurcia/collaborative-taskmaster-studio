"""Idempotent, opt-in provisioning for the H9 Firestore database."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

DEFINITION_PATH = Path(__file__).with_name("database.json")
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
DATABASE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,61}[a-z0-9]$")
Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class FirestoreDatabaseDefinition:
    schema_version: str
    database_id: str
    location: str
    type: str
    edition: str
    concurrency_mode: str
    delete_protection: str


@dataclass(frozen=True, slots=True)
class ProvisioningResult:
    status: str
    project_id: str
    database: FirestoreDatabaseDefinition
    command: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "project_id": self.project_id,
            "database": asdict(self.database),
            "command": list(self.command),
        }


def load_database_definition(path: Path = DEFINITION_PATH) -> FirestoreDatabaseDefinition:
    payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    expected = {
        "schema_version",
        "database_id",
        "location",
        "type",
        "edition",
        "concurrency_mode",
        "delete_protection",
    }
    if set(payload) != expected:
        raise ValueError("La declaración Firestore contiene campos desconocidos o ausentes.")
    definition = FirestoreDatabaseDefinition(**payload)
    if definition.schema_version != "1.0.0":
        raise ValueError("La versión de la declaración Firestore no está soportada.")
    if not DATABASE_ID_PATTERN.fullmatch(definition.database_id):
        raise ValueError("El ID de la base Firestore no es válido.")
    if definition.type != "FIRESTORE_NATIVE" or definition.edition != "STANDARD":
        raise ValueError("H9-01 requiere Firestore Native Standard.")
    if definition.concurrency_mode != "PESSIMISTIC":
        raise ValueError("H9-01 requiere concurrencia pesimista.")
    if definition.delete_protection != "DELETE_PROTECTION_ENABLED":
        raise ValueError("H9-01 requiere protección contra borrado.")
    return definition


def create_command(
    project_id: str,
    definition: FirestoreDatabaseDefinition,
    *,
    gcloud: str = "gcloud",
) -> tuple[str, ...]:
    _validate_project_id(project_id)
    return (
        gcloud,
        "firestore",
        "databases",
        "create",
        f"--project={project_id}",
        f"--database={definition.database_id}",
        f"--location={definition.location}",
        "--type=firestore-native",
        "--edition=standard",
        "--concurrency-mode=pessimistic",
        "--delete-protection",
        "--quiet",
    )


def provision_database(
    project_id: str,
    *,
    apply: bool = False,
    gcloud: str = "gcloud",
    runner: Runner = subprocess.run,
) -> ProvisioningResult:
    definition = load_database_definition()
    command = create_command(project_id, definition, gcloud=gcloud)
    if not apply:
        return ProvisioningResult("planned", project_id, definition, command)

    _run(
        runner,
        (
            gcloud,
            "services",
            "enable",
            "firestore.googleapis.com",
            f"--project={project_id}",
            "--quiet",
        ),
    )
    listed = _run(
        runner,
        (
            gcloud,
            "firestore",
            "databases",
            "list",
            f"--project={project_id}",
            "--format=json",
        ),
    )
    databases = cast(list[dict[str, Any]], json.loads(listed.stdout or "[]"))
    existing = next(
        (
            item
            for item in databases
            if str(item.get("name", "")).rsplit("/", 1)[-1] == definition.database_id
        ),
        None,
    )
    if existing is not None:
        _assert_matches(existing, definition)
        return ProvisioningResult("existing", project_id, definition, command)

    _run(runner, command)
    described = _run(
        runner,
        (
            gcloud,
            "firestore",
            "databases",
            "describe",
            f"--project={project_id}",
            f"--database={definition.database_id}",
            "--format=json",
        ),
    )
    _assert_matches(cast(dict[str, Any], json.loads(described.stdout)), definition)
    return ProvisioningResult("created", project_id, definition, command)


def _run(
    runner: Runner,
    command: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    return runner(command, check=True, capture_output=True, text=True)


def _assert_matches(
    actual: dict[str, Any],
    expected: FirestoreDatabaseDefinition,
) -> None:
    fields = {
        "locationId": expected.location,
        "type": expected.type,
        "edition": expected.edition,
        "concurrencyMode": expected.concurrency_mode,
        "deleteProtectionState": expected.delete_protection,
    }
    drift = {
        field: {"expected": value, "actual": actual.get(field)}
        for field, value in fields.items()
        if actual.get(field) != value
    }
    if drift:
        raise RuntimeError(f"La base Firestore existente no coincide: {json.dumps(drift)}")


def _validate_project_id(project_id: str) -> None:
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("El ID del proyecto de Google Cloud no es válido.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="ID exacto del proyecto Google Cloud")
    parser.add_argument("--gcloud", default="gcloud", help="Ruta al ejecutable gcloud")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Crea o verifica la base. Sin esta bandera solo muestra el plan.",
    )
    args = parser.parse_args(argv)
    result = provision_database(args.project, apply=args.apply, gcloud=args.gcloud)
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
