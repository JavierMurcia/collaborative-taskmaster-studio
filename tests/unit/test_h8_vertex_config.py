from collections.abc import Mapping

import pytest

from infrastructure.vertex import VertexSettings, inspect_vertex_readiness
from studio.domain.errors import DomainError


def vertex_environment(**overrides: str) -> Mapping[str, str]:
    values = {
        "STUDIO_ENABLE_VERTEX": "true",
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_CLOUD_PROJECT": "sentinel-taskmaster-dev",
        "GOOGLE_CLOUD_LOCATION": "global",
        "STUDIO_GEMINI_MODEL": "gemini-3.5-flash",
        "STUDIO_VERTEX_API_VERSION": "v1",
    }
    values.update(overrides)
    return values


def test_vertex_is_fail_closed_and_does_not_inspect_adc_when_disabled() -> None:
    called = False

    def forbidden_loader(
        *, scopes: tuple[str, ...], quota_project_id: str | None
    ) -> tuple[object, str | None]:
        nonlocal called
        called = True
        raise AssertionError("ADC must not be inspected in local mode")

    settings = VertexSettings.from_environment({})
    readiness = inspect_vertex_readiness(settings, credentials_loader=forbidden_loader)

    assert readiness.status == "disabled"
    assert readiness.cloud_calls_enabled is False
    assert called is False
    assert settings.max_model_output_tokens == 8_192
    assert settings.max_model_questions_per_project == 3


def test_model_limits_are_explicitly_configurable() -> None:
    settings = VertexSettings.from_environment(
        {
            "STUDIO_MAX_MODEL_OUTPUT_TOKENS": "2048",
            "STUDIO_MAX_MODEL_QUESTIONS_PER_PROJECT": "1",
        }
    )

    assert settings.max_model_output_tokens == 2_048
    assert settings.max_model_questions_per_project == 1


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("STUDIO_MAX_MODEL_OUTPUT_TOKENS", "63"),
        ("STUDIO_MAX_MODEL_OUTPUT_TOKENS", "8193"),
        ("STUDIO_MAX_MODEL_QUESTIONS_PER_PROJECT", "-1"),
        ("STUDIO_MAX_MODEL_QUESTIONS_PER_PROJECT", "many"),
    ],
)
def test_model_limits_fail_closed(name: str, value: str) -> None:
    with pytest.raises(DomainError) as captured:
        VertexSettings.from_environment({name: value})

    assert captured.value.code == "MODEL_LIMIT_INVALID"
    assert captured.value.context["variable"] == name


def test_vertex_reports_ready_with_adc_without_refreshing_credentials() -> None:
    captured: dict[str, object] = {}

    def fake_loader(
        *, scopes: tuple[str, ...], quota_project_id: str | None
    ) -> tuple[object, str | None]:
        captured["scopes"] = scopes
        captured["quota_project_id"] = quota_project_id
        return object(), "sentinel-taskmaster-dev"

    settings = VertexSettings.from_environment(vertex_environment())
    readiness = inspect_vertex_readiness(settings, credentials_loader=fake_loader)

    assert readiness.status == "ready"
    assert readiness.adc_available is True
    assert readiness.credentials_source == "application_default"
    assert readiness.cloud_calls_enabled is False
    assert captured["quota_project_id"] == "sentinel-taskmaster-dev"


def test_vertex_requires_explicit_google_vertex_mode() -> None:
    with pytest.raises(DomainError, match="GOOGLE_GENAI_USE_VERTEXAI") as captured:
        VertexSettings.from_environment(
            vertex_environment(GOOGLE_GENAI_USE_VERTEXAI="false")
        )

    assert captured.value.code == "VERTEX_MODE_NOT_CONFIRMED"


def test_model_questions_require_a_second_explicit_gate() -> None:
    with pytest.raises(DomainError) as captured:
        VertexSettings.from_environment(
            {
                "STUDIO_ENABLE_MODEL_QUESTIONS": "true",
                "STUDIO_ENABLE_VERTEX": "false",
            }
        )

    assert captured.value.code == "MODEL_QUESTIONS_REQUIRE_VERTEX"


