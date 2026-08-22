# Pendientes antes de iniciar H9 — Firestore

El alcance implementado, las garantías y la evidencia completa de H9 están consolidados en
[`08_HITO_H9_FIRESTORE.md`](08_HITO_H9_FIRESTORE.md). Este documento conserva únicamente las
decisiones y acciones cloud todavía pendientes.

## Estado

El código alcanzó **H9-10**, pero utiliza persistencia local. H9-01 continúa pendiente como recurso
real: existe una declaración y una herramienta de aprovisionamiento, pero no se ha creado ni
verificado ninguna base Firestore para Collaborative Taskmaster Studio. H9-02 solamente inicializa
el cliente cuando se habilita de forma explícita. H9-03 implementa el repositorio raíz de proyectos,
pero no lo activa ni consulta una base real. H9-04 añade briefings versionados y revisiones
inmutables como subcolecciones, también validados únicamente con dobles locales.
H9-05 añade decisiones humanas separadas y una trayectoria de eventos ordenada, sin activar RPCs.
H9-06 completa las subcolecciones previstas con metadatos inmutables de artefactos.
H9-07 incorpora transacciones con reintentos acotados para las cuatro mutaciones críticas, todavía
verificadas exclusivamente con dobles locales y sin activar RPCs.
H9-08 declara y verifica los índices requeridos por las consultas actuales. No requiere índices
compuestos y no aplica cambios a Google Cloud.
H9-09 propaga una expiración fija a todos los documentos del agregado y declara seis políticas TTL
con exención de índice; tampoco las aplica todavía en Google Cloud.
H9-10 ejecuta la misma matriz contractual contra el repositorio en memoria y el adaptador Firestore
mediante un doble determinista. No usa emulador, credenciales, red ni recursos de Google Cloud.

## Decisiones que faltan

1. **Proyecto Google Cloud definitivo.** Elegir si se crea un proyecto independiente o se utiliza
   temporalmente uno existente. No se debe asumir `sentinel-taskmaster-dev`, porque pertenece a otro
   producto.
2. **Facturación y presupuesto.** Vincular la cuenta de facturación correcta y definir alertas antes
   de habilitar servicios con consumo.
3. **Identidad administradora.** Confirmar qué cuenta ejecutará el aprovisionamiento y que tenga
   `roles/datastore.owner` o permisos equivalentes para crear y consultar la base.
4. **Autenticación local.** Reparar la renovación de tokens de `gcloud`; actualmente falla la
   validación del certificado TLS. No se debe desactivar la verificación SSL como solución.
5. **Región definitiva.** Confirmar `us-central1` para Firestore y el futuro Cloud Run. La región de
   una base Firestore no puede cambiarse después de crearla.
6. **Nombre de base.** Confirmar `collaborative-taskmaster`. Se propone una base nombrada para evitar
   colisiones con `(default)` si durante desarrollo se comparte un proyecto.
7. **Política de ciclo de vida cloud.** Mantener edición Standard, concurrencia pesimista y
   protección contra borrado; decidir PITR y autorizar la aplicación de las políticas TTL ya
   declaradas.

## Preparación ya disponible

- declaración versionada en `infrastructure/firestore/database.json`;
- aprovisionador cerrado por defecto en `infrastructure/firestore/provisioning.py`;
- verificación de creación, idempotencia y deriva mediante dobles locales;
- variables de entorno propuestas en `.env.example`;
- cliente oficial Firestore disponible como extra opcional y desactivado en runtime por defecto.
- repositorio Firestore de proyectos implementado y probado con dobles documentales, pero inactivo.
- subcolecciones de briefings y revisiones implementadas con escrituras agrupadas e inactivas.
- subcolecciones de aprobaciones y eventos implementadas y probadas con dobles locales.
- subcolección de metadatos de artefactos implementada sin almacenar archivos ni contenido.
- transacciones críticas reintentables, límite configurable `1..10` y error de agotamiento
  sanitizado, todo comprobado sin acceso a Google Cloud.
- manifiesto de índices versionado y verificador offline ejecutado durante el arranque local.
- retención fija de siete días, configurable `1..30`, propagada a los seis grupos documentales y
  verificada sin asumir borrado en cascada.
- matriz contractual compartida con doce casos aprobados sobre los backends local y Firestore doble.

## Comprobaciones previas obligatorias

Ejecutar en una terminal autenticada, sustituyendo el ID real:

```powershell
gcloud auth list
gcloud config get-value project
gcloud billing projects describe TU_PROJECT_ID
gcloud firestore databases list --project=TU_PROJECT_ID
```

Después, revisar el plan sin modificar Google Cloud:

```powershell
python -m infrastructure.firestore.provisioning --project TU_PROJECT_ID
```

El resultado debe indicar `status: planned`, base `collaborative-taskmaster`, región
`us-central1`, tipo `FIRESTORE_NATIVE`, edición `STANDARD` y protección contra borrado habilitada.

## Condición para iniciar H9

H9-01 puede completarse cuando estén confirmados el proyecto, la facturación, la identidad y la región;
`gcloud` pueda enumerar bases sin errores; y el equipo autorice explícitamente ejecutar:

```powershell
python -m infrastructure.firestore.provisioning --project TU_PROJECT_ID --apply
```

Tras la creación se debe guardar evidencia no sensible de `describe`, comprobar que no se incluyeron
credenciales en el repositorio y entonces documentar H9-01 como completado. H9-02 ya implementó el
cliente y la inicialización; `runtime_enabled` seguirá siendo `false` mientras no se habilite.

## Fuera de alcance por ahora

- activación del repositorio Firestore en el runtime;
- migración de datos locales;
- aplicación real de la declaración de índices, si una consulta futura exige compuestos;
- permisos de la cuenta de servicio de Cloud Run;
- activación real de las políticas TTL y verificación de su estado;
- despliegue y pruebas contra Firestore real.
