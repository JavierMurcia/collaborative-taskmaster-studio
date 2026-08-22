# Hito H10 — despliegue reproducible y rollback en Cloud Run

## Resultado

El 20 de agosto de 2026 se construyó, desplegó y validó Collaborative Taskmaster Studio en el
proyecto autorizado `sentinel-taskmaster-dev`. El servicio nuevo no sustituyó ningún servicio
Cloud Run activo porque el inventario inicial estaba vacío. H10-10 completó el recorrido integral
y H10-11 consolidó este procedimiento operativo y de reversión.

- Servicio: `collaborative-taskmaster-studio`
- Región: `us-central1`
- Revisión candidata: `collaborative-taskmaster-studio-00004-fqp`
- Tráfico: 100 % a la revisión lista
- Escalado: mínimo 0, máximo 1 y concurrencia 1
- Identidad: `taskmaster-studio-runtime@sentinel-taskmaster-dev.iam.gserviceaccount.com`
- Acceso: público mediante `roles/run.invoker` para `allUsers`
- URL: <https://collaborative-taskmaster-studio-760216344589.us-central1.run.app>

La imagen fue construida por Cloud Build, publicada en el repositorio regional e inmutable y
desplegada por digest, no mediante una etiqueta móvil:

- Build: `6fbd6f25-df34-4183-a530-08ef2a5c6e34`
- Digest: `sha256:3cedab2f2a07e62a2ae593d7b6f1cd78368c7528fd91f58723cc5363cf29c1a5`

La evidencia legible por máquinas está en
`infrastructure/cloud_run/deployment-evidence.json`.

## Recorrido de humo

El humo básico obtuvo HTTP 200 en liveness, startup, readiness y metadatos. El recorrido integral
de 13 pasos creó el proyecto `project_1c230bd56cfdc1de`, completó entrevista y briefing, incorporó
dos revisiones con feedback, registró aprobación humana, generó el artefacto Google ADK
`artifact_544fe7290e4d7ca3` y terminó el laboratorio con decisión `ready`. La trayectoria reunió 27
eventos auditables. Gemini completó tres operaciones y cinco respuestas inseguras o no disponibles
se resolvieron mediante fallback seguro sin interrumpir el flujo.

El recorrido puede repetirse de forma controlada. Sin `--functional` solo hace lecturas; con esa
bandera crea un único registro aislado para comprobar persistencia real:

```powershell
$url = "https://collaborative-taskmaster-studio-760216344589.us-central1.run.app"
.\.venv\Scripts\python.exe -m infrastructure.cloud_run.smoke_check --url $url
.\.venv\Scripts\python.exe -m infrastructure.cloud_run.smoke_check `
  --url $url --functional
