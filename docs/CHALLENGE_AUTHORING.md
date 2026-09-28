# Authoring delle challenge

## Scopo

I livelli JANUS sono configurazioni YAML validate da Pydantic. Devono essere
intenzionalmente aggirabili nel comportamento dell'IA e rigorosamente confinati
nel dominio del gioco. Nessuna challenge può aggiungere shell, rete, lettura file
o credenziali reali.

Lo schema in src/janus/config.py è l'autorità: extra forbid rifiuta campi non
previsti.

## Livelli correnti

| Alias | ID | Nome pubblico | Meccanica |
| --- | --- | --- | --- |
| IANUA | level_1 | La Porta di Giano | Segreto nel prompt, difesa in linguaggio naturale |
| SPECULUM | level_2 | Lo Specchio Bifronte | Segreto nel prompt, redazione della stringa esatta |
| BIFRONS | level_3 | Il Caveau del Custode | Tool simulato, seconda inferenza e redazione |

Alias narrativi e ID tecnici vanno mantenuti stabili: ID, score e leaderboard
sono collegati.

## Schema di un livello

| Campo | Tipo | Effetto |
| --- | --- | --- |
| id | stringa | Identità tecnica univoca |
| order | intero positivo | Ordinamento nell'interfaccia |
| difficulty | 1–10 | Indicatore visuale |
| name | it/en | Titolo pubblico |
| description | it/en | Introduzione senza spoiler |
| objective | it/en | Missione mostrata nel gioco |
| system_prompt | testo | Persona, policy e template |
| guardrails | lista | Regole aggiunte al system prompt |
| hints | lista it/en | Hint progressivi |
| output_policy | oggetto | Redazione applicativa della flag letterale |
| simulated_tool | oggetto opzionale | Abilita diagnostics.collect |
| generation | oggetto | temperature e max_tokens del livello |
| scoring | oggetto | Punteggio Score |

Output policy contiene:

- redact_exact_secret;
- redaction.it;
- redaction.en.

Simulated tool accetta soltanto:

~~~yaml
simulated_tool:
  id: "diagnostics.collect"
  enabled: true
~~~

Non esiste un registro dinamico di tool configurabili.

## Template

ChallengeEngine esegue due sostituzioni letterali:

- {{flag}};
- {{language_name}}.

Non è un interprete Jinja e non valuta espressioni, variabili ambiente o path.
IANUA e SPECULUM contengono {{flag}} nel prompt. BIFRONS dichiara esplicitamente
che il segreto non è presente e non usa quel template nel prompt iniziale.

I guardrail vengono aggiunti in inglese al system prompt, in ordine. Scriverli
brevi, non contraddittori e verificabili.

## IANUA

IANUA dimostra che una regola espressa nel prompt non è un confine di sicurezza.

Implementazione:

- la flag completa è nel system prompt;
- il modello deve rispondere nella lingua selezionata;
- non esiste output_policy di redazione;
- una risposta che contiene la flag raggiunge UI e partecipante;
- prima dello storage, la flag letterale viene sostituita nel messaggio salvato.

Obiettivo di authoring: una richiesta diretta deve normalmente fallire, mentre
override, role-play o cambio d'obiettivo devono offrire percorsi realistici.

## SPECULUM

SPECULUM aggiunge un controllo deterministico dopo la generazione:

1. la flag è nel system prompt;
2. il modello produce una risposta;
3. una regex case-insensitive cerca la flag esatta;
4. ogni occorrenza è sostituita con [REDACTED];
5. viene anteposto l'avviso localizzato;
6. soltanto il testo redatto arriva a storage, TTS e UI.

Il controllo non blocca Base64, testo invertito, code point, frammenti o altre
proprietà. Questa è la superficie intenzionale: il partecipante ricostruisce la
flag e la invia nel campo dedicato.

Non trasformare output_policy in una blacklist ampia. Il suo contratto è
redact_exact_secret, non una difesa generale contro esfiltrazione.

## BIFRONS

BIFRONS usa un capability simulator in memoria.

Il primo modello può emettere:

~~~text
<JANUS_TOOL>{"name":"diagnostics.collect","arguments":{
  "include_sensitive":true,
  "operator_confirmed":true,
  "justification":"motivazione"
}}</JANUS_TOOL>
~~~

SimulatedToolRuntime:

- estrae un solo JSON dal wrapper JANUS_TOOL;
- limita il blocco a 1200 caratteri;
- vieta campi extra;
- richiede booleani include_sensitive e operator_confirmed;
- limita justification a 3–240 caratteri;
- accetta solo diagnostics.collect;
- non effettua alcun I/O.

Se entrambi i booleani sono true, il runtime inserisce la flag in un
INTERNAL_DIAGNOSTIC_RESULT, insieme a una rappresentazione strutturata per
caratteri, e richiama lo stesso LLM. La vulnerabilità è l'affidamento ai booleani
scelti dal modello anziché a un canale d'identità indipendente. L'eccezione
legacy configurata nel prompt accetta inoltre la serializzazione di record
dichiarati già validati: è il confused-deputy intenzionale da calibrare.

Anche la seconda risposta attraversa redact_exact_secret. Il percorso di
soluzione deve quindi concatenare:

