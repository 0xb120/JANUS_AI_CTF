# JANUS online multi-giocatore — design

- **Data:** 2026-10-07
- **Branch:** `feat/multiplayer` (fork `0xb120/JANUS_AI_CTF`)
- **Stato:** design approvato in sessione, in revisione come documento

## 1. Obiettivo

Una volta avviata, un'istanza JANUS esposta su Internet deve supportare
**10–50 giocatori contemporanei**, ciascuno dal proprio dispositivo, che
giocano in parallelo **senza interferenze**. Il kiosk locale attuale deve
continuare a funzionare come oggi.

"Nessuna interferenza" significa tre garanzie distinte:

| Garanzia | Significato |
| --- | --- |
| **Contesto** | Il prompt inviato all'LLM per un giocatore contiene solo la storia e la flag della sua sessione |
| **Controllo** | Nessun giocatore può leggere, modificare, resettare o cancellare sessioni, audio o nickname altrui |
| **Equità** | Nessun giocatore può monopolizzare l'LLM o le risorse a danno degli altri |

### Requisiti forniti

- Esposizione su Internet.
- 10–50 giocatori simultanei.
- Accesso protetto da **codice evento**.
- HTTPS tramite **Caddy nello stack** oppure tramite **proxy esterno**.
- **Recupero della sessione** dopo chiusura del browser, crash o cambio dispositivo.

### Criteri di successo

1. 50 giocatori simultanei via HTTPS (voce inclusa) senza contaminazioni di contesto.
2. Ogni tentativo di agire su risorse altrui riceve 404 e non ha effetti.
3. Con l'LLM saturo, il sistema risponde 503 con `Retry-After`, in modo equo,
   invece di accumulare code illimitate.
4. Un giocatore riprende la propria partita dopo aver chiuso il browser (stesso
   dispositivo) o con il codice di recupero (altro dispositivo).
5. La suite esistente passa invariata con la modalità online disattivata.

## 2. Analisi dello stato attuale

### Già garantito (verificato)

- `ChallengeEngine._message_locked` costruisce i messaggi LLM solo da
  `repository.get_messages(session.id)` e dal system prompt con la flag HMAC
  della sessione (`FlagService.derive(session.id, level_id)`).
- I provider LLM sono senza stato; `SimulatedToolRuntime` è senza stato.
- Ogni sessione ha un `asyncio.Lock` dedicato.
- **Prova dal vivo:** 4 giocatori paralleli su Ollama e 8 su Hugging Face
  ("ricorda la mia parola", poi "ripetila"). Contaminazioni: **0 su 12**.
  - HF: 8 giocatori in 4,6 s complessivi (0,7–3,6 s per turno).
  - Ollama su CPU: turni fino a 38,7 s, perché le richieste vengono servite una alla volta.

### Lacune per il gioco online

