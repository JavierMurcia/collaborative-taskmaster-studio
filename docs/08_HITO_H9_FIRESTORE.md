# Hito H9 — Persistencia Firestore

## 1. Propósito

H9 prepara la persistencia final de Collaborative Taskmaster Studio en Cloud Firestore sin romper
el modo local ni activar recursos cloud de forma implícita. El hito cubre configuración, modelo de
datos, repositorio, concurrencia, retención, índices y pruebas de contrato.

Este documento registra el resultado consolidado de H9 a fecha **2026-08-14**.

> **Actualización posterior (2026-08-30):** las filas “pendiente” describen el corte de H9 y no el
> producto actual. La base nombrada se activó, el runtime productivo usa Firestore y las capas
> posteriores añadieron identidad multiusuario, conversaciones, catálogo y cola de construcción.
> La situación vigente está consolidada en
> [`26_IDENTIDAD_PERSISTENCIA_Y_CICLO_DE_DATOS.md`](26_IDENTIDAD_PERSISTENCIA_Y_CICLO_DE_DATOS.md).

## 2. Estado ejecutivo

| Área | Estado | Evidencia |
| --- | --- | --- |
| Implementación local H9-02 a H9-10 | Completada | Código, manifiestos y pruebas locales. |
| H9-01 — base Firestore real | Pendiente | Requiere proyecto, facturación, identidad y autorización. |
| Repositorio Firestore en runtime | Inactivo | `STUDIO_ENABLE_FIRESTORE=false` por defecto. |
| Llamadas a Google Cloud | Desactivadas | El arranque local no consulta ni escribe Firestore. |
| Índices y TTL en cloud | No aplicados | Los manifiestos están versionados y verificados offline. |
| Pruebas contra emulador o base real | Pendientes | H9-10 usa un doble documental determinista. |

La implementación no debe describirse todavía como “Firestore conectado”. La formulación correcta
es: **adaptador Firestore implementado y probado localmente; activación cloud pendiente**.

## 3. Historias de H9

| Historia | Resultado | Estado |
| --- | --- | --- |
| H9-01 | Crear o verificar una base Firestore Native. | Pendiente de nube. |
| H9-02 | Configuración validada e inicialización segura del cliente oficial. | Completa localmente. |
| H9-03 | Repositorio raíz `projects` con propietario, versión e idempotencia. | Completa localmente. |
| H9-04 | Briefings versionados y revisiones inmutables en subcolecciones. | Completa localmente. |
| H9-05 | Aprobaciones humanas y eventos auditables ordenados. | Completa localmente. |
| H9-06 | Metadatos inmutables de artefactos, sin almacenar archivos. | Completa localmente. |
| H9-07 | Transacciones críticas y reintentos acotados. | Completa localmente. |
| H9-08 | Inventario y manifiesto verificable de índices. | Completa localmente. |
| H9-09 | Retención TTL fija para todos los documentos de una demo. | Completa localmente. |
| H9-10 | Contrato compartido entre repositorio local y adaptador Firestore. | Completa con dobles. |

## 4. Arquitectura

El dominio y los casos de uso dependen de los puertos `ProjectRepository` y `EventRepository`. El
repositorio local y el adaptador Firestore implementan la misma superficie observable.

```text
API y servicios de aplicación
            |
            v
ProjectRepository + EventRepository
       |                       |
       v                       v
InMemory/JSON           FirestoreProjectRepository
                               |
                               v
                    Cliente oficial de Firestore
                    (desactivado por defecto)
```

El modelo Gemini y los agentes ADK no acceden directamente a Firestore. Toda persistencia pasa por
los servicios de aplicación y los puertos del dominio.

## 5. Modelo documental

La raíz de cada agregado es `projects/{project_id}`. Sus documentos subordinados se mantienen en
cinco subcolecciones:

```text
projects/{project_id}
├── briefings/vNNNNNN
├── revisions/rNNNNNN
├── approvals/{approval_id}
├── events/{event_id}
└── artifacts/{artifact_id}
```

### 5.1 Documento raíz

Conserva, entre otros datos:

- proyecto y propietario de sesión;
- estado y revisión activa;
- versión de concurrencia optimista;
- punteros a versiones y secuencia de eventos;
- huellas de operaciones idempotentes, nunca las claves sin procesar;
- `created_at`, `updated_at` y `expires_at`.

### 5.2 Briefings

