# Sentinel Taskmaster

Agente autónomo con autonomía controlada para investigar y resolver incidentes operativos simulados.

El proyecto se desarrolla para el track **Taskmaster** del All Things Agentic Hackathon. El agente investigará evidencia, formulará un plan, ejecutará herramientas autorizadas, evaluará riesgo y presupuesto, solicitará aprobación humana para acciones de alto impacto y verificará de forma independiente el resultado.

## Objetivo del MVP

Recuperar un servicio de pedidos simulado que presenta alta latencia y acumulación de cola, sin ejecutar acciones de alto riesgo sin aprobación humana explícita.

## Estructura

```text
app/        Interfaz y API
agent/      Orquestación ADK, planificación y verificación
simulator/  Estado del incidente y herramientas simuladas
policy/     Riesgo, presupuesto y aprobación humana
memory/     Memoria validada y trayectoria de auditoría
tests/      Pruebas deterministas
docs/       Arquitectura y guía de demostración
scripts/    Utilidades de desarrollo
```

## Próximo paso

El simulador determinista de la primera demo está disponible. Desde la raíz del proyecto, ejecútalo así:

```bash
python -m unittest discover -s tests -v
```

El fixture representa un servicio de pedidos degradado, incluye evidencia válida y un log engañoso, y expone las herramientas de inspección, reinicio de worker, limpieza de lote corrupto, escalado de capacidad y verificación de recuperación.

## Capa de control

`policy/` es la ruta obligatoria para ejecutar una acción: compara la propuesta con el catálogo permitido, evalúa riesgo y presupuesto, crea aprobaciones humanas para acciones altas y conserva eventos de auditoría. Las herramientas del simulador no son el mecanismo de autorización.

## Orquestador Sentinel

`agent/SentinelTaskmaster` ya une simulador, memoria validada y control de políticas. En el MVP crea el plan conservador, pone evidencia no confiable en cuarentena, ejecuta la recuperación reversible y la cierra con verificación independiente. También expone una ruta de demostración de acción alta que se pausa hasta recibir una decisión humana. El planificador determinista será el punto de reemplazo para Google ADK + Gemini.

## Interfaz local de demo

Inicia el panel local desde la raíz del proyecto:

```bash
python -m app.server
```

Abre `http://127.0.0.1:8000`. El panel permite investigar el incidente, ejecutar el plan conservador, mostrar la propuesta de escalado de alto riesgo, aprobarla o rechazarla, y reiniciar la demostración. No requiere dependencias externas.

## Gemini + Vertex AI (modo con límite de coste)

La integración está preparada, pero permanece desactivada por defecto. Copia `.env.example` a `.env`, instala `requirements.txt` y autentica tu sesión con Application Default Credentials. El adaptador limita cada planificación a tres pasos y 350 tokens; solamente recibe evidencia con procedencia verificada. Cualquier propuesta de Gemini queda validada contra el catálogo de acciones antes de alcanzar la política o las herramientas. Si Vertex AI no está configurado o falla, Sentinel vuelve al plan determinista.