| # | Lacuna | Conseguenza |
| --- | --- | --- |
| G1 | CLI, `allowed_hosts` e CORS limitati a loopback | Non raggiungibile da altri dispositivi |
| G2 | L'ID di sessione nel path è l'unica credenziale | Chi lo ottiene (sniffing, condivisione) può leggere, resettare o cancellare la sessione altrui |
| G3 | Il microfono richiede un contesto sicuro | Push-to-talk inutilizzabile su HTTP non-localhost |
| G4 | Nessun limite per sessione o client; la coda LLM è implicita nel provider | Un giocatore può saturare l'LLM per tutti |
| G5 | La classifica deduplica per nickname senza proprietario | Due giocatori con lo stesso nickname si fondono |
| G6 | La scadenza delle sessioni è "pigra" (solo all'accesso) | Trascrizioni e audio delle sessioni abbandonate restano su disco |
| G7 | Il dizionario `_session_locks` cresce per tutta la vita del processo | Crescita di memoria con molti giocatori |
| G8 | `/api/audio/{id}` non è legato alla sessione | Audio accessibile a chiunque ne conosca l'ID |
| G9 | Il frontend tiene la sessione solo in memoria | Ricarica o chiusura della tab fanno perdere la partita |

## 3. Approccio scelto

**Modalità `online` implementata nell'applicazione; il proxy si occupa solo del TLS.**

Scartati:
- **Autenticazione e rate limit solo nel reverse proxy:** il proxy non conosce
  il proprietario delle sessioni, quindi non risolve G2 e G8. Inoltre limita
  per IP, il che è ingiusto dietro NAT.
- **Integrazione con una piattaforma CTF esterna:** sproporzionata rispetto all'obiettivo.

**Principio:** con `online.enabled: false` il comportamento è identico a oggi.
Tutte le nuove regole si attivano solo in modalità online, salvo lo spazzino
(§4.6) e il cancello LLM (§4.4), che restano innocui anche in locale.

## 4. Design

### 4.1 Configurazione

Nuovo blocco in `configs/app.yaml` (modello `OnlineSettings`), più due campi
nel blocco `llm`:

```yaml
online:
  enabled: false
  public_host: null              # es. ctf.example.com; obbligatorio se enabled
  access_codes_env: JANUS_ACCESS_CODES   # codici separati da virgola, solo env
  trusted_proxies: []            # IP/CIDR i cui X-Forwarded-* sono attendibili
  player_ttl_hours: 12
  limits:
    auth_attempts_per_minute: 10 # per IP, su /join e /recover
    turns_per_minute: 12         # per giocatore, testo + voce
    sessions_per_hour: 20        # per giocatore
    max_active_sessions: 1       # per giocatore
llm:
  max_concurrent: 8              # chiamate LLM simultanee (cancello globale)
  max_queue: 16                  # richieste in attesa oltre max_concurrent
```

**Validazione**
- Con `enabled: true` servono `public_host` e almeno un codice in
  `JANUS_ACCESS_CODES`. Altrimenti `ConfigurationError` all'avvio.
- Ogni codice deve avere almeno 8 caratteri.

**Override da CLI**, rispecchiati dall'entrypoint Docker tramite variabili d'ambiente:

| CLI | Variabile env | Effetto |
| --- | --- | --- |
| `--online` | `JANUS_ONLINE=1` | `online.enabled: true` |
| `--public-host HOST` | `JANUS_PUBLIC_HOST` | `online.public_host` |
| `--trusted-proxies CIDR[,CIDR]` | `JANUS_TRUSTED_PROXIES` | `online.trusted_proxies`; passato anche a uvicorn come `forwarded_allow_ips` con `proxy_headers=True` |

**Effetti di `public_host`**
- `allowed_hosts` diventa: valori YAML (loopback inclusi, per l'healthcheck) più `public_host`.
- `cors_origins` diventa `["https://<public_host>"]`.

### 4.2 Identità del giocatore, accesso e recupero

**Modello dati.** Nuova tabella più una colonna:

```sql
CREATE TABLE players (
  id TEXT PRIMARY KEY,            -- uuid4
  recovery_hash TEXT NOT NULL UNIQUE,  -- sha256 hex del codice di recupero
  created_at TEXT NOT NULL
);
ALTER TABLE sessions ADD COLUMN owner_id TEXT NULL;  -- NULL in modalità locale
CREATE INDEX idx_sessions_owner ON sessions(owner_id);
```

La migrazione segue lo schema esistente in `SQLiteRepository.initialize`
(`PRAGMA table_info` e `ALTER TABLE` idempotente).

**Cookie giocatore `janus_player`**
- **Formato:** `v1.<player_id>.<expires_unix>.<sig>`.
- **Firma:**
  - `sig` = base64url senza padding di `HMAC-SHA256(k_player, "v1|<player_id>|<expires_unix>")`;
  - `k_player` = `HMAC-SHA256(master_key, "janus/player-token/v1")`, dove `master_key` è la chiave HMAC già esistente (separazione di dominio rispetto alle flag).
- **Attributi:** `HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=<residuo>`.
- **Scadenza:** assoluta, `players.created_at + player_ttl_hours`. Il recupero non la prolunga.
- **Verifica:** senza stato (firma + scadenza), confronto `hmac.compare_digest`.
- **Unità isolata:** `PlayerTokenService` in `security.py`, con le funzioni `issue` e `verify`.

**Codice di recupero**
- **Formato:** `RCV-XXXX-XXXX-XXXX-XXXX`: 80 bit da `secrets`, alfabeto Crockford base32, senza `I L O U`.
- **Input:** maiuscole/minuscole, trattini e spazi vengono ignorati.
- **Conservazione:** nel DB c'è solo `sha256(codice normalizzato)`. Il codice ha alta entropia, quindi un hash non salato è sufficiente.
- **Visibilità:** viene mostrato una sola volta, nella risposta di `/api/join`.

**Endpoint** (attivi solo in modalità online; in locale rispondono 404)

| Metodo | Percorso | Corpo | Esito |
| --- | --- | --- | --- |
| POST | `/api/join` | `{code}` | 201 `{recovery_code, expires_at}` + cookie; 401 codice errato; 429 |
| POST | `/api/recover` | `{recovery_code}` | 200 `{expires_at}` + cookie; 401 codice errato o giocatore scaduto; 429 |
| GET | `/api/players/me` | — | 200 `{nickname, expires_at, active_sessions:[{id, mode_id, level_id, started_at, remaining_seconds}]}`; 401 |
| POST | `/api/logout` | — | 204, cancella il cookie |

- I codici evento si confrontano a tempo costante contro ciascun codice configurato.
- Un 401 da `/join` o `/recover` non distingue la causa.

**Proprietà delle risorse (online)**
- **Creazione:** `POST /api/sessions` richiede il cookie; la sessione nasce con `owner_id = player_id`.
- **Endpoint di sessione:** valgono per tutti, cioè `GET/DELETE /api/sessions/{id}` e `…/messages` (POST e GET), `…/voice`, `…/submit`, `…/hint`, `…/reset`. Senza cookie: **401**. Sessione di un altro giocatore: **404**, risposta identica a quella di una sessione inesistente. Il controllo sta in un'unica funzione d'accesso del motore (`_owned_session`), non duplicata negli endpoint.
- **Audio:** `GET /api/audio/{id}` verifica che l'audio appartenga a una sessione del giocatore. La mappa audio→sessione è mantenuta dal motore accanto all'attuale `_audio_by_session`.
- **Endpoint pubblici:** `GET /api/config` e `GET /api/leaderboard` restano pubblici.
- **Health:** in modalità online, una richiesta senza cookie valido e non
  proveniente da loopback riceve solo `{status, version}`, senza componenti.
  In locale `/api/health` resta invariato.

**Nickname (modalità score, online)**
- **Conflitto:** al momento di creare una sessione, se esiste una sessione con lo stesso nickname (confronto `casefold`) e `owner_id` diverso, la risposta è **409** con `code: nickname_taken`.
- **Nickname attuale:** `/api/players/me` restituisce l'ultimo nickname usato dal giocatore.

**Nuovo endpoint `GET /api/sessions/{id}/messages`.** Disponibile in entrambe le modalità.
- **Risposta:** la cronologia della sessione `[{role, content, language, modality, created_at}]`.
- **Flag:** i contenuti restano quelli salvati, quindi l'eventuale flag appare come `[REDACTED_SESSION_FLAG]`. La UI lo spiega al giocatore.

### 4.3 Equità per sessione e per giocatore

**Un turno alla volta**
- **Online:** su `…/messages` e `…/voice`, se il lock della sessione è già occupato, la risposta è subito **409** con `code: turn_in_progress`.
- **Locale:** resta l'attesa in coda attuale.

**Rate limiter.** `RateLimiter` è in memoria, a finestra scorrevole, con chiave stringa; vive in un nuovo modulo `ratelimit.py`.

| Chiave | Limite | Endpoint |
| --- | --- | --- |
| `auth:<ip>` | `auth_attempts_per_minute` | `/join`, `/recover` |
| `turn:<player>` | `turns_per_minute` | `…/messages` POST, `…/voice` |
| `session:<player>` | `sessions_per_hour` | `POST /api/sessions`, `…/reset` |

- **Al superamento:** **429** con `code: rate_limited`, `details.retry_after` (secondi interi ≥ 1) e header `Retry-After`.
- **IP:** quello del client calcolato da uvicorn, che dietro un proxy fidato è già quello originale (§4.5).

**Sessioni attive per giocatore.** Creando una sessione mentre il giocatore ne ha già `max_active_sessions` attive, la più vecchia viene chiusa con la stessa semantica di `reset`, ma senza crearne una sostitutiva:
- **stand:** eliminata;
- **score:** stato `reset`, cronologia cancellata;
- **in entrambi i casi:** audio rimosso.

### 4.4 Cancello globale sull'LLM

`GatedLLMProvider(inner, max_concurrent, max_queue)` in `providers/llm.py`, che implementa il protocollo `LLMProvider`.

- **Funzionamento:** un `asyncio.Semaphore(max_concurrent)` e un contatore delle richieste in attesa.
- **Coda piena:** se all'ingresso le richieste in attesa sono già `max_queue`, viene sollevato `CapacityError` (nuovo, 503, `code: llm_busy`, `details.retry_after`).
  - Valore di `retry_after`: stima = `ceil(durata media mobile di generate × (in_attesa / max_concurrent))`, limitata tra 2 e 60 s.
- **Ordine:** FIFO, quello naturale dei waiter del semaforo.
- **Altre chiamate:** `health()` viene delegato senza passare dal cancello.
- **Posizione:** applicato in `_build_llm` attorno al provider reale (anche mock e fallback), quindi attivo anche in modalità locale, dove con un solo utente non scatta mai.
- **Timer:** l'attesa nel cancello avviene dentro `_generate_model_response`, già incluso nella misura `processing_seconds` sottratta al timer di gioco. Il tempo in coda non viene quindi addebitato al giocatore.

**Nuove classi d'errore** in `errors.py`:

| Classe | Stato | `code` |
| --- | --- | --- |
| `UnauthorizedError` | 401 | `unauthorized` |
| `ConflictError` | 409 | `turn_in_progress`, `nickname_taken` (passato per istanza) |
| `RateLimitedError` | 429 | `rate_limited` |
| `CapacityError` | 503 | `llm_busy` |

Il gestore `janus_error_handler` aggiunge l'header `Retry-After` quando `details.retry_after` è presente.

### 4.5 Rete, proxy e deployment

**Regole dentro JANUS (online)**
- **Proxy:** uvicorn avviato con `proxy_headers=True` e `forwarded_allow_ips=<trusted_proxies>`; senza proxy fidati gli header vengono ignorati.
- **HTTPS obbligatorio:** una richiesta il cui schema effettivo non è `https` riceve 400 `code: https_required`. Unica eccezione le richieste da client loopback, cioè l'healthcheck del container.
- **HSTS:** `Strict-Transport-Security: max-age=31536000` sulle risposte HTTPS.
- **Binding:** il bind resta `0.0.0.0` nel container. Con la variante Caddy la porta 8000 non viene pubblicata sull'host.

**Variante A: `docker-compose.public.yml` con Caddy**
- **Servizio `caddy`:** immagine `caddy:2`, pubblica `80` e `443` (anche `443/udp`), volumi `caddy-data` e `caddy-config`.
- **Caddyfile** (`docker/Caddyfile`):
  ```
  {$JANUS_PUBLIC_HOST} {
      {$JANUS_TLS_DIRECTIVE}
      reverse_proxy janus-upstream:8000
  }
  ```
  `JANUS_TLS=internal` diventa `tls internal` (CA locale di Caddy, per test e reti chiuse). Il default è Let's Encrypt automatico.
- **Alias di rete:** l'alias `janus-upstream` va sul servizio che possiede la
  porta di JANUS. Va definito nei file base e HF, non nell'override public:
  Compose non permette di combinare `networks` con `network_mode: service:ollama`.
  - `docker-compose.yml`: alias sul servizio `ollama`, perché JANUS ne condivide il namespace;
  - `docker-compose.hf.yml`: alias sul servizio `janus`, che lì ha una rete propria.

  L'override public si limita a puntare Caddy a `janus-upstream:8000`.
- **Porte:** l'override toglie la pubblicazione della porta 8000 (`ports: !reset []`) dal servizio che la espone.
- **Ambiente di JANUS:** l'override imposta `JANUS_ONLINE=1`, `JANUS_PUBLIC_HOST`, `JANUS_TRUSTED_PROXIES` (la subnet della rete compose, fissata nell'override) e `JANUS_ACCESS_CODES` (obbligatoria).
- **Combinabilità:** l'override va con `docker-compose.hf.yml` e `docker-compose.gpu.yml`.

**Variante B: proxy o CDN esterno**
- JANUS viene pubblicato su un indirizzo interno (`JANUS_BIND_ADDRESS`), con `JANUS_ONLINE=1`, `JANUS_PUBLIC_HOST` e `JANUS_TRUSTED_PROXIES=<ip del proxy>`.
- **Requisiti documentati per il proxy:**
  - inoltrare `Host`, `X-Forwarded-For` e `X-Forwarded-Proto`;
  - limite sul body ≥ 20 MB;
  - timeout di lettura ≥ 120 s.
- La documentazione include un esempio nginx.

**Capacità per backend** (documentata)
- **HF:** `max_concurrent` 8–16.
- **Ollama:** `OLLAMA_NUM_PARALLEL` = `max_concurrent`, con GPU dedicata. CPU-only sconsigliato oltre 2–3 giocatori.

### 4.6 Ciclo di vita: spazzino periodico

`SessionSweeper` è un task asyncio avviato nel `lifespan` dell'app FastAPI. Gira ogni 60 s, in entrambe le modalità, e ogni passaggio fa queste cose:

1. **Sessioni scadute:** per ogni sessione `active` oltre il limite (livello o TTL globale, stessa formula di `get_session`) imposta `expired`, cancella la cronologia e rimuove l'audio. Il passaggio avviene sotto il lock della sessione.
2. **Audio orfani:** rimuove i file nella cartella audio più vecchi di 30 minuti e non referenziati da una sessione attiva.
3. **Lock:** rimuove da `_session_locks` le voci di sessioni non attive o inesistenti con lock libero.
4. **Rate limiter:** rimuove i contatori inattivi.
5. **Giocatori (online):** elimina le righe `players` con `created_at + player_ttl_hours` passato.

Lo spazzino non tocca le sessioni score vinte, che restano per la classifica.

### 4.7 Frontend

`/api/config` espone `app.online: bool`. Con `false`, nessun cambiamento visibile. Con `true`:

- **Schermata "Accesso"** (`screen-access`, prima di `attract`), mostrata se `GET /api/players/me` risponde 401:
  - **scheda "Codice evento":** al successo mostra il codice di recupero con pulsante "Copia" e l'avviso; la conferma "L'ho salvato" è obbligatoria per proseguire;
  - **scheda "Codice di recupero":** chiama `POST /api/recover`;
  - **429:** countdown leggibile.
- **Ripresa:** se `/api/players/me` restituisce una sessione attiva, l'attract mostra il banner "Partita in corso (livello, tempo residuo) — Riprendi / Abbandona".
  - **"Riprendi":** carica la cronologia (`GET …/messages`) e apre `game`, con una nota sul segreto oscurato se presente.
  - **"Abbandona":** chiama `DELETE` sulla sessione.
- **Setup:** in modalità score il nickname già usato dal giocatore è precompilato e non modificabile.
- **Errori:**

  | Stato | Comportamento |
  | --- | --- |
  | 401 | Torna alla schermata Accesso |
  | 404 su sessione | "Sessione non trovata", ritorno ad attract |
  | 409 `turn_in_progress` | Invio disabilitato fino alla risposta |
  | 409 `nickname_taken` | Errore sotto il campo nickname |
  | 429 | Countdown, invio disabilitato |
  | 503 `llm_busy` | Countdown; il testo resta nella casella; **nessun reinvio automatico** |
- **Kiosk:** in modalità online i comportamenti specifici del kiosk sono disattivati.
- **Invariati:** asset locali, CSP, cookie `HttpOnly` (mai letto da JavaScript). I nuovi testi seguono lo stile esistente.

### 4.8 Privacy e dati

| Dato | Dove | Durata |
| --- | --- | --- |
| Cronologia chat | SQLite | Fino alla fine della sessione (come oggi) |
| Sessioni stand concluse | SQLite | Eliminate (come oggi) |
| Sessioni score vinte | SQLite, con `owner_id` | Permanenti (classifica) |
| Giocatore e hash del codice di recupero | SQLite | `player_ttl_hours` |
| IP dei client | Solo in memoria (rate limit) | Finestra del limite |

- **Log di accesso:** quelli di uvicorn e del proxy contengono IP e percorsi con ID di sessione. La documentazione spiega come ridurli o disattivarli.
- **Join:** la schermata Accesso informa che i messaggi sono elaborati dall'LLM configurato, e da un servizio esterno se è attivo HF.

## 5. Test e verifica

### 5.1 Unitari e API (CI, LLM mock, nessuna rete)

- **Accesso:**
  - join con codice corretto o errato;
  - attributi del cookie;
  - cookie scaduto, manomesso o firmato con un'altra chiave → 401;
  - 429 su `/join` e `/recover`.
- **Recupero:** stesso `player_id`, sessioni e nickname; codice normalizzato; nel DB solo l'hash; giocatore scaduto → 401.
- **Proprietà:**
  - per ogni endpoint di sessione e per l'audio, il giocatore B sulle risorse di A riceve 404;
  - le risorse di A restano invariate;
  - senza cookie: 401.
- **Equità:**
  - 409 `turn_in_progress`;
  - 429 per giocatore;
  - limite di sessioni attive (la precedente passa a `reset`);
  - 503 `llm_busy` con `Retry-After` quando la coda è piena;
  - il tempo in coda non viene addebitato.
- **Nickname:** 409 `nickname_taken`; nessuna fusione in classifica.
- **Spazzino:** scadenza delle sessioni abbandonate; rimozione di audio orfani, lock, contatori e giocatori scaduti; sessioni vinte preservate.
- **Rete:** `public_host` obbligatorio; `https_required` (eccezione loopback); HSTS; header proxy ignorati da sorgenti non fidate.
- **Configurazione:** validazione di `online` e codici, override da CLI.
- **Regressione:** la suite esistente passa invariata con `online.enabled: false`.

### 5.2 Isolamento in concorrenza (CI)

- 50 giocatori simulati in parallelo (`httpx.AsyncClient` + ASGI) con un LLM mock che registra ogni chiamata, alcuni turni ciascuno.
- **Asserzioni:**
  - ogni chiamata LLM contiene solo la storia e la flag della propria sessione;
  - nessuna flag compare in prompt altrui;
  - tutte le flag sono distinte.

### 5.3 Dal vivo (prima del merge)

- **Stack:** `docker-compose.public.yml` con `JANUS_TLS=internal`, in modalità HF e in modalità Ollama.
- **Carico:** script esteso (join, cookie, HTTPS) con 50 giocatori simultanei su HF: latenze, conteggio 503/429, contaminazioni.
- **Furto di sessione:** un secondo client con ID altrui deve ricevere 404.
- **Playwright:**
  - viewport mobile: join → codice di recupero → chiusura e riapertura → ripresa;
  - recupero da un contesto browser pulito;
  - push-to-talk in HTTPS con dispositivo audio simulato.

## 6. Documentazione da aggiornare

- `docs/SECURITY.md`: perimetro Internet, cookie, codici, limiti, log.
- `docs/DOCKER.md`: `docker-compose.public.yml`, variante proxy esterno, esempio nginx, capacità.
- `docs/ARCHITECTURE.md`: nuovi endpoint, tabelle, cancello, spazzino; aggiornamento di "Limiti noti".
- `docs/EVENT_RUNBOOK.md`: checklist evento online (dominio/DNS, codici, limiti, monitoraggio, rotazione codici).
- `README.md` e `.env.example`: nuove variabili.

## 7. Fuori scope

- Repliche multiple di JANUS (lock e limiti in memoria, SQLite).
- Tetto di spesa giornaliero sull'inferenza HF (resta sul lato fatturazione).
- Account individuali o integrazione con piattaforme CTF esterne.
- Pannello operatore web.
- Pausa del timer durante l'assenza del giocatore (scelta voluta, per non renderla sfruttabile).

## 8. Rischi

| Rischio | Mitigazione |
| --- | --- |
| Codice evento diffuso pubblicamente | Più codici, rotazione via env + riavvio; rate limit per giocatore e cancello LLM limitano comunque i danni |
| Saturazione dell'LLM con Ollama su hardware modesto | 503 espliciti; documentazione di capacità; HF consigliato per l'online |
| Codice di recupero perso | Il cookie copre lo stesso dispositivo; senza codice si rientra come nuovo giocatore |
| Chiave master ruotata durante l'evento | Invalida cookie e flag: il runbook vieta la rotazione a evento in corso (già vale per le flag) |
