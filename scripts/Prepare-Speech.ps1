param(
    [ValidateSet("tiny", "base", "small", "medium")]
    [string]$Model = "small",
    [string]$Destination = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$downloadScript = Join-Path $PSScriptRoot "prepare_speech_model.py"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Ambiente .venv non trovato. Esegui prima scripts\Install-Janus.ps1."
}
if (-not (Test-Path -LiteralPath $downloadScript)) {
    throw "Helper Python non trovato: $downloadScript"
}
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $Destination = Join-Path $env:LOCALAPPDATA "JANUS\models\faster-whisper-$Model"
}
$Destination = [System.IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $Destination | Out-Null

Write-Host "Download del modello faster-whisper-$Model in $Destination..."
& $venvPython $downloadScript --model $Model --destination $Destination
if ($LASTEXITCODE -ne 0) {
    throw "Il download o la verifica del modello Whisper non e riuscito."
}

foreach ($requiredFile in @("config.json", "model.bin", "tokenizer.json")) {
    $requiredPath = Join-Path $Destination $requiredFile
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Modello Whisper incompleto: manca $requiredFile"
    }
}

Write-Host "Whisper pronto in $Destination" -ForegroundColor Green
Write-Host "Start-Janus lo rilevera automaticamente se si usa la destinazione predefinita."
