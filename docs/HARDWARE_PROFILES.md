# Profili hardware

## Macchina di riferimento

| Componente | Configurazione | Budget operativo |
| --- | --- | --- |
| CPU | Intel Core i7-11850H, 8 core / 16 thread | STT e TTS su CPU |
| RAM | 80 GB installati | Circa 45 GB liberi se 35 GB sono già occupati |
| GPU | NVIDIA RTX A3000 Laptop, 6 GB VRAM | LLM 4B; evitare contesa speech |
| Storage | NVMe, oltre 100 GB liberi | Modelli, runtime, log e backup |

Il progetto deve restare utilizzabile anche su una macchina ridotta. La
dimensione nominale della RAM non sostituisce la misura dopo il boot dell'utente
evento.

## Baseline evento

La baseline è Qwen3 4B Instruct:

- percorso operativo predefinito: Ollama con tag qwen3:4b-instruct;
- percorso avanzato: GGUF Q5_K_M su server OpenAI-compatible;
- contesto di riferimento: 4096 token;
- thinking disabilitato;
- una sola sessione pubblica;
- faster-whisper small, CPU INT8;
- Piper, CPU;
- avatar SVG 2D.

configs/hardware.yaml descrive Qwen3 4B Q5_K_M con offload GPU completo come
riferimento per a3000_6gb. Ollama gestisce formato e quantizzazione del proprio
tag: registrare il manifest effettivo della build usata. Se è necessario
garantire esattamente Q5_K_M, usare il percorso OpenAI-compatible con un GGUF
verificato.

I modelli non sono inclusi nel repository. Prepare-Ollama scarica qwen3:4b-instruct;
acquisizione del GGUF e avvio di llama.cpp non sono automatizzati.

## Perché 4B

Sei GB di VRAM devono contenere pesi, cache KV e buffer di calcolo. Un 4B
quantizzato lascia più margine di un 8B e consente di mantenere:

- contesto multi-turn;
- latenza adatta a una fila;
- STT e TTS stabili sulla CPU;
- browser e animazioni reattivi;
- recovery prevedibile.

La quantizzazione modifica anche la risposta ai jailbreak. Ogni tag o GGUF va
testato come parte della challenge, non soltanto come carico hardware.

## Profili versionati

### a3000_6gb

Profilo principale:

| Parametro | Valore |
| --- | --- |
| VRAM minima | 5.5 GB |
| RAM libera minima | 16 GB |
| LLM | Qwen3 4B Instruct |
| Riferimento GGUF | Q5_K_M |
| Contesto | 4096 |
| GPU | Full offload se il benchmark lo conferma |
| STT | faster-whisper small CPU INT8 |
| TTS | Piper CPU |

Con Ollama, qwen3:4b-instruct è il valore predefinito di app.yaml, Start-Janus e
Prepare-Ollama.

### reduced_4gb

Per GPU da circa 4 GB e almeno 10 GB RAM libera:

| Parametro | Valore |
| --- | --- |
| LLM | Qwen3 4B Instruct |
| Riferimento GGUF | Q4_K_M |
| Contesto | 3072 |
| GPU | Offload adattato al margine |
| STT | faster-whisper base CPU INT8 |
| TTS | Piper o SAPI CPU |

Il modello resta 4B; si riducono quantizzazione, contesto e STT.

### cpu_fallback

Per assenza di GPU e almeno 8 GB RAM libera:

| Parametro | Valore |
| --- | --- |
| LLM | Qwen3 1.7B o 4B |
| Quantizzazione | Q4_K_M CPU |
| Contesto | 2048 |
| GPU layer | 0 |
| STT | faster-whisper tiny o base CPU INT8 |
| TTS | SAPI/Piper se sostenibile |

Questo profilo offre continuità, non equivalenza: latenza, italiano e solve rate
vanno riverificati.

## 8B: opzione, non baseline

Qwen3 8B può essere provato soltanto in modalità ibrida CPU/GPU. Non inserirlo
nella release evento finché non supera tutti questi criteri sulla macchina
finale:

- nessun OOM nella sessione più lunga;
- prova continuativa di almeno due ore;
- latenza p95 compatibile con il flusso dello stand;
- golden attack IT/EN di tutti i livelli;
- STT e Piper contemporaneamente disponibili;
- reset e cambio sessione senza crescita progressiva di RAM/VRAM;
- temperature senza throttling sostenuto.

Il beneficio qualitativo deve essere dimostrato rispetto al 4B. In assenza di un
vantaggio misurabile, mantenere qwen3:4b-instruct.

## Allocazione dei componenti

| Componente | a3000_6gb | reduced_4gb | cpu_fallback |
| --- | --- | --- | --- |
| LLM | GPU | GPU/ibrido | CPU |
| faster-whisper | CPU INT8 | CPU INT8 | CPU INT8 |
| Piper | CPU | CPU | CPU o disabilitato |
| SAPI | Fallback CPU | Fallback CPU | Fallback CPU |
| Web Speech | Solo fallback UI | Solo fallback UI | Solo fallback UI |
| Browser/avatar | GPU leggera | Effetti 2D | Effetti 2D |

