# Runbook evento

## Scheda release da compilare

Compilare questa sezione sulla copia usata allo stand; è l'unico punto della
documentazione lasciato intenzionalmente aperto.

| Campo | Valore operativo |
| --- | --- |
| Data e fascia oraria | ______________________________ |
| Release/commit JANUS | ______________________________ |
| Launcher previsto | STAND / ARENA |
| Provider LLM | Ollama / OpenAI-compatible |
| Modello, tag o file | ______________________________ |
| Quantizzazione verificata | ______________________________ |
| Hash o digest modello | ______________________________ |
| Profilo hardware | ______________________________ |
| DataDir | ______________________________ |
| Operatore principale | ______________________________ |
| Operatore di riserva | ______________________________ |
| Ultima prova offline | ______________________________ |
| Percorso backup Arena | ______________________________ |

## Ruoli

| Ruolo | Compito |
| --- | --- |
| Host | Regole, coda, tempi e passaggio tra partecipanti |
| Operatore | Launcher, chiusura kiosk, modalità e smoke test |
| Tecnico | Ollama, speech, GPU, log e recovery |

La tastiera rimane visibile all'operatore. JANUS non dispone di pannello admin o
PIN applicativo.

## Preparazione con rete

Eseguire dalla root del progetto:

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-Janus.ps1 -WithDevelopmentTools
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Prepare-Speech.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Prepare-Piper.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Prepare-Ollama.ps1
~~~

Risultato predefinito:

- Ollama qwen3:4b-instruct;
- faster-whisper small in %LOCALAPPDATA%\JANUS\models;
- Piper it_IT-paola-medium;
- Piper en_US-lessac-medium;
- ambiente Python .venv.

Modelli e voci non sono inclusi nel repository. Prepararli mentre la rete è
disponibile, verificare licenze e registrare tag/hash nella scheda release.

Se si usa un server OpenAI-compatible, acquisizione del GGUF e avvio del server
sono procedure separate non automatizzate dal progetto.

## Prova generale

Almeno un giorno prima:

1. riavviare il portatile;
2. disconnettere la rete;
3. avviare Ollama o il server locale scelto;
4. aprire JANUS_STAND.cmd;
5. completare un turno testuale e uno vocale in IT e EN;
6. risolvere i tre livelli con i golden attack;
7. verificare redazione SPECULUM e tool BIFRONS;
8. chiudere Edge e confermare l'arresto del backend;
9. aprire JANUS_ARENA.cmd;
10. creare un nickname, vincere e verificare la leaderboard;
11. riavviare Arena e verificare che il risultato persista;
12. eseguire la suite test;
13. eseguire due ripetizioni dei golden attack live IT/EN;
14. sostenere una prova continuativa di almeno due ore;
15. provare il profilo ridotto e la modalità senza speech.

Comando test:

~~~powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe .\scripts\validate_live_model.py --repeat 2
~~~

validate_live_model.py contiene soluzioni e resta uno strumento riservato agli
operatori.

## Layout fisico

- laptop al centro dello stand, una persona alla volta;
- alimentatore fissato e piano ventilato;
- microfono direzionale o headset;
- volume TTS sufficiente, sottotitoli sempre visibili;
- porte USB non accessibili;
- utente Windows dedicato e non amministratore;
- Edge kiosk sempre presidiato;
- cartello IT/EN con regole, durata e nota privacy.

Il pubblico può udire una flag: non è un problema perché ogni sessione usa UUID
e flag diversi.

## Apertura giornaliera

1. Accedere con l'utente evento.
2. Verificare che OneDrive non gestisca DataDir o modelli.
3. Controllare almeno 16 GB RAM libera per a3000_6gb.
4. Controllare spazio NVMe, alimentazione e temperatura.
5. Disconnettere o bloccare la rete esterna prevista dal profilo evento.
6. Verificare che Ollama sia attivo localmente.
7. Avviare il launcher scelto.
8. Attendere il preflight; non ignorare un errore LLM, STT o TTS.
9. Controllare che il footer mostri CORE ONLINE.
10. Eseguire un messaggio testo, un push-to-talk e una submission errata.
11. Abbandonare la sessione e verificare il ritorno pulito ad attract.

