# Sicurezza

## Principio

Le vulnerabilità di JANUS appartengono al gioco: prompt, output filter e
diagnostics.collect possono essere aggirati intenzionalmente. Il portatile, i
dati e i processi locali non devono dipendere dai guardrail del modello.

Il partecipante è autorizzato a manipolare JANUS tramite chat o voce. Non è
autorizzato a uscire dal kiosk, usare porte o scorciatoie di sistema, interrogare
direttamente le API o attaccare servizi e file del laptop.

## Asset

| Asset | Proprietà |
| --- | --- |
| Sistema operativo | Nessun accesso pubblico |
| JANUS_SECRET_KEY o janus.key | Confidenzialità e continuità durante Score |
| Sessione | Separazione dalle altre sessioni |
| Database Score | Integrità e disponibilità |
| Pseudonimi e messaggi | Minimizzazione e cancellazione prevista |
| Modelli e configurazioni | Integrità e provenienza |
| Postazione | Disponibilità durante l'evento |

Le flag sono segreti sintetici e temporanei. Non devono essere sostituite con
credenziali reali.

## Confini

~~~mermaid
flowchart LR
    U[Input non fidato] --> B[Browser kiosk]
    B --> A[API FastAPI]
    A --> E[ChallengeEngine]
    E --> M[LLM non fidato]
    M --> E
    E --> D[(SQLite)]
    E --> F[FlagService]
    F --> K[Chiave backend]
~~~

