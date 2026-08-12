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

Definir el fixture del incidente: estado inicial, evidencias válidas y engañosas, catálogo de herramientas, políticas de riesgo y criterios de verificación.
