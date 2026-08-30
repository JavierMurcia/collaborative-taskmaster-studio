# Archivos, datasets y visualizaciones

## Propósito

Este documento define el contrato de carga, inspección, asociación y análisis de archivos en los
chats del Studio. Los límites se derivan de `studio/capabilities/documents.py` y
`studio/capabilities/datasets.py`.

## Formatos admitidos

| Grupo | Extensiones | Tratamiento |
| --- | --- | --- |
| Texto | `.txt`, `.md`, `.csv`, `.json`, `.yaml`, `.yml`, `.xml` | Normalización y extracción de texto. |
| Documentos | `.pdf`, `.docx`, `.pptx` | Extracción acotada; nunca ejecución. |
| Hojas de cálculo | `.xlsx` | Lectura estructurada y resumen textual. |
| Imágenes | `.png`, `.jpg`, `.jpeg`, `.webp` | Medio multimodal validado por formato. |

El selector visual separa documentos e imágenes, pero ambos terminan en la misma biblioteca aislada
por sesión. La extensión es una primera restricción; los extractores también validan que el
contenido sea legible.

## Límites vigentes

| Límite | Valor |
| --- | ---: |
| Documentos por sesión | 12 |
| Carga directa por archivo | 25 MiB |
| CSV o XLSX grande | 600 MiB |
| Bloque de carga grande | 8 MiB |
| Cargas grandes activas por sesión | 1.200 MiB acumulados |
| Medios entregados al modelo | 16 MiB acumulados |
| Texto extraído conservado | 100.000 caracteres por documento |
| Filas inspeccionadas por hoja | 2.500 |
| Columnas inspeccionadas | 40 |
| Hojas XLSX | 24 |
| Puntos por gráfico | 24 |
| Gráficos por solicitud | 8 |

Los valores utilizan MiB aunque algunos mensajes de interfaz los abrevien como MB. Aumentar el
tamaño de transporte no aumenta la cantidad entregada al modelo: el análisis sigue siendo acotado.

## Carga directa

Para archivos de hasta 25 MiB, el navegador utiliza `POST /api/v1/collaborative/documents`. El
servidor:

1. resuelve al propietario verificado;
2. valida nombre, extensión, contenido y tamaño;
3. rechaza la carga si la sesión ya tiene doce documentos;
4. extrae texto, medio o snapshot de dataset;
5. guarda solamente la representación segura requerida;
6. devuelve un resumen para mostrarlo inmediatamente.

El original no se ejecuta ni se importa como código.

## Carga grande de CSV y XLSX

Solo CSV y XLSX mayores de 25 MiB utilizan el protocolo fragmentado:

1. `POST /api/v1/collaborative/document-uploads` reserva una carga y fija nombre y tamaño total.
2. `PUT /api/v1/collaborative/document-uploads/{id}?offset=N` añade un bloque de hasta 8 MiB.
3. El offset debe coincidir con la cantidad recibida; no se aceptan huecos ni sobrescrituras.
4. `POST .../{id}/complete` verifica el tamaño, inspecciona el dataset y crea el documento.
5. `DELETE .../{id}` cancela y limpia los temporales.

El navegador muestra el porcentaje calculado con los bytes confirmados. Si un bloque falla, la
carga conserva un error visible y puede cancelarse. Varias selecciones se procesan como cargas
independientes; el fallo de una no invalida las demás.

## Inspección segura

El inspector se abre mediante `GET /api/v1/collaborative/documents/{id}` y muestra metadatos,
contenido extraído y si la vista fue recortada. Para datasets también incluye nombres de hojas,
columnas, cantidad total de filas y una muestra acotada.

Las protecciones de XLSX incluyen límites de miembros ZIP, tamaño expandido, cadenas compartidas,
hojas, filas y columnas. Esto reduce el riesgo de archivos comprimidos hostiles y evita cargar un
libro completo en el modelo.

## Biblioteca y asociación a chats

Un documento puede estar:

