# Experiencia del chat y del Taskmaster publicado

## Propósito

Este documento describe la experiencia conversacional actual. Reemplaza como referencia operativa
las pantallas propuestas originalmente en `04_EXPERIENCIA_USUARIO_Y_DEMO.md`, que se conserva como
historia de diseño.

## Estructura de la interfaz

La aplicación utiliza una sola superficie de trabajo:

- barra superior con identidad del producto, nuevo chat y modelo activo;
- barra lateral con taller, agentes aprobados, conversaciones, conexiones, capacidades e identidad;
- historial de mensajes en el área central;
- bandeja de archivos asociada a la sesión y a la conversación;
- compositor persistente al pie del área visible.

El historial central es la región desplazable. Su scrollbar se mantiene en el borde derecho de la
ventana, no junto a la columna interior de mensajes. El compositor permanece disponible en la parte
inferior sin cubrir la última respuesta ni la bandeja de archivos.

## Inicio de una conversación

Antes del primer mensaje se presenta un estado de bienvenida y un compositor centrado. Cuando la
persona envía el primer mensaje:

1. el estado vacío desaparece;
2. la conversación hace una única transición hacia la disposición de historial;
3. el mensaje del usuario aparece en la parte superior del nuevo recorrido;
4. el indicador de Gemini se muestra después del mensaje, en la posición donde aparecerá la
   respuesta;
5. el compositor queda fijado en el área inferior.

La transición no se repite en mensajes posteriores y no debe mover el compositor de manera
impredecible.

## Compositor

- `Enter` envía el mensaje.
- `Shift+Enter` inserta una nueva línea.
- El texto admite hasta 6.000 caracteres.
- El botón `+` abre opciones separadas para documentos e imágenes.
- Se pueden seleccionar varios archivos en una operación.
- El botón de envío se desactiva cuando no existe texto ni una acción válida.

Las cargas aparecen inmediatamente en la bandeja con nombre, porcentaje y opción de cancelar. Un
fallo permanece visible con su causa y puede retirarse sin borrar los archivos que sí terminaron.

## Historial y conversaciones

Cada conversación guarda:

- identificador y título;
- fase del diseño;
- mensajes visibles;
- referencias a documentos adjuntos;
- artefactos de gráficos;
- agente del catálogo activo, cuando corresponde;
- actividad verificable del constructor.

El punto de la conversación seleccionada utiliza el color activo. Cambiar de conversación restaura
sus mensajes, archivos vinculados y Taskmaster asociado. Eliminarla exige confirmación y actualiza
el navegador y el repositorio del servidor.

## Respuestas enriquecidas

Una respuesta puede combinar:

- Markdown seguro;
- tablas con desplazamiento horizontal;
- bloques de código cuando el usuario realmente solicita código;
- actividad de herramientas;
- tarjetas de conexión;
- diseño estructurado del agente;
- estado de construcción;
- gráficos interactivos y datos de respaldo.

Si el usuario pide un gráfico y el Studio puede construir el artefacto, la respuesta no debe
ofrecer código de Matplotlib, Seaborn, Plotly o Chart.js como sustituto. El gráfico se dibuja dentro
del chat y el texto se limita a interpretarlo.

## Identidad y cuenta

Antes de autenticarse, el control inferior de la barra lateral muestra **Iniciar sesión**. Después
de autenticar muestra foto, nombre y cuenta. Al pulsar el nombre o la foto se puede iniciar el flujo
para cambiar de cuenta. La configuración permite:

- gestionar los archivos disponibles;
- revisar el estado de las conexiones;
- cerrar sesión.

Cerrar sesión elimina las cookies de identidad del Studio y vuelve al estado no autenticado. No
concede al navegador autoridad para elegir otro propietario sin pasar por Identity Platform.

## Diseño de un Taskmaster

Gemini acompaña a la persona hasta que el diseño contiene, como mínimo:

- misión u objetivo;
- usuario destinatario;
- entradas;
- resultados;
- flujo con al menos un paso;
- herramientas y restricciones relevantes.

El panel de diseño presenta completitud, framework recomendado, accesos y decisiones pendientes.
Al alcanzar un contrato válido aparece **Aprobar diseño y construir en laboratorio**. El botón no
debe mostrarse como disponible antes de cumplir el contrato.

## Construcción observable

Después de aprobar:

1. se crea un trabajo durable;
2. el chat muestra estados reales de cola y construcción;
3. el constructor genera el proyecto en `projects/`;
4. la interfaz vuelve a pedir autorización antes del laboratorio;
5. las pruebas se ejecutan solo después de la aprobación;
6. el agente aparece en el catálogo cuando el resultado es `ready`.

La etiqueta del constructor diferencia **Antigravity SDK** de
**Constructor local seguro · respaldo de Antigravity**. Una caída de Cloud Tasks o del trabajador
se presenta como error recuperable, nunca como construcción completada.

## Conversación con un agente publicado

Todo Taskmaster recibe un perfil conversacional universal construido a partir de su especificación.
El runtime clasifica la intención en cuatro tipos:

| Intención | Comportamiento |
| --- | --- |
| `conversation` | Explica el dominio, capacidades, límites o forma de uso. |
| `clarification` | Solicita la información mínima que falta para continuar. |
| `execution` | Inicia la tarea para la que fue creado, con entradas verificadas. |
| `approval` | Trata una decisión humana requerida por una acción protegida. |

El agente debe ser útil antes de ejecutar: explica qué puede hacer, ejemplos de solicitudes, datos
necesarios y límites. No responde con un esquema genérico de revisión si la persona solo está
conversando.

## Accesibilidad y estados

- Los controles tienen nombre accesible y foco visible.
- Los indicadores no dependen únicamente del color.
- Vacío, carga, éxito y error tienen mensajes diferentes.
- El historial preserva el orden de lectura.
- Las tablas y gráficos incluyen contenido textual o datos consultables.
- La reducción de movimiento del sistema debe evitar transiciones decorativas innecesarias.

## Criterios de aceptación

1. El primer mensaje produce una sola transición al historial.
2. El indicador de Gemini aparece junto a la futura respuesta.
3. El compositor permanece utilizable al final de cualquier historial.
4. La barra de desplazamiento está en el extremo derecho.
5. Una conversación seleccionada conserva el indicador activo.
6. Cambiar de chat restaura mensajes, adjuntos y agente.
7. Eliminar un chat no permite que vuelva a aparecer al recargar.
8. Un gráfico disponible se renderiza; no se sustituye por código.
9. El Taskmaster conversa según su especialidad antes de ejecutar.
10. Ninguna prueba se inicia sin aprobación humana explícita.
