# Validación y demostración actual

## Propósito

Este documento define el recorrido mínimo para demostrar las capacidades vigentes. Complementa el
caso académico histórico con archivos, dashboards, identidad y conversación con agentes publicados.

## Preparación

- Aplicación local o URL de Cloud Run disponible.
- Gemini 3.7 Flash habilitado cuando se quiera verificar el recorrido cloud.
- Identidad de prueba sin datos personales.
- Dos CSV o XLSX no confidenciales.
- Un documento PDF o DOCX pequeño.
- Un Taskmaster publicado o un diseño apto para construir.

No utilizar secretos, correo real sensible ni archivos de producción durante la demo pública.

## Recorrido A — Chat e identidad

1. Abrir la aplicación sin sesión: debe mostrarse **Iniciar sesión**.
2. Autenticarse y confirmar foto, nombre y cuenta en la parte inferior de la barra lateral.
3. Crear un chat y enviar el primer mensaje.
4. Verificar la transición única del compositor y la posición del indicador de Gemini.
5. Crear un segundo chat y alternar entre ambos.
6. Eliminar uno, recargar y confirmar que no reaparece.

Resultado: conversaciones separadas y asociadas a la identidad efectiva.

## Recorrido B — Archivos

1. Abrir `+` y elegir documento.
2. Seleccionar varios archivos.
3. Comprobar aparición inmediata, porcentaje y progreso independiente.
4. Inspeccionar el PDF o DOCX.
5. Quitar un dataset del chat sin eliminarlo de la sesión.
6. Volver a adjuntarlo y después eliminarlo definitivamente.

Resultado: biblioteca consultable, asociación por chat y eliminación diferenciada.

## Recorrido C — Datasets y gráficos

1. Adjuntar dos CSV/XLSX.
2. Solicitar: `Analiza en profundidad estos archivos y crea un dashboard comparativo colorido`.
3. Confirmar que aparecen varios gráficos y no código Python.
4. Revisar métricas, observaciones, fuente, hoja y tabla de datos.
5. Pedir una correlación o distribución específica.
6. Repetir con `genera gráficos visuales con datos aleatorios` y confirmar la etiqueta de datos
   simulados.

Resultado: artefactos renderizados, trazables y reproducibles.

## Recorrido D — Construcción

1. Describir un agente con objetivo, usuario, entradas, resultados y flujo.
2. Completar las preguntas hasta alcanzar un diseño válido.
3. Confirmar framework, accesos y decisiones pendientes.
4. Pulsar **Aprobar diseño y construir en laboratorio**.
5. Verificar el constructor efectivo y la actividad durable.
6. Esperar la solicitud separada de autorización de pruebas.
7. Autorizar y comprobar el laboratorio.
8. Confirmar que el agente aparece en el catálogo y que existe su proyecto durable.

Resultado: ninguna escritura ni prueba ocurre antes de su aprobación correspondiente.

## Recorrido E — Taskmaster conversacional

1. Abrir el agente publicado.
2. Preguntar `¿Qué puedes hacer y qué información necesitas?`.
3. Confirmar una respuesta específica a su misión.
4. Hacer una pregunta de dominio sin solicitar ejecución.
5. Solicitar una tarea incompleta y comprobar que pide aclaración.
6. Entregar las entradas y solicitar la ejecución.
7. Si existe una acción protegida, comprobar que solicita aprobación.

Resultado: el agente funciona primero como guía especializada y luego como ejecutor controlado.

## Matriz de aceptación

| Área | Aceptación |
| --- | --- |
| Identidad | La cuenta visible coincide con el propietario de los datos. |
| Chat | Historial, selección y eliminación sobreviven a recarga. |
| Compositor | Permanece accesible y no cubre contenido. |
| Archivos | Cargas múltiples progresan de manera independiente. |
| Inspección | Solo se presenta contenido extraído y acotado. |
| Datasets | Se respetan límites de filas, columnas y artefactos. |
| Gráficos | Se renderizan en el chat y conservan tabla de respaldo. |
| Construcción | Cloud Tasks y el constructor reportan estados reales. |
| Antigravity | La etiqueta coincide con el runtime efectivo. |
| Laboratorio | No se ejecuta sin aprobación. |
| Catálogo | Solo contiene agentes que aprobaron las pruebas. |
| Recuperación | Conversaciones, agentes y proyectos reaparecen para el mismo usuario. |

## Casos negativos obligatorios

- Archivo con extensión no admitida.
- Decimotercer documento en la misma sesión.
- CSV/XLSX mayor de 600 MiB.
- Cancelación durante una carga fragmentada.
- XLSX estructuralmente peligroso.
- Solicitud de gráfico sin dataset ni petición de datos simulados.
- Construcción cuando Cloud Tasks no puede despachar.
- Prueba sin autorización.
- Consulta de un recurso perteneciente a otra identidad.
- Recarga después de borrar una conversación.

Cada caso debe mostrar un error comprensible y conservar los recursos no relacionados.

## Evidencia recomendada

- revisión de Cloud Run;
- fecha y modelo activo;
- constructor efectivo;
- identificadores opacos de conversación, construcción y agente;
- capturas del progreso de carga y dashboard;
- resultado de la suite automatizada;
- manifiesto y hash del proyecto publicado;
- resultado del recorrido de humo.

No incluir tokens, documentos completos, correos, cookies ni datos personales en la evidencia.
