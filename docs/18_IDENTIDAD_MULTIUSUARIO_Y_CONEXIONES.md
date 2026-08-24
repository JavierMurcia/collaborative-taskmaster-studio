# Identidad multiusuario y conexiones externas

## Decisión

El Studio no usa el identificador enviado por el navegador como identidad en producción. En
`identity_platform`, cada solicitud protegida debe incluir un ID token de Firebase/Google Identity
Platform. El servidor verifica el token, obtiene el usuario y reemplaza `X-Studio-Session` por una
clave de aislamiento derivada por el servidor.

El modo `local` conserva una sesión anónima exclusivamente para desarrollo. La interfaz la muestra
como **Sesión local de desarrollo · Sin cuentas externas reales**.

## Aislamiento

Cada recurso se resuelve con una clave de usuario y espacio:

```text
Identity Platform ID token
  -> usuario verificado
  -> espacio personal o tenant
  -> clave de aislamiento del servidor
  -> conversaciones, documentos, agentes y conexiones
```

Una conexión contiene solo metadatos no secretos: propietario, espacio, proveedor, scopes, estado y
cuenta visible. Los tokens OAuth no se guardan en `connections.json`, en conversaciones ni en el
navegador.

## Experiencia por chat

1. El usuario escribe `Conecta mi Google Drive`.
2. Gemini responde normalmente.
3. El backend añade una oferta estructurada del plugin relevante.
4. El chat presenta proveedor, permisos mínimos y botón de conexión.
5. En modo local, el botón se detiene e informa que hace falta una identidad verificada.
6. En producción, la autorización deberá continuar en la página oficial del proveedor.
7. Las conexiones se consultan y revocan desde el panel izquierdo.

## Configuración pública de autenticación

```dotenv
STUDIO_AUTH_MODE=identity_platform
STUDIO_IDENTITY_PROJECT=sentinel-taskmaster-dev
STUDIO_FIREBASE_API_KEY=...
STUDIO_FIREBASE_AUTH_DOMAIN=...
STUDIO_FIREBASE_APP_ID=...
```

Estos valores identifican la aplicación web y pueden exponerse al cliente. No son tokens de usuario.

## OAuth y Drive implementados

El backend ya incorpora:

- `state` firmado y con vencimiento de diez minutos para impedir retornos manipulados;
- PKCE S256 para vincular la autorización con el intercambio de código;
- callback público dedicado que no confía en identificadores del navegador;
- canje y renovación de tokens exclusivamente en el servidor;
- revocación en Google al desconectar;
- cifrado AES-256-GCM antes de persistir el grant en Firestore;
- búsqueda, listado y lectura textual de Drive con `drive.readonly`;
- límite de lectura y rechazo de formatos binarios no admitidos;
- búsqueda y lectura de Drive desde el mismo ciclo de herramientas del Socio Colaborativo.

`connections.json` continúa almacenando únicamente metadatos. El documento cifrado usa una clave
derivada de espacio, usuario y plugin, por lo que una cuenta nunca puede recuperar el grant de otra.

## Configuración requerida del administrador

```dotenv
STUDIO_PUBLIC_BASE_URL=https://SERVICIO
STUDIO_GOOGLE_OAUTH_CLIENT_ID=...
STUDIO_GOOGLE_OAUTH_CLIENT_SECRET=...
STUDIO_OAUTH_STATE_SECRET=...
STUDIO_OAUTH_ENCRYPTION_KEY=...
```

`STUDIO_OAUTH_STATE_SECRET` debe ser aleatorio. `STUDIO_OAUTH_ENCRYPTION_KEY` debe contener exactamente
32 bytes codificados como Base64 URL-safe. En Cloud Run las seis referencias declaradas en
`infrastructure/cloud_run/runtime-config.json` se entregan desde Secret Manager usando versiones
numéricas; el servicio no crea, imprime ni descarga sus valores.

También se debe habilitar Identity Platform, Google Drive API y Firestore, registrar como URI de
retorno exacta:

```text
https://SERVICIO/api/v1/collaborative/connections/oauth/callback
```

y conceder a la cuenta de ejecución `roles/secretmanager.secretAccessor` únicamente sobre los seis
secretos declarados. Si falta cualquier pieza, el contrato falla cerrado con `setup_required` y no
simula una conexión.

El modo local en `127.0.0.1` sigue siendo deliberadamente anónimo y no conecta cuentas externas.
Para probar OAuth localmente se debe ejecutar el servidor en `identity_platform`, registrar la URI
local exacta y proporcionar Firestore y la clave cifrada; nunca se habilita OAuth para una identidad
local no verificada.

## Estado aplicado en Google Cloud

Estado comprobado el **22 de agosto de 2026** en el proyecto
`sentinel-taskmaster-dev`:

- Identity Platform inicializado;
- proveedor `google.com` habilitado con el cliente OAuth existente;
- aplicación web Firebase `Collaborative Taskmaster Studio Web` activa;
- dominios Firebase, Cloud Run y desarrollo local registrados;
- seis secretos declarados disponibles con versión numérica `1`;
- cuenta `taskmaster-studio-runtime@sentinel-taskmaster-dev.iam.gserviceaccount.com`
  autorizada como `Secret Accessor` únicamente en esos seis secretos;
- imagen `auth-f222aba` construida después de ejecutar las pruebas de Cloud Build;
- revisión Cloud Run `collaborative-taskmaster-studio-00005-w4x` sirviendo el 100 % del tráfico;
- despliegue verificado contra el digest inmutable
  `sha256:345afcbe0899614ad24388b5074f078276488a9581966b8a3db68578af1a981a`.

La validación manual de aceptación se conserva como paso separado: iniciar sesión con un usuario de
prueba, conectar Drive con alcance de solo lectura, leer un documento permitido, desconectar y
comprobar que otra identidad no puede recuperar la conversación ni la conexión.

## Reglas invariables

- Una conexión personal nunca se comparte con otro usuario.
- Los identificadores de propietario aportados por el cliente se ignoran en producción.
- Cada plugin solicita el scope mínimo declarado en su manifiesto.
- Los efectos de escritura siguen requiriendo aprobación humana aun con una conexión activa.
- Revocar una conexión bloquea inmediatamente su uso por los agentes.