## Launcher

### DEMO

~~~powershell
.\JANUS_DEMO.cmd
~~~

Usa mock, Stand e speech disabilitato. Serve soltanto per sviluppo o per mostrare
l'interfaccia; non genera risultati validi dell'evento.

### STAND

~~~powershell
.\JANUS_STAND.cmd
~~~

Usa il provider evento predefinito, modalità anonima e speech locale. Nessun
nickname o punteggio viene conservato.

### ARENA

~~~powershell
.\JANUS_ARENA.cmd
~~~

Usa il provider evento predefinito, richiede nickname e conserva i risultati
Score.

Per forzare Ollama in una release precedente o in un override esplicito:

~~~powershell
.\JANUS_STAND.cmd -Provider ollama
.\JANUS_ARENA.cmd -Provider ollama
~~~

Per l'alternativa OpenAI-compatible:

~~~powershell
.\JANUS_STAND.cmd -Provider openai_compatible -BaseUrl http://127.0.0.1:8080/v1 -Model qwen3-4b-janus
~~~

Il server deve essere già attivo. Non usare URL remoti.

## Preflight

Start-Janus interroga /api/health e richiede:

- LLM disponibile se il provider non è mock;
- STT disponibile se non è disabled;
- TTS disponibile se non è disabled.

Un mock fallback attivo produce health degraded e non è ammesso in evento.
Start-Janus salva:

~~~text
%LOCALAPPDATA%\JANUS\runtime\janus-server.out.log
%LOCALAPPDATA%\JANUS\runtime\janus-server.err.log
~~~

Se DataDir è personalizzato, i log sono in quel percorso.

## Regole da spiegare

> JANUS custodisce una flag fittizia diversa per ogni partita. Puoi scrivere o
> tenere premuto il pulsante per parlare in italiano o inglese. Induci JANUS a
> divulgarla e inviala nel campo RH26{...}. Sono consentiti attacchi al
> personaggio IA; non è consentito uscire dal kiosk, usare porte, terminale,
> filesystem, rete o API.

In Arena aggiungere:

- usare uno pseudonimo, non il nome reale;
- il primo turno non è penalizzato;
- i turni successivi e gli hint riducono il punteggio;
- il tempo di elaborazione non viene addebitato;
- la classifica conserva il miglior risultato per nickname e livello.

## Flusso Stand

1. Il partecipante seleziona livello e lingua Auto/IT/EN.
2. Avvia una sessione anonima.
3. Usa testo o push-to-talk.
4. Richiede hint se necessario.
5. Invia la flag nel dialog dedicato.
6. Dopo il risultato, la UI torna automaticamente ad attract.
7. Verificare che chat e audio non siano più visibili prima del turno seguente.

Il timer è nascosto in Stand, ma il TTL backend resta 20 minuti.

## Flusso Arena

1. Il partecipante inserisce uno pseudonimo valido.
2. Seleziona livello e lingua.
3. Gioca con timer visibile.
4. Alla vittoria vede score, tempo, turni e hint.
5. La leaderboard mostra i migliori risultati del livello.
6. Il risultato vinto viene conservato; la conversazione viene eliminata.

Nickname ammessi: 2–24 caratteri, alfanumerici, spazio, underscore, punto e
trattino.

## Voce

Percorso evento:

~~~text
MediaRecorder -> audio raw -> faster-whisper CPU
-> LLM locale -> Piper CPU -> WAV -> Edge + lipsync
~~~

Le voci Piper sono Paola IT e Lessac EN. Con TtsProvider auto:

- Piper se entrambi i modelli e JSON sono presenti;
- altrimenti SAPI;
- se il backend non produce audio, la UI può usare Web Speech;
- testo e sottotitoli restano sempre disponibili.

Web Speech è un fallback UI, non la voce da validare per l'evento.

Se lo stand è rumoroso:

- usare headset o microfono direzionale;
- tenere premuto PTT per tutta la frase;
- forzare IT o EN se Auto sbaglia;
- passare a input testuale se STT non è affidabile.

## Cambio modalità

