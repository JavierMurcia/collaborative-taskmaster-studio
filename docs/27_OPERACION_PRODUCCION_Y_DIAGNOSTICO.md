# Operación de producción y diagnóstico

## Propósito

Este runbook resume cómo opera la versión desplegada y cómo investigar fallos sin confundir el
estado del navegador con el estado durable.

## Topología

```text
Navegador
  -> Cloud Run / FastAPI
       -> Vertex AI / Gemini 3.7 Flash
       -> Identity Platform
       -> Firestore
       -> Cloud Storage privado
       -> Cloud Tasks
            -> endpoint interno autenticado
                 -> constructor aislado
                 -> runtime Antigravity separado
                 -> laboratorio sin credenciales ni red
```

La aplicación web no mantiene una construcción larga dentro de la petición que la inició. El
estado se guarda primero y Cloud Tasks entrega las fases `construct` y `test`.

## Configuración esencial

| Área | Variables o recursos |
| --- | --- |
| Modelo | `STUDIO_ENABLE_VERTEX`, `STUDIO_GEMINI_MODEL`, puertas `STUDIO_ENABLE_MODEL_*`. |
| Identidad | modo `identity_platform`, configuración pública de Firebase y cuenta de servicio. |
| Firestore | `STUDIO_ENABLE_FIRESTORE`, base `collaborative-taskmaster`. |
| Proyectos | `STUDIO_PROJECTS_ROOT`, `STUDIO_ENABLE_CLOUD_STORAGE`, bucket y prefijo. |
| Constructor | `STUDIO_AGENT_BUILDER`, `STUDIO_ANTIGRAVITY_PYTHON`. |
| Cola | proyecto, región, cola `taskmaster-builds`, URL y cuenta del trabajador. |
| Sandbox | `STUDIO_SANDBOX_TIMEOUT`. |

Los secretos no se escriben en `.env` dentro de la imagen. Producción utiliza identidades
administradas y Secret Manager cuando corresponde.

## Recursos y límites

- Cloud Run utiliza 2 GiB de memoria para admitir la inspección acotada de hojas grandes.
- El escalado permanece deliberadamente limitado para la demo; revisar máximo de instancias y
  concurrencia antes de aumentar tráfico.
- Cloud Tasks entrega una tarea por segundo y una concurrente, con dos intentos como máximo.
- La construcción de Cloud Build tiene un timeout de 900 segundos.
- Las cargas grandes se ensamblan en disco temporal y, por tanto, dependen de la vida de la
  instancia hasta completar la operación.

El límite de 600 MiB es un contrato de la aplicación, no una garantía de que cualquier libro de ese
tamaño será procesable. Las salvaguardas estructurales pueden rechazar XLSX expandidos o anómalos.

## Comprobaciones después de desplegar

1. Abrir la URL pública y comprobar que carga el CSS y JavaScript versionados.
2. Consultar la metainformación del runtime y verificar modelo, proveedor y constructor efectivo.
3. Iniciar sesión y confirmar que la identidad visible coincide con la cuenta elegida.
4. Crear y eliminar una conversación de prueba.
5. Cargar un documento pequeño, inspeccionarlo y eliminarlo.
6. Cargar dos CSV/XLSX y pedir un dashboard; confirmar gráficos y tabla de datos.
7. Abrir un Taskmaster publicado y realizar una consulta conversacional.
8. Iniciar una construcción controlada y comprobar transición de cola, construcción y aprobación.
9. Autorizar el laboratorio y confirmar publicación en el catálogo.
10. Revisar logs por errores, sin imprimir contenido sensible.

El recorrido automatizado existente puede ejecutarse con
`python -m infrastructure.cloud_run.journey_check --url <URL> --timeout 90` cuando las credenciales
y el entorno requeridos estén disponibles.

## Diagnóstico por síntoma

### La interfaz aparece sin estilos

- Comprobar que los assets estáticos están incluidos en la imagen.
- Verificar el identificador de versión de assets y la caché del navegador.
- Confirmar que la política de contenido permite los recursos previstos.

