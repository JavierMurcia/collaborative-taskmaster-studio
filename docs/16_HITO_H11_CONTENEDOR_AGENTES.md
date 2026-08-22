# H11 — Contenedor de agentes y recorrido vertical

## Objetivo

Cerrar la distancia entre «el laboratorio aprobó el proyecto» y «el usuario ya tiene su agente».
Después de una evaluación `ready`, la misma conversación añade el bloque **Agentes**.

## Experiencia implementada

1. El recorrido se presenta como una conversación vertical continua tipo Codex, sin barra de
   etapas ni redirecciones.
2. Cada pregunta y respuesta permanece visible; avanzar añade el siguiente bloque al mismo hilo en
   vez de sustituir la pantalla anterior.
3. El laboratorio solo añade el bloque Agentes cuando QG7 devuelve `ready`.
4. El agente aparece como una tarjeta de aplicación con estado, framework y revisión.
5. El usuario puede cambiar su nombre visible y escoger uno de ocho iconos.
6. **Abrir proyecto** recupera el recorrido y la trazabilidad de ese agente.
7. **Descargar ZIP** entrega el proyecto Google ADK, sus pruebas y su manifiesto.

La biblioteca del navegador conserva las identidades visuales de los proyectos creados por esa
sesión. El backend sigue comprobando la propiedad del proyecto para cada lectura y exportación.

## Exportación segura

Ruta:

```text
GET /api/v1/projects/{project_id}/export.zip
X-Studio-Session: <sesión propietaria>
```

La exportación:

- exige estado `listo_para_exportar` o `exportado`;
- recupera la revisión humana aprobada desde el repositorio persistente;
- vuelve a generar el árbol con la plantilla Google ADK versionada;
- valida que todas las rutas del ZIP sean relativas y no contengan `..`;
- no incluye credenciales;
- usa un directorio temporal confinado dentro de `generated/.exports/` y lo elimina al terminar;
- responde con `Cache-Control: no-store`.

Por tanto, la descarga no depende de que una instancia anterior de Cloud Run conserve su disco
efímero.

## Evidencia

- 427 pruebas automatizadas aprobadas.
- Ruff y mypy sin hallazgos.
- Prueba de API que elimina la generación original, reconstruye el ZIP y verifica archivos clave.
- Acceso con otra sesión rechazado con HTTP 403.
- Recorrido visual local completo: creación, entrevista, revisión, aprobación, generación,
   laboratorio, aparición del contenedor en el hilo, personalización y descarga.
- Vista móvil comprobada a 390 px sin desbordamiento horizontal.