```

En el equipo Windows actual, Python informa un error del almacén local de certificados al acceder
a HTTPS. La misma verificación pasó desde Cloud Shell. No se debe desactivar la validación TLS;
hasta reparar el almacén de confianza, ejecutar este comando en Cloud Shell o suministrar una CA
corporativa válida al entorno.

## Hallazgos incorporados

El despliegue real cerró dos huecos que las pruebas simuladas no revelaron:

1. La cuenta personalizada de Cloud Build necesita `roles/storage.objectViewer` limitado al
   bucket de fuentes `${PROJECT_ID}_cloudbuild`; no se concede a nivel del proyecto.
2. La CLI actual consulta APIs habilitadas con `gcloud services list --enabled`; el comando
   `gcloud services describe` no está disponible.

Además, Cloud Run omite la anotación `run.googleapis.com/minScale` cuando su valor es cero porque
es el valor predeterminado. El verificador acepta la ausencia como cero y rechaza cualquier valor
positivo.

## Coste y seguridad

El mínimo de instancias es cero, el máximo es uno y la concurrencia es uno durante esta demostración, por lo que
no permanece una instancia ociosa. Las llamadas, compilaciones, almacenamiento de imágenes,
Firestore y Vertex AI pueden generar consumo cuando se usan. No hay claves JSON ni API keys: Cloud
Run usa la identidad administrada y ADC. La base Firestore tiene protección contra borrado.

El presupuesto y sus limitaciones están documentados en
`docs/11_HITO_H10_PRESUPUESTO_ALERTAS.md`.

## Repetición del despliegue

Antes de cada despliegue se ejecutan las pruebas, se construye una etiqueta trazable y se despliega
el digest resultante. La revisión candidata debe recibir el 100 % del tráfico únicamente después de
que sus sondas y el recorrido de humo pasen. Las etiquetas móviles no son evidencia de la versión:
el digest y la revisión de Cloud Run son los identificadores autoritativos.

## Procedimiento de rollback

El rollback cambia únicamente el tráfico; no reconstruye imágenes, no borra revisiones y no modifica
Firestore. Debe ejecutarlo una identidad autorizada desde Cloud Shell o desde un equipo con TLS y
ADC válidos.

### 1. Capturar el estado antes del cambio

```powershell
$project = "sentinel-taskmaster-dev"
$region = "us-central1"
$service = "collaborative-taskmaster-studio"
$candidate = "collaborative-taskmaster-studio-00004-fqp"
$url = "https://collaborative-taskmaster-studio-760216344589.us-central1.run.app"

gcloud run services describe $service `
  --region $region --project $project `
  --format="yaml(status.traffic,status.latestReadyRevisionName)"

gcloud run revisions list --service $service `
  --region $region --project $project `
  --format="table(metadata.name,status.conditions[0].status,metadata.creationTimestamp)"
```

Seleccionar como `$fallback` una revisión anterior que aparezca lista y que tenga evidencia de humo
aprobada. La revisión `collaborative-taskmaster-studio-00002-9mk` pasó el humo de H10-09; antes de
usarla debe confirmarse que continúa disponible y lista.

### 2. Enviar el tráfico a la revisión anterior

```powershell
$fallback = "collaborative-taskmaster-studio-00002-9mk"

gcloud run services update-traffic $service `
  --region $region --project $project `
  --to-revisions "$fallback=100"

.\.venv\Scripts\python.exe -m infrastructure.cloud_run.smoke_check --url $url
```

La reversión se considera válida solamente si liveness, startup, readiness y metadatos responden
correctamente. Si el humo falla, se restaura inmediatamente la revisión candidata.

### 3. Restaurar la candidata cuando corresponda

```powershell
gcloud run services update-traffic $service `
  --region $region --project $project `
  --to-revisions "$candidate=100"

.\.venv\Scripts\python.exe -m infrastructure.cloud_run.smoke_check --url $url
```

No se debe borrar ninguna revisión hasta que el servicio elegido conserve el 100 % del tráfico y el
humo haya pasado. Los proyectos creados durante la demo son datos versionados; este rollback de
aplicación no intenta revertirlos ni eliminarlos.

## Evidencia y límites de verificación

- La revisión candidata, el build, el digest, el tráfico y el recorrido integral fueron confirmados
  desde Google Cloud y están registrados en `deployment-evidence.json`.
- El procedimiento de rollback usa comandos idempotentes de Cloud Run y un verificador incluido en
  el repositorio.
- H10-11 documenta el procedimiento, pero no desplaza tráfico deliberadamente sobre el servicio
  público: ejecutar una reversión real se reserva para una incidencia o un ensayo autorizado.
- En el equipo Windows actual, la consulta `gcloud run revisions list` no puede renovar el token por
  el error conocido del almacén local de certificados. No se desactivó TLS; la comprobación cloud
  debe ejecutarse desde Cloud Shell hasta reparar esa confianza local.

## Estado de H10-11

**Completado el 20 de agosto de 2026.** La documentación coincide con la revisión candidata final,
identifica una revisión anterior con humo aprobado, define validación posterior y restauración, y
separa explícitamente el rollback de aplicación de cualquier operación sobre datos.
