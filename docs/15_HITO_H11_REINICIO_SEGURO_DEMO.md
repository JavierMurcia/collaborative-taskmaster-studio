# H11-05 — reinicio seguro de la demostración

## Resultado

La interfaz dispone de **Reiniciar demo**. La operación restaura el proyecto activo con los datos
oficiales de H11-04 y exige escribir literalmente `REINICIAR_DEMO` antes de continuar.

## Garantías

- el encabezado `X-Studio-Session` identifica al propietario;
- una sesión no puede reiniciar el proyecto de otra;
- solo se reemplaza el agregado indicado en la URL;
- se eliminan sus revisiones, aprobaciones, eventos y metadatos de artefactos;
- se elimina únicamente `generated/<project_id>` después de validar que la ruta permanece dentro
  de la raíz generada;
- otros proyectos y sus archivos permanecen intactos;
- repetir la misma clave de idempotencia produce el mismo snapshot;
- el snapshot vuelve a versión 1, estado `idea`, sin revisión activa y sin trayectoria visible;
- Firestore usa borrado recursivo sobre el documento exacto, incluidas sus subcolecciones;
- el reinicio se registra en el log administrativo `studio.demo_reset`, fuera de la trayectoria
  visible que acaba de limpiarse.

El identificador lógico del fixture continúa siendo `academic_delivery_project`. La instancia de
almacenamiento conserva el identificador del proyecto activo para evitar colisiones entre sesiones
de la demostración pública.

## Contrato HTTP

```http
POST /api/v1/projects/{project_id}/demo/reset
X-Studio-Session: <sesión propietaria>
Idempotency-Key: <operación única>
Content-Type: application/json

{"confirmation":"REINICIAR_DEMO"}
```

La respuesta incluye el snapshot inicial, una trayectoria vacía, el identificador del fixture, el
recibo administrativo del reinicio y si existían archivos generados que retirar.

## Recorrido visual

1. Dentro del proyecto, pulse **Reiniciar demo**.
2. Lea el alcance mostrado en el diálogo.
3. Escriba `REINICIAR_DEMO`.
4. Pulse **Restaurar estado inicial**.
5. La interfaz abre el primer turno de la entrevista oficial.

## Verificación

```powershell
py -3.13 -m pytest tests\application\test_h11_demo_reset.py
```

La evidencia estructurada está en
[`evidence/h11-05-demo-reset.json`](evidence/h11-05-demo-reset.json).

## Siguiente historia

H11-06 debe preparar y ensayar el guion cronometrado del video de demostración.