Piper deve usare piper_use_cuda false. Spostare STT o TTS sulla A3000 riduce il
margine utile al modello.

## Percorsi

Gli script usano:

~~~text
%LOCALAPPDATA%\JANUS\models\faster-whisper-small
%LOCALAPPDATA%\JANUS\models\piper
%LOCALAPPDATA%\JANUS\runtime
~~~

Il runtime contiene:

- janus.sqlite3;
- janus.key, se la chiave non arriva dall'ambiente;
- audio effimero;
- log stdout/stderr;
- profilo Edge per modalità.

Questi percorsi sono fuori da OneDrive. Start-Janus consente DataDir, SttModel e
modelli Piper personalizzati tramite CLI.

Esempio:

~~~powershell
.\JANUS_STAND.cmd -DataDir C:\JANUS\runtime -SttModel C:\JANUS\models\faster-whisper-small
~~~

Modello Ollama e storage sono gestiti dall'installazione Ollama. Registrarne
percorso, tag e digest nella scheda release del runbook.

## Preparazione

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-Janus.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Prepare-Speech.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Prepare-Piper.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Prepare-Ollama.ps1
~~~

Questi passaggi richiedono rete. Dopo la preparazione eseguire avvio e test con
rete disconnessa.

## Benchmark

### Carico

Usare sempre la release evento:

- 10 turni brevi e 5 lunghi per lingua;
- un turno vocale per ogni risposta lunga;
- golden attack IANUA, SPECULUM e BIFRONS;
- BIFRONS con doppia inferenza;
- sessione fino al limite di cronologia;
- 20 sessioni consecutive;
- prova continuativa di due ore;
- reset, abbandono e riavvio Arena.

### Misure

- tempo di warm-up;
- tempo totale LLM;
- durata STT e Piper;
- p50, p95 e massimo per turno;
- picco RAM e VRAM;
- temperatura e throttling;
- errori provider e OOM;
- spazio runtime;
- solve rate IT/EN.

### Go/no-go consigliato

- zero crash e zero OOM nella prova continuativa;
- p95 di una risposta breve entro 20 secondi;
- UI reattiva e feedback thinking immediato;
- almeno 500 MB di margine VRAM al picco;
- almeno 4 GB RAM libera al picco;
- almeno 20 GB NVMe liberi prima dell'apertura;
- 100% dei test meccanici;
- golden path riproducibile in entrambe le lingue.

Controllo rapido del modello reale:

~~~powershell
.\.venv\Scripts\python.exe .\scripts\validate_live_model.py --repeat 2
~~~

Se un obiettivo non è raggiunto, degradare prima dell'evento. Non aumentare
timeout e modello per nascondere instabilità.

## Scala di degradazione

1. Chiudere software, sync e processi GPU non necessari.
2. Confermare qwen3:4b-instruct, non 8B.
3. Mantenere STT e Piper sulla CPU.
4. Ridurre contesto a 3072.
5. Usare il profilo reduced_4gb.
6. Passare faster-whisper da small a base.
7. Usare SAPI invece di Piper.
8. Disabilitare STT/TTS e continuare via testo.
9. Usare cpu_fallback dopo smoke test.

Avvio solo testo:

~~~powershell
.\JANUS_STAND.cmd -SttProvider disabled -TtsProvider disabled
~~~

Dopo ogni cambio:

1. riavviare JANUS;
2. verificare /api/health;
3. eseguire un turno IT e uno EN;
4. provare una submission;
5. eseguire un golden attack breve;
6. verificare cleanup.

## TTS evento

Prepare-Piper scarica:

- it_IT-paola-medium.onnx e relativo JSON;
- en_US-lessac-medium.onnx e relativo JSON.

Start-Janus con TtsProvider auto sceglie Piper solo se entrambi i modelli sono
completi; altrimenti usa SAPI. Per forzare:

~~~powershell
.\JANUS_STAND.cmd -TtsProvider piper
.\JANUS_STAND.cmd -TtsProvider sapi
~~~

Il preflight blocca l'apertura se il provider scelto non è sano. Web Speech può
leggere una risposta dal browser, ma non sostituisce il benchmark Piper.

## Termica e alimentazione

- usare l'alimentatore originale;
- piano rigido e prese d'aria libere;
- piano energetico testato;
- sospensione disabilitata durante l'apertura;
- nessun aggiornamento driver alla vigilia;
- controllo temperature dopo almeno un'ora;
- profilo reduced_4gb pronto.

L'avatar SVG è leggero. Prima di ridurre il contesto utile, eliminare processi e
carichi grafici estranei.

## Manifest hardware/software

Registrare nella scheda release:

- CPU, RAM libera e GPU;
- sistema operativo e driver NVIDIA;
- versione Ollama o server alternativo;
- tag e digest qwen3:4b-instruct oppure hash GGUF;
- quantizzazione effettiva;
- contesto e offload;
- modello faster-whisper;
- voci Piper e relativi file;
- versione JANUS;
- risultati soak e p95;
- data della prova offline.

Non includere JANUS_SECRET_KEY, janus.key, flag o pseudonimi nel manifest.
