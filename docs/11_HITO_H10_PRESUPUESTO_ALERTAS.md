# H10-12 — presupuesto y alertas de gasto

## Resultado

H10-12 adopta y verifica el presupuesto existente `sentinel-mvp-20k-cop` para el proyecto
`sentinel-taskmaster-dev`. No se creó un presupuesto duplicado ni se modificó la configuración
financiera desde el repositorio.

La consola de Google Cloud confirmó el 20 de agosto de 2026:

- período mensual;
- alcance exclusivo al proyecto **Sentinel Taskmaster Dev**;
- importe fijo de **20.000 COP**;
- alertas sobre gasto real al **50 %**, **80 %** y **100 %**;
- correo a administradores y usuarios de facturación;
- correo a propietarios del proyecto;
- sin canales de notificación de Cloud Monitoring;
- sin tema Pub/Sub;
- sin límite automático de inversión.

La evidencia no sensible y legible por máquinas está en
`infrastructure/cloud_run/budget-evidence.json`. El identificador de la cuenta de facturación no se
guarda en el repositorio.

## Declaración reproducible

`infrastructure/cloud_run/budget.json` es la política autoritativa. El módulo
`infrastructure.cloud_run.budget` valida que el presupuesto sea mensual, de alcance único, con los
tres umbrales exactos y sin automatizaciones capaces de suspender servicios.

El plan local no consulta Google Cloud ni crea recursos:

```powershell
$project = "sentinel-taskmaster-dev"
$billingAccount = "000000-000000-000000" # Sustituir solo en la terminal.

.\.venv\Scripts\python.exe -m infrastructure.cloud_run.budget_check `
  --project $project `
  --billing-account $billingAccount
```

El resultado incluye:

- el comando para habilitar `billingbudgets.googleapis.com`;
- el comando de creación para recuperación ante pérdida del presupuesto;
- tres consultas de verificación de solo lectura;
- indicadores explícitos `cloud_verified=false` y `budget_created=false`.

El comando de creación es una contingencia, no una operación cotidiana. Antes de utilizarlo se debe
listar por nombre para evitar duplicados y, después de crearlo, confirmar en la consola que la opción
de correo a propietarios del proyecto quede activa.

## Verificación de solo lectura

Desde Cloud Shell, con una identidad que pueda leer presupuestos:

```powershell
.\.venv\Scripts\python.exe -m infrastructure.cloud_run.budget_check `
  --project $project `
  --billing-account $billingAccount `
  --verify
```

La verificación falla si encuentra cero presupuestos, duplicados, otro importe, otro proyecto,
umbrales distintos, notificaciones programáticas, destinatarios faltantes o una cuenta de
facturación diferente. El valor de `$billingAccount` se usa durante la ejecución y no debe copiarse
a archivos versionados.

En el equipo Windows actual, `gcloud` no puede renovar credenciales por el problema conocido del
almacén local de certificados. La comprobación visual pasó en la consola autenticada; la
verificación CLI deberá repetirse desde Cloud Shell sin desactivar TLS.

## Qué protege y qué no

Un presupuesto de Google Cloud **envía alertas, pero no constituye un techo de gasto**. Los datos de
costos pueden tardar en aparecer y los servicios pueden seguir generando cargos después de alcanzar
el 100 %. H10-12 reduce el riesgo mediante alertas tempranas y conserva además las protecciones
operativas ya desplegadas:

- Cloud Run con mínimo cero y máximo una instancia;
- concurrencia uno;
- recorrido de Gemini acotado por tokens y pasos;
- compilaciones explícitas, no periódicas;
- ausencia de Pub/Sub o automatizaciones de apagado que amplíen permisos.

La respuesta a una alerta es humana: revisar el informe de costos, detener demostraciones no
necesarias y, si corresponde, redirigir tráfico o deshabilitar temporalmente el servicio siguiendo el
procedimiento operativo autorizado.

## Estado

**H10-12 completado el 20 de agosto de 2026.** Existe un único presupuesto adecuado para el
proyecto, la configuración fue contrastada con la consola, la política quedó versionada y la
verificación futura es reproducible y cerrada ante deriva.

## Referencias oficiales

- `gcloud billing budgets create`: <https://docs.cloud.google.com/sdk/gcloud/reference/billing/budgets/create>
- Administración mediante Cloud Billing Budget API:
  <https://docs.cloud.google.com/billing/docs/how-to/budget-api>
- Destinatarios y notificaciones de presupuesto:
  <https://docs.cloud.google.com/billing/docs/how-to/budgets-notification-recipients>
