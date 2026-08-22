# Respuestas de modelo grabadas

`recordings.json` contiene respuestas sintéticas que imitan el contrato de Vertex AI. No son
capturas literales del proveedor y no incluyen prompts completos, credenciales, encabezados ni
datos personales.

Cada entrada tiene un identificador estable, una operación (`purpose`), el payload estructurado y
metadatos deterministas. Las respuestas grandes pueden referenciar otro fixture dentro de
`tests/fixtures`; el reproductor impide resolver rutas fuera de ese directorio y aplica una
proyección declarada.

Para actualizar el catálogo:

1. mantenga `schema_version` y los identificadores estables o cree una versión nueva;
2. elimine secretos y contenido sensible antes de guardar;
3. incluya al menos un caso inválido que demuestre el rechazo de deriva contractual;
4. ejecute `pytest tests/contract/test_h8_recorded_model_contracts.py` sin configurar Vertex AI.

Esta suite nunca debe crear un cliente de red ni consumir créditos de Google Cloud.