1. Terminare la sessione corrente.
2. Chiudere Edge; Start-Janus arresta FastAPI.
3. Attendere la chiusura del processo.
4. Se si lascia Arena, copiare database e chiave soltanto a processi chiusi.
5. Avviare JANUS_STAND.cmd o JANUS_ARENA.cmd.
6. Ripetere smoke test.

Non esiste un cambio modalità dalla UI.

## Recovery

| Sintomo | Azione |
| --- | --- |
| Launcher segnala LLM non pronto | Verificare Ollama e qwen3:4b-instruct; non usare DEMO per mascherare il guasto |
| CORE WARMING persistente | Leggere janus-server.err.log, chiudere kiosk e riavviare |
| OOM GPU | Chiudere carichi estranei, usare profilo ridotto o Ollama 4B |
| LLM lento | Controllare GPU/termica; non passare a 8B |
| STT non pronto | Verificare cartella prepared; altrimenti riavviare con SttProvider disabled |
| Piper non pronto | Rieseguire Prepare-Piper con rete; durante l'evento usare TtsProvider sapi |
| SAPI senza voce IT/EN | Disabilitare TTS backend e usare testo/Web Speech se disponibile |
| Microfono negato | Consentire permesso a Edge o usare testo |
| Audio distorto/eco | Ridurre volume, usare headset e PTT |
| Browser chiuso | Il backend termina; riavviare il launcher |
| Arena non salva | Fermare nuove partite, chiudere launcher, controllare spazio e database |
| Chat precedente visibile | Stop, chiudere launcher e verificare cleanup prima di riaprire |
| Flag cross-session accettata | Stop immediato; non riaprire senza analisi |

Modalità testo:

~~~powershell
.\JANUS_STAND.cmd -SttProvider disabled -TtsProvider disabled
~~~

Il gioco, i livelli e le submission restano disponibili.

## Scala di degradazione hardware

1. Chiudere software e sync non necessari.
2. Confermare STT e Piper su CPU.
3. Restare su qwen3:4b-instruct.
4. Ridurre contesto/offload nel runtime LLM scelto.
5. Usare faster-whisper base.
6. Passare a SAPI.
7. Disabilitare speech.
8. Usare cpu_fallback soltanto dopo smoke test.

Dopo un cambio: riavvio, health, turno IT/EN, submission e golden attack breve.

## Privacy operativa

Mostrare prima del gioco:

- audio usato per la trascrizione e cancellato;
- chat eliminata alla fine della sessione;
- Stand anonimo;
- Arena conserva pseudonimo, risultato e timestamp;
- possibilità di giocare via testo;
- periodo di retention deciso dall'organizzatore.

Non raccogliere email o nome reale.

## Evento online

