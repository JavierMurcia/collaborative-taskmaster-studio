# H10-10 — recorrido integral del Collaborative Partner

## Propósito

Este recorrido demuestra que el producto no es un chat aislado. Una sola ejecución crea un
proyecto, guía una entrevista, captura requisitos, produce una especificación Taskmaster, incorpora
feedback, exige aprobación humana, genera un proyecto Google ADK y lo evalúa en el laboratorio.

## Secuencia verificable

1. crear un proyecto aislado con sesión e idempotencia propias;
2. iniciar la entrevista colaborativa;
3. responder las tres preguntas del catálogo, aunque Gemini cambie su redacción;
4. comprobar que el briefing está completo;
5. confirmar el briefing mediante una acción humana;
6. crear la revisión 1 de la especificación;
7. registrar feedback oficial sobre autonomía y prompt injection;
8. crear y comparar la revisión 2;
9. aprobar explícitamente la revisión 2;
10. generar el Taskmaster Google ADK con manifiesto y checksums;
11. ejecutar sus pruebas y tres escenarios de laboratorio;
12. exigir decisión `ready`;
13. leer la trayectoria auditable completa.

El recorrido se detiene ante cualquier código HTTP inesperado, pregunta fuera del catálogo,
briefing incompleto, aprobación ausente, artefacto inválido, escenario fallido o evento faltante.
Nunca intenta continuar silenciosamente.

## Ejecución local

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\integration\test_h10_journey_local.py
```

La prueba usa la composición real de servicios, el repositorio en memoria y el generador completo.
No utiliza credenciales ni consume Google Cloud. La imagen de producción incluye el extra
`laboratory`, que aporta `pytest` exclusivamente para validar los Taskmasters generados.

## Ejecución desplegada

El comando siguiente sí crea datos, puede invocar Gemini y puede consumir créditos. Debe ejecutarse
una sola vez por revisión candidata:

```powershell
$url = "https://collaborative-taskmaster-studio-760216344589.us-central1.run.app"
.\.venv\Scripts\python.exe -m infrastructure.cloud_run.journey_check `
  --url $url --timeout 90
```

La salida no contiene prompts completos, respuestas sensibles ni credenciales. Conserva IDs,
decisión del laboratorio, contadores de eventos del modelo y los 13 pasos HTTP.

## Criterio de aprobación

- revisión humana aprobada: 2;
- framework generado: Google ADK;
- validación del artefacto: `valid`;
- decisión del laboratorio: `ready`;
- escenarios normal, fallo y seguridad aprobados;
- eventos de briefing confirmado, aprobación, generación y evaluación presentes;
- eventos de Gemini o fallback seguro visibles en la trayectoria.

## Evidencia desplegada — 2026-08-20

- servicio: `https://collaborative-taskmaster-studio-760216344589.us-central1.run.app`;
- revisión: `collaborative-taskmaster-studio-00004-fqp`, con 100 % del tráfico;
- imagen inmutable: `sha256:3cedab2f2a07e62a2ae593d7b6f1cd78368c7528fd91f58723cc5363cf29c1a5`;
- límites de costo: cero instancias mínimas, una instancia máxima y concurrencia uno;
- recorrido: 13 pasos HTTP aprobados, revisión humana 2 y laboratorio `ready`;
- trazabilidad: 27 eventos, tres generaciones completadas por Gemini 3.5 Flash y cinco
  fallbacks seguros;
- controles demostrados: Gemini personalizó el proceso cuando respetó el contrato y el socio
  bloqueó tres preguntas que intentaron cambiar el alcance autorizado. Las dos operaciones de
  especificación no disponibles conservaron el estado y usaron el diseñador determinista.

La prueba real generó el proyecto `project_1c230bd56cfdc1de` y el artefacto
`artifact_544fe7290e4d7ca3`. No se registran credenciales, prompts completos ni el identificador de
sesión del usuario.
