param(
    [ValidateSet("stand", "score")]
    [string]$Mode = "stand",

    [ValidateSet("openai_compatible", "ollama", "mock")]
    [string]$Provider = "ollama",

    [string]$Model = "",
    [string]$BaseUrl = "",
    [string]$SttModel = "",
    [ValidateSet("faster_whisper", "disabled")]
    [string]$SttProvider = "faster_whisper",
    [ValidateSet("auto", "piper", "sapi", "disabled")]
    [string]$TtsProvider = "auto",
    [string]$PiperModelIt = "",
    [string]$PiperModelEn = "",
    [int]$Port = 8000,
    [string]$DataDir = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$configDir = Join-Path $projectRoot "configs"

if ([string]::IsNullOrWhiteSpace($Model)) {
    $Model = if ($Provider -eq "ollama") { "qwen3:4b-instruct" } else { "qwen3-4b-janus" }
}
if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
    $BaseUrl = if ($Provider -eq "ollama") {
        "http://127.0.0.1:11434"
    } else {
        "http://127.0.0.1:8080/v1"
    }
}

if ([string]::IsNullOrWhiteSpace($DataDir)) {
    $DataDir = Join-Path $env:LOCALAPPDATA "JANUS\runtime"
}
$DataDir = [System.IO.Path]::GetFullPath($DataDir)
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

if ([string]::IsNullOrWhiteSpace($SttModel)) {
    $preparedWhisper = Join-Path $env:LOCALAPPDATA "JANUS\models\faster-whisper-small"
    $preparedWhisperReady = Test-Path -LiteralPath $preparedWhisper -PathType Container
    foreach ($requiredFile in @("config.json", "model.bin", "tokenizer.json")) {
        if (-not (Test-Path -LiteralPath (Join-Path $preparedWhisper $requiredFile) -PathType Leaf)) {
            $preparedWhisperReady = $false
        }
    }
    if ($SttProvider -eq "faster_whisper" -and -not $preparedWhisperReady) {
        throw "Modello STT offline assente. Esegui scripts\Prepare-Speech.ps1 oppure usa -SttProvider disabled."
    }
    $SttModel = if ($preparedWhisperReady) { $preparedWhisper } else { "small" }
}

$piperDir = Join-Path $env:LOCALAPPDATA "JANUS\models\piper"
if ([string]::IsNullOrWhiteSpace($PiperModelIt)) {
    $PiperModelIt = Join-Path $piperDir "it_IT-paola-medium.onnx"
}
if ([string]::IsNullOrWhiteSpace($PiperModelEn)) {
    $PiperModelEn = Join-Path $piperDir "en_US-lessac-medium.onnx"
}
$piperReady = (Test-Path -LiteralPath $PiperModelIt) -and
    (Test-Path -LiteralPath "$PiperModelIt.json") -and
    (Test-Path -LiteralPath $PiperModelEn) -and
    (Test-Path -LiteralPath "$PiperModelEn.json")
if ($TtsProvider -eq "auto") {
    $TtsProvider = if ($piperReady) { "piper" } else { "sapi" }
}
if ($TtsProvider -eq "piper" -and -not $piperReady) {
    throw "Voci Piper offline incomplete. Esegui scripts\Prepare-Piper.ps1 oppure usa -TtsProvider sapi."
}

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
} else {
    $python = (Get-Command python -ErrorAction Stop).Source
}

$env:PYTHONPATH = Join-Path $projectRoot "src"
$serverOut = Join-Path $DataDir "janus-server.out.log"
$serverErr = Join-Path $DataDir "janus-server.err.log"
$ownedOllama = $null
$server = $null
try {
if ($Provider -eq "ollama") {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null
    } catch {
        $ollama = Get-Command ollama -ErrorAction Stop
        $ollamaOut = Join-Path $DataDir "ollama.out.log"
        $ollamaErr = Join-Path $DataDir "ollama.err.log"
        $ownedOllama = Start-Process -FilePath $ollama.Source -ArgumentList @("serve") `
            -WorkingDirectory $DataDir -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $ollamaOut -RedirectStandardError $ollamaErr
        $ollamaReady = $false
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            if ($ownedOllama.HasExited) {
                $details = if (Test-Path -LiteralPath $ollamaErr) {
                    Get-Content -Raw -LiteralPath $ollamaErr
                } else {
                    "Nessun log Ollama disponibile."
                }
                throw "Ollama non si e avviato.`n$details"
            }
            try {
                Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null
                $ollamaReady = $true
                break
            } catch {
                Start-Sleep -Milliseconds 500
            }
        }
        if (-not $ollamaReady) {
            throw "Ollama non ha superato il preflight entro 20 secondi."
        }
    }
}
$serverArgs = @(
    "-m", "janus",
    "--config-dir", $configDir,
    "--mode", $Mode,
    "--llm-provider", $Provider,
    "--llm-base-url", $BaseUrl,
    "--llm-model", $Model,
    "--stt-provider", $SttProvider,
    "--stt-model", $SttModel,
    "--tts-provider", $TtsProvider,
    "--piper-model-it", $PiperModelIt,
    "--piper-model-en", $PiperModelEn,
    "--data-dir", $DataDir,
    "--host", "127.0.0.1",
    "--port", $Port
)

$server = Start-Process -FilePath $python -ArgumentList $serverArgs `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr

$url = "http://127.0.0.1:$Port/"
$ready = $false
$health = $null
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if ($server.HasExited) {
            $details = if (Test-Path -LiteralPath $serverErr) {
                Get-Content -Raw -LiteralPath $serverErr
            } else {
                "Nessun log disponibile."
            }
            throw "JANUS non si e avviato.`n$details"
        }
        try {
            $health = Invoke-RestMethod -Uri "${url}api/health" -TimeoutSec 15
            $ready = $true
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        throw "JANUS non ha superato il preflight HTTP entro 20 secondi."
    }
    if ($Provider -ne "mock" -and -not $health.components.llm.available) {
        throw "LLM locale non pronto: $($health.components.llm.detail)"
    }
    if ($SttProvider -ne "disabled" -and -not $health.components.stt.available) {
        throw "STT locale non pronto: $($health.components.stt.detail)"
    }
    if ($TtsProvider -ne "disabled" -and -not $health.components.tts.available) {
        throw "TTS locale non pronto: $($health.components.tts.detail)"
    }

    $edgeCandidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\Edge\Application\msedge.exe")
    )
    $edge = $edgeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($edge) {
        $profileDir = Join-Path $DataDir "edge-profile-$Mode"
        $browser = Start-Process -FilePath $edge -PassThru -ArgumentList @(
            "--kiosk", $url,
            "--edge-kiosk-type=fullscreen",
            "--no-first-run",
            "--disable-session-crashed-bubble",
            "--user-data-dir=$profileDir"
        )
        Wait-Process -Id $browser.Id
    } else {
        Write-Warning "Microsoft Edge non trovato: apertura nel browser predefinito senza kiosk."
        Start-Process $url
        Wait-Process -Id $server.Id
    }
} finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -ErrorAction SilentlyContinue
        Wait-Process -Id $server.Id -ErrorAction SilentlyContinue
    }
    if ($ownedOllama -and -not $ownedOllama.HasExited) {
        Stop-Process -Id $ownedOllama.Id -ErrorAction SilentlyContinue
        Wait-Process -Id $ownedOllama.Id -ErrorAction SilentlyContinue
    }
}