- disponible en la sesión;
- adjunto a la conversación activa;
- en proceso de carga;
- fallido o cancelado.

Adjuntar y eliminar son acciones distintas. Quitar un documento del chat conserva el archivo en la
sesión. Eliminarlo lo borra de la biblioteca y elimina sus referencias de todas las conversaciones
locales activas. Ambas acciones requieren que el documento pertenezca al usuario efectivo.

## Análisis de datasets

El análisis no ejecuta Python proporcionado por Gemini. `DatasetAnalysisService` examina snapshots
validados y produce un contrato `ChartArtifact` persistible. La solicitud se activa con intenciones
como analizar, comparar, visualizar, tendencia, distribución, correlación o dashboard.

Cuando existen varios datasets, el servicio puede tomar hasta ocho documentos y producir gráficos
por archivo hasta alcanzar el límite global. Una petición profunda obtiene más perspectivas de una
misma hoja que una solicitud sencilla.

## Artefacto de gráfico

Cada gráfico contiene:

- título y descripción;
- tipo y variante analítica;
- dos columnas semánticas;
- hasta veinticuatro puntos;
- documento y hoja de origen;
- paleta de hasta ocho colores;
- hasta cuatro métricas destacadas;
- hasta tres observaciones calculadas.

Tipos disponibles:

| Tipo | Uso habitual |
| --- | --- |
| Barras | Comparación de categorías. |
| Barras horizontales | Etiquetas extensas o ranking. |
| Línea | Tendencia temporal u ordenada. |
| Área | Evolución y magnitud acumulada. |
| Sectores | Composición con pocas categorías. |
| Anillo | Participación y concentración. |
| Dispersión | Correlación entre dos variables numéricas. |

La selección se basa en tipos de columna, cardinalidad, palabras de la solicitud y calidad de los
datos. Los gráficos de dispersión añaden una tendencia lineal y R² cuando Google Charts puede
calcularlos.

## Renderizado

La API devuelve artefactos, no imágenes ni código arbitrario. El navegador carga el paquete
`corechart` de Google Charts y renderiza el tipo correspondiente. Cada tarjeta incluye:

- encabezado de análisis visual;
- métricas;
- lienzo interactivo;
- descripción y observaciones;
- fuente y hoja;
- tabla desplegable con los datos usados.

Si Google Charts no carga, los datos del artefacto siguen disponibles y la respuesta no debe
inventar que el gráfico fue producido. La dependencia remota debe considerarse al aplicar una
política de seguridad de contenido.

## Datos simulados

Una solicitud explícita de ejemplo o datos aleatorios puede producir un conjunto determinista de
demostración. La semilla procede del mensaje para que la misma petición sea reproducible. Los
artefactos se etiquetan como **Datos simulados** y no se mezclan con conclusiones sobre documentos
reales.

## Manejo de errores

| Código o condición | Resultado esperado |
| --- | --- |
| Formato no permitido | Rechazo antes de extraer. |
| Más de doce documentos | Se solicita eliminar uno. |
| Archivo vacío | Rechazo sin crear registro. |
| Directo mayor de 25 MiB | Se usa carga grande solo si es CSV/XLSX. |
| CSV/XLSX mayor de 600 MiB | Rechazo antes de reservar. |
| Offset incorrecto | El bloque no se incorpora. |
| XLSX peligroso o ilegible | Rechazo y limpieza de temporales. |
| Sin datos estructurados | Respuesta explicativa sin gráfico falso. |
| Código de gráficos en la respuesta | Se reemplaza por una interpretación del artefacto generado. |

## Privacidad y seguridad

- Un nombre de archivo o una celda nunca se interpreta como instrucción del sistema.
- No se registran contenidos completos en la auditoría.
- Los identificadores de documento tienen formato cerrado y se resuelven dentro del propietario.
- Las imágenes se limitan por tamaño acumulado antes de llegar al modelo.
- Los temporales de cargas canceladas se eliminan.
- La carga de un archivo no concede por sí misma permiso para una acción externa.
