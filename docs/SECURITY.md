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
- Bind loopback e presidio fisico sono parte del deployment.

## Controlli implementati

### Rete

- La CLI accetta soltanto 127.0.0.1 o localhost.
- Start-Janus forza 127.0.0.1.
- Gli URL LLM devono avere host 127.0.0.1, localhost o ::1.
- TrustedHostMiddleware usa l'allowlist YAML.
- CORS usa origini localhost e non abilita credenziali.
- I provider non accettano un URL remoto passato per singola richiesta.

Ollama o il server OpenAI-compatible devono essere configurati a loro volta per
non ascoltare sulla LAN.

### Schemi e limiti

- I modelli API vietano campi extra.
- Il testo è limitato a 4000 caratteri dalla configurazione.
- La submission è limitata a 256 caratteri.
- La leaderboard limita la query a 1–100 risultati.
- L'audio è limitato a 20 MB prima e dopo la lettura.
- I formati temporanei sono ridotti a WAV, WebM, Ogg, MP3, M4A/MP4 o bin.
- Nickname: 2–24 caratteri, alfanumerici, spazio, underscore, punto e trattino.

Non sono implementati rate limiting server-side o un limite globale di
concorrenza. La UI disabilita i controlli durante un turno, ma questo non
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

/api/docs espone OpenAPI sulla macchina. Non esiste autenticazione per gli
endpoint pubblici. Questo è accettabile soltanto nel deployment locale
presidiato. Non pubblicare la porta 8000, non usare 0.0.0.0 e non configurare
port forwarding.

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
