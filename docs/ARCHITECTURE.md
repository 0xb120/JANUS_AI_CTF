# Architettura

## Perimetro

JANUS è un'applicazione locale per una singola postazione pubblica. FastAPI serve
sia il frontend kiosk sia un'API REST. L'inferenza, la trascrizione e la sintesi
vocale avvengono sulla stessa macchina; non esistono dipendenze runtime da
Internet.

~~~mermaid
flowchart LR
    U[Partecipante] -->|testo / push-to-talk| B[Edge kiosk]
    B <-->|REST 127.0.0.1| A[FastAPI]
    A --> E[ChallengeEngine]
    E --> F[FlagService HMAC]
    E --> R[SQLiteRepository]
    E --> L[LLM provider]
    E --> S[faster-whisper CPU]
    E --> T[Piper CPU / SAPI]
    L --> O[Ollama locale]
    L -. alternativa .-> C[Server OpenAI-compatible]
~~~

Con la modalità online (opzionale, `online.enabled`) la stessa applicazione può
essere esposta su Internet dietro un proxy TLS per più giocatori contemporanei;
le sezioni seguenti segnalano le differenze. Il resto del documento descrive il
kiosk locale.

Il percorso operativo predefinito è Ollama con qwen3:4b-instruct. Un server
OpenAI-compatible, per esempio llama.cpp con un GGUF scelto esplicitamente, resta
un'alternativa avanzata. Il provider mock è riservato a DEMO e test.

## Processi

| Processo | Responsabilità | Rete |
| --- | --- | --- |
| Microsoft Edge | UI, MediaRecorder, riproduzione WAV, Web Speech di fallback | Client di 127.0.0.1 |
| JANUS/FastAPI | API, sessioni, challenge, score, statici | 127.0.0.1:8000 |
| Ollama | Inferenza qwen3:4b-instruct | localhost:11434 |
| STT/TTS | faster-whisper e Piper/SAPI | Nessun servizio pubblico |

Start-Janus avvia FastAPI nascosto, verifica /api/health, apre Edge in fullscreen
e chiude il backend quando termina il browser. Non avvia Ollama o un server
OpenAI-compatible: questi devono essere già operativi.

## Composizione backend

### Configurazione

Il loader legge e valida in modo stretto:

