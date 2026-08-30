# Estado actual del producto

## Propósito

Este documento es el punto de entrada autoritativo para conocer qué ofrece actualmente
Collaborative Taskmaster Studio. Los documentos `01` a `16` conservan decisiones, hitos y diseños
anteriores; cuando exista una diferencia entre ellos y este documento, prevalecen este estado, la
arquitectura final y el comportamiento cubierto por pruebas.

Fecha de consolidación: **2026-08-30**.

## Resultado disponible

El Studio permite que una persona:

1. inicie una sesión verificada o utilice el modo local de desarrollo;
2. mantenga varias conversaciones aisladas;
3. adjunte documentos, imágenes y datasets a una conversación;
4. converse con Gemini 3.7 Flash y utilice capacidades de solo lectura autorizadas;
5. convierta una necesidad en un diseño estructurado de Taskmaster;
6. apruebe explícitamente el diseño antes de construir;
7. construya el proyecto con Antigravity SDK cuando el runtime está disponible, o con el
   constructor local seguro identificado como respaldo;
8. autorice por separado las pruebas del laboratorio;
9. publique el Taskmaster aprobado en el catálogo;
10. converse con el Taskmaster según su especialidad y le solicite ejecuciones concretas;
11. recupere conversaciones, agentes y proyectos durables al volver a iniciar sesión.

La aplicación pública está en:

<https://collaborative-taskmaster-studio-760216344589.us-central1.run.app>

## Componentes activos

| Área | Implementación actual |
| --- | --- |
| Interfaz | HTML, CSS y JavaScript sin framework de cliente. |
| API | FastAPI bajo `/api/v1`. |
| Modelo | Gemini 3.7 Flash mediante Vertex AI. |
| Identidad | Google Identity Platform en producción; sesión anónima en desarrollo. |
| Persistencia | Firestore para estado y catálogos; almacenamiento local como adaptador de desarrollo. |
| Proyectos | Árboles de archivos bajo `projects/` y réplica privada en Cloud Storage. |
| Construcción | Cloud Tasks y trabajador autenticado; Antigravity aislado o constructor controlado. |
| Laboratorio | Proceso sin credenciales, red bloqueada, tiempo acotado y aprobación humana. |
| Documentos | Extracción segura de texto, imágenes y datasets por sesión. |
| Visualizaciones | Artefactos de datos deterministas renderizados con Google Charts. |

## Dos experiencias conversacionales

### Socio del Studio

El chat principal ayuda a investigar, aclarar requisitos, interpretar archivos y diseñar un agente.
Gemini puede conversar libremente dentro de las políticas, pero no obtiene permiso para escribir
proyectos, aprobar pruebas ni producir efectos externos.

### Taskmaster publicado

Un agente del catálogo conserva un perfil conversacional derivado de su misión, entradas,
resultados y límites. Puede explicar qué hace, pedir aclaraciones y distinguir una consulta de una
solicitud de ejecución. Las acciones continúan sujetas a las conexiones y aprobaciones declaradas.

La especificación detallada está en
[`24_EXPERIENCIA_CHAT_Y_TASKMASTER.md`](24_EXPERIENCIA_CHAT_Y_TASKMASTER.md).

## Archivos y análisis

La sesión admite hasta doce documentos. Los formatos de oficina, texto e imagen se cargan de forma
directa hasta 25 MiB. CSV y XLSX pueden alcanzar 600 MiB mediante bloques de 8 MiB. Un archivo
grande no se entrega completo al modelo: el servidor produce una vista acotada y estructurada.

Los datasets pueden generar hasta ocho artefactos por solicitud. La interfaz muestra gráficos de
barras, barras horizontales, líneas, áreas, sectores, anillos y dispersión, además de métricas,
observaciones y la tabla de datos utilizada.

Los contratos y límites están en
[`25_ARCHIVOS_DATASETS_Y_VISUALIZACIONES.md`](25_ARCHIVOS_DATASETS_Y_VISUALIZACIONES.md).

## Persistencia y aislamiento

El propietario efectivo nunca se toma directamente de un identificador arbitrario del navegador.
En producción se deriva de la identidad verificada. Conversaciones, documentos, conexiones,
agentes, construcciones y proyectos quedan separados por ese propietario.

La eliminación de un chat borra su registro del almacenamiento local del navegador y solicita su
eliminación al repositorio del servidor. Eliminar un documento lo quita de la biblioteca de la
sesión y de las referencias activas de las conversaciones. Los proyectos publicados utilizan un
manifiesto de hashes y no se empaquetan en ZIP o RAR.

Consulte
[`26_IDENTIDAD_PERSISTENCIA_Y_CICLO_DE_DATOS.md`](26_IDENTIDAD_PERSISTENCIA_Y_CICLO_DE_DATOS.md).

## Operación

Cloud Run atiende la interfaz y la API. Cloud Tasks desacopla las fases de construcción y prueba de
la petición web. El trabajador valida OIDC, recupera el contrato durable y ejecuta operaciones
idempotentes. El runtime de Antigravity vive en un entorno Python separado para evitar conflictos
de dependencias y reducir la autoridad del proceso web.

El procedimiento operativo se encuentra en
[`27_OPERACION_PRODUCCION_Y_DIAGNOSTICO.md`](27_OPERACION_PRODUCCION_Y_DIAGNOSTICO.md).

## Garantías que no cambian

- Diseñar una capacidad no equivale a conectarla.
- El modelo no aprueba sus propias acciones.
- Los datos recuperados de archivos, páginas o conectores son contenido no confiable.
- Las operaciones de lectura y escritura se separan.
- El constructor declara si utilizó Antigravity o el respaldo local.
- Un proyecto solo entra al catálogo después de completar el laboratorio.
- Los fallos de nube cierran el flujo de forma segura; no se simula éxito.
- La telemetría visible describe acciones y resultados, nunca razonamiento privado del modelo.

## Documentación relacionada

- Contrato canónico: [`02_CONTRATO_TASKMASTER_SPECIFICATION.md`](02_CONTRATO_TASKMASTER_SPECIFICATION.md).
- Arquitectura desplegada: [`12_DIAGRAMA_ARQUITECTURA_FINAL.md`](12_DIAGRAMA_ARQUITECTURA_FINAL.md).
- Ingeniero y plugins: [`17_ARQUITECTURA_INGENIERO_PLUGINS.md`](17_ARQUITECTURA_INGENIERO_PLUGINS.md).
- Cola y trabajador: [`21_COLA_CONSTRUCCION_AISLADA.md`](21_COLA_CONSTRUCCION_AISLADA.md) y
  [`22_WORKER_CLOUD_TASKS.md`](22_WORKER_CLOUD_TASKS.md).
- Validación funcional actual: [`28_VALIDACION_Y_DEMO_ACTUAL.md`](28_VALIDACION_Y_DEMO_ACTUAL.md).
