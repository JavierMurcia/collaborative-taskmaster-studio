# Arquitectura del Ingeniero de agentes y plugins

## Flujo implementado

```text
Gemini 3.7 Flash (socio colaborativo)
  → AgentDraft estructurado y visible
  → confirmación humana CONSTRUIR_AGENTE
  → contrato inmutable con SHA-256
  → selector automático de framework y plugins mínimos
  → orquestador Antigravity SDK o constructor local controlado
  → generador Google ADK / Gen AI SDK / Antigravity / Genkit
  → paquete con manifiesto, contrato y gateway de plugins
  → aprobación humana antes del laboratorio
  → pruebas sin red ni credenciales
  → catálogo persistente de agentes aprobados
```

Gemini diseña; no escribe en el repositorio, no aprueba pruebas y no activa conexiones. El
Ingeniero consume exclusivamente el contrato confirmado. Cada evento mostrado en el chat es una
acción o un resultado observable, no una cadena privada de razonamiento.

## Selector de frameworks

La elección es determinista y se calcula con la misión, flujo, entradas, salidas, restricciones y
acciones externas del borrador. Los destinos soportados son Google ADK, Google Gen AI SDK, Genkit
y la plantilla Antigravity. Que una plantilla exista no significa que su runtime esté instalado.
`GET /api/v1/meta` publica el constructor realmente activo y la disponibilidad comprobada de cada
backend.

La ruta segura por defecto es `controlled_adk`, ejecutada por
`ControlledConstructionOrchestrator`. Al instalar `google-antigravity` en un entorno Python
separado y declarar `STUDIO_AGENT_BUILDER=antigravity` junto con
`STUDIO_ANTIGRAVITY_PYTHON`, el Studio activa `AntigravitySdkOrchestrator`: primero genera
una base reproducible y luego permite que el SDK la inspeccione y refine exclusivamente mediante
tres herramientas confinadas (`list_project_files`, `read_project_file` y `write_project_file`).
El SDK no recibe navegador, red, terminal, credenciales ni acceso fuera de la carpeta del paquete.

El trabajador se inicia como un subproceso efímero para evitar el conflicto de `protobuf` entre el
SDK Antigravity y Vertex AI. Cada lectura y escritura queda registrada en
`.studio/antigravity-orchestration.json`; el
manifiesto se vuelve a calcular con checksums después de la orquestación. Las pruebas siguen siendo
una fase separada y requieren aprobación humana. Si el SDK no está instalado, el Studio no lo
simula ni le atribuye la construcción al modelo.

## Registro y gateway de plugins

El catálogo cerrado declara versión, proveedor, autenticación, permisos, operaciones, riesgo y
necesidad de aprobación. El selector elige como máximo tres plugins relevantes y evita añadir
integraciones que el agente no necesita.

Disponibles localmente:

- investigación web del Studio;
- lectura confinada del workspace;
- documentos adjuntos del Studio.

Declarados pero pendientes de conexión:

- Google Drive;
- GitHub;
- Gmail;
- Google Calendar.

Cada paquete contiene `plugins.json` y `studio_plugin_gateway.py`. El gateway falla de forma cerrada
si el plugin no fue declarado, necesita conexión, carece de adaptador o intenta escribir sin
aprobación humana. Las credenciales nunca se incorporan al ZIP.

## Catálogo de agentes

Solo una construcción con todas sus pruebas aprobadas puede entrar en `.studio-data/agent-catalog.json`.
Cada ficha conserva propietario de sesión, versión, icono, framework, constructor real, plugins,
digest del contrato y directorio del artefacto. Archivar una ficha no elimina de forma recursiva el
paquete generado.

La barra lateral obtiene las fichas desde `GET /api/v1/collaborative/agents`. El usuario puede
abrir la descripción del agente dentro del mismo chat o archivarlo. La ejecución con efectos reales
continúa bloqueada hasta conectar y aprobar los plugins correspondientes.

## Límites externos pendientes

La implementación local está completa y verificable. Estas capacidades dependen de estado externo
y no pueden activarse de manera honesta solo escribiendo código:

1. instalar y configurar Google Agents CLI en un entorno compatible;
2. instalar el SDK Antigravity en su entorno aislado y declarar su intérprete absoluto;
3. completar OAuth para Drive, GitHub, Gmail o Calendar;
4. otorgar IAM y desplegar en Gemini Enterprise Agent Platform.

Cada una permanece marcada como `setup_required` o `connection_required` hasta que exista la
autorización y la credencial administrada correspondiente.

La primera integración del SDK trabaja con un solo ingeniero y herramientas confinadas. La
delegación a subagentes especializados, la ejecución de terminal y la publicación automática no
están habilitadas en este hito; se incorporarán solo con presupuestos, políticas y aprobaciones
específicas.
