param(
    [string]$ProjectId = "sentinel-taskmaster-dev",
    [string]$Location = "global",
    [string]$Model = "gemini-3.7-flash",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$studioRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $studioRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "No se encontró el entorno local .venv. Completa primero la instalación del proyecto."
}

$env:STUDIO_ENABLE_VERTEX = "true"
$env:GOOGLE_GENAI_USE_VERTEXAI = "true"
$env:GOOGLE_CLOUD_PROJECT = $ProjectId
$env:GOOGLE_CLOUD_LOCATION = $Location
$env:STUDIO_GEMINI_MODEL = $Model
$env:STUDIO_VERTEX_API_VERSION = "v1"

if ($env:GOOGLE_API_KEY -or $env:GEMINI_API_KEY) {
    throw "El modo Vertex AI usa ADC. Elimina GOOGLE_API_KEY y GEMINI_API_KEY de esta terminal."
}

Push-Location $studioRoot
try {
    $readinessJson = & $pythonPath -c "import json; from infrastructure.vertex import VertexSettings, inspect_vertex_readiness; print(json.dumps(inspect_vertex_readiness(VertexSettings.from_environment()).model_dump()))"
    if ($LASTEXITCODE -ne 0) {
        throw "No fue posible verificar la configuración local de Vertex AI."
    }
    $readiness = $readinessJson | ConvertFrom-Json
    if ($readiness.status -ne "ready") {
        Write-Host "Vertex AI no está listo: $($readiness.message)" -ForegroundColor Red
        Write-Host "Autentica ADC con: gcloud auth application-default login" -ForegroundColor Yellow
        exit 1
    }

    Write-Host "Vertex AI verificado antes del primer prompt." -ForegroundColor Green
    Write-Host "Modelo: $($readiness.model) | Proyecto: $($readiness.project) | Region: $($readiness.location)"
    if ($CheckOnly) {
        exit 0
    }
    & $pythonPath -m app.main
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
