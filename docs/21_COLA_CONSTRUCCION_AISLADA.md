# Cola durable y construcción aislada

## Resultado

La construcción iniciada desde el taller ya no depende de la vida de la petición web. Cada cambio
de estado se registra en Firestore y, en producción, Cloud Tasks entrega cada fase a un endpoint
interno autenticado. El generador determinista continúa ejecutándose en un proceso separado que no
hereda credenciales de Google, Gemini ni OAuth.

En producción, el estado durable utiliza la colección Firestore `agent_build_queue` y el despacho
usa la cola regional `taskmaster-builds`. En desarrollo, el estado utiliza documentos JSON atómicos
dentro de `.studio-data/build-queue/` y el despacho permanece local.

## Recorrido controlado

1. La persona confirma explícitamente la construcción.
2. Studio guarda el contrato aprobado y el trabajo en estado `queued`.
3. Cloud Tasks entrega únicamente el identificador de construcción y la operación. El endpoint
   recupera el contrato desde Firestore después de validar el token OIDC y la cola de origen.
4. Un trabajador aislado recibe la especificación, el destino y la huella del contrato recuperado.
5. El trabajador genera el proyecto dentro de la carpeta obligatoria `projects/` y devuelve evidencia
   en `.studio/isolated-builder.json`.
6. Studio guarda el borrador completo en el almacén durable antes de solicitar permiso para probar.
7. La persona aprueba o rechaza las pruebas.
8. Si las aprueba, Cloud Tasks entrega la fase de prueba; el laboratorio restaura el proyecto cuando
   sea necesario, ejecuta las puertas de
   seguridad y publica el agente en el catálogo solo cuando todas pasan.

El proyecto permanece como un árbol de archivos navegable. La cola no usa ZIP o RAR como formato de
persistencia.

## Recuperación y reintentos

- `queued` y `building`: se recuperan como trabajos pendientes y vuelven a construcción.
- `testing`: recupera el proyecto durable y vuelve a ejecutar las verificaciones.
- `awaiting_test_approval`: se conserva sin avanzar; requiere una nueva decisión humana.
- `completed`, `stopped` y `failed`: no se reejecutan automáticamente.
- Un error inesperado del proceso puede reintentarse una vez. Un error de dominio o un segundo fallo
  detiene la operación de forma cerrada y auditable.

## Fronteras de seguridad

- El proceso aislado recibe una lista mínima de variables del sistema.
- No recibe `GOOGLE_APPLICATION_CREDENTIALS`, claves de Gemini ni secretos OAuth.
- No recibe herramientas de red, conexiones del usuario o facultad para aprobar pruebas.
- No elige libremente comandos: ejecuta el registro cerrado de generadores instalados.
- El manifiesto y las huellas SHA-256 continúan siendo obligatorios.
- El acceso a una construcción sigue limitado a su propietario.
- El endpoint interno acepta exclusivamente un token OIDC para la audiencia exacta de Cloud Run,
  emitido para `taskmaster-build-worker`, y rechaza tareas ajenas a la cola declarada.
- La identidad web solo puede encolar en `taskmaster-builds`; la identidad del trabajador solo puede
  invocar Cloud Run. Ninguna de las dos puede asumir la función de la otra.

## Señales operativas

`GET /api/v1/meta` expone `build_orchestration` con:

- `durable_queue`;
- `worker_isolated`;
- `runtime`;
- `max_attempts`;
- `restart_recovery`.

Estas señales permiten comprobar que Cloud Run está usando Firestore y el trabajador aislado antes
de aceptar construcciones reales.

## Verificación

Las pruebas automatizadas cubren el aislamiento de credenciales, el contrato del subproceso, el
aislamiento por propietario, la enumeración de trabajos pendientes y la restauración de una
construcción antes de la aprobación de pruebas.