- Cada modificación crea `briefings/vNNNNNN`.
- El identificador refleja una versión creciente.
- Las versiones anteriores permanecen disponibles para reconstrucción y auditoría.

### 5.3 Revisiones

- Cada diseño se almacena como `revisions/rNNNNNN`.
- Una revisión existente no se sobrescribe.
- La aprobación se proyecta al reconstruir el agregado sin sustituir el documento de revisión
  original.

### 5.4 Aprobaciones

- Cada decisión humana utiliza un documento independiente.
- Registra estado, responsable, fecha y nota.
- Una aprobación de alto impacto requiere una revisión activa válida.

### 5.5 Eventos

- Cada evento recibe una secuencia monotónica por proyecto.
- La consulta implementada ordena `events` por `sequence`.
- Se admite recuperar eventos posteriores a una secuencia conocida.
- No se almacena razonamiento privado del modelo.

### 5.6 Artefactos

Firestore conserva exclusivamente metadatos:

- identificador y revisión;
- ruta relativa validada;
- checksum SHA-256;
- framework y versión de plantilla;
- resultado de validación.

Los archivos generados, manifiestos completos e informes permanecen fuera de Firestore.

## 6. Garantías del repositorio

### 6.1 Aislamiento

- La lectura puede exigir `owner_session_id`.
- Un propietario diferente recibe una denegación explícita.
- Los snapshots devueltos son copias defensivas.
- Los identificadores y rutas se validan antes de construir referencias.

### 6.2 Idempotencia

- Cada mutación exige una clave idempotente no vacía.
- Se almacena una huella derivada de operación y contenido.
- Repetir la misma solicitud devuelve el resultado existente.
- Reutilizar la clave con contenido distinto genera un conflicto.

### 6.3 Concurrencia

- El agregado mantiene una versión creciente.
- Las escrituras exigen `expected_version`.
- Una versión obsoleta se rechaza y no sobrescribe cambios recientes.
- Las operaciones críticas verifican el estado raíz dentro de una transacción.

### 6.4 Atomicidad

La creación de proyecto y briefing inicial usa un lote. Las siguientes mutaciones utilizan
transacciones acotadas:

- añadir revisión;
- registrar aprobación;
- registrar metadatos de artefacto;
- añadir evento y asignar su secuencia.

Los reintentos se configuran entre `1` y `10`; el valor predeterminado es `5`. Al agotarse, la capa
de persistencia devuelve un error sanitizado y no expone detalles internos del proveedor.

### 6.5 Integridad

- Los subdocumentos deben pertenecer al proyecto raíz.
- Las revisiones y metadatos son inmutables.
- Un artefacto debe referenciar una revisión existente.
- La expiración de cada subdocumento debe coincidir con la raíz.
- Los documentos corruptos o incompletos se rechazan al reconstruir el agregado.

## 7. Base declarada

`infrastructure/firestore/database.json` define el recurso deseado:

| Propiedad | Valor |
| --- | --- |
| ID | `collaborative-taskmaster` |
| Región | `us-central1` |
| Tipo | `FIRESTORE_NATIVE` |
| Edición | `STANDARD` |
| Concurrencia | `PESSIMISTIC` |
| Protección contra borrado | Habilitada |

Esta declaración no crea la base. El aprovisionador funciona en modo plan salvo que se use una
opción explícita de aplicación.

## 8. Índices

La implementación actual tiene una consulta ordenada: eventos por `sequence`. El índice automático
de campo único cubre esa consulta, por lo que el manifiesto no declara índices compuestos.

El verificador offline rechaza:

- consultas sin cobertura;
- índices duplicados;
- índices compuestos innecesarios;
- anulaciones incompatibles con las consultas;
- estructuras desconocidas en el manifiesto.

`cloud_applied=false` indica que el manifiesto no se ha aplicado a Google Cloud.

## 9. Retención de sesiones demo

La retención predeterminada es de siete días desde la creación del proyecto y puede configurarse
entre `1` y `30` días. El vencimiento es fijo: una actualización posterior no extiende la vida de la
sesión.

El mismo `expires_at` se escribe en los seis grupos:

- `projects`;
- `briefings`;
- `revisions`;
- `approvals`;
- `events`;
- `artifacts`.

Cada grupo tiene su propia política porque no se presupone eliminación en cascada. El timestamp TTL
se exime de indexación porque no participa en consultas de la aplicación. Las políticas siguen sin
aplicarse en cloud.

## 10. Configuración