- Browser e modello non decidono vittoria o score.
- Il modello non riceve la chiave HMAC.
- Output LLM e input utente sono dati non fidati.
- Il tool BIFRONS non è un confine di sicurezza reale.
- In modalità locale (default) bind loopback e presidio fisico sono parte del
  deployment; in modalità online le sostituiscono cookie, proprietà delle
  risorse e limiti (vedere [Modalità online](#modalità-online-internet)).

## Controlli implementati

### Rete

- La CLI accetta soltanto 127.0.0.1 o localhost; 0.0.0.0 è ammesso solo in un
  container o dietro un proxy (modalità online, vedere sotto).
- Start-Janus forza 127.0.0.1.
- Gli URL LLM devono avere host 127.0.0.1, localhost o ::1, salvo il provider
  opzionale `huggingface`, vincolato a `https://router.huggingface.co`.
- TrustedHostMiddleware usa l'allowlist YAML.
- CORS usa origini localhost e non abilita credenziali.
- I provider non accettano un URL remoto passato per singola richiesta.

Ollama o il server OpenAI-compatible devono essere configurati a loro volta per
non ascoltare sulla LAN.

### Inferenza remota Hugging Face (opzionale)

Con `llm.provider: huggingface` il perimetro cambia:

- system prompt, flag di sessione e messaggi dei partecipanti vengono inviati
  a Hugging Face e all'operatore di inferenza (es. `:nscale`); il segreto di
  gioco lascia la macchina, la chiave HMAC no;
- la postazione necessita di uscita Internet verso `router.huggingface.co` e
  `huggingface.co`: l'assunzione "offline" non vale più;
- senza suffisso di operatore il router può cambiare operatore nel tempo;
  fissarlo quando contano accordi sul trattamento dei dati;
- `HF_TOKEN` va trattato come segreto: solo variabile d'ambiente, mai YAML o
  repository, permessi minimi (`inference.serverless.write`);
- con `HF_BILL_TO` l'health check verifica che il token possa addebitare
  l'organizzazione, perché in caso contrario il router addebita l'account
  personale senza segnalarlo;
- informare i partecipanti che i messaggi sono elaborati da un servizio
  esterno.

### Schemi e limiti

- I modelli API vietano campi extra.
- Il testo è limitato a 4000 caratteri dalla configurazione.
- La submission è limitata a 256 caratteri.
- La leaderboard limita la query a 1–100 risultati.
- L'audio è limitato a 20 MB prima e dopo la lettura.
- I formati temporanei sono ridotti a WAV, WebM, Ogg, MP3, M4A/MP4 o bin.
- Nickname: 2–24 caratteri, alfanumerici, spazio, underscore, punto e trattino.

In modalità locale non sono implementati rate limiting server-side o un limite
globale di concorrenza (in modalità online sì, vedere sotto). La UI disabilita i controlli durante un turno, ma questo non
protegge da richieste costruite direttamente. Per l'evento l'API deve restare
irraggiungibile dalla rete e il kiosk deve essere presidiato.

### Flag

- Chiave di almeno 32 byte da ambiente o file locale.
- HMAC-SHA256 su session UUID e level ID.
- Token Base32 e namespace di livello.
- Confronto su digest tramite compare_digest.
- Nessuna flag in chiaro in SQLite.
- Messaggi salvati redigono la flag letterale.
- Submission separata dalla chat.

Se si perde o cambia la chiave, una sessione precedente non può più essere
verificata. Conservare chiave e database insieme per la durata di Arena.

### Tool simulato

diagnostics.collect:

- è l'unico nome accettato;
- usa JSON validato con campi extra vietati;
- non accede a shell, rete, ambiente, database o filesystem;
- riceve secret soltanto come argomento in memoria;
- restituisce trusted_context soltanto se entrambi i booleani sono true.

La fiducia nei booleani del modello è il bug didattico. Non estendere il runtime
con dispatcher dinamici o eval.

### Audio

- L'audio in ingresso usa NamedTemporaryFile in una directory dedicata.
- Il file viene eliminato in finally dopo STT.
- I WAV TTS hanno ID esadecimali opachi.
- resolve rifiuta ID non validi e traversal.
- I WAV di sessione vengono eliminati a vittoria, reset, abbandono o scadenza.
- Il provider elimina WAV residui al proprio avvio.
- Piper usa modelli configurati dall'operatore, non path dell'utente.

### Frontend

La chat renderizza contenuto tramite createTextNode e textContent. La
evidenziazione delle flag crea elementi code e assegna ancora textContent.
Gli usi di innerHTML contengono stringhe statiche dell'applicazione, non input o
output generato.

Il client non usa CDN, analytics, cookie o localStorage. Web Speech è un fallback
locale del browser.

Non è presente una Content Security Policy esplicita. Il kiosk non deve navigare
verso pagine esterne.

### Persistenza

SQLite conserva i messaggi durante la sessione attiva. Alla vittoria li elimina;
all'abbandono elimina la sessione con cascade. A ogni avvio il repository rimuove
tutte le sessioni tranne le Score vinte.

Le sessioni Score vinte conservano:

- pseudonimo;
- livello;
- score;
- turni e hint;
- timestamp e stato;
- processing_seconds.

Non conservano la conversazione dopo la vittoria. Stand non lascia risultati
dopo la chiusura normale o il riavvio.

## Modalità online (Internet)

Con `online.enabled: true` (`--online` o `JANUS_ONLINE=1`) JANUS può essere
esposto su Internet per 10–50 giocatori contemporanei. Il perimetro cambia:
i client non sono più presidiati, il TLS è terminato da un proxy (Caddy nello
stack, o un proxy esterno) e l'applicazione impone da sola accesso, proprietà
delle risorse e limiti. Deployment in [DOCKER.md](DOCKER.md#modalità-online-su-internet).
Con la modalità disattivata il comportamento resta quello del kiosk locale.

### Accesso e identità

- **Codici evento:** letti solo dall'ambiente (`JANUS_ACCESS_CODES`, separati
  da virgola, almeno 8 caratteri ciascuno), mai da YAML o repository. Il
  confronto è a tempo costante contro ogni codice configurato. Si ruotano
  aggiornando `.env` e rieseguendo `docker compose up -d` con lo stesso elenco di
  file `-f` del deployment (altrimenti `janus` viene ricreato senza modalità
  online; vedere [DOCKER.md](DOCKER.md#modalità-online-su-internet)).
- **Cookie `janus_player`:** `v1.<player_id>.<scadenza>.<firma>`, con firma
  HMAC-SHA256 derivata dalla chiave master con separazione di dominio rispetto
  alle flag. Attributi: `HttpOnly; Secure; SameSite=Strict; Path=/`. La verifica
  è senza stato (firma e scadenza) e il cookie non è mai letto da JavaScript.
  La scadenza è assoluta: `player_ttl_hours` (default 12) dalla creazione del
  giocatore.
- **Codice di recupero:** `RCV-XXXX-XXXX-XXXX-XXXX`, 80 bit casuali, mostrato
  una sola volta alla creazione. Nel database resta solo l'hash SHA-256.
  Permette di riprendere su un altro dispositivo con lo stesso giocatore e non
  prolunga la scadenza. Se perso, il giocatore rientra con un nuovo accesso.
- Un 401 da `/api/join` e `/api/recover` non distingue la causa (codice errato,
  giocatore scaduto).
- **Logout senza stato:** `POST /api/logout` cancella il cookie su quel
  dispositivo, ma un cookie rubato resta valido fino alla scadenza
  (`player_ttl_hours`).
- **Limite noto:** chiunque conosca il codice evento può creare nuove identità
  di giocatore. Il danno è limitato dal cancello globale sull'LLM e dalla
  rotazione del codice.

### Proprietà delle risorse

Ogni sessione ha un `owner_id`. Tutti gli endpoint di sessione e
`GET /api/audio/{id}` verificano il proprietario in un'unica funzione del
motore. Senza cookie la risposta è 401; con la risorsa di un altro giocatore la
risposta è 404, identica a quella di una risorsa inesistente, senza effetti.
`GET /api/config` e `GET /api/leaderboard` restano pubblici. In modalità score
un nickname già usato da un altro giocatore (confronto senza maiuscole) dà 409
`nickname_taken`: la classifica non fonde giocatori distinti.

Un giocatore ha al massimo una sessione attiva: crearne una nuova chiude la
precedente. La creazione è serializzata per giocatore e le letture durante un
turno in corso non fanno scadere la sessione.

### Limiti e cancello LLM

| Limite | Chiave | Default | Endpoint |
| --- | --- | --- | --- |
| `auth_attempts_per_minute` | IP | 10 | `/api/join`, `/api/recover` |
| `turns_per_minute` | giocatore | 12 | messaggi e voce |
| `sessions_per_hour` | giocatore | 20 | creazione e reset di sessioni |
| `max_active_sessions` | giocatore | 1 | sessioni attive contemporanee |

- Il limite sugli accessi conta **solo i tentativi falliti** per IP. Superato
  il limite, tutti i tentativi da quell'IP (anche corretti) sono bloccati fino
  alla fine della finestra di 60 s: i giocatori dietro uno stesso NAT possono
  entrare insieme finché non sbagliano.
- Al superamento: 429 `rate_limited` con `details.retry_after` e header
  `Retry-After`.
- Un solo turno alla volta per sessione: un secondo turno mentre il primo è in
  corso riceve 409 `turn_in_progress`.
- **Cancello LLM:** `llm.max_concurrent` (default 8) chiamate simultanee e
  `llm.max_queue` (default 16) in attesa, in ordine FIFO. A coda piena: 503
  `llm_busy` con `Retry-After`. Un turno rifiutato con `llm_busy` viene annullato
  (rollback): non costa un turno e non lascia messaggi, quindi riprovare è
  gratuito. Il tempo in coda non è addebitato al giocatore.
- I contatori e i lock sono in memoria: con più repliche i limiti non sono
  condivisi. JANUS online supporta una sola istanza.

### HTTPS, HSTS e proxy

- Una richiesta con schema effettivo non `https` riceve 400 `https_required`;
  le risposte HTTPS portano `Strict-Transport-Security: max-age=31536000`.
- L'eccezione vale solo per un client locale **diretto**: loopback senza
  `Forwarded`, `X-Forwarded-For` o `X-Forwarded-Proto`, che è il modo in cui
  l'healthcheck del container raggiunge JANUS.
- Gli header `X-Forwarded-*` sono accettati solo dagli indirizzi in
  `--trusted-proxies` / `JANUS_TRUSTED_PROXIES`. Con `docker-compose.public.yml`
  è solo l'indirizzo fisso di Caddy (`JANUS_CADDY_IP`, default `172.30.57.10`,
  dentro `JANUS_SUBNET`), non l'intera rete: altri container non possono
  falsificare IP e schema.
- Un proxy sullo stesso host **deve** essere elencato in
  `JANUS_TRUSTED_PROXIES`, altrimenti ogni client appare locale ai limiti
  (esenzione da HTTPS e da health completo inclusa). JANUS registra un avviso
  all'avvio se la modalità online parte senza proxy fidati.
- `allowed_hosts` include `public_host`; CORS accetta solo
  `https://<public_host>`.

### Health ridotto

In modalità online `/api/health` senza cookie valido e da un client non locale
diretto restituisce solo `{status, version}`, senza i componenti (provider,
modelli, percorsi), e non avvia controlli sui provider: riporta l'ultimo stato
noto, oppure `unknown` se nessun controllo è ancora stato eseguito. I controlli
completi (giocatori, healthcheck locale, modalità locale) sono condivisi in una
cache di 15 secondi, così molti client che interrogano `/api/health` non
moltiplicano le chiamate ai provider (con Hugging Face, chiamate esterne con il
token dell'operatore).

### Log di accesso e privacy

- Il log di accesso di uvicorn registra IP e percorsi, che contengono gli ID di
  sessione. È attivo di default: si disattiva con `python -m janus
  --no-access-log` oppure, con Docker, con `JANUS_ACCESS_LOG=0` in `.env`. Se
  resta attivo, limitare la retention dei log del container (driver di logging
  Docker con `max-size`/`max-file`). Gli errori applicativi restano comunque nel
  log.
- Caddy non scrive log di accesso finché nel `docker/Caddyfile` non si aggiunge
  una direttiva `log`: non aggiungerla, oppure limitarne i campi, se non serve.
- I dati dei giocatori (riga `players` con hash del recupero) vivono
  `player_ttl_hours`.
- Gli IP dei client restano solo in memoria, per la finestra del limite.
- Informare i giocatori che i messaggi sono elaborati dall'LLM configurato e,
  con Hugging Face, da un servizio esterno.

### Chiave master

Non ruotare `JANUS_SECRET_KEY` (né eliminare `janus.key`) a evento in corso: la
chiave firma sia le flag sia i cookie giocatore. Cambiarla invalida tutti i
cookie e le flag delle sessioni attive.

## Provider mock

MockLLMProvider è intenzionalmente capace di simulare solve path e non rappresenta
un modello di sicurezza.

- JANUS_DEMO.cmd lo seleziona esplicitamente.
- configs/app.yaml imposta fallback_to_mock false.
- STAND e ARENA verificano la disponibilità del provider reale.
- /api/health segnala degraded se un fallback mock è attivo.

Non abilitare fallback_to_mock nella release evento. Un LLM guasto deve produrre
un arresto/preflight fallito, non una classifica ottenuta con il simulatore.

## Supply chain

Il repository non contiene modelli LLM, Whisper o voci Piper. Durante la
preparazione:

1. scaricare soltanto da fonti approvate;
2. registrare nome, versione/tag, quantizzazione, dimensione e hash;
3. verificare licenza e attribution di ogni artefatto;
4. completare i test con rete disconnessa;
5. conservare una copia nota buona fuori dal laptop pubblico.

Prepare-Ollama, Prepare-Speech e Prepare-Piper richiedono rete. Non eseguirli
durante una sessione pubblica.

## Hardening della postazione

Questi controlli sono operativi e non sono applicati dal codice JANUS:

- account Windows dedicato senza privilegi amministrativi;
- Edge kiosk e postazione sempre presidiata;
- firewall del profilo evento;
- Wi-Fi e reti non necessarie disabilitati;
- porte USB non accessibili al pubblico;
- notifiche, aggiornamenti e sincronizzazione cloud sospesi;
- modelli fuori da OneDrive;
- alimentazione e raffreddamento stabili;
- nessuna password digitata davanti al pubblico.

Start-Janus non implementa PIN admin né blocca tutte le scorciatoie OS. La
chiusura del browser termina anche il backend e deve essere riservata
all'operatore.

## Superficie API locale

JANUS non serve `/api/docs` né `/api/redoc` (interfacce Swagger/ReDoc
disattivate); lo schema OpenAPI resta pubblico su `/api/openapi.json` e descrive
le rotte senza esporre dati. Non esiste autenticazione per gli endpoint
pubblici. Questo è accettabile soltanto nel deployment locale presidiato. In
modalità locale (default) non pubblicare la porta 8000, non usare 0.0.0.0 e non
configurare port forwarding. In modalità online gli endpoint di gioco sono
protetti come descritto in [Modalità online](#modalità-online-internet);
`/api/openapi.json`, `/api/config`, `/api/leaderboard` e `/api/health` (ridotto)
restano accessibili senza cookie.

Il database non va aperto o copiato mentre JANUS è in esecuzione. Effettuare
backup dopo aver chiuso il launcher.

## Privacy tecnica

- Input audio cancellato dopo la trascrizione.
- WAV TTS effimeri.
- Messaggi conservati soltanto durante sessioni attive.
- Nessun nickname in Stand.
- Soli risultati minimi in Score.
- Nessuna telemetria remota.

La nota al pubblico deve spiegare che in Score viene conservato uno pseudonimo
con risultato e timestamp e che è possibile giocare via testo. Retention ed
eventuali obblighi legali restano responsabilità dell'organizzatore.

## Minacce e risposta

| Evento | Risposta |
| --- | --- |
| Provider LLM non disponibile | Non aprire STAND/ARENA; ripristinare Ollama o server locale |
| Health degraded per mock | Arrestare la modalità evento |
| Flag di altra sessione accettata | Stop immediato, preservare versione e log |
| Dati di una chat precedente visibili | Stop, chiudere launcher e analizzare database |
| HTML o script eseguito dalla chat | Stop, non riaprire senza correzione |
| Accesso a desktop o API da parte del pubblico | Interrompere sessione e ripristinare kiosk |
| Database corrotto | Passare a Stand o ripristinare backup a processi chiusi |
| Chiave esposta | Chiudere Arena, ruotare chiave e azzerare dati associati |
| OOM o throttling | Applicare il profilo ridotto, poi ripetere preflight e smoke test |

Una violazione di confine non è un solve della challenge.

## Checklist release

- [ ] Ollama/server LLM ascolta soltanto in locale.
- [ ] fallback_to_mock è false.
- [ ] JANUS_SECRET_KEY o janus.key è protetta.
- [ ] DataDir è fuori da OneDrive.
- [ ] Hash di modelli e voci registrati.
- [ ] IANUA, SPECULUM e BIFRONS provati IT/EN.
- [ ] Test cross-session e reset superati.
- [ ] Audio temporaneo verificato.
- [ ] Nickname malevoli renderizzati come testo.
- [ ] Account kiosk senza privilegi.
- [ ] Firewall e prova offline completati.
- [ ] Backup Score provato a processi chiusi.
- [ ] Postazione fisicamente presidiata.
