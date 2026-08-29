# Worker durable con Cloud Tasks

## Propósito

La construcción y las pruebas de un Taskmaster se ejecutan después de responder a la petición web
que inició la operación. Esto evita perder un trabajo cuando Cloud Run termina, reinicia o reemplaza
la instancia que atendió al usuario.

## Contrato de entrega

- Cola: `taskmaster-builds` en `us-central1`.
- Destino: `POST /api/v1/internal/build-worker`.
- Cuerpo mínimo: versión de esquema, identificador de construcción y operación `construct` o `test`.
- Identidad: token OIDC de `taskmaster-build-worker@<project>.iam.gserviceaccount.com`.
- Audiencia: origen HTTPS exacto de la aplicación, sin ruta.
- Acuse: cualquier `2xx` termina la tarea; un fallo transitorio devuelve error para permitir un único
  reintento adicional.

El endpoint no confía en los encabezados de Cloud Tasks como identidad. Primero valida el token de
Google, su emisor, audiencia, correo verificado y cuenta de servicio exacta. Los encabezados se usan
solo como una segunda restricción para la cola y el nombre de tarea esperados.

## Recuperación e idempotencia

La tarea no transporta contratos ni datos del propietario. El worker recupera el trabajo desde
Firestore por su identificador y comprueba el estado durable antes de actuar. Una entrega repetida
no vuelve a construir ni probar una fase ya completada. Los identificadores deterministas de tarea
permiten que una repetición del despacho sea tratada como la misma operación.

## Límites operativos

- una tarea por segundo;
- una tarea concurrente;
- dos intentos como máximo;
- espera entre 10 y 60 segundos;
- plazo de entrega de cinco minutos por petición.

Si Cloud Tasks está habilitado pero el cliente, la configuración o la identidad no están disponibles,
la aplicación marca la orquestación como no preparada y falla de forma cerrada. En producción nunca
degrada silenciosamente al ejecutor residente en el proceso web.

## Permisos mínimos

- `taskmaster-studio-runtime`: `roles/cloudtasks.enqueuer` limitado a la cola y permiso para usar la
  cuenta de servicio del trabajador.
- `taskmaster-build-worker`: `roles/run.invoker` sobre el servicio.
- agente de servicio administrado de Cloud Tasks: `roles/cloudtasks.serviceAgent`, creado por Google
  al habilitar la API y verificado antes del despliegue.

La configuración reproducible está en `infrastructure/cloud_tasks/queue.json`; el plan validado está
en `infrastructure/cloud_tasks/provisioning.py`.

## Referencias oficiales

- [Crear tareas HTTP con autenticación](https://docs.cloud.google.com/tasks/docs/creating-http-target-tasks)
- [Configurar colas y reintentos](https://docs.cloud.google.com/tasks/docs/configuring-queues)
- [Autenticación servicio a servicio en Cloud Run](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)