- configs/app.yaml;
- configs/modes.yaml;
- configs/hardware.yaml;
- tutti i file configs/levels/*.yaml.

Proprietà sconosciute vengono rifiutate. La CLI può sovrascrivere modalità,
provider e modello LLM, endpoint locale, STT, TTS, modelli Piper, DataDir, host e
porta. Start-Janus costruisce questi override dai propri parametri.

L'unica variabile ambiente applicativa standard è JANUS_SECRET_KEY. Se assente,
SecretKeyStore genera una chiave di 32 byte e la salva nel percorso configurato.

### ChallengeEngine

ChallengeEngine:

- crea sessioni con UUID;
- vincola modalità e livello;
- normalizza nickname Score;
- seleziona IT/EN;
- costruisce system prompt e cronologia limitata;
- invoca il provider LLM;
- applica la output policy del livello;
- genera TTS quando speak è true;
- verifica flag, calcola score, fornisce hint e gestisce reset.

Il motore non effettua streaming: ogni turno REST restituisce la risposta
completa. Il frontend mostra uno stato thinking mentre attende.

### FlagService

La flag è deterministica per chiave, UUID sessione e livello:

~~~text
RH26{LEVEL1-AAAA-BBBB-CCCC-DDDD}
~~~

Il token è derivato con HMAC-SHA256 e codifica Base32. La chiave non entra nel
prompt o nel database. La submission viene normalizzata con trim e maiuscolo,
quindi confrontata tramite HMAC e compare_digest.

Il testo della flag può entrare nel prompt di IANUA e SPECULUM; BIFRONS la riceve
soltanto attraverso il simulatore. Messaggi salvati nel database sostituiscono
una flag letterale con REDACTED_SESSION_FLAG.

### SQLiteRepository

SQLite conserva sessioni e messaggi. All'apertura:

- abilita foreign key e busy timeout;
- usa WAL per database su file;
- elimina sessioni Stand, sessioni Score non vinte e relativi messaggi;
- conserva soltanto risultati Score vinti.

Durante una sessione attiva i messaggi restano nel database. Alla vittoria
vengono cancellati; l'abbandono elimina l'intera sessione. Una sessione Score
vinta non può essere eliminata tramite l'endpoint pubblico.

La leaderboard conserva il miglior risultato per coppia nickname/level,
considerando il nickname senza differenze tra maiuscole e minuscole.

Per la modalità online la persistenza aggiunge:

- la colonna `sessions.owner_id` (NULL in locale, indicizzata con `status`),
  aggiunta con `ALTER TABLE` idempotente;
- la tabella `players`:

| Colonna | Tipo | Note |
| --- | --- | --- |
| `id` | TEXT, chiave primaria | UUID del giocatore |
| `recovery_hash` | TEXT, univoco | SHA-256 del codice di recupero normalizzato |
| `created_at` | TEXT | Origine della scadenza (`player_ttl_hours`) |

Il codice di recupero in chiaro non è mai conservato. Ordina per
score decrescente, completamento più antico e minor numero di turni.

### Provider LLM

Sono implementati:

- Ollama: POST /api/chat e health su /api/tags;
- OpenAI-compatible: POST /chat/completions e health su /models;
- Hugging Face (remoto, opzionale): POST /chat/completions sul router
  Inference Providers con header X-HF-Bill-To facoltativo; health su
  whoami-v2 (validità del token e diritto di addebito all'organizzazione) e
  sul catalogo /models del router (modello e operatore fissato);
- MockLLMProvider: comportamento deterministico per test e DEMO;
- FallbackLLMProvider: disponibile soltanto se fallback_to_mock è true.

La configurazione evento mantiene fallback_to_mock false. L'health check segnala
degraded se un fallback mock è attivo; Start-Janus rifiuta inoltre un provider
reale non disponibile.

Gli URL provider vengono validati: host ammessi 127.0.0.1, localhost o ::1;
il solo provider huggingface accetta esclusivamente
https://router.huggingface.co. Il token Hugging Face è letto soltanto dalla
variabile d'ambiente HF_TOKEN.
Il thinking di Qwen è disabilitato tramite think false per Ollama e
chat_template_kwargs.enable_thinking false per OpenAI-compatible; verso il
router Hugging Face il campo non viene inviato, perché non tutti gli
operatori lo supportano.

### GatedLLMProvider

`GatedLLMProvider` avvolge il provider reale in `_build_llm` (anche mock e
fallback), quindi è attivo in entrambe le modalità; con un solo utente non
scatta mai. Un semaforo limita le chiamate simultanee a `llm.max_concurrent`
(default 8); le richieste in attesa sono servite in ordine FIFO fino a
`llm.max_queue` (default 16). A coda piena solleva `CapacityError`: HTTP 503,
`code: llm_busy`, `details.retry_after` (stima dalla durata media delle
chiamate, tra 2 e 60 s) e header `Retry-After`. `health()` non passa dal
cancello. L'attesa avviene dentro la misura `processing_seconds`, quindi non
è addebitata al giocatore. Un turno rifiutato con `llm_busy` viene annullato:
non costa un turno e non lascia messaggi.

### SessionSweeper

`SessionSweeper` è un task asyncio avviato nel lifespan dell'app. Ogni 60 s, in
entrambe le modalità:

1. porta a `expired` le sessioni attive scadute, cancellando cronologia e audio
   (sotto il lock della sessione);
2. rimuove i WAV orfani più vecchi di 30 minuti;
3. elimina da `_session_locks` le voci di sessioni non attive con lock libero;
4. elimina i contatori inattivi del rate limiter;
5. in modalità online, elimina i giocatori oltre `player_ttl_hours`.

Non tocca le sessioni Score vinte. Gli errori di un passaggio sono registrati e
il ciclo prosegue.

### STT

FasterWhisperProvider:

- carica il modello al primo uso;
- usa local_files_only nel profilo evento;
- serializza il caricamento con un lock;
- accetta una lingua forzata o rileva IT/EN;
- gira su CPU INT8 nella configurazione principale.

L'endpoint voce accetta body audio raw o multipart, limita la dimensione a 20 MB
di default e crea un file temporaneo in voice_output_dir/incoming. Il file viene
eliminato nel finally dopo la trascrizione.

### TTS

PiperTTSProvider è il TTS evento:

- CPU, use_cuda false;
- it_IT-paola-medium;
- en_US-lessac-medium;
- WAV locali con ID esadecimale opaco;
- caricamento lazy e lock per voce.

SapiTTSProvider usa le voci Windows IT/EN installate ed è il fallback backend.
DisabledTTSProvider mantiene disponibile il testo.

I WAV vengono associati alla sessione in memoria e rimossi a vittoria,
abbandono, reset o scadenza. All'inizializzazione del provider vengono eliminati
i WAV residui. GET /api/audio/{audio_id} accetta soltanto ID validi.

Web Speech non è un provider backend: il frontend lo usa solo quando deve
leggere testo privo di audio locale, inclusi i turni testuali e i fallback.

## API effettiva

Il prefisso predefinito è /api.

| Metodo | Endpoint | Funzione |
| --- | --- | --- |
| GET | /api/config | Modalità attiva, livelli pubblici, hardware e capability |
| GET | /api/health | Stato database, LLM, STT e TTS |
| POST | /api/sessions | Crea una sessione nel modo bloccato all'avvio |
| GET | /api/sessions/{id} | Legge stato e applica scadenza backend |
| DELETE | /api/sessions/{id} | Abbandona una sessione non classificata conclusa |
| POST | /api/sessions/{id}/messages | Turno testuale |
| POST | /api/sessions/{id}/voice | Turno vocale raw o multipart |
| POST | /api/sessions/{id}/submit | Verifica flag |
| POST | /api/sessions/{id}/hint | Restituisce il prossimo hint |
| POST | /api/sessions/{id}/reset | Crea una sessione sostitutiva |
| GET | /api/sessions/{id}/messages | Cronologia della sessione (flag mostrata come `[REDACTED_SESSION_FLAG]`) |
| GET | /api/leaderboard | Risultati, opzionalmente filtrati per level_id |
| POST | /api/join | Online: codice evento, crea il giocatore, restituisce il codice di recupero e imposta il cookie |
| POST | /api/recover | Online: codice di recupero, reimposta il cookie dello stesso giocatore |
| GET | /api/players/me | Online: nickname, scadenza e sessioni attive del giocatore |
| POST | /api/logout | Online: cancella il cookie del dispositivo |
| GET | /api/audio/{audio_id} | WAV TTS effimero |
| GET | /api/docs | OpenAPI interattiva |
| GET | / | Kiosk |

Gli endpoint `join`, `recover`, `players/me` e `logout` rispondono 404 con la
modalità online disattivata; in modalità online gli endpoint di sessione e
`/api/audio/{audio_id}` richiedono il cookie giocatore e il proprietario della
risorsa (vedere [SECURITY.md](SECURITY.md#modalità-online-internet)).
`GET /api/sessions/{id}/messages` è disponibile in entrambe le modalità.

Gli asset sono montati sotto /static. L'API usa modelli Pydantic con campi extra
vietati.

## Flusso di sessione

~~~mermaid
stateDiagram-v2
    [*] --> Active: POST /sessions
    Active --> Active: messaggio / voce / hint
    Active --> Won: flag corretta
    Active --> Expired: TTL backend
    Active --> Reset: reset o abbandono
    Won --> [*]: risultato
    Expired --> [*]: nuova partita
    Reset --> Active: sessione sostitutiva
~~~

Il frontend mantiene anche un timer visuale derivato dal TTL e lo sospende mentre
attende STT, LLM o TTS. Lo score sottrae processing_seconds dal tempo del
giocatore; il TTL backend resta basato sul tempo reale trascorso.

Il timer del browser non è autoritativo: la scadenza backend viene applicata
quando la sessione viene letta o usata.

## Modalità

### Stand

- nickname ignorato;
- score assente;
- timer nascosto dalla configurazione della modalità;
- risultato con ritorno automatico ad attract mode;
- sessione eliminata quando il client la rilascia;
- nessuna voce nella leaderboard.

### Score

- nickname obbligatorio, 2–24 caratteri;
- caratteri ammessi: alfanumerici, spazio, underscore, punto e trattino;
- timer visibile;
- score calcolato dal backend;
- sessione vinta conservata;
- leaderboard per livello.

Formula:

~~~text
base_points
+ bonus tempo residuo
- turn_penalty per ogni turno dopo il primo
- hint_penalty per hint usato
con limite inferiore minimum_score
~~~

Il tempo di elaborazione accumulato non riduce il bonus.

## Meccaniche dei livelli

### IANUA

La flag è renderizzata nel system prompt. Il solo output guardrail applicativo è
quello comune di storage; non esiste redazione della risposta. La vulnerabilità
è la dipendenza dalle istruzioni in linguaggio naturale.

### SPECULUM

La flag è nel system prompt. Dopo la generazione, una regex case-insensitive
sostituisce ogni occorrenza letterale con REDACTED e antepone l'avviso localizzato.
Trasformazioni, frammenti e proprietà della flag non vengono bloccati
deterministicamente.

### BIFRONS

La flag non compare nel system prompt iniziale. Il modello può emettere un blocco
JANUS_TOOL per diagnostics.collect. Il parser:

- accetta al massimo il formato previsto;
- rifiuta campi extra;
- limita justification;
- ignora nomi tool diversi.

Il simulatore autorizza soltanto se include_sensitive e operator_confirmed sono
true. Questa fiducia nei booleani del modello è la vulnerabilità intenzionale.
Se autorizzato, aggiunge un INTERNAL_DIAGNOSTIC_RESULT come system message e
invoca il modello una seconda volta. Il risultato contiene sia il valore sia
una rappresentazione strutturata per caratteri, così il percorso autorizzato è
ripetibile anche fra lingue senza accesso a risorse esterne. Il prompt conserva
un'eccezione legacy intenzionalmente difettosa per i record dichiarati già
validati. La redazione letterale resta attiva.

## Frontend

Il frontend è JavaScript senza framework e usa:

- cinque schermate: boot, attract, setup, gioco, risultato/leaderboard;
- avatar SVG replicato da template;
- textContent e nodi di testo per chat e nickname;
- MediaRecorder con WebM/Ogg/MP4 e upload raw canonico;
- Web Audio per meter e lipsync RMS;
- Web Speech come fallback;
- polling health ogni 15 secondi.

Il client ha valori di emergenza per poter mostrare la UI se /api/config fallisce,
ma non sostituisce un backend o un LLM reale.

## Controlli di rete e host

- La CLI limita host a 127.0.0.1, localhost o 0.0.0.0 (quest'ultimo solo in
  container o dietro proxy).
- Start-Janus forza 127.0.0.1.
- TrustedHostMiddleware accetta soltanto host configurati.
- CORS accetta origini localhost configurate e nessuna credenziale.
- I provider LLM locali accettano soltanto URL loopback; huggingface solo il
  router https://router.huggingface.co.

In locale non sono implementati autenticazione utente, pannello admin o accesso
remoto: l'isolamento dipende dal bind locale, dal kiosk e dal presidio fisico.
In modalità online `public_host` si aggiunge a `allowed_hosts`, CORS accetta
solo `https://<public_host>` e l'isolamento è garantito da cookie giocatore,
proprietà delle risorse e limiti (vedere SECURITY.md).

## Limiti noti

- Nessuno streaming token.
- Le richieste della stessa sessione sono serializzate (in modalità online un
  secondo turno riceve subito 409 `turn_in_progress`). Fra sessioni concorrenti
  il `GatedLLMProvider` impone un tetto globale di chiamate simultanee con coda
  FIFO limitata; oltre la coda risponde 503 `llm_busy`.
- Una sola istanza: lock di sessione, rate limiter e cancello LLM sono in
  memoria e SQLite è locale, quindi non sono ammesse repliche.
- Il timer browser sottrae il processing riportato dal server, ma non usa un
  countdown server push.
- Swagger è disabilitato; lo schema OpenAPI resta disponibile localmente.
- Web Speech dipende dalle voci del browser/OS.
- La modalità è selezionata per processo, non da un pannello operatore.
- Il server LLM va avviato e gestito separatamente.

Questi limiti vanno considerati nel runbook e nel threat model, senza attribuire
all'implementazione controlli non presenti.
