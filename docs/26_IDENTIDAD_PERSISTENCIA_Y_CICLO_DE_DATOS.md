# Identidad, persistencia y ciclo de los datos

## Propósito

Este documento consolida dónde se guarda cada tipo de información, cómo se aísla y qué significa
eliminarla. Complementa los documentos `08`, `18` y `20` con las conversaciones y adjuntos
incorporados posteriormente.

## Modos de identidad

| Modo | Uso | Propietario efectivo |
| --- | --- | --- |
| `local` | Desarrollo y pruebas sin cuentas reales. | Sesión local controlada. |
| `identity_platform` | Producción multiusuario. | Clave derivada del token verificado. |

En producción, el servidor valida el token o la cookie de Identity Platform. El valor de
`X-Studio-Session` enviado por el navegador no decide por sí solo qué datos puede consultar una
persona.

## Recursos aislados

La misma clave de propietario delimita:

- conversaciones y memoria recuperable;
- documentos y datasets;
- conexiones y grants OAuth;
- diseños y revisiones;
- construcciones pendientes;
- agentes publicados;
- proyectos y estado de runtime.

Una consulta no debe enumerar recursos de otro propietario aunque conozca su identificador.

## Matriz de persistencia

| Recurso | Desarrollo local | Producción |
| --- | --- | --- |
| Conversaciones | `localStorage` y repositorio local JSON. | Caché del navegador y Firestore. |
| Documentos extraídos | `.studio-data/documents/`. | Almacenamiento efímero de la instancia mientras no exista almacén durable específico. |
| Cargas parciales | `.studio-data/large-uploads/`. | Disco temporal de Cloud Run durante la carga. |
| Proyectos del Studio | Repositorio JSON o memoria. | Firestore. |
| Cola de construcción | `.studio-data/build-queue/`. | Colección `agent_build_queue` en Firestore. |
| Catálogo de agentes | Estado local. | Firestore por propietario. |
| Árbol del Taskmaster | `projects/<nombre>/`. | Cloud Storage privado y restauración validada. |
| Memoria del Taskmaster | `runtime-state.json`. | Objeto durable separado del manifiesto. |
| Tokens OAuth | Vault local seguro según configuración. | Firestore cifrado; nunca en el chat. |

Los adjuntos cargados en Cloud Run son actualmente temporales. Una conversación puede conservar la
referencia, pero el archivo no debe prometerse como durable después de reemplazar la instancia. Esta
limitación debe permanecer visible hasta incorporar un almacén privado de adjuntos.

## Sincronización de conversaciones

El navegador conserva una copia para respuesta inmediata y sincroniza el registro con el servidor.
El contrato limita cada mensaje a 6.000 caracteres, cada conversación a doce referencias de
documento y el JSON serializado a 256.000 bytes.

Al abrir la aplicación:

1. se resuelve la identidad;
2. se consulta el repositorio del servidor;
3. se combinan únicamente registros del mismo propietario;
4. una conversación eliminada no se restaura desde una copia anterior;
5. se descartan referencias a documentos que ya no existen en la sesión.

## Eliminación

### Conversación

La interfaz solicita confirmación, elimina la copia local y llama a
`DELETE /api/v1/collaborative/conversations/{id}`. El repositorio del servidor aplica la operación
al propietario verificado. La eliminación del chat no elimina automáticamente un agente publicado
ni el proyecto que este representa.

### Documento

`DELETE /api/v1/collaborative/documents/{id}` elimina la representación extraída. La interfaz lo
quita de la biblioteca y de las referencias de las conversaciones. Si existía una carga parcial,
cancelarla elimina sus archivos temporales.

### Agente

La acción del catálogo archiva o elimina el registro mediante el endpoint de agentes. Debe
distinguirse de borrar la conversación desde la que se construyó. La conservación del árbol durable
se rige por la política de proyectos y no se infiere de una acción visual ambigua.

### Sesión

Cerrar sesión elimina las cookies del Studio y la identidad activa del navegador. No equivale a
borrar todos los datos de la cuenta. Una eliminación integral debe ser una operación administrativa
explícita, auditable y separada.

## Proyectos durables

Un Taskmaster terminado se conserva como archivos individuales, nunca como ZIP o RAR. El
manifiesto contiene rutas, tamaños y SHA-256. Cloud Storage publica el manifiesto al final; por eso
una copia incompleta no se declara válida. Al restaurar se verifican rutas y hashes antes de usar el
proyecto.

`runtime-state.json` cambia durante las conversaciones con el agente y queda fuera del manifiesto
inmutable de construcción. Esta separación permite memoria mutable sin esconder cambios en el
código aprobado.

## Retención y limpieza

- Los agregados demo de Firestore declaran una retención de siete días cuando la configuración TTL
  está aplicada.
- Las cargas parciales deben limpiarse al cancelar, completar o detectar un estado inválido.
- Los documentos de sesión no tienen todavía una promesa de durabilidad cloud.
- Los proyectos publicados requieren una política explícita antes de una eliminación material.
- Los logs deben conservar identificadores, tamaños y resultados, no contenido de documentos ni
  tokens.

## Conexiones externas

Drive, Gmail y Calendar solicitan scopes de solo lectura independientes. Los grants se cifran antes
de persistirlos y se revocan desde la interfaz. Cambiar de cuenta inicia un nuevo flujo de identidad;
no reasigna recursos existentes a la nueva cuenta.

## Riesgos conocidos

1. Los adjuntos en Cloud Run son efímeros y necesitan almacenamiento durable si se desea conservarlos.
2. La caché del navegador no es la fuente de autoridad en producción.
3. Cerrar sesión no debe presentarse como eliminación de cuenta.
4. Borrar un chat, un agente y un proyecto son operaciones distintas.
5. La restauración de proyectos debe seguir verificando hashes y límites de cantidad y tamaño.

## Criterios de aceptación

- Un usuario autenticado no puede leer conversaciones, archivos ni agentes de otro usuario.
- Un chat eliminado no reaparece en incógnito ni después de recargar.
- Un documento eliminado deja de estar adjunto en todos los chats visibles.
- Los tokens OAuth no aparecen en respuestas, almacenamiento del navegador ni auditoría.
- Un proyecto restaurado coincide con su manifiesto.
- La interfaz distingue cerrar sesión de eliminar datos.