def test_model_questions_can_be_explicitly_enabled_with_vertex() -> None:
    settings = VertexSettings.from_environment(
        vertex_environment(STUDIO_ENABLE_MODEL_QUESTIONS="true")
    )

    assert settings.enabled is True
    assert settings.model_questions_enabled is True


def test_model_briefing_requires_its_own_explicit_gate() -> None:
    with pytest.raises(DomainError) as captured:
        VertexSettings.from_environment(
            {
                "STUDIO_ENABLE_MODEL_BRIEFING": "true",
                "STUDIO_ENABLE_VERTEX": "false",
            }
        )

    assert captured.value.code == "MODEL_BRIEFING_REQUIRES_VERTEX"


def test_model_briefing_can_be_enabled_independently_from_questions() -> None:
    settings = VertexSettings.from_environment(
        vertex_environment(STUDIO_ENABLE_MODEL_BRIEFING="true")
    )

    assert settings.model_briefing_enabled is True
    assert settings.model_questions_enabled is False


def test_model_specification_requires_its_own_explicit_gate() -> None:
    with pytest.raises(DomainError) as captured:
        VertexSettings.from_environment(
            {
                "STUDIO_ENABLE_MODEL_SPECIFICATION": "true",
                "STUDIO_ENABLE_VERTEX": "false",
            }
        )

    assert captured.value.code == "MODEL_SPECIFICATION_REQUIRES_VERTEX"


def test_model_specification_can_be_enabled_independently() -> None:
    settings = VertexSettings.from_environment(
        vertex_environment(STUDIO_ENABLE_MODEL_SPECIFICATION="true")
    )

    assert settings.model_specification_enabled is True
    assert settings.model_questions_enabled is False
    assert settings.model_briefing_enabled is False


def test_model_revision_requires_its_own_explicit_gate() -> None:
    with pytest.raises(DomainError) as captured:
        VertexSettings.from_environment(
            {
                "STUDIO_ENABLE_MODEL_REVISION": "true",
                "STUDIO_ENABLE_VERTEX": "false",
            }
        )

    assert captured.value.code == "MODEL_REVISION_REQUIRES_VERTEX"


def test_model_revision_can_be_enabled_independently() -> None:
    settings = VertexSettings.from_environment(
        vertex_environment(STUDIO_ENABLE_MODEL_REVISION="true")
    )

    assert settings.model_revision_enabled is True
    assert settings.model_specification_enabled is False


def test_vertex_rejects_api_keys_because_h8_uses_adc() -> None:
    with pytest.raises(DomainError, match="ADC") as captured:
        VertexSettings.from_environment(vertex_environment(GOOGLE_API_KEY="secret"))

    assert captured.value.code == "VERTEX_API_KEY_FORBIDDEN"
    assert "secret" not in str(captured.value)


def test_vertex_reports_unavailable_adc_without_leaking_exception_details() -> None:
    def unavailable_loader(
        *, scopes: tuple[str, ...], quota_project_id: str | None
    ) -> tuple[object, str | None]:
        raise RuntimeError("secret credential path")

    settings = VertexSettings.from_environment(vertex_environment())
    readiness = inspect_vertex_readiness(settings, credentials_loader=unavailable_loader)
    serialized = readiness.model_dump_json()

    assert readiness.status == "adc_unavailable"
    assert readiness.adc_available is False
    assert "secret credential path" not in serialized


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        ("STUDIO_ENABLE_VERTEX", "perhaps", "VERTEX_BOOLEAN_INVALID"),
        ("GOOGLE_CLOUD_PROJECT", "Invalid Project", "VERTEX_PROJECT_INVALID"),
        ("STUDIO_VERTEX_API_VERSION", "v1beta", "VERTEX_API_VERSION_INVALID"),
    ],
)
def test_vertex_rejects_invalid_configuration(name: str, value: str, code: str) -> None:
    with pytest.raises(DomainError) as captured:
        VertexSettings.from_environment(vertex_environment(**{name: value}))

    assert captured.value.code == code