```dotenv
GOOGLE_CLOUD_PROJECT=
STUDIO_PERSISTENCE=local
STUDIO_ENABLE_FIRESTORE=false
STUDIO_FIRESTORE_DATABASE=collaborative-taskmaster
STUDIO_FIRESTORE_LOCATION=us-central1
STUDIO_FIRESTORE_TRANSACTION_MAX_ATTEMPTS=5
STUDIO_FIRESTORE_DEMO_RETENTION_DAYS=7
```

Reglas:

- Firestore permanece desactivado si `STUDIO_ENABLE_FIRESTORE` no es `true`.
- La base y región deben coincidir con la declaración versionada.
- El cliente oficial es una dependencia opcional.
- Construir el cliente no equivale a verificar la base ni activa el repositorio.
- Las credenciales nunca se guardan en `.env`, Firestore, artefactos o Git.

## 11. Estados de preparación

La API distingue explícitamente:

1. configuración cargada;
2. cliente inicializado;
3. base verificada;
4. repositorio activo;
5. llamadas cloud habilitadas.

En el estado actual solamente la configuración, el código y las verificaciones offline están
disponibles. El repositorio utilizado por la aplicación continúa siendo local.

## 12. Pruebas H9-10

La matriz contractual parametrizada ejecuta seis comportamientos sobre dos implementaciones, para
un total de doce casos:

1. creación, lectura, replay idempotente y copia defensiva;
2. aislamiento por propietario y proyecto inexistente;
3. conflicto al reutilizar una clave con otro contenido;
4. rechazo de una escritura con versión obsoleta;
5. ciclo revisión-aprobación-artefacto dentro del agregado;
6. orden, filtro y replay de eventos auditables.

Backends examinados:

- `InMemoryRepository`;
- `FirestoreProjectRepository` con doble documental determinista.

El doble reproduce referencias, snapshots, lotes, consultas y transacciones necesarios para el
contrato. No abre puertos, no carga ADC y no se conecta a Google Cloud.

## 13. Evidencia de verificación

Ejecución local registrada el 2026-08-14:

```text
247 passed, 5 warnings
Ruff: All checks passed
mypy: Success, 66 source files
git diff --check: sin errores de whitespace
```

Comandos reproducibles desde la raíz del proyecto:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe app studio agents infrastructure adapters sandbox
.\.venv\Scripts\python.exe -m infrastructure.firestore.indexes
.\.venv\Scripts\python.exe -m infrastructure.firestore.retention_check
```

La prueba contractual aislada se ejecuta con:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\contract\test_h9_repository_contract.py
```

## 14. Criterios de aceptación

### Cumplidos localmente

- El dominio depende de puertos y no del SDK de Firestore.
- Existe un adaptador que implementa el agregado completo.
- Idempotencia, concurrencia, inmutabilidad y aislamiento están probados.
- Índices y retención cuentan con manifiestos verificables offline.
- Los backends local y Firestore superan el mismo contrato observable.
- El modo local no afirma ni simula una conexión cloud.

### Pendientes para cierre cloud

- Seleccionar y confirmar el proyecto Google Cloud definitivo.
- Vincular facturación y configurar alertas de presupuesto.
- Confirmar identidad administradora y cuenta de servicio del runtime.
- Crear o verificar la base `collaborative-taskmaster` en `us-central1`.
- Aplicar y verificar las políticas TTL.
- Activar el adaptador desde la composición de la aplicación.
- Ejecutar pruebas contra el emulador oficial o una base de desarrollo aislada.
- Desplegar y validar el flujo de recuperación en Cloud Run.

## 15. Procedimiento seguro para continuar

Antes de crear recursos:

```powershell
gcloud auth list
gcloud config get-value project
gcloud billing projects describe TU_PROJECT_ID
gcloud firestore databases list --project=TU_PROJECT_ID
```

Después se revisa el plan sin mutar Google Cloud:

```powershell
.\.venv\Scripts\python.exe -m infrastructure.firestore.provisioning `
  --project TU_PROJECT_ID
```

La creación solo debe realizarse después de una autorización explícita y con presupuesto,
identidad, región y nombre confirmados. Los pasos pendientes se mantienen en
`docs/07_PENDIENTES_PRE_H9.md`.

## 16. Resultado

H9 deja lista la capa Firestore desde el punto de vista de diseño, código y contratos locales. No
deja lista todavía la operación cloud. Esta separación permite continuar el desarrollo sin costos
ni credenciales, y reduce el riesgo cuando se autorice la activación real.
