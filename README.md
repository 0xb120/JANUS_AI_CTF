# JANUS — AI Security CTF

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![CI](https://github.com/Redragon948/JANUS_AI_CTF/actions/workflows/ci.yml/badge.svg)](https://github.com/Redragon948/JANUS_AI_CTF/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-D22128?logo=apache)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows)](https://www.microsoft.com/windows)
[![Event: RomHack 2026](https://img.shields.io/badge/Event-RomHack%202026-7C3AED)](https://romhack.io/)

**JANUS** è una challenge CTF locale e bilingue di **AI security**, sviluppata
per lo stand MeetHack a RomHack 2026. Il partecipante conversa in italiano o in
inglese con un custode IA che protegge una flag univoca per sessione. L'obiettivo
è sfruttare debolezze intenzionali nei guardrail, restando nel perimetro sicuro e
isolato del gioco.

Il progetto offre un'esperienza completa da stand: interfaccia kiosk, input
testuale e vocale, risposte vocali, tre livelli progressivi, modalità anonima o
competitiva e classifica locale. Tutti i componenti runtime possono funzionare
offline sulla stessa macchina.

> **English summary:** JANUS is an offline, bilingual AI-security CTF for
> RomHack 2026. Players use prompt-injection and confused-deputy techniques to
> recover a per-session flag from a local LLM. The application includes a kiosk
> UI, optional local speech, three levels and a local leaderboard.

## Indice

- [Caratteristiche](#caratteristiche)
- [Come funziona](#come-funziona)
- [Quick start](#quick-start)
- [Installazione completa](#installazione-completa)
- [Configurazione dei modelli](#configurazione-dei-modelli)
- [Utilizzo](#utilizzo)
- [Configurazione](#configurazione)
- [Sviluppo e test](#sviluppo-e-test)
- [Sicurezza e privacy](#sicurezza-e-privacy)
- [Struttura del progetto](#struttura-del-progetto)
- [Documentazione](#documentazione)
- [Contribuire](#contribuire)
- [Citazione e licenza](#citazione-e-licenza)

## Caratteristiche

- esperienza **IT/EN** con rilevamento della lingua per testo e voce;
- backend Python/FastAPI e frontend kiosk senza dipendenze web remote;
- flag diversa per sessione, derivata tramite HMAC e mai salvata in chiaro;
- tre challenge progressive su prompt injection, data exfiltration e confused
  deputy;
- modalità **Stand** anonima e modalità **Arena** con punteggio e leaderboard;
- provider LLM per **Ollama**, server **OpenAI-compatible** e mock deterministico;
- speech-to-text locale con **faster-whisper**;
- text-to-speech locale con **Piper**, con fallback Windows SAPI;
- persistenza SQLite, cleanup automatico e protezioni per l'esecuzione locale;
- launcher PowerShell/Windows e suite di test automatizzata.

I pesi LLM, i modelli Whisper e le voci Piper **non sono inclusi** nel
repository. Gli script di preparazione li installano separatamente in locale.

## Come funziona

```mermaid
flowchart LR
    P[Partecipante] -->|testo o push-to-talk| K[Edge kiosk]
    K <-->|REST su 127.0.0.1| A[FastAPI]
    A --> E[ChallengeEngine]
    E --> F[FlagService HMAC]
    E --> D[(SQLite)]
    E --> L[LLM locale]
    E --> S[faster-whisper]
    E --> T[Piper / SAPI]
    L --> O[Ollama]
    L -. alternativa .-> C[Server OpenAI-compatible]
```

| Livello | Alias | Concetto didattico |
| --- | --- | --- |
| 1 | **IANUA — La Porta di Giano** | Prompt injection contro una regola fragile |
| 2 | **SPECULUM — Lo Specchio Bifronte** | Esfiltrazione trasformata e output filtering |
| 3 | **BIFRONS — Il Caveau del Custode** | Confused deputy e confini di autorizzazione |

Ogni sessione riceve una flag nel formato
`RH26{LEVEL1-AAAA-BBBB-CCCC-DDDD}`. L'applicazione, non il modello, verifica la
submission e calcola l'eventuale punteggio.

## Quick start

Per provare subito interfaccia e flusso senza scaricare modelli:

```powershell
git clone https://github.com/Redragon948/JANUS_AI_CTF.git
cd JANUS_AI_CTF
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-Janus.ps1 -WithDevelopmentTools
.\JANUS_DEMO.cmd
```

La modalità Demo usa un provider mock deterministico e disabilita STT/TTS. È
ideale per sviluppo e verifica della UI, ma non rappresenta la configurazione
dell'evento.

## Installazione completa

### Prerequisiti

- Windows 10/11;
- Python 3.11 o successivo;
- Microsoft Edge per l'avvio kiosk automatico;
- Ollama o un server LLM OpenAI-compatible locale, salvo la modalità Demo;
- accesso a Internet durante il solo download iniziale dei modelli;
- spazio locale esterno a OneDrive per modelli e dati runtime.

### Installazione automatica

Da PowerShell, nella root del repository:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-Janus.ps1
```

Lo script crea `.venv` e installa JANUS con le dipendenze speech. Per includere
anche pytest, coverage e Ruff:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-Janus.ps1 -WithDevelopmentTools
```

### Installazione manuale per sviluppo

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[speech,dev]"
```

## Configurazione dei modelli

### Ollama

Con Ollama già installato e avviato:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Prepare-Ollama.ps1
```

Il modello evento predefinito è `qwen3:4b-instruct`. È possibile indicare un
tag differente:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Prepare-Ollama.ps1 -Model qwen3:4b-instruct
```

### Speech-to-text e text-to-speech

Preparare i modelli vocali prima dell'uso offline:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Prepare-Speech.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Prepare-Piper.ps1
```

Destinazioni predefinite:

```text
%LOCALAPPDATA%\JANUS\models\faster-whisper-small
%LOCALAPPDATA%\JANUS\models\piper
```

Le voci Piper evento sono `it_IT-paola-medium` e `en_US-lessac-medium`. Se
Piper non è disponibile e il provider TTS è `auto`, JANUS prova Windows SAPI.

Per usare un modello Whisper o un percorso differente:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Prepare-Speech.ps1 -Model base
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Prepare-Speech.ps1 -Destination C:\JANUS\models\faster-whisper-small
```

## Utilizzo

### Launcher disponibili

| Comando | Modalità | LLM | Voce | Scenario |
| --- | --- | --- | --- | --- |
| `.\JANUS_DEMO.cmd` | Stand | Mock | Disabilitata | Demo e sviluppo UI |
| `.\JANUS_STAND.cmd` | Stand | Ollama | Locale | Partita anonima |
| `.\JANUS_ARENA.cmd` | Score | Ollama | Locale | Nickname, score e leaderboard |

La modalità viene fissata dall'operatore all'avvio e non può essere cambiata dal
browser durante una sessione.

### Avvio con Ollama

Con Ollama e il modello già pronti:

```powershell
.\JANUS_STAND.cmd
.\JANUS_ARENA.cmd
```

I launcher usano `qwen3:4b-instruct` su `http://127.0.0.1:11434`, eseguono il
preflight di LLM/STT/TTS, avviano FastAPI su `127.0.0.1:8000` e aprono Edge a
schermo intero. Alla chiusura del browser arrestano anche il backend.

### Server OpenAI-compatible

Avviare separatamente il server locale su `http://127.0.0.1:8080/v1`, quindi:

```powershell
.\JANUS_STAND.cmd -Provider openai_compatible -BaseUrl http://127.0.0.1:8080/v1 -Model qwen3-4b-janus
.\JANUS_ARENA.cmd -Provider openai_compatible -BaseUrl http://127.0.0.1:8080/v1 -Model qwen3-4b-janus
```

### Override operativi

```powershell
# Dati runtime in un percorso dedicato
.\JANUS_STAND.cmd -DataDir C:\JANUS\runtime

# Esecuzione solo testuale
.\JANUS_STAND.cmd -SttProvider disabled -TtsProvider disabled

# TTS tramite Windows SAPI
.\JANUS_STAND.cmd -TtsProvider sapi

# Modello Whisper già preparato in un percorso personalizzato
.\JANUS_STAND.cmd -SttModel C:\JANUS\models\faster-whisper-small
```

Il `DataDir` predefinito è `%LOCALAPPDATA%\JANUS\runtime` e contiene database,
chiave, audio effimero, profilo Edge e log. Per una leaderboard persistente è
necessario conservare insieme database e chiave.

## Configurazione

La configurazione dichiarativa è suddivisa in:

- `configs/app.yaml`: applicazione, provider, limiti e speech;
- `configs/modes.yaml`: modalità Stand e Score;
- `configs/hardware.yaml`: profili hardware raccomandati;
- `configs/levels/*.yaml`: prompt, hint, policy e scoring dei livelli.

Il loader rifiuta proprietà sconosciute. CLI e `Start-Janus.ps1` possono
sovrascrivere modalità, provider, modelli, speech, `DataDir`, host e porta.

L'unica variabile ambiente applicativa standard è `JANUS_SECRET_KEY`. Sono
accettati testo raw di almeno 32 byte, `hex:` con almeno 64 cifre o `base64:` con
almeno 32 byte decodificati. Il file `.env` non viene caricato automaticamente;
[`.env.example`](.env.example) documenta il formato. Se la variabile non è
impostata, JANUS genera e persiste una chiave nel `DataDir`.

## Sviluppo e test

Eseguire la suite automatica:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Controllare stile e qualità statica:

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests scripts
```

Con Ollama e il modello evento disponibili, gli organizzatori possono eseguire
anche i golden attack reali IT/EN:

```powershell
.\.venv\Scripts\python.exe .\scripts\validate_live_model.py --repeat 2
```

> Lo script di validazione contiene percorsi risolutivi ed è destinato agli
> operatori. Non deve essere visibile sulla postazione kiosk durante la gara.

La suite copre API, modalità, scoring, concorrenza, flag HMAC, output policy di
SPECULUM, tool simulato di BIFRONS, provider locali, TTS e protezione dal path
traversal degli artefatti audio.

## Sicurezza e privacy

JANUS è progettato come laboratorio locale e consensuale:

- API e provider LLM accettano soltanto endpoint loopback;
- Trusted Host e CORS sono limitati alla macchina locale;
- ogni sessione usa una flag HMAC distinta e confronti constant-time;
- il tool `diagnostics.collect` è un simulatore in memoria senza accesso a rete,
  shell o filesystem;
- i messaggi frontend sono renderizzati come testo, non come HTML arbitrario;
- il fallback mock è disattivato nelle configurazioni evento;
- audio in ingresso e WAV TTS sono temporanei e vengono eliminati;
- sessioni anonime e sessioni Score non vinte vengono ripulite al riavvio;
- la flag non viene memorizzata in chiaro nel database.

Il kiosk rimane una postazione fisica da presidiare. Prima di un evento leggere
il [runbook operativo](docs/EVENT_RUNBOOK.md) e il documento di
[sicurezza](docs/SECURITY.md).

> **Uso responsabile:** le vulnerabilità dei guardrail sono intenzionali e
> confinate alla challenge. Usare tecniche di prompt injection soltanto su
> sistemi propri o per i quali si dispone di autorizzazione esplicita.

## Struttura del progetto

```text
JANUS_AI_CTF/
├── configs/                 # Applicazione, modalità, hardware e livelli
├── .github/workflows/       # Verifica automatica su GitHub Actions
├── docs/                    # Architettura, sicurezza e runbook
├── scripts/                 # Installazione, avvio e preparazione modelli
├── src/janus/               # Backend, provider e frontend kiosk
│   ├── providers/           # Ollama/OpenAI-compatible, STT e TTS
│   └── web/                 # HTML, CSS e JavaScript
├── tests/                   # Suite pytest
├── JANUS_DEMO.cmd           # Demo senza modelli
├── JANUS_STAND.cmd          # Modalità anonima
├── JANUS_ARENA.cmd          # Modalità competitiva
└── pyproject.toml           # Packaging e dipendenze
```

## Documentazione

- [Architettura](docs/ARCHITECTURE.md) — componenti, flussi, API e persistenza;
- [Authoring delle challenge](docs/CHALLENGE_AUTHORING.md) — progettazione e
  modifica sicura dei livelli;
- [Runbook evento](docs/EVENT_RUNBOOK.md) — preparazione, preflight e procedure
  operative;
- [Sicurezza](docs/SECURITY.md) — modello di sicurezza e limiti del kiosk;
- [Profili hardware](docs/HARDWARE_PROFILES.md) — baseline e fallback;
- [Sintesi tecnica PDF](docs/JANUS_Sintesi_Tecnica.pdf).

## Contribuire

Issue e pull request sono benvenute. Prima di proporre una modifica:

1. creare un branch dedicato;
2. mantenere l'esecuzione runtime offline e su loopback;
3. evitare di introdurre segreti, modelli o dati dei partecipanti nel commit;
4. eseguire `pytest` e `ruff check`;
5. documentare ogni variazione a challenge, configurazione o procedura evento.

Le modifiche ai prompt o ai provider devono essere validate sia in italiano sia
in inglese e non devono trasformare i tool simulati in capacità reali.

## Citazione e licenza

JANUS è distribuito con licenza [Apache License 2.0](LICENSE). È consentito
usare, modificare e ridistribuire il software, anche commercialmente, purché
siano rispettati i termini della licenza e siano conservati gli avvisi di
copyright, licenza e attribuzione presenti in [NOTICE](NOTICE).

Per citare il progetto in articoli, talk, workshop o materiale didattico, usare
i metadati GitHub disponibili in [`CITATION.cff`](CITATION.cff), oppure:

```text
JANUS contributors. JANUS — Offline Bilingual AI Security CTF for RomHack 2026.
https://github.com/Redragon948/JANUS_AI_CTF
```

Modelli e dipendenze di terze parti mantengono le rispettive licenze e
condizioni d'uso; non sono redistribuiti in questo repository.
