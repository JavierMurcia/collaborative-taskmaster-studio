# Persistencia durable de Taskmasters

## Resultado

El Studio conserva siempre un proyecto terminado dentro de `projects/<nombre>/`. En desarrollo
esa carpeta es la copia canónica local. En Cloud Run, cuando se habilita el adaptador, cada archivo
se replica directamente en Cloud Storage y el catálogo multiusuario se guarda en Firestore.

No se generan ZIP, RAR ni otro contenedor. La estructura del proyecto permanece navegable.

## Flujo

1. El constructor crea el proyecto en `STUDIO_PROJECTS_ROOT`.
2. El laboratorio ejecuta sus verificaciones sin red ni credenciales.
3. Solo cuando todas pasan, el adaptador calcula una huella determinista del árbol.
4. Los archivos se escriben bajo un prefijo derivado de un hash del propietario.
5. El manifiesto se publica al final; por ello, una carga interrumpida nunca queda declarada como
   proyecto completo.
6. Firestore registra el URI, huella, tamaño y cantidad de archivos junto con el agente.
7. Después de un reinicio, el runtime restaura el árbol y valida cada SHA-256 antes de ejecutarlo.
8. `runtime-state.json` se conserva por separado para que la memoria cambie sin invalidar el
   manifiesto inmutable de construcción.

## Disposición de objetos

```text
gs://BUCKET/taskmaster-projects/
  users/<sha256-owner>/
    projects/<project-id>/
      builds/<build-id>/
        taskmaster.specification.json
        app/...
        tests/...
        _studio/project-manifest.json
        _studio/runtime-state.json
```

Los identificadores sin procesar del usuario no aparecen en las rutas del bucket.

## Configuración

```text
STUDIO_ENABLE_FIRESTORE=true
STUDIO_ENABLE_CLOUD_STORAGE=true
STUDIO_PROJECTS_BUCKET=<bucket privado existente>
STUDIO_PROJECTS_BUCKET_PREFIX=taskmaster-projects
STUDIO_PROJECTS_MAX_FILES=500
STUDIO_PROJECTS_MAX_TOTAL_BYTES=50000000
```

La imagen de producción instala `google-cloud-firestore` y `google-cloud-storage`. Ambos clientes
usan Application Default Credentials; no se permiten archivos JSON de claves.

## Permisos mínimos

La identidad de Cloud Run necesita:

- `roles/datastore.user` limitado a la base Firestore del Studio;
- acceso de lectura y escritura de objetos limitado al bucket de proyectos;
- ningún permiso para hacer públicos los objetos o administrar IAM del bucket.

La creación del bucket y la asignación del permiso son operaciones externas y deben ejecutarse de
forma explícita por la persona administradora. El código de aplicación no crea infraestructura.

## Comportamiento seguro

- Cloud Storage desactivado: el Studio utiliza exclusivamente `projects/`.
- Cloud Storage solicitado sin dependencia o ADC: la aplicación inicia, pero `health/ready`
  permanece no listo y no afirma durabilidad.
- fallo durante una carga: la construcción se marca fallida y no se registra en el catálogo.
- propietario diferente, URI ajeno, ruta relativa insegura o checksum incorrecto: restauración
  bloqueada.
- tamaño o cantidad de archivos superior al límite: carga bloqueada antes del primer objeto.

## Verificación

Las pruebas cubren configuración sin nube, límites previos a la carga, ausencia de archivos
comprimidos, aislamiento por propietario, manifiesto inmutable, restauración y memoria runtime.