Per un evento su Internet con più giocatori (modalità online, stack
`docker-compose.public.yml`). Deployment in
[DOCKER.md](DOCKER.md#modalità-online-su-internet), modello di sicurezza in
[SECURITY.md](SECURITY.md#modalità-online-internet).

### Checklist

- [ ] Dominio scelto e record DNS (A/AAAA) verso l'host, propagato.
- [ ] Porte 80 e 443 aperte da Internet (necessarie al certificato automatico
  con `JANUS_TLS=acme`).
- [ ] `JANUS_PUBLIC_HOST` e `JANUS_ACCESS_CODES` impostati in `.env`; codici
  generati con
  `python -c "import secrets; print(secrets.token_urlsafe(9))"`.
- [ ] Backend scelto: Hugging Face o GPU consigliati; Ollama su CPU solo fino a
  2–3 giocatori.
- [ ] Limiti rivisti in `configs/app.yaml` (`online.limits`, `llm.max_concurrent`,
  `llm.max_queue`, `player_ttl_hours` almeno pari alla durata dell'evento).
- [ ] Prova di carico con `scripts/online_load_test.py` e il numero di
  giocatori atteso (`--players N`): nessuna contaminazione, latenze e 503/429
  accettabili.
- [ ] I comandi `docker compose` usano l'elenco `-f` del deployment (o
  `COMPOSE_FILE` in `.env`, vedere DOCKER.md).
- [ ] `docker compose ps` mostra `janus` e `caddy` in esecuzione e `janus`
  `healthy`; `docker compose logs janus caddy` senza errori né avvisi su proxy
  non fidati.
- [ ] Chiave `JANUS_SECRET_KEY` o `janus.key` stabile: non ruotarla a evento in
  corso (invalida cookie e flag).
- [ ] Database pulito: rimuovere i giocatori di test (prove di carico e prova
  generale) prima dell'apertura. Con lo stack fermo (`docker compose ... down`,
  stesso elenco `-f`) eseguire `docker volume rm janus_janus-data`, poi
  riavviare con `up -d`. **Attenzione:** il volume contiene anche la classifica
  e la chiave HMAC `janus.key`: vengono cancellate entrambe (fare prima il backup
  se servono; con `JANUS_SECRET_KEY` in `.env` la chiave resta quella).
- [ ] Backup della classifica (`docker compose cp janus:/data/janus.sqlite3 .`).

### Rotazione del codice evento

Se il codice è diffuso fuori dal pubblico previsto: aggiornare
`JANUS_ACCESS_CODES` in `.env` (anche più codici separati da virgola) e rieseguire
`up -d` con lo stesso elenco di file del deployment, per esempio
`docker compose -f docker-compose.yml -f docker-compose.public.yml up -d`
(aggiungere `-f docker-compose.hf.yml` o `-f docker-compose.gpu.yml` se usati;
`docker-compose.proxy.yml` al posto di `public` con proxy esterno). Senza
l'override `janus` viene ricreato senza modalità online. In alternativa
impostare `COMPOSE_FILE` in `.env` (vedere DOCKER.md). I giocatori già dentro non vengono espulsi: il cookie
resta valido fino a `player_ttl_hours`. Il codice è solo per nuovi ingressi; il
danno è limitato dal cancello LLM e dai limiti per giocatore.

### Cosa dire ai giocatori

- Alla prima entrata compare un **codice di recupero** (`RCV-XXXX-XXXX-XXXX-XXXX`):
  va copiato e conservato, perché è mostrato una sola volta.
- Chiudendo il browser sullo stesso dispositivo si riprende la partita; da un
  altro dispositivo serve il codice di recupero. Il codice non prolunga la
  scadenza e, se perso, si rientra con il codice evento come nuovo giocatore.
- Il nickname resta riservato al giocatore che lo ha usato per primo. Chi perde
  sia il cookie sia il codice di recupero rientra come nuovo giocatore e deve
  scegliere un nickname diverso.
- Alla ripresa la cronologia mostra la flag come `[REDACTED_SESSION_FLAG]`
  (non è mai conservata) e il timer continua a scorrere anche se si è assenti.
- Se compare un messaggio di attesa (503 o 429), basta riprovare dopo il
  conto alla rovescia: un turno rifiutato per LLM occupato non costa un turno.
- I messaggi sono elaborati dall'LLM configurato (e da Hugging Face, un servizio
  esterno, se attivo).

## Chiusura

1. Non accettare nuove sessioni.
2. Terminare o abbandonare quella attiva.
3. Chiudere Edge e attendere l'arresto del backend.
4. Chiudere Ollama/server LLM se previsto.
5. In Arena, copiare janus.sqlite3 e janus.key insieme.
6. Verificare log, spazio e incidenti.
7. Applicare la retention a database e backup.
8. Spegnere la macchina se non rimane presidiata.

Il DataDir predefinito è:

~~~text
%LOCALAPPDATA%\JANUS\runtime
~~~

Non copiare il database mentre JANUS è in esecuzione.

## Registro incidenti

Annotare senza prompt, flag o dati personali:

- orario;
- release e profilo;
- componente;
- sintomo;
- azione eseguita;
- esito;
- follow-up tecnico.

## Checklist rapida

- [ ] Modello e voci presenti localmente.
- [ ] Rete esterna non necessaria.
- [ ] Ollama/server locale pronto.
- [ ] CORE ONLINE.
- [ ] Nessun mock in STAND/ARENA.
- [ ] Testo IT/EN funzionante.
- [ ] PTT, Piper e sottotitoli funzionanti oppure fallback dichiarato.
- [ ] Modalità corretta.
- [ ] DataDir fuori da OneDrive.
- [ ] Spazio, RAM, VRAM e temperatura adeguati.
- [ ] Kiosk presidiato.
- [ ] Reset/abbandono pulito tra partecipanti.
