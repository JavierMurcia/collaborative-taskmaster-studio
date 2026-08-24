# Validación de Identity Platform y Google Drive

## Objetivo

Demostrar que la aplicación desplegada usa identidad real, separa los datos de cada usuario y
autoriza Google Drive sin guardar tokens en el navegador ni conceder permisos de escritura.

## Evidencia automatizada

| Componente | Resultado |
| --- | --- |
| Aplicación web Firebase | Activa |
| Proveedor Google | Habilitado |
| Dominios autorizados | Firebase, Cloud Run, `localhost` y `127.0.0.1` |
| Secretos declarados | 6 de 6, versiones numéricas |
| Acceso de la cuenta runtime | Limitado a los seis secretos |
| Cloud Build | `SUCCESS` (`195e2b6a-ac20-4ff3-ad81-81a2692e4d4a`) |
| Imagen | `auth-f222aba` con digest inmutable |
| Revisión Cloud Run | `collaborative-taskmaster-studio-00005-w4x` |
| Tráfico | 100 % a la revisión nueva |
| Escalado | Mínimo 0, máximo 1 |

## Recorrido manual de aceptación

1. Abrir `https://collaborative-taskmaster-studio-760216344589.us-central1.run.app`.
2. Iniciar sesión con un usuario de prueba autorizado por la pantalla OAuth.
3. Crear una conversación y pedir: `Conecta mi Google Drive`.
4. Revisar que el consentimiento solicite acceso de solo lectura.
5. Autorizar y regresar al chat mediante el callback del servicio.
6. Pedir la búsqueda de un archivo conocido y leer un documento admitido.
7. Confirmar que el resultado identifica su fuente y que no se ejecuta ninguna escritura.
8. Desconectar Drive desde la misma interfaz.
9. Repetir una lectura y comprobar que falla con conexión requerida.
10. Entrar con un segundo usuario y verificar que no aparecen conversaciones, agentes ni conexiones
    del primer usuario.

## Criterios de aprobación

- No existe sesión anónima en producción.
- El servidor deriva el propietario desde el ID token verificado.
- El callback OAuth valida `state` y PKCE.
- Los grants permanecen cifrados en Firestore.
- Drive opera exclusivamente con `drive.readonly`.
- Desconectar revoca el acceso inmediatamente.
- No se muestran secretos, refresh tokens ni credenciales en respuestas, logs o interfaz.

## Resultado pendiente

La infraestructura y el despliegue están aprobados automáticamente. Este documento se completa con
la fecha, las dos cuentas de prueba y el resultado del recorrido manual, sin registrar correos,
tokens ni identificadores sensibles.

## Corrección de compatibilidad del acceso

Durante los primeros recorridos, tanto `signInWithPopup` como el helper embebido de Firebase
quedaron bloqueados por el aislamiento del navegador y mostraron una ventana blanca. El dominio de
Cloud Run y los dominios locales sí estaban autorizados, por lo que el problema no era la lista de
orígenes sino la dependencia del iframe de Firebase.

El acceso utiliza ahora un flujo OAuth de servidor sobre el callback que ya está registrado. El
servidor firma `state`, exige PKCE, intercambia el código directamente con Google y convierte la
identidad del proveedor en un ID token de Identity Platform. El navegador solo recibe la sesión al
regresar al mismo origen; el API continúa verificando el ID token y derivando el propietario sin
aceptar identificadores enviados por el cliente. Ya no se carga el SDK de Firebase ni se crea un
iframe de autenticación, por lo que el acceso no puede quedar esperando silenciosamente.
