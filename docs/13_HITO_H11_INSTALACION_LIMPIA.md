# H11-03 — instalación limpia y recorrido local reproducible

## Propósito

Demostrar que una persona puede partir únicamente del repositorio, crear un entorno Python 3.13
nuevo, instalar el perfil local, ejecutar las pruebas y abrir la aplicación sin reutilizar el
entorno de desarrollo del autor, credenciales o servicios de Google Cloud.

## Comando único

Desde la raíz del repositorio:

```powershell
py -3.13 scripts\verify_clean_install.py
```

El comando puede descargar paquetes desde PyPI cuando no estén en la caché local. No aprovisiona
recursos, no invoca Gemini y no se conecta a Firestore.

## Qué verifica

1. copia el código a una carpeta temporal, omitiendo Git, entornos virtuales, cachés, `.env`, datos
   locales y artefactos generados;
2. crea un entorno virtual independiente con el intérprete que ejecutó el comando;
3. instala exactamente el perfil documentado con `pip install -e ".[dev]"`;
4. importa la composición real de la aplicación;
5. ejecuta el recorrido integral local de entrevista, briefing, feedback, aprobación, generación y
   laboratorio;
6. ejecuta la suite completa disponible en el perfil `dev`;
7. inicia un servidor nuevo en un puerto local libre;
8. exige HTTP 200 en interfaz, liveness, readiness y OpenAPI;
9. termina el servidor y elimina la carpeta temporal incluso ante un fallo.

## Aislamiento cloud

El verificador elimina del proceso cualquier API key o ruta de credenciales y fuerza a `false`:

- `STUDIO_ENABLE_VERTEX`;
- las cuatro puertas `STUDIO_ENABLE_MODEL_*`;
- `STUDIO_ENABLE_FIRESTORE`.

También elimina `PORT` y las variables `K_*` reservadas para evitar que una terminal heredada se
interprete como Cloud Run. Los directorios de datos y generación apuntan exclusivamente a la carpeta
temporal.

## Hallazgos corregidos

La primera ejecución limpia demostró que tres grupos de pruebas suponían instalados paquetes
opcionales de Google aunque el README solo exige `.[dev]`:

- detección del módulo `google.cloud.firestore` cuando el paquete raíz `google` no existe;
- carga del punto de entrada ADK real sin el extra `vertex`;
- dos pruebas del SDK transaccional sin el extra `firestore`.

Las pruebas ahora usan detección segura u omisión explícita. Siguen ejecutándose normalmente cuando
se instalan sus extras, pero el modo local determinista ya no depende de ellos.

## Evidencia final

La verificación de 2026-08-20 usó Python 3.13.1 y terminó con:

| Control | Resultado |
| --- | --- |
| Instalación `.[dev]` en entorno nuevo | aprobada |
| Importación del paquete | aprobada |
| Recorrido integral local | 1 prueba aprobada |
| Suite del perfil base | 421 aprobadas, 5 opcionales omitidas |
| `/` | HTTP 200 |
| `/health/live` | HTTP 200 |
| `/health/ready` | HTTP 200 |
| `/openapi.json` | HTTP 200 |
| Integraciones cloud | desactivadas |
| Credenciales copiadas | no |
| Carpeta temporal eliminada | sí |

Las cinco omisiones corresponden exclusivamente a comprobaciones que requieren Google ADK o el SDK
de Firestore. La evidencia legible por máquina está en
[`evidence/h11-03-clean-install.json`](evidence/h11-03-clean-install.json).

## Diagnóstico

Para conservar el entorno temporal cuando falle una máquina distinta:

```powershell
py -3.13 scripts\verify_clean_install.py --keep-temporary
```

El comando informa la ubicación únicamente en esa ejecución. No registre archivos del entorno
temporal ni credenciales en Git.

## Criterio de aceptación

H11-03 se considera aprobado cuando el comando termina con `status: passed`, la prueba integral y
la suite base pasan, los cuatro endpoints responden 200, no se habilita cloud y la limpieza final se
confirma.
