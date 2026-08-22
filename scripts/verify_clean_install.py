"""Verify a clean local installation without credentials or Google Cloud calls."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

EXCLUDED_NAMES = frozenset(
    {
        ".coverage",
        ".env",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".studio-data",
        ".venv",
        "__pycache__",
        "generated",
    }
)
CLOUD_ENABLE_FLAGS = (
    "STUDIO_ENABLE_VERTEX",
    "STUDIO_ENABLE_MODEL_QUESTIONS",
    "STUDIO_ENABLE_MODEL_BRIEFING",
    "STUDIO_ENABLE_MODEL_SPECIFICATION",
    "STUDIO_ENABLE_MODEL_REVISION",
    "STUDIO_ENABLE_FIRESTORE",
)
SENSITIVE_ENVIRONMENT = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
)


def copy_source_snapshot(source: Path, destination: Path) -> int:
    """Copy a checkout-like snapshot while excluding local state and credentials."""

    copied = 0
    for current, directories, filenames in os.walk(source):
        directories[:] = [name for name in directories if name not in EXCLUDED_NAMES]
        current_path = Path(current)
        for filename in filenames:
            if filename in EXCLUDED_NAMES or Path(filename).suffix in {".pyc", ".pyo"}:
                continue
            candidate = current_path / filename
            relative = candidate.relative_to(source)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            copied += 1
    if not (destination / "pyproject.toml").is_file():
        raise RuntimeError("El snapshot limpio no contiene pyproject.toml.")
    return copied


def clean_local_environment(
    source: Mapping[str, str],
    *,
    port: int,
    data_directory: Path,
    generated_root: Path,
) -> dict[str, str]:
    """Return a local-only environment that cannot opt into cloud integrations."""

    environment = dict(source)
    for name in (*CLOUD_ENABLE_FLAGS, *SENSITIVE_ENVIRONMENT):
        environment.pop(name, None)
    for name in ("PORT", "K_SERVICE", "K_REVISION", "K_CONFIGURATION"):
        environment.pop(name, None)
    environment.update(
        {
            "PYTHONUTF8": "1",
            "STUDIO_ENV": "development",
            "STUDIO_HOST": "127.0.0.1",
            "STUDIO_PORT": str(port),
            "STUDIO_DATA_DIRECTORY": str(data_directory),
            "STUDIO_GENERATED_ROOT": str(generated_root),
            **{name: "false" for name in CLOUD_ENABLE_FLAGS},
        }
    )
    return environment


def _run(command: list[str], *, cwd: Path, environment: Mapping[str, str]) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=dict(environment),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    if completed.returncode != 0:
        raise RuntimeError(
            f"Falló el comando ({completed.returncode}): {' '.join(command)}\n{output[-4000:]}"
        )
    return output


def _venv_python(virtual_environment: Path) -> Path:
    if os.name == "nt":
        return virtual_environment / "Scripts" / "python.exe"
    return virtual_environment / "bin" / "python"


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _probe(url: str, *, timeout_seconds: float = 30.0) -> tuple[int, bytes]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2.0) as response:  # noqa: S310 - fixed localhost URL
                return response.status, response.read()
        except (URLError, TimeoutError, ConnectionError) as error:
            last_error = error
            time.sleep(0.2)
    raise RuntimeError(f"El servidor limpio no respondió en {url}: {last_error}")


def _count_passed(output: str) -> int:
    matches = re.findall(r"(\d+) passed", output)
    if not matches:
        raise RuntimeError(f"Pytest no informó pruebas aprobadas:\n{output[-2000:]}")
    return int(matches[-1])


def _count_skipped(output: str) -> int:
    matches = re.findall(r"(\d+) skipped", output)
    return int(matches[-1]) if matches else 0


def verify_clean_install(source: Path, *, keep_temporary: bool = False) -> dict[str, Any]:
    """Create a new venv, install, test and probe a local-only server."""

    source = source.resolve()
    temporary = Path(tempfile.mkdtemp(prefix="taskmaster-studio-clean-"))
    snapshot = temporary / "source"
    virtual_environment = temporary / "venv"
    runtime_data = temporary / "runtime-data"
    generated_root = temporary / "generated"
    server_log = temporary / "server.log"
    server: subprocess.Popen[str] | None = None

    try:
        file_count = copy_source_snapshot(source, snapshot)
        port = _available_port()
        environment = clean_local_environment(
            os.environ,
            port=port,
            data_directory=runtime_data,
            generated_root=generated_root,
        )

        _run([sys.executable, "-m", "venv", str(virtual_environment)], cwd=snapshot, environment=environment)
        python = _venv_python(virtual_environment)
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "-e",
                ".[dev]",
            ],
            cwd=snapshot,
            environment=environment,
        )
        import_output = _run(
            [str(python), "-c", "from app.main import create_app; assert create_app"],
            cwd=snapshot,
            environment=environment,
        )
        journey_output = _run(
            [
                str(python),
                "-m",
                "pytest",
                "tests/integration/test_h10_journey_local.py",
            ],
            cwd=snapshot,
            environment=environment,
        )
        full_output = _run(
            [str(python), "-m", "pytest"],
            cwd=snapshot,
            environment=environment,
        )

        with server_log.open("w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [str(python), "-m", "app.main"],
                cwd=snapshot,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            base_url = f"http://127.0.0.1:{port}"
            live_status, live_body = _probe(f"{base_url}/health/live")
            ready_status, ready_body = _probe(f"{base_url}/health/ready")
            root_status, root_body = _probe(f"{base_url}/")
            openapi_status, openapi_body = _probe(f"{base_url}/openapi.json")

        live = json.loads(live_body)
        ready = json.loads(ready_body)
        openapi = json.loads(openapi_body)
        if live.get("status") != "alive":
            raise RuntimeError(f"Liveness inesperado: {live!r}")
        if ready.get("status") != "ready":
            raise RuntimeError(f"Readiness inesperado: {ready!r}")
        if b"Collaborative Taskmaster Studio" not in root_body:
            raise RuntimeError("La interfaz raíz no contiene el título esperado.")
        if openapi.get("info", {}).get("title") != "Collaborative Taskmaster Studio":
            raise RuntimeError("El contrato OpenAPI no contiene el título esperado.")

        return {
            "schema_version": "1.0.0",
            "status": "passed",
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "snapshot_files": file_count,
            "installation": 'python -m pip install --no-input -e ".[dev]"',
            "package_import": "passed" if not import_output else "passed_with_output",
            "journey_tests_passed": _count_passed(journey_output),
            "full_tests_passed": _count_passed(full_output),
            "full_tests_skipped": _count_skipped(full_output),
            "http": {
                "/": root_status,
                "/health/live": live_status,
                "/health/ready": ready_status,
                "/openapi.json": openapi_status,
            },
            "cloud_integrations_enabled": False,
            "credentials_copied": False,
            "temporary_directory_removed": not keep_temporary,
        }
    except Exception as error:
        log_excerpt = ""
        if server_log.is_file():
            log_excerpt = server_log.read_text(encoding="utf-8", errors="replace")[-3000:]
        if log_excerpt:
            raise RuntimeError(f"{error}\nÚltimo log del servidor:\n{log_excerpt}") from error
        raise
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            with suppress(subprocess.TimeoutExpired):
                server.wait(timeout=5)
            if server.poll() is None:
                server.kill()
                server.wait(timeout=5)
        if not keep_temporary:
            shutil.rmtree(temporary, ignore_errors=True)
        else:
            print(f"Directorio temporal conservado: {temporary}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verifica una instalación local limpia sin invocar Google Cloud."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Raíz del repositorio que se copiará a una carpeta temporal.",
    )
    parser.add_argument(
        "--keep-temporary",
        action="store_true",
        help="Conserva la carpeta temporal para diagnóstico.",
    )
    args = parser.parse_args()
    result = verify_clean_install(args.source, keep_temporary=args.keep_temporary)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
