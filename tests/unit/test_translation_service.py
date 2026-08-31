import pytest

from studio.application.translation_service import TranslationService
from studio.domain.errors import DomainError
from studio.ports.model_gateway import ModelRequest, ModelResult


class RecordingTranslationGateway:
    def __init__(self) -> None:
        self.request: ModelRequest | None = None

    def generate_structured(self, request: ModelRequest) -> ModelResult:
        self.request = request
        return ModelResult.model_validate(
            {
                "payload": {"translations": ["Hello", "Keep **Markdown**"]},
                "metadata": {
                    "provider": "test",
                    "model": "gemini-test",
                    "location": "local",
                    "latency_ms": 1,
                    "usage": {},
                },
            }
        )


def test_translation_preserves_order_and_uses_a_non_executing_prompt() -> None:
    gateway = RecordingTranslationGateway()

    result = TranslationService(gateway, "gemini-test").translate(
        ("Hola", "Conserva **Markdown**"), "en"
    )

    assert result == ("Hello", "Keep **Markdown**")
    assert gateway.request is not None
    assert gateway.request.purpose == "conversation_translation"
    assert "Do not answer" in gateway.request.system_instruction
    assert "Return exactly one translation" in gateway.request.system_instruction
    assert "Translate short greetings" in gateway.request.system_instruction
    assert "Never leave ordinary source-language prose untranslated" in gateway.request.system_instruction
    assert gateway.request.response_schema["properties"]["translations"]["minItems"] == 2


def test_translation_requires_the_connected_model() -> None:
    with pytest.raises(DomainError) as caught:
        TranslationService(None, "gemini-test").translate(("Hola",), "en")

    assert caught.value.code == "TRANSLATION_UNAVAILABLE"
