param(
    [string]$ItalianVoice = "it_IT-paola-medium",
    [string]$EnglishVoice = "en_US-lessac-medium",
    [string]$Destination = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Ambiente .venv non trovato. Esegui prima scripts\Install-Janus.ps1."
}
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $Destination = Join-Path $env:LOCALAPPDATA "JANUS\models\piper"
}
$Destination = [System.IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $Destination | Out-Null

Write-Host "Download delle voci Piper locali in $Destination..."
& $venvPython -m piper.download_voices --data-dir $Destination $ItalianVoice $EnglishVoice
if ($LASTEXITCODE -ne 0) {
    throw "Il download delle voci Piper non e riuscito."
}

foreach ($voice in @($ItalianVoice, $EnglishVoice)) {
    $modelPath = Join-Path $Destination "$voice.onnx"
    $configPath = "$modelPath.json"
    if (-not (Test-Path -LiteralPath $modelPath) -or -not (Test-Path -LiteralPath $configPath)) {
        throw "Voce Piper incompleta: $voice"
    }
}

Write-Host "Voci Piper italiane e inglesi pronte." -ForegroundColor Green
Write-Host "Start-Janus le rilevera automaticamente dalla destinazione predefinita."
