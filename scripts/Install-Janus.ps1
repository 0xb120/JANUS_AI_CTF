param(
    [switch]$WithDevelopmentTools
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvDir = Join-Path $projectRoot ".venv"

$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) {
    & $launcher.Source -3.11 -m venv $venvDir
} else {
    $python = (Get-Command python -ErrorAction Stop).Source
    & $python -m venv $venvDir
}
if ($LASTEXITCODE -ne 0) {
    throw "Creazione dell'ambiente virtuale non riuscita."
}

$venvPython = Join-Path $venvDir "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Aggiornamento di pip non riuscito."
}
$extras = if ($WithDevelopmentTools) { ".[speech,dev]" } else { ".[speech]" }
& $venvPython -m pip install --editable $extras
if ($LASTEXITCODE -ne 0) {
    throw "Installazione delle dipendenze JANUS non riuscita."
}

Write-Host "JANUS installato in $venvDir" -ForegroundColor Green
Write-Host "I pesi LLM, Whisper e le voci Piper non sono inclusi: prepararli prima della prova offline."