### Los chats reaparecen después de eliminarlos

- Distinguir caché local de repositorio cloud.
- Confirmar que el `DELETE` llegó con la identidad correcta.
- Verificar que la sincronización no vuelva a insertar una copia antigua.
- Probar en incógnito con la misma cuenta para aislar el almacenamiento local.

### Cloud Tasks no recibe la construcción

- Revisar que la cola, región y URL del servicio coincidan.
- Confirmar que el agente de servicio de Cloud Tasks puede emitir el token.
- Verificar audiencia, correo de la cuenta trabajadora y permiso de invocación.
- Buscar el trabajo durable en `agent_build_queue`; no volver a crear contratos manualmente.

### El runtime no muestra Antigravity

- Consultar el constructor efectivo publicado por `/api/v1/meta`.
- Verificar `STUDIO_AGENT_BUILDER=antigravity`.
- Confirmar que `STUDIO_ANTIGRAVITY_PYTHON` apunta al entorno aislado y que contiene el SDK
  compatible.
- Si no está disponible, la interfaz debe declarar el respaldo local; no corregir la etiqueta sin
  corregir el runtime.

### Una carga grande falla

- Confirmar que sea CSV o XLSX y no supere 600 MiB.
- Revisar el offset del último bloque aceptado.
- Verificar memoria y espacio temporal de la instancia.
- Para XLSX, revisar límites de miembros, expansión y cadenas compartidas.
- Cancelar la carga fallida antes de reintentar para liberar temporales.

### Gemini devuelve código en lugar de un gráfico

- Comprobar que la solicitud llegó con los documentos adjuntos.
- Verificar que `DatasetAnalysisService` generó artefactos.
- Inspeccionar el campo `artifacts` de la respuesta, no solo el texto del modelo.
- Confirmar que Google Charts pudo cargar `corechart` en el navegador.
- La función `_chart_aware_reply` debe retirar propuestas de código cuando existe un artefacto.

### Un proyecto desaparece al reiniciar

- Verificar el registro del agente en Firestore y el URI del manifiesto.
- Confirmar acceso del servicio al bucket privado.
- Restaurar únicamente mediante el adaptador que valida SHA-256.
- No reconstruir el árbol a partir del texto del chat.

## Rollback

Cloud Run conserva revisiones anteriores. Un rollback debe:

1. identificar la última revisión verificada;
2. mover tráfico sin cambiar contratos persistidos;
3. comprobar compatibilidad del esquema Firestore;
4. repetir el recorrido de humo;
5. registrar revisión, causa y resultado.

No se debe borrar la revisión defectuosa hasta conservar evidencia suficiente. Los trabajos de
Cloud Tasks en curso deben revisarse antes de redirigir tráfico a una versión con contrato distinto.

## Observabilidad segura

Registrar:

- identificadores opacos;
- estado anterior y nuevo;
- duración, tamaño y cantidad;
- proveedor y constructor efectivo;
- códigos de error y resultados de verificación.

No registrar:

- prompts o documentos completos;
- tokens OAuth o cookies;
- credenciales;
- razonamiento privado del modelo;
- contenido de correo, Drive o Calendar.

## Referencias

- Despliegue original: [`09_HITO_H10_CLOUD_RUN.md`](09_HITO_H10_CLOUD_RUN.md).
- Cola: [`21_COLA_CONSTRUCCION_AISLADA.md`](21_COLA_CONSTRUCCION_AISLADA.md).
- Worker: [`22_WORKER_CLOUD_TASKS.md`](22_WORKER_CLOUD_TASKS.md).
- Proyectos: [`20_PERSISTENCIA_DURABLE_PROYECTOS.md`](20_PERSISTENCIA_DURABLE_PROYECTOS.md).
- Identidad: [`18_IDENTIDAD_MULTIUSUARIO_Y_CONEXIONES.md`](18_IDENTIDAD_MULTIUSUARIO_Y_CONEXIONES.md).
