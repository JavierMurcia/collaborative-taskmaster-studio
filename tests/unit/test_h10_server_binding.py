"""H10-02 server binding contract for local execution and Cloud Run."""

from collections.abc import Mapping

import pytest

import app.main as main_module
from app.server import ServerBinding, resolve_server_binding


def test_default_binding_remains_local_only() -> None:
    binding = resolve_server_binding({})

    assert binding.host == "127.0.0.1"
    assert binding.port == 8080
    assert binding.source == "default"


def test_local_binding_accepts_explicit_studio_variables() -> None:
    binding = resolve_server_binding(
        {
            "STUDIO_HOST": " 0.0.0.0 ",
            "STUDIO_PORT": " 9000 ",
        }
    )

    assert binding.host == "0.0.0.0"
    assert binding.port == 9000
    assert binding.source == "studio"


def test_cloud_run_port_forces_all_interfaces_and_wins_over_local_settings() -> None:
    binding = resolve_server_binding(
        {
            "PORT": " 8087 ",
            "STUDIO_HOST": "127.0.0.1",
            "STUDIO_PORT": "9000",
        }
    )

    assert binding.host == "0.0.0.0"
    assert binding.port == 8087
    assert binding.source == "cloud_run"


def test_run_passes_the_resolved_binding_to_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(application: str, **options: object) -> None:
        captured["application"] = application
        captured.update(options)

    monkeypatch.setattr(
        main_module,
        "resolve_server_binding",
        lambda: ServerBinding("0.0.0.0", 8181, "cloud_run"),
    )
    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)

    main_module.run()

    assert captured == {
        "application": "app.main:app",
        "host": "0.0.0.0",
        "port": 8181,
        "reload": False,
    }


@pytest.mark.parametrize(
    ("environment", "variable"),
    [
        ({"PORT": ""}, "PORT"),
        ({"PORT": "http"}, "PORT"),
        ({"PORT": "0"}, "PORT"),
        ({"PORT": "65536"}, "PORT"),
        ({"STUDIO_PORT": "eight"}, "STUDIO_PORT"),
        ({"STUDIO_HOST": "   "}, "STUDIO_HOST"),
    ],
)
def test_invalid_bindings_fail_closed(
    environment: Mapping[str, str],
    variable: str,
) -> None:
    with pytest.raises(ValueError, match=variable):
        resolve_server_binding(environment)
