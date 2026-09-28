param(
    [string]$Model = "qwen3:4b-instruct"
)

$ErrorActionPreference = "Stop"
$ollama = Get-Command ollama -ErrorAction Stop
Write-Host "Download del modello locale $Model tramite Ollama..."
& $ollama.Source pull $Model
if ($LASTEXITCODE -ne 0) {
    throw "Il download del modello Ollama non e riuscito."
}
Write-Host "Modello pronto. Avvia JANUS aggiungendo: -Provider ollama" -ForegroundColor Green