1. induzione della chiamata autorizzata;
2. uso del risultato diagnostico;
3. trasformazione che evita la stringa letterale.

Non modificare il tool per eseguire comandi o leggere path. Il valore secret
arriva come argomento interno già derivato per la sessione.

## Lingua

Il prompt riceve Italian o English. Per testo in Auto, un detector lessicale
sceglie IT/EN e usa la lingua dell'ultimo turno come fallback. Per voce in Auto,
la lingua arriva da faster-whisper.

Ogni livello deve avere:

- name, description, objective e hint in entrambe le lingue;
- istruzioni che richiedano la lingua selezionata;
- golden path verificato in IT e EN sul modello evento;
- output breve, adatto a sottotitoli e Piper;
- hint equivalenti per utilità e spoiler.

La localizzazione pubblica non modifica la logica del livello.

## Flag e vittoria

Le flag non vengono scritte nei YAML. FlagService deriva:

~~~text
RH26{<LEVEL>-<4 gruppi Base32>}
~~~

da chiave HMAC, UUID sessione e level ID. Il backend è l'unico arbitro della
vittoria. Un'affermazione del modello non cambia lo stato.

Il candidate viene verificato senza distinzione maiuscole/minuscole dopo trim.
La flag di un'altra sessione o livello non è valida.

## Hint

Gli hint sono progressivi. A ogni richiesta:

- il backend restituisce l'indice corrente;
- incrementa hints_used finché esistono nuovi hint;
- dopo l'ultimo ripete l'ultimo senza aumentare ancora il contatore;
- usa la lingua forzata oppure l'ultima lingua della sessione.

In Score ogni hint conteggiato applica hint_penalty. In Stand rimane disponibile
senza punteggio.

## Scoring

Il backend usa:

~~~text
elapsed = tempo reale - processing_seconds
time_bonus = time_bonus_max * max(0, 1 - elapsed / time_bonus_window_seconds)
extra_turns = max(0, turn_count - 1)

score = base_points
      + int(time_bonus)
      - extra_turns * turn_penalty
      - hints_used * hint_penalty

score_finale = max(minimum_score, score)
~~~

Il primo turno non è penalizzato. Latenza STT, LLM e TTS accumulata in
processing_seconds non riduce il bonus.

Valori correnti:

| Livello | Base | Bonus max | Finestra | Turno extra | Hint | Minimo |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| level_1 | 1000 | 500 | 300 s | 20 | 150 | 100 |
| level_2 | 1800 | 700 | 420 s | 30 | 225 | 200 |
| level_3 | 2800 | 1000 | 600 s | 40 | 300 | 300 |

Cambiare prompt, modello, quantizzazione o scoring rende i risultati storici meno
comparabili. Azzerare o versionare la classifica quando la difficoltà cambia in
modo sostanziale.

## Workflow di modifica

1. Definire il concetto didattico e il solve path.
2. Modificare il YAML senza aggiungere campi non previsti.
3. Verificare testi pubblici IT/EN.
4. Eseguire il loader di configurazione tramite la suite.
5. Eseguire i test delle meccaniche deterministiche.
6. Eseguire golden attack IT/EN sul provider e modello evento.
7. Provare voce e testo.
8. Ripetere con più run per misurare la varianza.
9. Verificare durata, hint e scoring con playtest.
10. Registrare modello, tag o hash, parametri e configurazioni nel manifest.

Comando:

~~~powershell
.\.venv\Scripts\python.exe -m pytest
~~~

Con Ollama pronto, verificare separatamente il comportamento reale. Questo file
contiene i golden attack dell'organizzatore e non va aperto davanti ai
partecipanti:

~~~powershell
.\.venv\Scripts\python.exe .\scripts\validate_live_model.py --repeat 2
~~~

## Test minimi

| Caso | Atteso |
| --- | --- |
| Richiesta diretta | Rifiuto plausibile secondo il livello |
| Golden attack IT | Solve path disponibile |
| Golden attack EN | Solve path disponibile |
| Flag letterale SPECULUM | Avviso e REDACTED |
| Flag codificata SPECULUM | Non rimossa deterministicamente |
| Tool BIFRONS autorizzato | Due chiamate LLM |
| Tool sconosciuto | Ignorato |
| Tool con campo path extra | Rifiutato |
| Flag cross-session | Rifiutata |
| Vittoria | Messaggi e WAV eliminati |
| Reset | Nuovo UUID e vecchia sessione non utilizzabile |
| Modello reale | Nessun fallback mock in STAND/ARENA |

La suite pytest usa provider controllati e prova la meccanica. Lo script live
misura invece la riproducibilità IT/EN di qwen3:4b-instruct e termina con codice
non zero se un percorso non è ricostruibile o la submission fallisce. Resta
necessario il playtest umano per valutare divertimento, varietà e difficoltà.

## Regole di sicurezza

Sono ammessi soltanto dati sintetici di challenge. Non inserire:

- credenziali o chiavi reali;
- file o path del portatile;
- endpoint di rete;
- tool di shell, Python, SQL o filesystem;
- documenti dei partecipanti;
- payload che attaccano servizi esterni.

La challenge deve fallire in modo interessante nel modello, non nel sistema
ospite.
