# JANUS online multi-giocatore — piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** permettere a 10–50 giocatori su Internet di giocare in parallelo sulla stessa istanza JANUS senza interferenze di contesto, controllo o equità, con recupero della sessione, lasciando invariato il kiosk locale.

**Architecture:** nuova modalità `online` (`configs/app.yaml` → `online.enabled`).
- **Accesso:** un codice evento fa ottenere un cookie giocatore firmato (HMAC derivato dalla chiave esistente) e un codice di recupero.
- **Proprietà:** ogni sessione e ogni audio sono legati al giocatore (`sessions.owner_id`).
- **Equità:** limiti per giocatore e per IP in memoria, e un cancello globale (`GatedLLMProvider`) che risponde 503 quando la coda LLM è piena.
- **Pulizia:** uno spazzino periodico chiude sessioni scadute, audio orfani, lock, contatori e giocatori.
- **Docker:** il TLS è affidato a Caddy (`docker-compose.public.yml`) o a un proxy esterno, con fiducia negli header `X-Forwarded-*` limitata ai proxy configurati.

**Tech Stack:** Python 3.11+, FastAPI/Starlette, uvicorn (`proxy_headers`, `forwarded_allow_ips` con CIDR), SQLite, httpx, pytest; frontend vanilla JS/HTML/CSS; Docker Compose v2, Caddy 2.

**Spec:** `docs/superpowers/specs/2026-10-07-multiplayer-online-design.md`

## Global Constraints

- Il lavoro avviene sul branch `feat/multiplayer` del fork `0xb120/JANUS_AI_CTF`. Nessuna interazione con `Redragon948/JANUS_AI_CTF`.
- Con `online.enabled: false` il comportamento resta identico a oggi: tutti i 52 test esistenti passano senza modifiche.
- Comandi di verifica (usare il venv esistente, non `uv run`, per non generare `uv.lock`):
  - test: `.venv/bin/python -m pytest`;
  - lint: `.venv/bin/ruff check src tests scripts`.
- `ruff` usa `line-length = 100`, target `py311`.
- Il codice segue lo stile esistente: messaggi d'errore in inglese, testi UI in italiano, commenti solo dove spiegano un "perché".
- **Segreti:** codici evento solo dalla variabile `JANUS_ACCESS_CODES` (separati da virgola, ognuno ≥ 8 caratteri), mai da YAML o repository.
- **Cookie:** si chiama `janus_player`, ha formato `v1.<player_id>.<expires_unix>.<sig>` e attributi `HttpOnly; Secure; SameSite=Strict; Path=/`.
- **Codice di recupero:** formato `RCV-XXXX-XXXX-XXXX-XXXX`, alfabeto Crockford `0123456789ABCDEFGHJKMNPQRSTVWXYZ`, 80 bit; nel DB solo `sha256` del codice normalizzato.
- **Default dei limiti:**

  | Limite | Default |
  | --- | --- |
  | Tentativi d'accesso **falliti** per IP | 10/min |
  | Turni per giocatore | 12/min |
  | Sessioni create per giocatore | 20/ora |
  | Sessioni attive per giocatore | 1 |
  | `llm.max_concurrent` | 8 |
  | `llm.max_queue` | 16 |
  | `player_ttl_hours` | 12 |
- **Codici d'errore API:** `unauthorized` (401), `turn_in_progress` / `nickname_taken` (409), `rate_limited` (429), `llm_busy` (503), `https_required` (400). Con `details.retry_after` va aggiunto l'header `Retry-After`.
- Una sessione o un audio di un altro giocatore rispondono **404** con lo stesso corpo di una risorsa inesistente.
- **Precisazione rispetto alla spec §4.3:** il limite per IP su `/join` e `/recover` conta solo i tentativi **falliti** (protezione dal brute force); superata la soglia blocca anche i tentativi corretti fino alla fine della finestra. Così molti giocatori dietro lo stesso NAT possono entrare insieme.

## Review Focus

1. **Frontend e 409 `turn_in_progress`:** un secondo invio mentre JANUS risponde non deve chiudere la partita. Oggi `handleTurnError` tratta ogni 409 come "sessione non più attiva". Test: Task 11, step 6; Task 15, step 7 (Playwright con risposta simulata).
2. **Molti giocatori dietro lo stesso NAT:** devono poter entrare tutti in pochi secondi. Test: Task 7, `test_successful_joins_from_one_ip_are_not_rate_limited`.
3. **Riavvio del server durante l'evento:** le sessioni attive vengono eliminate all'avvio (`SQLiteRepository.initialize`), mentre i cookie restano validi. `/players/me` non deve proporre la ripresa e la vecchia sessione deve rispondere 404 senza errori 500. Test: Task 8, `test_restart_drops_active_sessions_but_keeps_the_player`.
4. **Codice di recupero digitato a mano:** minuscole, spazi, trattini mancanti o senza `RCV-` devono funzionare. Test: Task 3, `test_recovery_code_normalization_accepts_human_input`; Task 7, `test_recover_restores_the_same_player_from_a_new_device`.
5. **Stesso giocatore in due tab:** la sessione nuova chiude quella vecchia; il turno successivo nella tab vecchia deve ricevere un 409 `invalid_session` gestito (fine partita), non un crash. Test: Task 6, `test_new_session_retires_the_previous_one_and_old_tab_gets_invalid_session`.

---

## Mappa dei file

| File | Responsabilità |
| --- | --- |
| `src/janus/config.py` | `OnlineSettings`, `OnlineLimits`, `llm.max_concurrent/max_queue`, host/CORS effettivi |
| `configs/app.yaml` | Valori di default del blocco `online` e del cancello LLM |
| `src/janus/__main__.py` | Flag CLI `--online`, `--public-host`, `--trusted-proxies`; opzioni proxy di uvicorn |
| `src/janus/errors.py` | `UnauthorizedError`, `ConflictError`, `RateLimitedError`, `CapacityError` |
| `src/janus/ratelimit.py` (nuovo) | `RateLimiter` a finestra scorrevole in memoria |
| `src/janus/security.py` | `FlagService.derive_subkey`, `PlayerTokenService`, `AccessCodeVerifier`, funzioni del codice di recupero |
| `src/janus/domain.py` | `SessionRecord.owner_id` (escluso dalla serializzazione) |
| `src/janus/repository.py` | Tabella `players`, colonna `owner_id`, query per proprietario e nickname |
| `src/janus/providers/llm.py` | `GatedLLMProvider` |
| `src/janus/engine.py` | Proprietà, `open_session`, `turn_in_progress`, cronologia, audio per sessione, panoramica giocatore, helper dello spazzino |
| `src/janus/sweeper.py` (nuovo) | `SessionSweeper` |
| `src/janus/api.py` | Endpoint online, dipendenza `current_player`, middleware HTTPS/HSTS, health ridotto, cablaggio cancello e spazzino |
| `src/janus/web/index.html`, `app.js`, `styles.css` | Schermata Accesso, ripresa, gestione dei nuovi errori |
| `docker/entrypoint.sh`, `docker-compose.yml`, `docker-compose.hf.yml` | Variabili online, alias `janus-upstream` |
| `docker-compose.public.yml`, `docker/Caddyfile` (nuovi) | Caddy con TLS automatico o interno |
| `scripts/online_load_test.py` (nuovo) | Prova di carico e isolamento contro un'istanza online |
| `tests/test_ratelimit.py`, `tests/test_repository_online.py`, `tests/test_engine_online.py`, `tests/test_api_online.py`, `tests/test_sweeper.py`, `tests/test_isolation.py` (nuovi) | Test |
| `docs/*.md`, `README.md`, `.env.example` | Documentazione |

---

### Task 1: configurazione online, cancello LLM e flag CLI

**Files:**
- Modify: `src/janus/config.py`
- Modify: `configs/app.yaml`
- Modify: `src/janus/__main__.py`
- Modify: `pyproject.toml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `OnlineLimits(auth_attempts_per_minute=10, turns_per_minute=12, sessions_per_hour=20, max_active_sessions=1)`;
  - `OnlineSettings(enabled: bool, public_host: str | None, access_codes_env: str, trusted_proxies: list[str], player_ttl_hours: int, limits: OnlineLimits)`;
  - `AppSettings.online: OnlineSettings`;
  - `AppSettings.effective_allowed_hosts() -> list[str]`;
  - `AppSettings.effective_cors_origins() -> list[str]`;
  - `LLMSettings.max_concurrent: int`, `LLMSettings.max_queue: int`;
  - `janus.__main__._proxy_options(config: LoadedConfig) -> dict[str, object]`.

- [ ] **Step 1: scrivere i test che falliscono** (in coda a `tests/test_config.py`)

```python
from janus.config import AppSettings, OnlineLimits, OnlineSettings  # noqa: E402


def test_online_mode_defaults_to_disabled_and_keeps_local_hosts():
    config = load_config(ROOT / "configs")

    assert config.app.online.enabled is False
    assert config.app.online.limits == OnlineLimits()
    assert config.app.effective_allowed_hosts() == config.app.allowed_hosts
    assert config.app.effective_cors_origins() == config.app.cors_origins
    assert (config.app.llm.max_concurrent, config.app.llm.max_queue) == (8, 16)


def test_online_mode_requires_a_bare_public_host():
    with pytest.raises(PydanticValidationError, match="requires public_host"):
        OnlineSettings(enabled=True)
    with pytest.raises(PydanticValidationError, match="bare hostname"):
        OnlineSettings(enabled=True, public_host="https://ctf.example.com")


def test_online_mode_exposes_only_the_public_origin():
    app = AppSettings(online=OnlineSettings(enabled=True, public_host="CTF.Example.com"))

    assert "ctf.example.com" in app.effective_allowed_hosts()
    assert "127.0.0.1" in app.effective_allowed_hosts()
    assert app.effective_cors_origins() == ["https://ctf.example.com"]


def test_trusted_proxies_must_be_ip_networks():
    online = OnlineSettings(trusted_proxies=["172.30.57.0/24", "10.0.0.5"])

    assert online.trusted_proxies == ["172.30.57.0/24", "10.0.0.5/32"]
    with pytest.raises(PydanticValidationError, match="trusted_proxies"):
        OnlineSettings(trusted_proxies=["proxy.local"])


def test_cli_enables_online_mode_and_wires_proxy_headers(monkeypatch):
    import janus.__main__ as entrypoint

    captured = {}
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "janus",
            "--online",
            "--public-host",
            "ctf.example.com",
            "--trusted-proxies",
            "172.30.57.0/24",
            "--host",
            "0.0.0.0",
        ],
    )
    monkeypatch.setattr(
        entrypoint, "create_app", lambda loaded_config: captured.setdefault("config", loaded_config)
    )
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda app, **kwargs: captured.setdefault("run", kwargs)
    )
    main()

    online = captured["config"].app.online
    assert online.enabled is True
    assert online.public_host == "ctf.example.com"
    assert online.trusted_proxies == ["172.30.57.0/24"]
    assert captured["run"]["proxy_headers"] is True
    assert captured["run"]["forwarded_allow_ips"] == "172.30.57.0/24"


def test_cli_ignores_forwarded_headers_without_trusted_proxies(monkeypatch):
    import janus.__main__ as entrypoint

    captured = {}
    monkeypatch.setattr(sys, "argv", ["janus"])
    monkeypatch.setattr(entrypoint, "create_app", lambda loaded_config: object())
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda app, **kwargs: captured.setdefault("run", kwargs)
    )
    main()

    assert captured["run"]["proxy_headers"] is False
    assert "forwarded_allow_ips" not in captured["run"]
```

- [ ] **Step 2: verificare che falliscano**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: FAIL, con `ImportError: cannot import name 'OnlineLimits'`.

- [ ] **Step 3: implementare in `src/janus/config.py`**

In testa al file aggiungere `import ipaddress` e `import re` (accanto agli import esistenti).

Dentro `LLMSettings`, subito dopo `fallback_to_mock: bool = True`:

```python
    # Global gate in front of the provider: concurrent calls and bounded waiting queue.
    max_concurrent: int = Field(default=8, ge=1, le=256)
    max_queue: int = Field(default=16, ge=0, le=10_000)
```

Prima di `class AppSettings`:

```python
_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")


class OnlineLimits(StrictModel):
    auth_attempts_per_minute: int = Field(default=10, ge=1, le=1000)
    turns_per_minute: int = Field(default=12, ge=1, le=1000)
    sessions_per_hour: int = Field(default=20, ge=1, le=10_000)
    max_active_sessions: int = Field(default=1, ge=1, le=10)


class OnlineSettings(StrictModel):
    """Internet-facing multiplayer mode; disabled keeps the local kiosk unchanged."""

    enabled: bool = False
    public_host: str | None = None
    access_codes_env: str = "JANUS_ACCESS_CODES"
    trusted_proxies: list[str] = Field(default_factory=list)
    player_ttl_hours: int = Field(default=12, ge=1, le=168)
    limits: OnlineLimits = Field(default_factory=OnlineLimits)

    @field_validator("public_host")
    @classmethod
    def bare_hostname(cls, value: str | None) -> str | None:
        if value is None:
            return None
        host = value.strip().lower()
        if not _HOSTNAME.fullmatch(host):
            raise ValueError("public_host must be a bare hostname such as ctf.example.com")
        return host

    @field_validator("trusted_proxies")
    @classmethod
    def ip_networks_only(cls, value: list[str]) -> list[str]:
        networks = []
        for item in value:
            try:
                networks.append(str(ipaddress.ip_network(item.strip(), strict=False)))
            except ValueError as exc:
                raise ValueError(f"trusted_proxies entries must be IPs or CIDRs: {item!r}") from exc
        return networks

    @model_validator(mode="after")
    def public_host_when_enabled(self) -> OnlineSettings:
        if self.enabled and not self.public_host:
            raise ValueError("online mode requires public_host")
        return self
```

Dentro `AppSettings`, dopo `speech: SpeechSettings = ...`:

```python
    online: OnlineSettings = Field(default_factory=OnlineSettings)

    def effective_allowed_hosts(self) -> list[str]:
        hosts = list(self.allowed_hosts)
        if self.online.enabled and self.online.public_host not in hosts:
            hosts.append(self.online.public_host)
        return hosts

    def effective_cors_origins(self) -> list[str]:
        if self.online.enabled:
            return [f"https://{self.online.public_host}"]
        return list(self.cors_origins)
```

- [ ] **Step 4: aggiornare `configs/app.yaml`**

Nel blocco `llm:`, dopo `fallback_to_mock: false`:

```yaml
  max_concurrent: 8
  max_queue: 16
```

In fondo al file:

```yaml
online:
  enabled: false
  public_host: null
  access_codes_env: "JANUS_ACCESS_CODES"
  trusted_proxies: []
  player_ttl_hours: 12
  limits:
    auth_attempts_per_minute: 10
    turns_per_minute: 12
    sessions_per_hour: 20
    max_active_sessions: 1
```

- [ ] **Step 5: aggiornare `src/janus/__main__.py`**

Import: `from .config import AppSettings, LLMSettings, LoadedConfig, OnlineSettings, SpeechSettings, load_config`.

Dopo l'argomento `--port`:

```python
    parser.add_argument(
        "--online",
        action="store_true",
        help="Enable the Internet-facing multiplayer mode (requires --public-host and "
        "JANUS_ACCESS_CODES).",
    )
    parser.add_argument("--public-host", default=None, help="Public DNS name, e.g. ctf.example.com")
    parser.add_argument(
        "--trusted-proxies",
        default=None,
        help="Comma-separated IPs/CIDRs whose X-Forwarded-* headers are trusted.",
    )
```

Prima del blocco `if app_updates:`:

```python
    online_updates: dict[str, object] = {}
    if args.online:
        online_updates["enabled"] = True
    if args.public_host is not None:
        online_updates["public_host"] = args.public_host
    if args.trusted_proxies is not None:
        online_updates["trusted_proxies"] = [
            item for item in args.trusted_proxies.split(",") if item.strip()
        ]
    if online_updates:
        app_updates["online"] = OnlineSettings.model_validate(
            {**config.app.online.model_dump(), **online_updates}
        )
```

Sostituire la chiamata finale `uvicorn.run(...)` con:

```python
    uvicorn.run(
        create_app(loaded_config=config),
        host=args.host,
        port=args.port,
        **_proxy_options(config),
    )
```

E aggiungere a livello di modulo, prima di `main`:

```python
def _proxy_options(config: LoadedConfig) -> dict[str, object]:
    """Trust X-Forwarded-* only from the configured proxies, never by default."""

    proxies = config.app.online.trusted_proxies
    if not proxies:
        return {"proxy_headers": False}
    return {"proxy_headers": True, "forwarded_allow_ips": ",".join(proxies)}
```

- [ ] **Step 6: vincolo minimo su uvicorn in `pyproject.toml`**

Sostituire `"uvicorn[standard]>=0.30,<1",` con `"uvicorn[standard]>=0.54,<1",`. È la versione verificata, che accetta i CIDR in `forwarded_allow_ips`.

- [ ] **Step 7: verificare che passino e che il resto non sia rotto**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests scripts`
Expected: PASS (58 test), `All checks passed!`.

- [ ] **Step 8: commit**

```bash
git add src/janus/config.py configs/app.yaml src/janus/__main__.py pyproject.toml tests/test_config.py
git commit -m "Add online-mode configuration, LLM gate settings and proxy CLI flags"
```

---

### Task 2: nuove classi d'errore, `Retry-After` e `RateLimiter`

**Files:**
- Modify: `src/janus/errors.py`
- Create: `src/janus/ratelimit.py`
- Modify: `src/janus/api.py` (solo `janus_error_handler`)
- Test: `tests/test_ratelimit.py`

**Interfaces:**
- Produces:
  - `UnauthorizedError(message)` (401, `unauthorized`);
  - `ConflictError(message, *, code: str, details=None)` (409);
  - `RateLimitedError(retry_after: int)` (429, `rate_limited`, `details={"retry_after": n}`);
  - `CapacityError(retry_after: int)` (503, `llm_busy`);
  - `RateLimiter(clock=time.monotonic)`, con i metodi:
    - `.check(key, *, limit, window_seconds) -> None` (alza `RateLimitedError`);
    - `.record(key, *, window_seconds) -> None`;
    - `.hit(key, *, limit, window_seconds) -> None` (`check` + `record`);
    - `.prune(max_window_seconds) -> int`.

- [ ] **Step 1: scrivere i test che falliscono** (`tests/test_ratelimit.py`)

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from janus.api import create_app
from janus.errors import CapacityError, ConflictError, RateLimitedError
from janus.providers.llm import MockLLMProvider
from janus.providers.stt import DisabledSTTProvider
from janus.providers.tts import DisabledTTSProvider
from janus.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_hit_allows_up_to_the_limit_then_reports_retry_after():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    for _ in range(3):
        limiter.hit("turn:a", limit=3, window_seconds=60)

    with pytest.raises(RateLimitedError) as raised:
        limiter.hit("turn:a", limit=3, window_seconds=60)
    assert raised.value.details["retry_after"] == 60

    clock.now += 30
    with pytest.raises(RateLimitedError) as raised:
        limiter.hit("turn:a", limit=3, window_seconds=60)
    assert raised.value.details["retry_after"] == 30

    clock.now += 31
    limiter.hit("turn:a", limit=3, window_seconds=60)


def test_keys_are_independent():
    limiter = RateLimiter(clock=FakeClock())
    limiter.hit("turn:a", limit=1, window_seconds=60)
    limiter.hit("turn:b", limit=1, window_seconds=60)
    with pytest.raises(RateLimitedError):
        limiter.hit("turn:a", limit=1, window_seconds=60)


def test_check_does_not_record_and_record_does_not_check():
    limiter = RateLimiter(clock=FakeClock())
    for _ in range(5):
        limiter.check("auth:ip", limit=2, window_seconds=60)
    limiter.record("auth:ip", window_seconds=60)
    limiter.record("auth:ip", window_seconds=60)
    with pytest.raises(RateLimitedError):
        limiter.check("auth:ip", limit=2, window_seconds=60)


def test_prune_drops_idle_keys():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    limiter.hit("a", limit=5, window_seconds=60)
    clock.now += 10
    limiter.hit("b", limit=5, window_seconds=60)
    clock.now += 3595

    assert limiter.prune(3600) == 1
    assert limiter.prune(3600) == 0


def test_error_classes_carry_codes_and_retry_after():
    assert ConflictError("busy", code="turn_in_progress").code == "turn_in_progress"
    assert ConflictError("busy", code="turn_in_progress").status_code == 409
    assert CapacityError(7).status_code == 503
    assert CapacityError(7).details == {"retry_after": 7}


def test_error_handler_adds_retry_after_header(loaded_config, repository, flag_service):
    app = create_app(
        loaded_config=loaded_config,
        repository=repository,
        flag_service=flag_service,
        llm_provider=MockLLMProvider(),
        stt_provider=DisabledSTTProvider(),
        tts_provider=DisabledTTSProvider(),
    )

    @app.get("/api/test-rate-limited")
    async def rate_limited():
        raise RateLimitedError(7)

    response = TestClient(app).get("/api/test-rate-limited")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"
    assert response.json()["error"]["code"] == "rate_limited"
```

- [ ] **Step 2: verificare che falliscano**

Run: `.venv/bin/python -m pytest tests/test_ratelimit.py -q`
Expected: FAIL, con `ImportError` su `CapacityError` / `janus.ratelimit`.

- [ ] **Step 3: aggiungere le classi in fondo a `src/janus/errors.py`**

```python
class UnauthorizedError(JanusError):
    code = "unauthorized"
    status_code = 401


class ConflictError(JanusError):
    status_code = 409

    def __init__(self, message: str, *, code: str, details: dict | None = None) -> None:
        super().__init__(message, details=details)
        self.code = code


class RateLimitedError(JanusError):
    code = "rate_limited"
    status_code = 429

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            f"Too many requests; retry in {retry_after} s", details={"retry_after": retry_after}
        )


class CapacityError(JanusError):
    code = "llm_busy"
    status_code = 503

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            f"JANUS is at capacity; retry in {retry_after} s", details={"retry_after": retry_after}
        )
```

- [ ] **Step 4: creare `src/janus/ratelimit.py`**

```python
"""In-memory sliding-window rate limiting for a single JANUS process."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable

from .errors import RateLimitedError


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def _recent(self, key: str, window_seconds: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        horizon = self._clock() - window_seconds
        while hits and hits[0] <= horizon:
            hits.popleft()
        return hits

    def check(self, key: str, *, limit: int, window_seconds: float) -> None:
        hits = self._recent(key, window_seconds)
        if len(hits) >= limit:
            retry_after = max(1, math.ceil(hits[0] + window_seconds - self._clock()))
            raise RateLimitedError(retry_after)

    def record(self, key: str, *, window_seconds: float) -> None:
        self._recent(key, window_seconds).append(self._clock())

    def hit(self, key: str, *, limit: int, window_seconds: float) -> None:
        self.check(key, limit=limit, window_seconds=window_seconds)
        self.record(key, window_seconds=window_seconds)

    def prune(self, max_window_seconds: float) -> int:
        horizon = self._clock() - max_window_seconds
        idle = [key for key, hits in self._hits.items() if not hits or hits[-1] <= horizon]
        for key in idle:
            del self._hits[key]
        return len(idle)
```

- [ ] **Step 5: `Retry-After` nel gestore d'errore di `src/janus/api.py`**

Sostituire il corpo di `janus_error_handler` con:

```python
    @app.exception_handler(JanusError)
    async def janus_error_handler(request: Request, exc: JanusError) -> JSONResponse:
        del request
        headers = {}
        if "retry_after" in exc.details:
            headers["Retry-After"] = str(exc.details["retry_after"])
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.message,
                "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            },
            headers=headers,
        )
```

- [ ] **Step 6: verificare**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests scripts`
Expected: PASS.

- [ ] **Step 7: commit**

```bash
git add src/janus/errors.py src/janus/ratelimit.py src/janus/api.py tests/test_ratelimit.py
git commit -m "Add online error classes, Retry-After header and in-memory rate limiter"
```

---

### Task 3: primitive di sicurezza (cookie, codici evento, codice di recupero)

**Files:**
- Modify: `src/janus/security.py`
- Test: `tests/test_security.py`

**Interfaces:**
- Produces:
  - `FlagService.derive_subkey(label: bytes) -> bytes`;
  - `PlayerToken(player_id: str, expires_at: int)` (dataclass frozen);
  - `PlayerTokenService(key: bytes, clock=time.time)`, con `.issue(player_id: str, expires_at: int) -> str` e `.verify(token: str | None) -> PlayerToken | None`;
  - `AccessCodeVerifier(codes: Sequence[str])`, con `.from_env(name: str)` (classmethod) e `.verify(candidate: str) -> bool`;
  - `generate_recovery_code() -> str`;
  - `normalize_recovery_code(raw: str) -> str | None`;
  - `hash_recovery_code(normalized: str) -> str`;
  - `PLAYER_TOKEN_LABEL = b"janus/player-token/v1"`.

- [ ] **Step 1: scrivere i test che falliscono** (in coda a `tests/test_security.py`)

```python
import re
import uuid

import pytest

from janus.errors import ConfigurationError
from janus.security import (
    PLAYER_TOKEN_LABEL,
    AccessCodeVerifier,
    FlagService,
    PlayerTokenService,
    generate_recovery_code,
    hash_recovery_code,
    normalize_recovery_code,
)


def _tokens(now=1_000_000.0):
    service = FlagService(b"k" * 32)
    return PlayerTokenService(service.derive_subkey(PLAYER_TOKEN_LABEL), clock=lambda: now)


def test_player_token_round_trip_and_expiry():
    player_id = str(uuid.uuid4())
    token = _tokens().issue(player_id, 1_000_100)

    assert token.startswith("v1.")
    verified = _tokens().verify(token)
    assert verified is not None and verified.player_id == player_id
    assert _tokens(now=1_000_100.0).verify(token) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t[:-2] + ("AA" if not t.endswith("AA") else "BB"),
        lambda t: t.replace(".1000100.", ".1999999."),
        lambda t: "v2" + t[2:],
        lambda t: t + ".extra",
        lambda t: "",
    ],
)
def test_player_token_rejects_tampering(mutate):
    token = _tokens().issue(str(uuid.uuid4()), 1_000_100)
    assert _tokens().verify(mutate(token)) is None


def test_player_token_key_is_domain_separated_from_flags():
    service = FlagService(b"k" * 32)
    other = PlayerTokenService(FlagService(b"x" * 32).derive_subkey(PLAYER_TOKEN_LABEL))
    token = PlayerTokenService(service.derive_subkey(PLAYER_TOKEN_LABEL)).issue(
        str(uuid.uuid4()), 4_000_000_000
    )

    assert other.verify(token) is None
    assert service.derive_subkey(PLAYER_TOKEN_LABEL) != service.derive_subkey(b"other")


def test_access_codes_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("JANUS_ACCESS_CODES", " event-code-1 ,second-code ")
    verifier = AccessCodeVerifier.from_env("JANUS_ACCESS_CODES")

    assert verifier.verify("event-code-1")
    assert verifier.verify(" second-code ")
    assert not verifier.verify("event-code")


@pytest.mark.parametrize("raw", ["", " , ", "short"])
def test_access_codes_must_exist_and_be_long_enough(monkeypatch, raw):
    monkeypatch.setenv("JANUS_ACCESS_CODES", raw)
    with pytest.raises(ConfigurationError):
        AccessCodeVerifier.from_env("JANUS_ACCESS_CODES")


def test_recovery_codes_are_formatted_and_unique():
    codes = {generate_recovery_code() for _ in range(200)}

    assert len(codes) == 200
    for code in codes:
        assert re.fullmatch(r"RCV-[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}", code)


def test_recovery_code_normalization_accepts_human_input():
    code = generate_recovery_code()
    expected = normalize_recovery_code(code)

    assert expected is not None and len(expected) == 16
    assert normalize_recovery_code(code.lower()) == expected
    assert normalize_recovery_code(code.replace("-", " ")) == expected
    assert normalize_recovery_code(code.removeprefix("RCV-")) == expected
    assert normalize_recovery_code("RCV-ILOU-0000-0000-0000") is None
    assert normalize_recovery_code("RCV-1234") is None
    assert hash_recovery_code(expected) == hash_recovery_code(normalize_recovery_code(code.lower()))
    assert len(hash_recovery_code(expected)) == 64
```

- [ ] **Step 2: verificare che falliscano**

Run: `.venv/bin/python -m pytest tests/test_security.py -q`
Expected: FAIL, con `ImportError: cannot import name 'PLAYER_TOKEN_LABEL'`.

- [ ] **Step 3: implementare in `src/janus/security.py`**

Import aggiuntivi in testa: `import time`, `from collections.abc import Callable, Sequence`, `from dataclasses import dataclass`.

Dentro `FlagService`, dopo `__init__`:

```python
    def derive_subkey(self, label: bytes) -> bytes:
        """Independent key for another purpose; the master key never leaves this class."""

        return hmac.new(self._key, label, hashlib.sha256).digest()
```

In fondo al file:

```python
PLAYER_TOKEN_LABEL = b"janus/player-token/v1"
RECOVERY_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32: no I, L, O, U
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass(frozen=True)
class PlayerToken:
    player_id: str
    expires_at: int


class PlayerTokenService:
    """Stateless signed player cookies: ``v1.<player_id>.<expires_unix>.<sig>``."""

    def __init__(self, key: bytes, clock: Callable[[], float] = time.time) -> None:
        if len(key) < 32:
            raise ValueError("player token key must contain at least 32 bytes")
        self._key = key
        self._clock = clock

    def _sign(self, player_id: str, expires_at: int) -> str:
        mac = hmac.new(
            self._key, f"v1|{player_id}|{expires_at}".encode("ascii"), hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")

    def issue(self, player_id: str, expires_at: int) -> str:
        return f"v1.{player_id}.{expires_at}.{self._sign(player_id, expires_at)}"

    def verify(self, token: str | None) -> PlayerToken | None:
        if not token:
            return None
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != "v1":
            return None
        _, player_id, expires_raw, signature = parts
        if not _UUID.fullmatch(player_id) or not expires_raw.isdigit():
            return None
        expires_at = int(expires_raw)
        if not hmac.compare_digest(self._sign(player_id, expires_at), signature):
            return None
        if expires_at <= self._clock():
            return None
        return PlayerToken(player_id=player_id, expires_at=expires_at)


class AccessCodeVerifier:
    def __init__(self, codes: Sequence[str]) -> None:
        cleaned = [code.strip() for code in codes if code.strip()]
        if not cleaned:
            raise ConfigurationError("Online mode requires at least one access code")
        if any(len(code) < 8 for code in cleaned):
            raise ConfigurationError("Access codes must contain at least 8 characters")
        self._codes = [code.encode("utf-8") for code in cleaned]

    @classmethod
    def from_env(cls, name: str) -> AccessCodeVerifier:
        return cls(os.environ.get(name, "").split(","))

    def verify(self, candidate: str) -> bool:
        encoded = candidate.strip().encode("utf-8")
        matched = False
        for code in self._codes:
            # Compare against every code so timing does not reveal which one matched.
            matched |= hmac.compare_digest(encoded, code)
        return matched


def generate_recovery_code() -> str:
    value = secrets.randbits(80)
    characters = []
    for _ in range(16):
        characters.append(RECOVERY_ALPHABET[value & 31])
        value >>= 5
    body = "".join(characters)
    return "RCV-" + "-".join(body[index : index + 4] for index in range(0, 16, 4))


def normalize_recovery_code(raw: str) -> str | None:
    cleaned = re.sub(r"[\s-]", "", raw).upper()
    cleaned = cleaned.removeprefix("RCV")
    if len(cleaned) != 16 or any(character not in RECOVERY_ALPHABET for character in cleaned):
        return None
    return cleaned


def hash_recovery_code(normalized: str) -> str:
    # 80 random bits: an unsalted hash is not brute-forceable offline.
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()
```

- [ ] **Step 4: verificare**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests scripts`
Expected: PASS.

- [ ] **Step 5: commit**

```bash
git add src/janus/security.py tests/test_security.py
git commit -m "Add signed player tokens, access code verifier and recovery codes"
```

---

### Task 4: modello dati: giocatori e proprietario delle sessioni

**Files:**
- Modify: `src/janus/domain.py`
- Modify: `src/janus/repository.py`
- Test: `tests/test_repository_online.py`

**Interfaces:**
- Produces:
  - `SessionRecord.owner_id: str | None` (escluso da `model_dump`);
  - `SQLiteRepository.create_player(player_id: str, recovery_hash: str, created_at: datetime) -> None`;
  - `.find_player_by_recovery_hash(recovery_hash: str) -> tuple[str, datetime] | None`;
  - `.delete_players_created_before(cutoff: datetime) -> int`;
  - `.active_sessions_for_owner(owner_id: str) -> list[SessionRecord]` (più vecchie prima);
  - `.active_session_ids() -> list[str]`;
  - `.nickname_taken_by_other(nickname: str, owner_id: str) -> bool`;
  - `.latest_nickname_for_owner(owner_id: str) -> str | None`;
  - `.list_messages(session_id: str) -> list[MessageRecord]`.

- [ ] **Step 1: scrivere i test che falliscono** (`tests/test_repository_online.py`)

```python
from __future__ import annotations

import sqlite3
from datetime import timedelta

from janus.domain import Language, MessageRecord, SessionRecord, SessionStatus, utc_now
from janus.repository import SQLiteRepository


def _session(owner, *, nickname=None, mode="score", status=SessionStatus.ACTIVE, age=0):
    return SessionRecord(
        id=f"{owner or 'local'}-{nickname}-{age}-{status.value}",
        mode_id=mode,
        level_id="level_1",
        nickname=nickname,
        status=status,
        owner_id=owner,
        started_at=utc_now() - timedelta(seconds=age),
    )


def test_owner_id_is_persisted_but_never_serialized(repository):
    stored = repository.create_session(_session("p1", nickname="Ada"))

    assert repository.get_session(stored.id).owner_id == "p1"
    assert "owner_id" not in repository.get_session(stored.id).model_dump()


def test_players_round_trip_and_expire(repository):
    created = utc_now() - timedelta(hours=2)
    repository.create_player("p1", "a" * 64, created)
    repository.create_player("p2", "b" * 64, utc_now())

    assert repository.find_player_by_recovery_hash("a" * 64) == ("p1", created)
    assert repository.find_player_by_recovery_hash("c" * 64) is None
    assert repository.delete_players_created_before(utc_now() - timedelta(hours=1)) == 1
    assert repository.find_player_by_recovery_hash("a" * 64) is None


def test_active_sessions_for_owner_are_oldest_first(repository):
    repository.create_session(_session("p1", nickname="Ada", age=50))
    repository.create_session(_session("p1", nickname="Ada", age=10))
    repository.create_session(_session("p1", nickname="Ada", age=99, status=SessionStatus.WON))
    repository.create_session(_session("p2", nickname="Bob", age=5))

    sessions = repository.active_sessions_for_owner("p1")

    assert [item.started_at < sessions[-1].started_at for item in sessions[:-1]] == [True]
    assert {item.owner_id for item in sessions} == {"p1"}
    assert len(repository.active_session_ids()) == 3


def test_nickname_ownership_is_case_insensitive_and_ignores_local_sessions(repository):
    repository.create_session(_session("p1", nickname="Ädá"))
    repository.create_session(_session(None, nickname="Local"))

    assert repository.nickname_taken_by_other("äDÁ", "p2") is True
    assert repository.nickname_taken_by_other("Ädá", "p1") is False
    assert repository.nickname_taken_by_other("local", "p2") is False
    assert repository.latest_nickname_for_owner("p1") == "Ädá"
    assert repository.latest_nickname_for_owner("p9") is None


def test_list_messages_returns_the_whole_history(repository):
    session = repository.create_session(_session("p1", nickname="Ada"))
    for index in range(30):
        repository.add_message(
            MessageRecord(
                session_id=session.id, role="user", content=f"m{index}", language=Language.ITALIAN
            )
        )

    assert [item.content for item in repository.list_messages(session.id)] == [
        f"m{index}" for index in range(30)
    ]


def test_existing_databases_gain_the_owner_column(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.execute(
        """CREATE TABLE sessions (id TEXT PRIMARY KEY, mode_id TEXT NOT NULL,
        level_id TEXT NOT NULL, nickname TEXT, status TEXT NOT NULL, started_at TEXT NOT NULL,
        completed_at TEXT, turn_count INTEGER NOT NULL DEFAULT 0,
        hints_used INTEGER NOT NULL DEFAULT 0, score INTEGER, last_language TEXT)"""
    )
    legacy.commit()
    legacy.close()

    repository = SQLiteRepository(path)
    stored = repository.create_session(_session("p1", nickname="Ada"))

    assert repository.get_session(stored.id).owner_id == "p1"
    repository.close()
```

- [ ] **Step 2: verificare che falliscano**

Run: `.venv/bin/python -m pytest tests/test_repository_online.py -q`
Expected: FAIL, con `ValidationError` (campo `owner_id` sconosciuto) o `AttributeError`.

- [ ] **Step 3: `src/janus/domain.py`**

In `SessionRecord`, dopo `last_language: Language | None = None`:

```python
    # Online mode only. Never serialized: API clients must not learn player ids.
    owner_id: str | None = Field(default=None, exclude=True)
```

- [ ] **Step 4: `src/janus/repository.py`: schema e migrazione**

Dentro `initialize`, nello script `CREATE TABLE IF NOT EXISTS sessions (...)`, aggiungere dopo `last_language TEXT` la riga `, owner_id TEXT`. In coda allo stesso `executescript`, prima della chiusura `"""`:

```sql
                CREATE TABLE IF NOT EXISTS players (
                    id TEXT PRIMARY KEY,
                    recovery_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
```

Dopo il blocco esistente `if "processing_seconds" not in columns: ...`:

```python
            if "owner_id" not in columns:
                connection.execute("ALTER TABLE sessions ADD COLUMN owner_id TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_id, status)"
            )
```

In `_session_from_row` aggiungere `owner_id=row["owner_id"],`. In `create_session` aggiungere la colonna `owner_id` alla lista `INSERT`, un tredicesimo `?` e il valore `session.owner_id` in coda alla tupla.

- [ ] **Step 5: `src/janus/repository.py`: nuove query** (prima di `def close`)

```python
    def create_player(self, player_id: str, recovery_hash: str, created_at: datetime) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO players (id, recovery_hash, created_at) VALUES (?, ?, ?)",
                (player_id, recovery_hash, created_at.isoformat()),
            )
            connection.commit()
        finally:
            self._close(connection)

    def find_player_by_recovery_hash(self, recovery_hash: str) -> tuple[str, datetime] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT id, created_at FROM players WHERE recovery_hash = ?", (recovery_hash,)
            ).fetchone()
        finally:
            self._close(connection)
        if row is None:
            return None
        return row["id"], datetime.fromisoformat(row["created_at"])

    def delete_players_created_before(self, cutoff: datetime) -> int:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "DELETE FROM players WHERE created_at < ?", (cutoff.isoformat(),)
            )
            connection.commit()
        finally:
            self._close(connection)
        return cursor.rowcount

    def active_sessions_for_owner(self, owner_id: str) -> list[SessionRecord]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM sessions WHERE owner_id = ? AND status = 'active'
                ORDER BY started_at ASC
                """,
                (owner_id,),
            ).fetchall()
        finally:
            self._close(connection)
        return [self._session_from_row(row) for row in rows]

    def active_session_ids(self) -> list[str]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT id FROM sessions WHERE status = 'active'").fetchall()
        finally:
            self._close(connection)
        return [row["id"] for row in rows]

    def nickname_taken_by_other(self, nickname: str, owner_id: str) -> bool:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT DISTINCT nickname FROM sessions
                WHERE nickname IS NOT NULL AND owner_id IS NOT NULL AND owner_id != ?
                """,
                (owner_id,),
            ).fetchall()
        finally:
            self._close(connection)
        # Python casefold matches the leaderboard's identity rule, including non-ASCII.
        wanted = nickname.casefold()
        return any(row["nickname"].casefold() == wanted for row in rows)

    def latest_nickname_for_owner(self, owner_id: str) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT nickname FROM sessions WHERE owner_id = ? AND nickname IS NOT NULL
                ORDER BY started_at DESC LIMIT 1
                """,
                (owner_id,),
            ).fetchone()
        finally:
            self._close(connection)
        return row["nickname"] if row else None

    def list_messages(self, session_id: str) -> list[MessageRecord]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)
            ).fetchall()
        finally:
            self._close(connection)
        return [
            MessageRecord(
                id=row["id"],
                session_id=row["session_id"],
                role=row["role"],
                content=row["content"],
                language=Language(row["language"]),
                modality=InputModality(row["modality"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]
```

- [ ] **Step 6: verificare**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests scripts`
Expected: PASS.

- [ ] **Step 7: commit**

```bash
git add src/janus/domain.py src/janus/repository.py tests/test_repository_online.py
git commit -m "Persist session owners and players for online mode"
```

---

### Task 5: cancello globale sull'LLM

**Files:**
- Modify: `src/janus/providers/llm.py`
- Modify: `src/janus/api.py` (cablaggio in `create_app`)
- Test: `tests/test_providers.py`

**Interfaces:**
- Consumes: `CapacityError(retry_after)` (Task 2); `LLMSettings.max_concurrent/max_queue` (Task 1).
- Produces: `GatedLLMProvider(inner: LLMProvider, *, max_concurrent: int, max_queue: int, clock=time.monotonic)`, con `.generate(...)`, `.health()` e `.waiting: int` (proprietà).

- [ ] **Step 1: scrivere i test che falliscono** (in coda a `tests/test_providers.py`)

```python
from janus.errors import CapacityError  # noqa: E402
from janus.providers.llm import GatedLLMProvider  # noqa: E402


class _HeldLLM:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.active = 0
        self.peak = 0

    async def generate(self, messages, *, temperature, max_tokens):
        self.active += 1
        self.peak = max(self.peak, self.active)
        await self.release.wait()
        self.active -= 1
        return "ok"

    async def health(self):
        from janus.domain import HealthComponent

        return HealthComponent(available=True, detail="held")


def test_gate_caps_concurrency_and_rejects_beyond_the_queue():
    async def exercise():
        inner = _HeldLLM()
        gate = GatedLLMProvider(inner, max_concurrent=2, max_queue=1)
        message = [ChatMessage(role="user", content="hi")]
        running = [
            asyncio.create_task(gate.generate(message, temperature=0.1, max_tokens=16))
            for _ in range(3)
        ]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert inner.active == 2
        assert gate.waiting == 1
        with pytest.raises(CapacityError) as raised:
            await gate.generate(message, temperature=0.1, max_tokens=16)
        assert 2 <= raised.value.details["retry_after"] <= 60
        inner.release.set()
        results = await asyncio.gather(*running)
        assert results == ["ok", "ok", "ok"]
        assert inner.peak == 2
        assert gate.waiting == 0
        assert (await gate.health()).detail == "held"

    asyncio.run(exercise())


def test_gate_releases_slots_when_the_provider_fails():
    class Failing:
        async def generate(self, messages, *, temperature, max_tokens):
            raise ProviderError("down")

        async def health(self):
            raise AssertionError("unused")

    async def exercise():
        gate = GatedLLMProvider(Failing(), max_concurrent=1, max_queue=0)
        for _ in range(3):
            with pytest.raises(ProviderError):
                await gate.generate([], temperature=0.1, max_tokens=16)

    asyncio.run(exercise())


def test_gate_cancellation_does_not_leak_queue_slots():
    async def exercise():
        inner = _HeldLLM()
        gate = GatedLLMProvider(inner, max_concurrent=1, max_queue=1)
        first = asyncio.create_task(gate.generate([], temperature=0.1, max_tokens=16))
        await asyncio.sleep(0)
        waiting = asyncio.create_task(gate.generate([], temperature=0.1, max_tokens=16))
        await asyncio.sleep(0)
        assert gate.waiting == 1
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        assert gate.waiting == 0
        inner.release.set()
        await first

    asyncio.run(exercise())
```

- [ ] **Step 2: verificare che falliscano**

Run: `.venv/bin/python -m pytest tests/test_providers.py -q`
Expected: FAIL, con `ImportError: cannot import name 'GatedLLMProvider'`.

- [ ] **Step 3: implementare in `src/janus/providers/llm.py`**

Import aggiuntivi: `import asyncio`, `import math`, `import time`, e `from ..errors import CapacityError, ProviderError` (al posto dell'import singolo di `ProviderError`). Aggiungere dopo `FallbackLLMProvider`:

```python
class GatedLLMProvider:
    """Caps concurrent model calls and bounds the waiting queue across all sessions.

    Waiters are served FIFO by the semaphore. When the queue is full the caller
    gets CapacityError (HTTP 503) with a retry estimate instead of waiting forever.
    """

    def __init__(
        self,
        inner: LLMProvider,
        *,
        max_concurrent: int,
        max_queue: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.inner = inner
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._max_queue = max_queue
        self._clock = clock
        self._waiting = 0
        self._average_seconds = 5.0

    @property
    def waiting(self) -> int:
        return self._waiting

    def _retry_after(self) -> int:
        estimate = self._average_seconds * (self._waiting + 1) / self._max_concurrent
        return min(60, max(2, math.ceil(estimate)))

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if self._semaphore.locked() and self._waiting >= self._max_queue:
            raise CapacityError(self._retry_after())
        self._waiting += 1
        try:
            await self._semaphore.acquire()
        finally:
            self._waiting -= 1
        started = self._clock()
        try:
            return await self.inner.generate(
                messages, temperature=temperature, max_tokens=max_tokens
            )
        finally:
            self._semaphore.release()
            elapsed = self._clock() - started
            self._average_seconds = 0.8 * self._average_seconds + 0.2 * elapsed

    async def health(self) -> HealthComponent:
        return await self.inner.health()
```

- [ ] **Step 4: cablare il cancello in `create_app` (`src/janus/api.py`)**

Importare `GatedLLMProvider` insieme agli altri provider. Nella costruzione di `ChallengeEngine` sostituire `llm_provider or _build_llm(config),` con:

```python
        GatedLLMProvider(
            llm_provider or _build_llm(config),
            max_concurrent=config.app.llm.max_concurrent,
            max_queue=config.app.llm.max_queue,
        ),
```

- [ ] **Step 5: verificare**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests scripts`
Expected: PASS. I test esistenti di concorrenza continuano a passare, perché il cancello con default 8/16 è trasparente.

- [ ] **Step 6: commit**

```bash
git add src/janus/providers/llm.py src/janus/api.py tests/test_providers.py
git commit -m "Gate LLM calls with a global concurrency cap and bounded queue"
```

---

### Task 6: motore: proprietà, sessione unica, turno in corso, cronologia, audio, panoramica

**Files:**
- Modify: `src/janus/engine.py`
- Test: `tests/test_engine_online.py`

**Interfaces:**
- Consumes: `ConflictError` (Task 2); `SessionRecord.owner_id` e le query del repository (Task 4); `config.app.online` (Task 1).
- Produces (tutti con `owner_id: str | None = None`; `None` significa modalità locale, cioè nessun controllo):
  - `ChallengeEngine.create_session(*, mode_id=None, level_id=None, nickname=None, owner_id=None) -> SessionRecord`;
  - `async open_session(*, mode_id=None, level_id=None, nickname=None, owner_id=None) -> SessionRecord`;
  - `async get_session_serialized(session_id, owner_id=None)`;
  - `async message(session_id, text, *, language, modality, speak, transcript, owner_id=None)`;
  - `async voice(session_id, audio_path, *, language, speak, owner_id=None)`;
  - `async submit_serialized(session_id, candidate, owner_id=None)`;
  - `async hint_serialized(session_id, language, owner_id=None)`;
  - `async reset_serialized(session_id, owner_id=None)`;
  - `async delete_serialized(session_id, owner_id=None)`;
  - `history(session_id, owner_id=None) -> list[MessageRecord]`;
  - `audio_path(audio_id, owner_id=None) -> Path | None`;
  - `remaining_seconds(session: SessionRecord) -> int`;
  - `player_overview(owner_id: str) -> dict[str, object]`, nel formato `{"nickname": str | None, "active_sessions": [{"id", "mode_id", "level_id", "started_at", "remaining_seconds"}]}`.

- [ ] **Step 1: scrivere i test che falliscono** (`tests/test_engine_online.py`)

```python
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from janus.config import OnlineSettings
from janus.domain import AudioArtifact, HealthComponent, SessionStatus
from janus.engine import ChallengeEngine
from janus.errors import ConflictError, InvalidSessionError, NotFoundError
from janus.providers.llm import MockLLMProvider
from janus.providers.stt import DisabledSTTProvider
from janus.providers.tts import DisabledTTSProvider


class FileTTS:
    def __init__(self, directory: Path) -> None:
        self.output_dir = directory

    async def synthesize(self, text, language):
        audio_id = uuid.uuid4().hex
        (self.output_dir / f"{audio_id}.wav").write_bytes(b"RIFF" + b"\0" * 100)
        return AudioArtifact(id=audio_id)

    def resolve(self, audio_id):
        path = self.output_dir / f"{audio_id}.wav"
        return path if path.is_file() else None

    def delete(self, audio_id):
        (self.output_dir / f"{audio_id}.wav").unlink(missing_ok=True)

    async def health(self):
        return HealthComponent(available=True, detail="file tts")


class BlockingLLM:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, messages, *, temperature, max_tokens):
        self.started.set()
        await self.release.wait()
        return "reply"

    async def health(self):
        return HealthComponent(available=True, detail="blocking")


@pytest.fixture
def online_config(loaded_config):
    app = loaded_config.app.model_copy(
        update={
            "default_mode": "score",
            "online": OnlineSettings(enabled=True, public_host="ctf.example.com"),
        }
    )
    return loaded_config.model_copy(update={"app": app})


def _engine(config, repository, flag_service, llm=None, tts=None):
    return ChallengeEngine(
        config,
        repository,
        flag_service,
        llm or MockLLMProvider(),
        DisabledSTTProvider(),
        tts or DisabledTTSProvider(),
    )


def test_foreign_sessions_look_exactly_like_missing_ones(online_config, repository, flag_service):
    engine = _engine(online_config, repository, flag_service)
    session = asyncio.run(engine.open_session(nickname="Ada", owner_id="p1"))

    async def attempts():
        calls = [
            engine.get_session_serialized(session.id, owner_id="p2"),
            engine.message(session.id, "hi", owner_id="p2"),
            engine.submit_serialized(session.id, "RH26{x}", owner_id="p2"),
            engine.hint_serialized(session.id, owner_id="p2"),
            engine.reset_serialized(session.id, owner_id="p2"),
            engine.delete_serialized(session.id, owner_id="p2"),
        ]
        for call in calls:
            with pytest.raises(NotFoundError, match="^Session not found$"):
                await call

    asyncio.run(attempts())
    with pytest.raises(NotFoundError, match="^Session not found$"):
        engine.history(session.id, owner_id="p2")
    unchanged = repository.get_session(session.id)
    assert unchanged.status is SessionStatus.ACTIVE
    assert unchanged.turn_count == 0


def test_owner_can_play_and_read_history(online_config, repository, flag_service):
    engine = _engine(online_config, repository, flag_service)
    session = asyncio.run(engine.open_session(nickname="Ada", owner_id="p1"))

    asyncio.run(engine.message(session.id, "Ciao JANUS", owner_id="p1"))

    roles = [item.role for item in engine.history(session.id, owner_id="p1")]
    assert roles == ["user", "assistant"]


def test_nickname_belongs_to_the_first_player(online_config, repository, flag_service):
    engine = _engine(online_config, repository, flag_service)
    asyncio.run(engine.open_session(nickname="Ada", owner_id="p1"))

    with pytest.raises(ConflictError) as raised:
        asyncio.run(engine.open_session(nickname=" ada ", owner_id="p2"))
    assert raised.value.code == "nickname_taken"
    asyncio.run(engine.open_session(nickname="Ada", owner_id="p1"))


def test_new_session_retires_the_previous_one_and_old_tab_gets_invalid_session(
    online_config, repository, flag_service
):
    engine = _engine(online_config, repository, flag_service)
    first = asyncio.run(engine.open_session(nickname="Ada", owner_id="p1"))
    second = asyncio.run(engine.open_session(nickname="Ada", owner_id="p1"))

    assert repository.get_session(first.id).status is SessionStatus.RESET
    assert [item.id for item in repository.active_sessions_for_owner("p1")] == [second.id]
    with pytest.raises(InvalidSessionError):
        asyncio.run(engine.message(first.id, "still here?", owner_id="p1"))


def test_second_turn_while_one_is_running_is_rejected(online_config, repository, flag_service):
    llm = BlockingLLM()
    engine = _engine(online_config, repository, flag_service, llm=llm)

    async def exercise():
        session = await engine.open_session(nickname="Ada", owner_id="p1")
        first = asyncio.create_task(engine.message(session.id, "one", owner_id="p1"))
        await llm.started.wait()
        with pytest.raises(ConflictError) as raised:
            await engine.message(session.id, "two", owner_id="p1")
        assert raised.value.code == "turn_in_progress"
        with pytest.raises(NotFoundError):
            await engine.message(session.id, "intruder", owner_id="p2")
        llm.release.set()
        await first

    asyncio.run(exercise())


def test_local_mode_still_queues_concurrent_turns(loaded_config, repository, flag_service):
    llm = BlockingLLM()
    engine = _engine(loaded_config, repository, flag_service, llm=llm)

    async def exercise():
        session = engine.create_session(level_id="level_1")
        first = asyncio.create_task(engine.message(session.id, "one"))
        await llm.started.wait()
        second = asyncio.create_task(engine.message(session.id, "two"))
        await asyncio.sleep(0)
        llm.release.set()
        await asyncio.gather(first, second)

    asyncio.run(exercise())


def test_audio_is_only_served_to_its_owner(online_config, repository, flag_service, tmp_path):
    engine = _engine(online_config, repository, flag_service, tts=FileTTS(tmp_path))
    session = asyncio.run(engine.open_session(nickname="Ada", owner_id="p1"))
    turn = asyncio.run(engine.message(session.id, "parla", speak=True, owner_id="p1"))
    audio_id = turn.audio_url.rsplit("/", 1)[-1]

    assert engine.audio_path(audio_id, owner_id="p1") is not None
    assert engine.audio_path(audio_id, owner_id="p2") is None
    assert engine.audio_path("0" * 32, owner_id="p1") is None


def test_player_overview_reports_active_sessions_and_remaining_time(
    online_config, repository, flag_service
):
    engine = _engine(online_config, repository, flag_service)
    session = asyncio.run(engine.open_session(nickname="Ada", level_id="level_2", owner_id="p1"))

    overview = engine.player_overview("p1")

    assert overview["nickname"] == "Ada"
    [active] = overview["active_sessions"]
    assert active["id"] == session.id
    assert active["level_id"] == "level_2"
    assert 590 <= active["remaining_seconds"] <= 600
    assert engine.player_overview("p2") == {"nickname": None, "active_sessions": []}
```

- [ ] **Step 2: verificare che falliscano**

Run: `.venv/bin/python -m pytest tests/test_engine_online.py -q`
Expected: FAIL, con `AttributeError: 'ChallengeEngine' object has no attribute 'open_session'`.

- [ ] **Step 3: implementare in `src/janus/engine.py`**

Import: aggiungere `ConflictError` all'import da `.errors`.

In `__init__`, dopo `self._audio_by_session`:

```python
        self._audio_session: dict[str, str] = {}
```

Sostituire `create_session` con le tre funzioni seguenti (la validazione è invariata, solo estratta):

```python
    def _build_session(
        self,
        *,
        mode_id: str | None,
        level_id: str | None,
        nickname: str | None,
        owner_id: str | None,
    ) -> SessionRecord:
        selected_mode = mode_id or self.config.app.default_mode
        selected_level = level_id or self.config.app.default_level
        mode = self.config.modes.get(selected_mode)
        if mode is None:
            raise ValidationError("Unknown game mode", details={"mode_id": selected_mode})
        if selected_level not in self.config.levels:
            raise ValidationError("Unknown challenge level", details={"level_id": selected_level})

        if mode.nickname_required:
            if nickname is None:
                raise ValidationError("A nickname is required in score mode")
            normalized_nickname = self._normalize_nickname(nickname)
        else:
            # Stand sessions are anonymous by construction, even if a client submits a name.
            normalized_nickname = None

        return SessionRecord(
            id=str(uuid.uuid4()),
            mode_id=selected_mode,
            level_id=selected_level,
            nickname=normalized_nickname,
            owner_id=owner_id,
        )

    def create_session(
        self,
        *,
        mode_id: str | None = None,
        level_id: str | None = None,
        nickname: str | None = None,
        owner_id: str | None = None,
    ) -> SessionRecord:
        return self.repository.create_session(
            self._build_session(
                mode_id=mode_id, level_id=level_id, nickname=nickname, owner_id=owner_id
            )
        )

    async def open_session(
        self,
        *,
        mode_id: str | None = None,
        level_id: str | None = None,
        nickname: str | None = None,
        owner_id: str | None = None,
    ) -> SessionRecord:
        record = self._build_session(
            mode_id=mode_id, level_id=level_id, nickname=nickname, owner_id=owner_id
        )
        if owner_id is not None:
            if record.nickname is not None and self.repository.nickname_taken_by_other(
                record.nickname, owner_id
            ):
                raise ConflictError("Nickname already in use", code="nickname_taken")
            # One player cannot multiply their LLM share by opening parallel sessions.
            limit = self.config.app.online.limits.max_active_sessions
            active = self.repository.active_sessions_for_owner(owner_id)
            for stale in active[: max(0, len(active) - limit + 1)]:
                try:
                    async with self._session_lock(stale.id):
                        self._retire(stale.id)
                except NotFoundError:
                    continue
        return self.repository.create_session(record)
```

Estrarre il calcolo del tempo e riscrivere `get_session` in modo equivalente:

```python
    def _time_limit_seconds(self, session: SessionRecord) -> int:
        level_limit = self.config.levels[session.level_id].time_limit_seconds
        return min(level_limit, self.config.app.session_ttl_minutes * 60)

    @staticmethod
    def _effective_elapsed(session: SessionRecord) -> float:
        return max(
            0.0,
            (utc_now() - session.started_at).total_seconds() - session.processing_seconds,
        )

    def remaining_seconds(self, session: SessionRecord) -> int:
        return max(0, int(self._time_limit_seconds(session) - self._effective_elapsed(session)))

    def get_session(self, session_id: str) -> SessionRecord:
        session = self.repository.get_session(session_id)
        if session.status is SessionStatus.ACTIVE:
            if self._effective_elapsed(session) >= self._time_limit_seconds(session):
                expired = self.repository.set_status(session.id, SessionStatus.EXPIRED)
                self.repository.delete_messages(session.id)
                self._purge_audio(session.id)
                return expired
        return session

    def _owned(self, session_id: str, owner_id: str | None) -> SessionRecord:
        session = self.get_session(session_id)
        if owner_id is not None and session.owner_id != owner_id:
            # Same error as a missing session: ids of other players are not confirmed.
            raise NotFoundError("Session not found")
        return session

    def _retire(self, session_id: str) -> None:
        session = self.get_session(session_id)
        if session.status is not SessionStatus.ACTIVE:
            return
        self._purge_audio(session.id)
        if session.mode_id == "stand":
            self.repository.delete_session(session.id)
        else:
            self.repository.set_status(session.id, SessionStatus.RESET)
            self.repository.delete_messages(session.id)

    def _reject_if_turn_running(self, session_id: str, owner_id: str | None) -> asyncio.Lock:
        lock = self._session_lock(session_id)
        if self.config.app.online.enabled and lock.locked():
            self._owned(session_id, owner_id)
            raise ConflictError(
                "A turn is already in progress for this session", code="turn_in_progress"
            )
        return lock
```

Sostituire `get_session_serialized`:

```python
    async def get_session_serialized(
        self, session_id: str, owner_id: str | None = None
    ) -> SessionRecord:
        async with self._session_lock(session_id):
            return self._owned(session_id, owner_id)
```

Sostituire `_purge_audio`:

```python
    def _purge_audio(self, session_id: str) -> None:
        for audio_id in self._audio_by_session.pop(session_id, set()):
            self._audio_session.pop(audio_id, None)
            self.tts.delete(audio_id)
```

In `_message_locked`, subito dopo `self._audio_by_session.setdefault(session.id, set()).add(artifact.id)`:

```python
                    self._audio_session[artifact.id] = session.id
```

Sostituire `message` (firma con `owner_id`):

```python
    async def message(
        self,
        session_id: str,
        text: str,
        *,
        language: LanguagePreference = LanguagePreference.AUTO,
        modality: InputModality = InputModality.TEXT,
        speak: bool = False,
        transcript: str | None = None,
        owner_id: str | None = None,
    ) -> TurnResult:
        async with self._reject_if_turn_running(session_id, owner_id):
            self._owned(session_id, owner_id)
            return await self._message_locked(
                session_id,
                text,
                language=language,
                modality=modality,
                speak=speak,
                transcript=transcript,
            )
```

In `voice`:
- aggiungere il parametro keyword `owner_id: str | None = None`;
- sostituire `async with self._session_lock(session_id):` con `async with self._reject_if_turn_running(session_id, owner_id):`;
- come prima istruzione dentro il blocco, prima di `self._require_active(session_id)`, inserire `self._owned(session_id, owner_id)`.

Sostituire i quattro wrapper serializzati:

```python
    async def submit_serialized(
        self, session_id: str, candidate: str, owner_id: str | None = None
    ) -> SubmissionResult:
        async with self._session_lock(session_id):
            self._owned(session_id, owner_id)
            return self.submit(session_id, candidate)

    async def hint_serialized(
        self,
        session_id: str,
        language: LanguagePreference = LanguagePreference.AUTO,
        owner_id: str | None = None,
    ) -> HintResult:
        async with self._session_lock(session_id):
            self._owned(session_id, owner_id)
            return self.hint(session_id, language)

    async def reset_serialized(self, session_id: str, owner_id: str | None = None) -> SessionRecord:
        async with self._session_lock(session_id):
            self._owned(session_id, owner_id)
            return self.reset(session_id)

    async def delete_serialized(self, session_id: str, owner_id: str | None = None) -> None:
        async with self._session_lock(session_id):
            self._owned(session_id, owner_id)
            self.delete(session_id)
```

In `reset`, nella chiamata `self.create_session(...)` aggiungere `owner_id=session.owner_id,`.

In fondo alla classe:

```python
    def history(self, session_id: str, owner_id: str | None = None) -> list[MessageRecord]:
        self._owned(session_id, owner_id)
        return self.repository.list_messages(session_id)

    def audio_path(self, audio_id: str, owner_id: str | None = None) -> Path | None:
        if owner_id is not None:
            session_id = self._audio_session.get(audio_id)
            if session_id is None:
                return None
            try:
                self._owned(session_id, owner_id)
            except NotFoundError:
                return None
        return self.tts.resolve(audio_id)

    def player_overview(self, owner_id: str) -> dict[str, object]:
        active = []
        for stored in self.repository.active_sessions_for_owner(owner_id):
            session = self.get_session(stored.id)
            if session.status is SessionStatus.ACTIVE:
                active.append(
                    {
                        "id": session.id,
                        "mode_id": session.mode_id,
                        "level_id": session.level_id,
                        "started_at": session.started_at.isoformat(),
                        "remaining_seconds": self.remaining_seconds(session),
                    }
                )
        return {
            "nickname": self.repository.latest_nickname_for_owner(owner_id),
            "active_sessions": active,
        }
```

- [ ] **Step 4: verificare**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests scripts`
Expected: PASS, compresi i test esistenti di `test_engine.py` e `test_concurrency.py`.

- [ ] **Step 5: commit**

```bash
git add src/janus/engine.py tests/test_engine_online.py
git commit -m "Enforce session ownership, single active session and turn exclusivity online"
```

---

### Task 7: API online: accesso, recupero, HTTPS, health ridotto

**Files:**
- Modify: `src/janus/api.py`
- Test: `tests/test_api_online.py` (prima parte)

**Interfaces:**
- Consumes:
  - `RateLimiter` (Task 2);
  - `PlayerTokenService`, `PLAYER_TOKEN_LABEL`, `AccessCodeVerifier`, `generate_recovery_code`, `normalize_recovery_code`, `hash_recovery_code` (Task 3);
  - `create_player`, `find_player_by_recovery_hash` (Task 4);
  - `player_overview` (Task 6);
  - `effective_allowed_hosts`, `effective_cors_origins` (Task 1).
- Produces:
  - `PLAYER_COOKIE = "janus_player"` (costante di modulo);
  - endpoint `POST /api/join`, `POST /api/recover`, `GET /api/players/me`, `POST /api/logout`;
  - nested function `current_player(request) -> str | None` dentro `create_app`, usata dal Task 8;
  - `AppContainer.rate_limiter`, `.player_tokens`, `.access_codes`;
  - `/api/config` → `app.online: bool`.

- [ ] **Step 1: scrivere i test che falliscono** (`tests/test_api_online.py`)

```python
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from janus.api import create_app
from janus.config import OnlineLimits, OnlineSettings
from janus.domain import utc_now
from janus.providers.llm import MockLLMProvider
from janus.providers.stt import DisabledSTTProvider
from janus.providers.tts import DisabledTTSProvider
from janus.security import hash_recovery_code

ACCESS_CODE = "event-code-2026"


@pytest.fixture
def make_app(monkeypatch, loaded_config, repository, flag_service):
    monkeypatch.setenv("JANUS_ACCESS_CODES", ACCESS_CODE)

    def factory(*, limits=None, llm=None, tts=None, repo=None):
        online = OnlineSettings(
            enabled=True, public_host="ctf.example.com", limits=limits or OnlineLimits()
        )
        app_settings = loaded_config.app.model_copy(
            update={"default_mode": "score", "online": online}
        )
        return create_app(
            loaded_config=loaded_config.model_copy(update={"app": app_settings}),
            repository=repo or repository,
            flag_service=flag_service,
            llm_provider=llm or MockLLMProvider(),
            stt_provider=DisabledSTTProvider(),
            tts_provider=tts or DisabledTTSProvider(),
        )

    return factory


def https_client(app) -> TestClient:
    return TestClient(app, base_url="https://testserver")


def join(client: TestClient) -> str:
    response = client.post("/api/join", json={"code": ACCESS_CODE})
    assert response.status_code == 201, response.text
    return response.json()["recovery_code"]


def test_online_requires_access_codes(monkeypatch, make_app):
    monkeypatch.delenv("JANUS_ACCESS_CODES")
    from janus.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        make_app()


def test_join_sets_a_hardened_cookie_and_returns_a_recovery_code(make_app):
    client = https_client(make_app())

    response = client.post("/api/join", json={"code": ACCESS_CODE})

    assert response.status_code == 201
    assert response.json()["recovery_code"].startswith("RCV-")
    cookie = response.headers["set-cookie"].lower()
    for attribute in ("janus_player=v1.", "httponly", "secure", "samesite=strict", "path=/"):
        assert attribute in cookie
    me = client.get("/api/players/me").json()
    assert me["nickname"] is None
    assert me["active_sessions"] == []
    assert me["expires_at"]


def test_wrong_codes_are_rate_limited_per_ip(make_app):
    client = https_client(make_app(limits=OnlineLimits(auth_attempts_per_minute=3)))

    for _ in range(3):
        assert client.post("/api/join", json={"code": "wrong-code"}).status_code == 401
    blocked = client.post("/api/join", json={"code": ACCESS_CODE})

    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1


def test_successful_joins_from_one_ip_are_not_rate_limited(make_app):
    app = make_app(limits=OnlineLimits(auth_attempts_per_minute=3))

    for _ in range(15):
        join(https_client(app))



def test_recover_rejects_unknown_and_expired_codes(make_app, repository):
    client = https_client(make_app())
    assert client.post("/api/recover", json={"recovery_code": "RCV-0000-0000-0000-0000"}).status_code == 401

    repository.create_player(
        "6f1c1f4e-0c3a-4f0e-9a52-1d1b6f3f6a10",
        hash_recovery_code("1111222233334444"),
        utc_now() - timedelta(hours=13),
    )
    expired = client.post("/api/recover", json={"recovery_code": "RCV-1111-2222-3333-4444"})
    assert expired.status_code == 401



def test_logout_clears_the_cookie(make_app):
    client = https_client(make_app())
    join(client)

    assert client.post("/api/logout").status_code == 204
    assert client.get("/api/players/me").status_code == 401


def test_plain_http_is_refused_and_https_gets_hsts(make_app):
    app = make_app()

    plain = TestClient(app).get("/api/config")
    secure = https_client(app).get("/api/config")

    assert plain.status_code == 400
    assert plain.json()["error"]["code"] == "https_required"
    assert secure.headers["strict-transport-security"] == "max-age=31536000"
    assert secure.json()["app"]["online"] is True


def test_health_hides_components_from_anonymous_clients(make_app):
    app = make_app()
    anonymous = https_client(app).get("/api/health").json()
    player = https_client(app)
    join(player)

    assert set(anonymous) == {"status", "version"}
    assert "components" in player.get("/api/health").json()


def test_public_host_is_accepted_and_others_are_not(make_app):
    app = make_app()

    assert TestClient(app, base_url="https://ctf.example.com").get("/api/config").status_code == 200
    assert TestClient(app, base_url="https://evil.example").get("/api/config").status_code == 400


def test_online_endpoints_do_not_exist_in_local_mode(loaded_config, repository, flag_service):
    app = create_app(
        loaded_config=loaded_config,
        repository=repository,
        flag_service=flag_service,
        llm_provider=MockLLMProvider(),
        stt_provider=DisabledSTTProvider(),
        tts_provider=DisabledTTSProvider(),
    )
    client = TestClient(app)

    assert client.post("/api/join", json={"code": ACCESS_CODE}).status_code == 404
    assert client.get("/api/config").json()["app"]["online"] is False
    assert "components" in client.get("/api/health").json()
```

- [ ] **Step 2: verificare che falliscano**

Run: `.venv/bin/python -m pytest tests/test_api_online.py -q`
Expected: FAIL (404 su `/api/join`, cookie assente, `https_required` assente).

- [ ] **Step 3: implementare in `src/janus/api.py`: import e modelli**

Import aggiuntivi:

```python
import ipaddress
import time
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, Query, Request

from .domain import LanguagePreference, SessionRecord, utc_now
from .errors import JanusError, NotFoundError, UnauthorizedError, ValidationError
from .ratelimit import RateLimiter
from .security import (
    PLAYER_TOKEN_LABEL,
    AccessCodeVerifier,
    FlagService,
    PlayerToken,
    PlayerTokenService,
    SecretKeyStore,
    generate_recovery_code,
    hash_recovery_code,
    normalize_recovery_code,
)
```

(Gli import esistenti dalle stesse fonti vanno fusi con questi, non duplicati.)

Costante e modelli dopo `HintRequest`:

```python
PLAYER_COOKIE = "janus_player"


class JoinRequest(APIModel):
    code: str = Field(min_length=1, max_length=256)


class RecoverRequest(APIModel):
    recovery_code: str = Field(min_length=1, max_length=64)
```

`AppContainer`:

```python
class AppContainer:
    def __init__(
        self,
        config: LoadedConfig,
        engine: ChallengeEngine,
        *,
        rate_limiter: RateLimiter,
        player_tokens: PlayerTokenService,
        access_codes: AccessCodeVerifier | None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.rate_limiter = rate_limiter
        self.player_tokens = player_tokens
        self.access_codes = access_codes
```

- [ ] **Step 4: implementare in `create_app`: componenti, middleware, dipendenza**

Subito dopo la costruzione di `engine`:

```python
    online = config.app.online
    limits = online.limits
    access_codes = AccessCodeVerifier.from_env(online.access_codes_env) if online.enabled else None
    player_tokens = PlayerTokenService(flag_service.derive_subkey(PLAYER_TOKEN_LABEL))
    rate_limiter = RateLimiter()
    container = AppContainer(
        config,
        engine,
        rate_limiter=rate_limiter,
        player_tokens=player_tokens,
        access_codes=access_codes,
    )
```

(Rimuovere la vecchia riga `container = AppContainer(config, engine)`.)

`TrustedHostMiddleware` e `CORSMiddleware` diventano:

```python
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.app.effective_allowed_hosts())
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.app.effective_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )
```

Dopo la definizione di `prefix` e del middleware `kiosk_security_headers`:

```python
    def _client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def _is_loopback(request: Request) -> bool:
        try:
            return ipaddress.ip_address(_client_ip(request)).is_loopback
        except ValueError:
            return False

    def _player_token(request: Request) -> PlayerToken | None:
        return player_tokens.verify(request.cookies.get(PLAYER_COOKIE))

    def current_player(request: Request) -> str | None:
        if not online.enabled:
            return None
        token = _player_token(request)
        if token is None:
            raise UnauthorizedError("Join the event with an access code first")
        return token.player_id

    def _require_online() -> None:
        if not online.enabled:
            raise NotFoundError("Not found")

    def _issue_cookie(response: Response, player_id: str, created_at: datetime) -> datetime:
        expires_at = created_at + timedelta(hours=online.player_ttl_hours)
        expires_unix = int(expires_at.timestamp())
        response.set_cookie(
            PLAYER_COOKIE,
            player_tokens.issue(player_id, expires_unix),
            max_age=max(0, expires_unix - int(time.time())),
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
        return expires_at

    if online.enabled:

        @app.middleware("http")
        async def require_https(request: Request, call_next):
            # Loopback stays reachable over HTTP for the container healthcheck.
            if request.url.scheme != "https" and not _is_loopback(request):
                message = "HTTPS is required"
                return JSONResponse(
                    status_code=400,
                    content={
                        "detail": message,
                        "error": {"code": "https_required", "message": message, "details": {}},
                    },
                )
            response = await call_next(request)
            if request.url.scheme == "https":
                response.headers["Strict-Transport-Security"] = "max-age=31536000"
            return response
```

- [ ] **Step 5: `config` e `health`**

Nel dizionario `"app"` di `public_config` aggiungere `"online": online.enabled,`.

Firma di `health`: `async def health(request: Request) -> dict[str, Any]:`. Prima del `return` finale:

```python
        if online.enabled and not _is_loopback(request) and _player_token(request) is None:
            return {"status": status, "version": config.app.version}
```

- [ ] **Step 6: endpoint di accesso** (dopo `health`)

```python
    @app.post(f"{prefix}/join", status_code=201)
    async def join(payload: JoinRequest, request: Request, response: Response) -> dict[str, Any]:
        _require_online()
        auth_key = f"auth:{_client_ip(request)}"
        # Only failures count: many players behind one NAT must be able to join together.
        rate_limiter.check(auth_key, limit=limits.auth_attempts_per_minute, window_seconds=60)
        if not access_codes.verify(payload.code):
            rate_limiter.record(auth_key, window_seconds=60)
            raise UnauthorizedError("Invalid access code")
        player_id = str(uuid.uuid4())
        recovery_code = generate_recovery_code()
        created_at = utc_now()
        repo.create_player(
            player_id, hash_recovery_code(normalize_recovery_code(recovery_code)), created_at
        )
        expires_at = _issue_cookie(response, player_id, created_at)
        return {"recovery_code": recovery_code, "expires_at": expires_at.isoformat()}

    @app.post(f"{prefix}/recover")
    async def recover(
        payload: RecoverRequest, request: Request, response: Response
    ) -> dict[str, Any]:
        _require_online()
        auth_key = f"auth:{_client_ip(request)}"
        rate_limiter.check(auth_key, limit=limits.auth_attempts_per_minute, window_seconds=60)
        normalized = normalize_recovery_code(payload.recovery_code)
        player = repo.find_player_by_recovery_hash(hash_recovery_code(normalized)) if normalized else None
        if player is None or player[1] + timedelta(hours=online.player_ttl_hours) <= utc_now():
            rate_limiter.record(auth_key, window_seconds=60)
            raise UnauthorizedError("Invalid or expired recovery code")
        expires_at = _issue_cookie(response, player[0], player[1])
        return {"expires_at": expires_at.isoformat()}

    @app.get(f"{prefix}/players/me")
    async def player_me(request: Request) -> dict[str, Any]:
        _require_online()
        token = _player_token(request)
        if token is None:
            raise UnauthorizedError("Join the event with an access code first")
        return {
            **engine.player_overview(token.player_id),
            "expires_at": datetime.fromtimestamp(token.expires_at, UTC).isoformat(),
        }

    @app.post(f"{prefix}/logout", status_code=204)
    async def logout() -> Response:
        _require_online()
        result = Response(status_code=204)
        result.delete_cookie(PLAYER_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return result
```

La riga `player = repo.find_player_by_recovery_hash(...)` supera i 100 caratteri: spezzarla in due righe, con `player = None` seguito da `if normalized: player = repo.find_player_by_recovery_hash(hash_recovery_code(normalized))`.

- [ ] **Step 7: verificare**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests scripts`
Expected: PASS.

- [ ] **Step 8: commit**

```bash
git add src/janus/api.py tests/test_api_online.py
git commit -m "Add online join, recovery, logout, HTTPS enforcement and reduced health"
```

---

### Task 8: API online: proprietà sugli endpoint di sessione, cronologia, audio, limiti

**Files:**
- Modify: `src/janus/api.py`
- Test: `tests/test_api_online.py` (seconda parte)

**Interfaces:**
- Consumes: `current_player`, `rate_limiter`, `limits` (Task 7); metodi del motore con `owner_id` (Task 6).
- Produces: `GET /api/sessions/{id}/messages`, che restituisce `{"session_id": str, "messages": [{"role", "content", "language", "modality", "created_at"}]}`.

- [ ] **Step 1: scrivere i test che falliscono** (in coda a `tests/test_api_online.py`)

```python
import uuid

from janus.domain import AudioArtifact, HealthComponent
from janus.repository import SQLiteRepository


class FileTTS:
    def __init__(self, directory):
        self.output_dir = directory

    async def synthesize(self, text, language):
        audio_id = uuid.uuid4().hex
        (self.output_dir / f"{audio_id}.wav").write_bytes(b"RIFF" + b"\0" * 100)
        return AudioArtifact(id=audio_id)

    def resolve(self, audio_id):
        path = self.output_dir / f"{audio_id}.wav"
        return path if path.is_file() else None

    def delete(self, audio_id):
        (self.output_dir / f"{audio_id}.wav").unlink(missing_ok=True)

    async def health(self):
        return HealthComponent(available=True, detail="file tts")


def _player(app, nickname):
    client = https_client(app)
    join(client)
    session = client.post("/api/sessions", json={"level_id": "level_1", "nickname": nickname})
    assert session.status_code == 201, session.text
    return client, session.json()


def test_every_session_endpoint_hides_foreign_sessions(make_app, tmp_path):
    app = make_app(tts=FileTTS(tmp_path))
    alice, session = _player(app, "Alice")
    bob, _ = _player(app, "Bob")
    sid = session["id"]
    turn = alice.post(f"/api/sessions/{sid}/messages", json={"text": "ciao", "speak": True}).json()
    audio_path = turn["audio_url"]
    missing = bob.get(f"/api/sessions/{uuid.uuid4()}").json()

    attempts = [
        bob.get(f"/api/sessions/{sid}"),
        bob.get(f"/api/sessions/{sid}/messages"),
        bob.post(f"/api/sessions/{sid}/messages", json={"text": "hijack"}),
        bob.post(f"/api/sessions/{sid}/voice", content=b"RIFF....", headers={"content-type": "audio/wav"}),
        bob.post(f"/api/sessions/{sid}/submit", json={"flag": "RH26{x}"}),
        bob.post(f"/api/sessions/{sid}/hint", json={}),
        bob.post(f"/api/sessions/{sid}/reset"),
        bob.delete(f"/api/sessions/{sid}"),
    ]
    for response in attempts:
        assert response.status_code == 404, response.request.url
        assert response.json() == missing
    assert bob.get(audio_path).status_code == 404

    mine = alice.get(f"/api/sessions/{sid}").json()
    assert mine["status"] == "active" and mine["turn_count"] == 1
    assert "owner_id" not in mine
    assert alice.get(audio_path).status_code == 200
    history = alice.get(f"/api/sessions/{sid}/messages").json()
    assert [item["role"] for item in history["messages"]] == ["user", "assistant"]


def test_nickname_taken_by_another_player(make_app):
    app = make_app()
    _player(app, "Ada")
    other = https_client(app)
    join(other)

    response = other.post("/api/sessions", json={"level_id": "level_1", "nickname": "ADA"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "nickname_taken"


def test_turn_and_session_rate_limits(make_app):
    app = make_app(limits=OnlineLimits(turns_per_minute=2, sessions_per_hour=2))
    client, session = _player(app, "Ada")
    sid = session["id"]

    for _ in range(2):
        assert client.post(f"/api/sessions/{sid}/messages", json={"text": "x"}).status_code == 200
    limited = client.post(f"/api/sessions/{sid}/messages", json={"text": "x"})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"

    assert client.post("/api/sessions", json={"level_id": "level_1", "nickname": "Ada"}).status_code == 201
    assert client.post("/api/sessions", json={"level_id": "level_1", "nickname": "Ada"}).status_code == 429


def test_one_active_session_per_player(make_app):
    app = make_app()
    client, first = _player(app, "Ada")

    second = client.post("/api/sessions", json={"level_id": "level_2", "nickname": "Ada"}).json()

    assert client.get(f"/api/sessions/{first['id']}").json()["status"] == "reset"
    assert [item["id"] for item in client.get("/api/players/me").json()["active_sessions"]] == [
        second["id"]
    ]


def test_restart_drops_active_sessions_but_keeps_the_player(make_app, tmp_path):
    database = tmp_path / "restart.sqlite3"
    first_repo = SQLiteRepository(database)
    client, session = _player(make_app(repo=first_repo), "Ada")
    first_repo.close()

    restarted = make_app(repo=SQLiteRepository(database))
    survivor = https_client(restarted)
    survivor.cookies = client.cookies

    assert survivor.get("/api/players/me").json()["active_sessions"] == []
    assert survivor.get(f"/api/sessions/{session['id']}").status_code == 404


def test_recover_restores_the_same_player_from_a_new_device(make_app):
    app = make_app()
    first = https_client(app)
    recovery = join(first)
    session = first.post("/api/sessions", json={"level_id": "level_1", "nickname": "Ada"}).json()

    second = https_client(app)
    typed = recovery.lower().replace("-", " ").removeprefix("rcv ")
    assert second.post("/api/recover", json={"recovery_code": typed}).status_code == 200
    me = second.get("/api/players/me").json()

    assert me["nickname"] == "Ada"
    assert [item["id"] for item in me["active_sessions"]] == [session["id"]]
    assert second.get(f"/api/sessions/{session['id']}").status_code == 200


def test_players_me_and_sessions_require_the_cookie(make_app):
    client = https_client(make_app())
    forged = {"Cookie": "janus_player=v1.forged.123.sig"}

    assert client.get("/api/players/me").status_code == 401
    assert client.post("/api/sessions", json={"nickname": "Ada"}).status_code == 401
    assert client.get("/api/players/me", headers=forged).status_code == 401
```

- [ ] **Step 2: verificare che falliscano**

Run: `.venv/bin/python -m pytest tests/test_api_online.py -q`
Expected: FAIL (le sessioni non hanno proprietario; Bob riceve 200).

- [ ] **Step 3: implementare in `src/janus/api.py`**

Aggiungere, accanto agli altri helper dentro `create_app`:

```python
    def _limit_turn(owner: str | None) -> None:
        if owner is not None:
            rate_limiter.hit(f"turn:{owner}", limit=limits.turns_per_minute, window_seconds=60)

    def _limit_session(owner: str | None) -> None:
        if owner is not None:
            rate_limiter.hit(
                f"session:{owner}", limit=limits.sessions_per_hour, window_seconds=3600
            )
```

Riscrivere gli endpoint di sessione. `Depends` va nel valore di default e non in un'annotazione: con `from __future__ import annotations` FastAPI non vedrebbe le closure locali.

```python
    @app.post(f"{prefix}/sessions", response_model=SessionRecord, status_code=201)
    async def create_session(
        payload: CreateSessionRequest, owner: str | None = Depends(current_player)
    ) -> SessionRecord:
        requested_mode = payload.mode_id or config.app.default_mode
        if requested_mode != config.app.default_mode:
            raise ValidationError(
                "Game mode is fixed by the operator at startup",
                details={"active_mode": config.app.default_mode},
            )
        _limit_session(owner)
        return await engine.open_session(
            mode_id=config.app.default_mode,
            level_id=payload.level_id,
            nickname=payload.nickname,
            owner_id=owner,
        )

    @app.get(f"{prefix}/sessions/{{session_id}}", response_model=SessionRecord)
    async def get_session(
        session_id: str, owner: str | None = Depends(current_player)
    ) -> SessionRecord:
        return await engine.get_session_serialized(session_id, owner_id=owner)

    @app.delete(f"{prefix}/sessions/{{session_id}}", status_code=204)
    async def delete_session(
        session_id: str, owner: str | None = Depends(current_player)
    ) -> Response:
        await engine.delete_serialized(session_id, owner_id=owner)
        return Response(status_code=204)

    @app.post(f"{prefix}/sessions/{{session_id}}/messages")
    async def send_message(
        session_id: str, payload: MessageRequest, owner: str | None = Depends(current_player)
    ) -> dict[str, Any]:
        _limit_turn(owner)
        return (
            await engine.message(
                session_id,
                payload.text,
                language=payload.language,
                speak=payload.speak,
                owner_id=owner,
            )
        ).model_dump(mode="json")

    @app.get(f"{prefix}/sessions/{{session_id}}/messages")
    async def session_history(
        session_id: str, owner: str | None = Depends(current_player)
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "messages": [
                {
                    "role": item.role,
                    "content": item.content,
                    "language": item.language.value,
                    "modality": item.modality.value,
                    "created_at": item.created_at.isoformat(),
                }
                for item in engine.history(session_id, owner_id=owner)
            ],
        }
```

In `send_voice`:
- aggiungere il parametro `owner: str | None = Depends(current_player),` dopo `speak`;
- come prima istruzione del corpo, `_limit_turn(owner)`;
- passare `owner_id=owner` a `engine.voice(...)`.

`submit_flag`, `request_hint`, `reset_session`:

```python
    @app.post(f"{prefix}/sessions/{{session_id}}/submit")
    async def submit_flag(
        session_id: str, payload: SubmitRequest, owner: str | None = Depends(current_player)
    ) -> dict[str, Any]:
        result = await engine.submit_serialized(session_id, payload.flag, owner_id=owner)
        return result.model_dump(mode="json")

    @app.post(f"{prefix}/sessions/{{session_id}}/hint")
    async def request_hint(
        session_id: str,
        payload: HintRequest | None = None,
        language: Annotated[LanguagePreference, Query()] = LanguagePreference.AUTO,
        owner: str | None = Depends(current_player),
    ) -> dict[str, Any]:
        selected_language = payload.language if payload is not None else language
        return (
            await engine.hint_serialized(session_id, selected_language, owner_id=owner)
        ).model_dump(mode="json")

    @app.post(f"{prefix}/sessions/{{session_id}}/reset", response_model=SessionRecord)
    async def reset_session(
        session_id: str, owner: str | None = Depends(current_player)
    ) -> SessionRecord:
        _limit_session(owner)
        return await engine.reset_serialized(session_id, owner_id=owner)
```

`audio`:

```python
    @app.get(f"{prefix}/audio/{{audio_id}}")
    async def audio(audio_id: str, owner: str | None = Depends(current_player)) -> FileResponse:
        path = engine.audio_path(audio_id, owner_id=owner)
        if path is None:
            raise NotFoundError("Audio artifact not found")
        return FileResponse(path, media_type="audio/wav", filename=f"janus-{audio_id}.wav")
```

- [ ] **Step 4: verificare**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests scripts`
Expected: PASS.

- [ ] **Step 5: commit**

```bash
git add src/janus/api.py tests/test_api_online.py
git commit -m "Bind online sessions, history and audio to their player and apply rate limits"
```

---

### Task 9: spazzino periodico

**Files:**
- Modify: `src/janus/engine.py` (helper)
- Create: `src/janus/sweeper.py`
- Modify: `src/janus/api.py` (lifespan)
- Test: `tests/test_sweeper.py`

**Interfaces:**
- Consumes: `RateLimiter.prune` (Task 2); `active_session_ids`, `delete_players_created_before` (Task 4); `_audio_session` (Task 6).
- Produces:
  - `ChallengeEngine.expire_if_due(session_id) -> bool` (async);
  - `.prune_locks() -> int`;
  - `.sweep_orphan_audio(max_age_seconds: float, now: float | None = None) -> int`;
  - `SweepReport` (dataclass: `expired`, `locks_pruned`, `audio_removed`, `limiter_keys_pruned`, `players_removed`);
  - `SessionSweeper(engine, *, rate_limiter, interval_seconds=60.0, audio_max_age_seconds=1800.0)`, con `.sweep_once() -> SweepReport` (async) e `.run()` (async);
  - `AppContainer.sweeper`.

- [ ] **Step 1: scrivere i test che falliscono** (`tests/test_sweeper.py`)

```python
from __future__ import annotations

import asyncio
import os
import time
from datetime import timedelta

from fastapi.testclient import TestClient

from janus.api import create_app
from janus.config import OnlineSettings
from janus.domain import SessionRecord, SessionStatus, utc_now
from janus.engine import ChallengeEngine
from janus.providers.llm import MockLLMProvider
from janus.providers.stt import DisabledSTTProvider
from janus.providers.tts import DisabledTTSProvider
from janus.ratelimit import RateLimiter
from janus.sweeper import SessionSweeper


class DirTTS(DisabledTTSProvider):
    def __init__(self, directory):
        super().__init__()
        self.output_dir = directory

    def delete(self, audio_id):
        (self.output_dir / f"{audio_id}.wav").unlink(missing_ok=True)


def _engine(config, repository, flag_service, tts=None):
    return ChallengeEngine(
        config, repository, flag_service, MockLLMProvider(), DisabledSTTProvider(),
        tts or DisabledTTSProvider(),
    )


def test_sweep_expires_abandoned_sessions_and_prunes_locks(loaded_config, repository, flag_service):
    engine = _engine(loaded_config, repository, flag_service)
    abandoned = repository.create_session(
        SessionRecord(
            id="old", mode_id="score", level_id="level_1", nickname="Ada",
            started_at=utc_now() - timedelta(hours=2),
        )
    )
    fresh = engine.create_session(level_id="level_1")
    asyncio.run(engine.get_session_serialized(fresh.id))
    asyncio.run(engine.get_session_serialized(abandoned.id))

    report = asyncio.run(SessionSweeper(engine, rate_limiter=RateLimiter()).sweep_once())

    assert report.expired == 0  # the read above already expired it lazily
    assert repository.get_session("old").status is SessionStatus.EXPIRED
    assert report.locks_pruned == 1
    assert set(engine._session_locks) == {fresh.id}


def test_sweep_expires_sessions_nobody_reads(loaded_config, repository, flag_service):
    engine = _engine(loaded_config, repository, flag_service)
    repository.create_session(
        SessionRecord(
            id="ghost", mode_id="score", level_id="level_1", nickname="Ada",
            started_at=utc_now() - timedelta(hours=2),
        )
    )

    report = asyncio.run(SessionSweeper(engine, rate_limiter=RateLimiter()).sweep_once())

    assert report.expired == 1
    assert repository.get_session("ghost").status is SessionStatus.EXPIRED


def test_sweep_removes_old_orphan_audio_only(loaded_config, repository, flag_service, tmp_path):
    engine = _engine(loaded_config, repository, flag_service, tts=DirTTS(tmp_path))
    old_orphan = tmp_path / ("a" * 32 + ".wav")
    new_orphan = tmp_path / ("b" * 32 + ".wav")
    referenced = tmp_path / ("c" * 32 + ".wav")
    for path in (old_orphan, new_orphan, referenced):
        path.write_bytes(b"RIFF")
    stale = time.time() - 3600
    os.utime(old_orphan, (stale, stale))
    os.utime(referenced, (stale, stale))
    engine._audio_session["c" * 32] = "some-session"

    assert engine.sweep_orphan_audio(1800) == 1
    assert not old_orphan.exists() and new_orphan.exists() and referenced.exists()


def test_sweep_drops_expired_players_only_online(loaded_config, repository, flag_service):
    online = loaded_config.model_copy(
        update={
            "app": loaded_config.app.model_copy(
                update={"online": OnlineSettings(enabled=True, public_host="ctf.example.com")}
            )
        }
    )
    repository.create_player("p-old", "a" * 64, utc_now() - timedelta(hours=13))
    repository.create_player("p-new", "b" * 64, utc_now())

    local_report = asyncio.run(
        SessionSweeper(_engine(loaded_config, repository, flag_service), rate_limiter=RateLimiter()).sweep_once()
    )
    online_report = asyncio.run(
        SessionSweeper(_engine(online, repository, flag_service), rate_limiter=RateLimiter()).sweep_once()
    )

    assert local_report.players_removed == 0
    assert online_report.players_removed == 1
    assert repository.find_player_by_recovery_hash("b" * 64) is not None


def test_app_lifespan_starts_and_stops_the_sweeper(loaded_config, repository, flag_service):
    app = create_app(
        loaded_config=loaded_config,
        repository=repository,
        flag_service=flag_service,
        llm_provider=MockLLMProvider(),
        stt_provider=DisabledSTTProvider(),
        tts_provider=DisabledTTSProvider(),
    )

    with TestClient(app) as client:
        assert client.get("/api/config").status_code == 200
        assert app.state.janus.sweeper.running is True
    assert app.state.janus.sweeper.running is False
```

- [ ] **Step 2: verificare che falliscano**

Run: `.venv/bin/python -m pytest tests/test_sweeper.py -q`
Expected: FAIL, con `ModuleNotFoundError: No module named 'janus.sweeper'`.

- [ ] **Step 3: helper nel motore** (`src/janus/engine.py`, in fondo alla classe; importare `time` già presente)

```python
    async def expire_if_due(self, session_id: str) -> bool:
        try:
            async with self._session_lock(session_id):
                if self.repository.get_session(session_id).status is not SessionStatus.ACTIVE:
                    return False
                return self.get_session(session_id).status is SessionStatus.EXPIRED
        except NotFoundError:
            return False

    def prune_locks(self) -> int:
        removed = 0
        for session_id, lock in list(self._session_locks.items()):
            if lock.locked():
                continue
            try:
                active = self.repository.get_session(session_id).status is SessionStatus.ACTIVE
            except NotFoundError:
                active = False
            if not active:
                # Safe: acquiring a lock never yields between lookup and acquire,
                # and a free lock has no waiters.
                del self._session_locks[session_id]
                removed += 1
        return removed

    def sweep_orphan_audio(self, max_age_seconds: float, now: float | None = None) -> int:
        output_dir = getattr(self.tts, "output_dir", None)
        if output_dir is None:
            return 0
        horizon = (time.time() if now is None else now) - max_age_seconds
        removed = 0
        for path in Path(output_dir).glob("*.wav"):
            if path.stem in self._audio_session:
                continue
            try:
                stale = path.stat().st_mtime < horizon
            except OSError:
                continue
            if stale:
                self.tts.delete(path.stem)
                removed += 1
        return removed
```

- [ ] **Step 4: creare `src/janus/sweeper.py`**

```python
"""Periodic cleanup so abandoned sessions do not keep transcripts, audio or memory."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from .domain import utc_now
from .engine import ChallengeEngine
from .ratelimit import RateLimiter

logger = logging.getLogger("janus.sweeper")


@dataclass
class SweepReport:
    expired: int = 0
    locks_pruned: int = 0
    audio_removed: int = 0
    limiter_keys_pruned: int = 0
    players_removed: int = 0


class SessionSweeper:
    def __init__(
        self,
        engine: ChallengeEngine,
        *,
        rate_limiter: RateLimiter,
        interval_seconds: float = 60.0,
        audio_max_age_seconds: float = 1800.0,
    ) -> None:
        self.engine = engine
        self.rate_limiter = rate_limiter
        self.interval_seconds = interval_seconds
        self.audio_max_age_seconds = audio_max_age_seconds
        self.running = False

    async def sweep_once(self) -> SweepReport:
        report = SweepReport()
        for session_id in self.engine.repository.active_session_ids():
            if await self.engine.expire_if_due(session_id):
                report.expired += 1
        report.locks_pruned = self.engine.prune_locks()
        report.audio_removed = self.engine.sweep_orphan_audio(self.audio_max_age_seconds)
        report.limiter_keys_pruned = self.rate_limiter.prune(3600)
        online = self.engine.config.app.online
        if online.enabled:
            cutoff = utc_now() - timedelta(hours=online.player_ttl_hours)
            report.players_removed = self.engine.repository.delete_players_created_before(cutoff)
        return report

    async def run(self) -> None:
        self.running = True
        try:
            while True:
                await asyncio.sleep(self.interval_seconds)
                try:
                    await self.sweep_once()
                except Exception:  # noqa: BLE001 -- cleanup must never stop the server.
                    logger.exception("Session sweep failed")
        finally:
            self.running = False
```

- [ ] **Step 5: lifespan in `src/janus/api.py`**

Import: `import contextlib`, `from contextlib import asynccontextmanager`, `from .sweeper import SessionSweeper`. Dopo la creazione di `rate_limiter` e prima di `container = AppContainer(...)`:

```python
    sweeper = SessionSweeper(engine, rate_limiter=rate_limiter)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(sweeper.run())
        await asyncio.sleep(0)
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
```

Aggiungere `sweeper=sweeper` ad `AppContainer`, con il nuovo parametro keyword `sweeper: SessionSweeper` e `self.sweeper = sweeper`. Passare `lifespan=lifespan` a `FastAPI(...)`.

- [ ] **Step 6: verificare**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests scripts`
Expected: PASS.

- [ ] **Step 7: commit**

```bash
git add src/janus/engine.py src/janus/sweeper.py src/janus/api.py tests/test_sweeper.py
git commit -m "Sweep expired sessions, orphan audio, idle locks and expired players"
```

---

### Task 10: test di isolamento in concorrenza (50 giocatori)

**Files:**
- Create: `tests/test_isolation.py`

**Interfaces:**
- Consumes: l'intera API online (Task 7–8), `FlagService.derive` (esistente).

- [ ] **Step 1: scrivere il test**

```python
"""Fifty concurrent online players: no prompt ever carries another session's context."""

from __future__ import annotations

import asyncio
import re

import httpx

from janus.api import create_app
from janus.config import OnlineLimits, OnlineSettings
from janus.domain import HealthComponent
from janus.providers.stt import DisabledSTTProvider
from janus.providers.tts import DisabledTTSProvider

PLAYERS = 50
ACCESS_CODE = "event-code-2026"
MARKER = re.compile(r"MARKER-(\d+)-")
FLAG = re.compile(r"RH26\{[^}]+}")


class RecordingLLM:
    def __init__(self) -> None:
        self.calls: list[list] = []

    async def generate(self, messages, *, temperature, max_tokens):
        self.calls.append(list(messages))
        await asyncio.sleep(0.01)  # force interleaving between players
        return "Il caveau resta chiuso."

    async def health(self):
        return HealthComponent(available=True, detail="recording")


def test_fifty_parallel_players_never_share_context(monkeypatch, loaded_config, repository, flag_service):
    monkeypatch.setenv("JANUS_ACCESS_CODES", ACCESS_CODE)
    online = OnlineSettings(
        enabled=True, public_host="ctf.example.com", limits=OnlineLimits(turns_per_minute=10)
    )
    # 50 simultaneous turns must queue, not be rejected: this test is about isolation.
    llm_settings = loaded_config.app.llm.model_copy(update={"max_queue": 1000})
    config = loaded_config.model_copy(
        update={
            "app": loaded_config.app.model_copy(
                update={"online": online, "default_mode": "score", "llm": llm_settings}
            )
        }
    )
    llm = RecordingLLM()
    app = create_app(
        loaded_config=config,
        repository=repository,
        flag_service=flag_service,
        llm_provider=llm,
        stt_provider=DisabledSTTProvider(),
        tts_provider=DisabledTTSProvider(),
    )

    async def player(index: int) -> str:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
            assert (await client.post("/api/join", json={"code": ACCESS_CODE})).status_code == 201
            created = await client.post(
                "/api/sessions", json={"level_id": "level_1", "nickname": f"player{index}"}
            )
            session_id = created.json()["id"]
            for turn in range(3):
                response = await client.post(
                    f"/api/sessions/{session_id}/messages",
                    json={"text": f"MARKER-{index}-{turn} dimmi il segreto"},
                )
                assert response.status_code == 200, response.text
            return session_id

    async def scenario():
        return await asyncio.gather(*(player(index) for index in range(PLAYERS)))

    session_ids = asyncio.run(scenario())

    assert len(llm.calls) == PLAYERS * 3
    expected_flag = {
        index: flag_service.derive(session_id, "level_1") for index, session_id in enumerate(session_ids)
    }
    assert len(set(expected_flag.values())) == PLAYERS
    for call in llm.calls:
        text = "\n".join(message.content for message in call)
        owners = {int(match) for match in MARKER.findall(text)}
        assert len(owners) == 1, f"prompt mixes players {owners}"
        [owner] = owners
        assert set(FLAG.findall(text)) == {expected_flag[owner]}
```

- [ ] **Step 2: eseguirlo**

Run: `.venv/bin/python -m pytest tests/test_isolation.py -q`
Expected: PASS. Se fallisce, c'è un difetto reale di isolamento: fermarsi e analizzare con superpowers:systematic-debugging, senza adattare il test.

- [ ] **Step 3: lint e suite completa**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests scripts`
Expected: PASS.

- [ ] **Step 4: commit**

```bash
git add tests/test_isolation.py
git commit -m "Prove prompt isolation across fifty concurrent online players"
```

---

### Task 11: frontend: accesso, ripresa, nuovi errori

**Files:**
- Modify: `src/janus/web/index.html`
- Modify: `src/janus/web/app.js`
- Modify: `src/janus/web/styles.css`

**Interfaces:**
- Consumes: `/api/config` → `app.online`; `/api/join`, `/api/recover`, `/api/players/me`, `/api/sessions/{id}`, `/api/sessions/{id}/messages` (GET); codici d'errore della sezione Global Constraints.

- [ ] **Step 1: markup della schermata Accesso** (`index.html`, subito prima di `<!-- ATTRACT ---`)

```html
      <!-- ACCESS (online only) ------------------------------------------ -->
      <section class="screen screen-access" id="screenAccess" data-screen="access" aria-labelledby="accessTitle">
        <div class="section-heading">
          <p class="eyebrow">ACCESSO ONLINE</p>
          <h2 id="accessTitle">Entra nella sfida.</h2>
          <p>I messaggi che invii vengono elaborati dal modello IA configurato per l'evento.</p>
        </div>

        <div class="segmented access-tabs" id="accessTabs" role="tablist" aria-label="Modalità di accesso">
          <button type="button" role="tab" id="accessTabJoin" aria-selected="true" aria-controls="joinForm">CODICE EVENTO</button>
          <button type="button" role="tab" id="accessTabRecover" aria-selected="false" aria-controls="recoverForm">CODICE DI RECUPERO</button>
        </div>

        <form class="setup-form" id="joinForm" novalidate>
          <div class="setup-block">
            <div class="field-heading"><label for="accessCodeInput">CODICE EVENTO</label><span>fornito dagli organizzatori</span></div>
            <div class="input-frame">
              <span aria-hidden="true">&gt;</span>
              <input id="accessCodeInput" type="text" maxlength="256" autocomplete="off" spellcheck="false" placeholder="Inserisci il codice">
            </div>
            <p class="field-error" id="joinError" role="alert"></p>
          </div>
          <button class="btn btn-primary btn-wide" id="joinButton" type="submit"><span>ENTRA</span><span class="btn-arrow" aria-hidden="true">→</span></button>
        </form>

        <form class="setup-form" id="recoverForm" novalidate hidden>
          <div class="setup-block">
            <div class="field-heading"><label for="recoveryCodeInput">CODICE DI RECUPERO</label><span>formato RCV-XXXX-XXXX-XXXX-XXXX</span></div>
            <div class="input-frame">
              <span aria-hidden="true">&gt;</span>
              <input id="recoveryCodeInput" type="text" maxlength="64" autocomplete="off" spellcheck="false" placeholder="RCV-…">
            </div>
            <p class="field-error" id="recoverError" role="alert"></p>
          </div>
          <button class="btn btn-primary btn-wide" id="recoverButton" type="submit"><span>RIPRENDI</span><span class="btn-arrow" aria-hidden="true">→</span></button>
        </form>

        <div class="recovery-card" id="recoveryCard" hidden>
          <p class="eyebrow">IL TUO CODICE DI RECUPERO</p>
          <code id="recoveryCodeValue"></code>
          <p>Conservalo: serve per riprendere la partita da un altro dispositivo. Non verrà mostrato di nuovo.</p>
          <div class="attract-actions">
            <button class="btn btn-ghost" id="copyRecoveryButton" type="button">COPIA</button>
            <button class="btn btn-primary" id="recoverySavedButton" type="button"><span>L'HO SALVATO</span><span class="btn-arrow" aria-hidden="true">→</span></button>
          </div>
        </div>
      </section>
```

Nel blocco attract, subito dopo il `<div class="attract-actions">…</div>`:

```html
          <div class="resume-banner" id="resumeBanner" role="status" hidden>
            <p id="resumeText"></p>
            <div class="attract-actions">
              <button class="btn btn-primary" id="resumeButton" type="button"><span>RIPRENDI</span></button>
              <button class="btn btn-ghost" id="abandonButton" type="button">ABBANDONA</button>
            </div>
          </div>
```

- [ ] **Step 2: stili** (in fondo a `styles.css`, prima di `@media (prefers-reduced-motion: reduce)`)

```css
.access-tabs { margin-bottom: 1.25rem; }
.access-tabs button { cursor: pointer; }
.access-tabs button[aria-selected="true"] { background: var(--green); color: #03100d; }
.recovery-card { display: grid; gap: 1rem; max-width: 40rem; }
.recovery-card code {
  font-family: var(--font-mono);
  font-size: clamp(1.1rem, 4vw, 1.8rem);
  letter-spacing: 0.08em;
  padding: 1rem;
  border: 1px solid var(--line-bright);
  background: var(--panel);
  user-select: all;
  overflow-wrap: anywhere;
}
.resume-banner {
  margin-top: 1.5rem;
  padding: 1rem;
  border: 1px solid var(--line-bright);
  background: var(--panel);
  display: grid;
  gap: 0.75rem;
}
```

- [ ] **Step 3: API client** (`app.js`, dentro l'oggetto `API`, dopo `leaderboard`)

```js
    join(code) {
      return this.request("/join", { method: "POST", body: { code }, timeoutMs: 8_000 });
    },

    recover(recoveryCode) {
      return this.request("/recover", { method: "POST", body: { recovery_code: recoveryCode }, timeoutMs: 8_000 });
    },

    me() {
      return this.request("/players/me", { timeoutMs: 6_000 });
    },

    getSession(sessionId) {
      return this.request(`/sessions/${encodeURIComponent(sessionId)}`, { timeoutMs: 6_000 });
    },

    history(sessionId) {
      return this.request(`/sessions/${encodeURIComponent(sessionId)}/messages`, { timeoutMs: 8_000 });
    },
```

- [ ] **Step 4: stato, elementi, eventi**

In `state` aggiungere: `online: false, resumeCandidate: null, composerLockedUntil: 0,`.

In `cacheElements` aggiungere: `accessTabs: $("#accessTabs"), accessTabJoin: $("#accessTabJoin"), accessTabRecover: $("#accessTabRecover"), joinForm: $("#joinForm"), accessCodeInput: $("#accessCodeInput"), joinError: $("#joinError"), joinButton: $("#joinButton"), recoverForm: $("#recoverForm"), recoveryCodeInput: $("#recoveryCodeInput"), recoverError: $("#recoverError"), recoverButton: $("#recoverButton"), recoveryCard: $("#recoveryCard"), recoveryCodeValue: $("#recoveryCodeValue"), copyRecoveryButton: $("#copyRecoveryButton"), recoverySavedButton: $("#recoverySavedButton"), resumeBanner: $("#resumeBanner"), resumeText: $("#resumeText"), resumeButton: $("#resumeButton"), abandonButton: $("#abandonButton"),`.

In `bindEvents` aggiungere:

```js
    els.joinForm.addEventListener("submit", event => { void submitJoin(event); });
    els.recoverForm.addEventListener("submit", event => { void submitRecover(event); });
    els.accessTabJoin.addEventListener("click", () => selectAccessTab("join"));
    els.accessTabRecover.addEventListener("click", () => selectAccessTab("recover"));
    els.copyRecoveryButton.addEventListener("click", () => { void copyRecoveryCode(); });
    els.recoverySavedButton.addEventListener("click", () => { void enterOnline(); });
    els.resumeButton.addEventListener("click", () => { void resumeSession(); });
    els.abandonButton.addEventListener("click", () => { void abandonResumable(); });
```

- [ ] **Step 5: avvio e funzioni online**

In `init`, sostituire `showScreen("attract");` (quello subito dopo `els.app.hidden = false;`) con:

```js
    state.online = Boolean(state.config.app?.online);
    if (state.online) await enterOnline();
    else showScreen("attract");
```

Aggiungere queste funzioni (ad esempio subito prima di `function cacheElements`):

```js
  function errorCode(error) {
    return error?.payload?.error?.code || "";
  }

  function retryAfterSeconds(error) {
    return Math.max(1, Number(error?.payload?.error?.details?.retry_after) || 5);
  }

  function selectAccessTab(tab) {
    const join = tab === "join";
    els.accessTabJoin.setAttribute("aria-selected", String(join));
    els.accessTabRecover.setAttribute("aria-selected", String(!join));
    els.joinForm.hidden = !join;
    els.recoverForm.hidden = join;
    (join ? els.accessCodeInput : els.recoveryCodeInput).focus();
  }

  function showAccess(message) {
    els.recoveryCard.hidden = true;
    els.accessTabs.hidden = false;
    els.joinError.textContent = "";
    els.recoverError.textContent = "";
    selectAccessTab("join");
    showScreen("access");
    if (message) toast(message, "warning", 6000);
  }

  function accessErrorMessage(error) {
    if (error.status === 429) return `Troppi tentativi: riprova tra ${retryAfterSeconds(error)} s.`;
    if (error.status === 401) return "Codice non valido.";
    return error.message;
  }

  async function enterOnline() {
    try {
      adoptPlayer(await API.me());
      showScreen("attract");
    } catch (error) {
      if (error.status === 401) showAccess();
      else {
        showScreen("attract");
        toast(error.message, "error", 6000);
      }
    }
  }

  function adoptPlayer(me) {
    if (me.nickname) state.latestNickname = me.nickname;
    const active = (me.active_sessions || [])[0] || null;
    state.resumeCandidate = active;
    els.resumeBanner.hidden = !active;
    if (active) {
      const level = getLevel(active.level_id);
      els.resumeText.textContent = `Partita in corso: ${level?.name || active.level_id} — tempo residuo ${formatDuration(active.remaining_seconds)}.`;
    }
  }

  async function submitJoin(event) {
    event.preventDefault();
    const code = els.accessCodeInput.value.trim();
    if (!code) {
      els.joinError.textContent = "Inserisci il codice evento.";
      return;
    }
    setButtonLoading(els.joinButton, true, "VERIFICA…");
    try {
      const result = await API.join(code);
      els.accessCodeInput.value = "";
      els.recoveryCodeValue.textContent = result.recovery_code;
      els.joinForm.hidden = true;
      els.recoverForm.hidden = true;
      els.accessTabs.hidden = true;
      els.recoveryCard.hidden = false;
      els.recoverySavedButton.focus();
    } catch (error) {
      els.joinError.textContent = accessErrorMessage(error);
    } finally {
      setButtonLoading(els.joinButton, false);
    }
  }

  async function submitRecover(event) {
    event.preventDefault();
    const code = els.recoveryCodeInput.value.trim();
    if (!code) {
      els.recoverError.textContent = "Inserisci il codice di recupero.";
      return;
    }
    setButtonLoading(els.recoverButton, true, "VERIFICA…");
    try {
      await API.recover(code);
      els.recoveryCodeInput.value = "";
      await enterOnline();
    } catch (error) {
      els.recoverError.textContent = accessErrorMessage(error);
    } finally {
      setButtonLoading(els.recoverButton, false);
    }
  }

  async function copyRecoveryCode() {
    try {
      await navigator.clipboard.writeText(els.recoveryCodeValue.textContent);
      toast("Codice copiato.", "info", 2500);
    } catch {
      toast("Copia non riuscita: annota il codice a mano.", "warning", 4000);
    }
  }

  async function resumeSession() {
    const candidate = state.resumeCandidate;
    if (!candidate) return;
    try {
      const [rawSession, history] = await Promise.all([API.getSession(candidate.id), API.history(candidate.id)]);
      state.sessionEpoch += 1;
      state.session = normalizeSession(rawSession, candidate.level_id, state.latestNickname);
      state.selectedLevelId = state.session.level_id;
      enterGame({});
      const messages = history.messages || [];
      for (const item of messages) {
        addMessage(item.role === "assistant" ? "assistant" : "user", item.content, { language: item.language });
      }
      if (messages.some(item => item.content.includes("[REDACTED_SESSION_FLAG]"))) {
        addMessage("system", "Per sicurezza la flag non viene mai salvata: nella cronologia ripresa appare oscurata.");
      }
      setTimerRemaining(candidate.remaining_seconds);
      els.resumeBanner.hidden = true;
      state.resumeCandidate = null;
    } catch (error) {
      if (error.status === 401) return showAccess("Accesso scaduto: rientra con il codice.");
      els.resumeBanner.hidden = true;
      state.resumeCandidate = null;
      toast(error.status === 404 ? "La partita non è più disponibile." : error.message, "warning", 5000);
    }
  }

  async function abandonResumable() {
    const candidate = state.resumeCandidate;
    if (!candidate) return;
    try {
      await API.deleteSession(candidate.id);
    } catch (error) {
      if (error.status !== 404) toast(error.message, "error", 5000);
    }
    els.resumeBanner.hidden = true;
    state.resumeCandidate = null;
  }

  function lockComposerFor(seconds, reason) {
    const until = Date.now() + seconds * 1000;
    state.composerLockedUntil = until;
    setControlsEnabled(false);
    toast(`${reason}: riprova tra ${seconds} s.`, "warning", Math.min(seconds, 10) * 1000);
    window.setTimeout(() => {
      if (state.composerLockedUntil !== until) return;
      state.composerLockedUntil = 0;
      if (!state.busy) setControlsEnabled(true);
    }, seconds * 1000);
  }
```

- [ ] **Step 6: gestione errori**

In `setControlsEnabled`, sostituire la prima riga con:

```js
    const active = enabled && state.session?.status === "active" && Date.now() >= (state.composerLockedUntil || 0);
```

All'inizio di `handleTurnError(error)`, prima del blocco `if (error.status === 409 && ...)` esistente:

```js
    const code = errorCode(error);
    if (state.online && error.status === 401) {
      showAccess("Accesso scaduto: rientra con il codice o con il codice di recupero.");
      return;
    }
    if (code === "turn_in_progress") {
      toast("JANUS sta ancora rispondendo: attendi la risposta.", "warning", 4000);
      return;
    }
    if (error.status === 429 || code === "llm_busy") {
      addMessage("error", "Messaggio non inviato: riprova tra poco.");
      lockComposerFor(retryAfterSeconds(error), code === "llm_busy" ? "JANUS è sovraccarico" : "Troppe richieste");
      return;
    }
    if (state.online && error.status === 404) {
      state.session = null;
      toast("Sessione non trovata.", "warning", 5000);
      showScreen("attract");
      return;
    }
```

Così il blocco esistente `if (error.status === 409 ...)` riceve solo i 409 di sessione non più attiva (`invalid_session`), perché `turn_in_progress` è già uscito.

In `sendTextMessage`, nel `catch`, ripristinare il testo per i casi da ritentare. Sostituire:

```js
      if (isCurrentSession(sessionId, epoch)) handleTurnError(error);
```

con:

```js
      if (isCurrentSession(sessionId, epoch)) {
        if (["llm_busy", "rate_limited", "turn_in_progress"].includes(errorCode(error))) {
          els.messageInput.value = text;
          autoSizeComposer();
        }
        handleTurnError(error);
      }
```

In `startSession`, nel `catch`, prima di `setAvatarState("alert");`:

```js
      if (errorCode(error) === "nickname_taken") {
        els.nicknameError.textContent = "Nickname già in uso da un altro giocatore.";
        els.nicknameInput.focus();
        return;
      }
      if (state.online && error.status === 401) {
        showAccess("Accesso scaduto: rientra con il codice.");
        return;
      }
```

In `openSetup`, dopo `els.nicknameError.textContent = "";`:

```js
    const lockedNickname = state.online && state.mode === "score" ? state.latestNickname : "";
    els.nicknameInput.readOnly = Boolean(lockedNickname);
    if (lockedNickname) {
      els.nicknameInput.value = lockedNickname;
      els.nicknameCounter.textContent = `${lockedNickname.length}/24`;
    }
```

In `finishSession`, sostituire `if (state.mode === "stand") startResultResetCountdown();` con:

```js
    // The automatic return to attract exists for the next kiosk participant only.
    if (state.mode === "stand" && !state.online) startResultResetCountdown();
```

- [ ] **Step 7: controllo di sintassi**

Run: `node --check src/janus/web/app.js && echo OK`
Expected: `OK`. La verifica funzionale nel browser è nel Task 15.

- [ ] **Step 8: suite e commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (nessun test Python dipende dal JS, ma l'HTML servito resta valido).

```bash
git add src/janus/web/index.html src/janus/web/app.js src/janus/web/styles.css
git commit -m "Add online access screen, session resume and online error handling to the UI"
```

---

### Task 12: Docker: entrypoint, alias, Caddy

**Files:**
- Modify: `docker/entrypoint.sh`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.hf.yml`
- Create: `docker-compose.public.yml`
- Create: `docker/Caddyfile`

**Interfaces:**
- Consumes: flag CLI `--online`, `--public-host`, `--trusted-proxies` (Task 1).
- Produces:
  - variabili `.env`: `JANUS_PUBLIC_HOST`, `JANUS_ACCESS_CODES`, `JANUS_TLS` (`acme`|`internal`), `JANUS_HTTP_PORT`, `JANUS_HTTPS_PORT`, `JANUS_SUBNET`;
  - rete compose `${JANUS_SUBNET:-172.30.57.0/24}`, l'unica i cui `X-Forwarded-*` sono creduti.

- [ ] **Step 1: `docker/entrypoint.sh`**

Dopo la riga `set -- ${JANUS_LLM_BASE_URL:+--llm-base-url "$JANUS_LLM_BASE_URL"} "$@"`:

```sh
set -- ${JANUS_PUBLIC_HOST:+--public-host "$JANUS_PUBLIC_HOST"} \
    ${JANUS_TRUSTED_PROXIES:+--trusted-proxies "$JANUS_TRUSTED_PROXIES"} "$@"
case "${JANUS_ONLINE:-0}" in
    1|true|yes) set -- --online "$@" ;;
esac
```

- [ ] **Step 2: alias `janus-upstream`**

In `docker-compose.yml`, nel servizio `ollama`, dopo `volumes:`:

```yaml
    networks:
      default:
        # Caddy (docker-compose.public.yml) reaches JANUS through this name:
        # JANUS shares this service's network namespace.
        aliases: [janus-upstream]
```

In `docker-compose.hf.yml`, nel servizio `janus`, dopo `ports:`:

```yaml
    networks:
      default:
        aliases: [janus-upstream]
```

- [ ] **Step 3: creare `docker/Caddyfile`**

```
(tls-acme) {
	# Default: automatic Let's Encrypt certificate for JANUS_PUBLIC_HOST.
}

(tls-internal) {
	tls internal
}

{$JANUS_PUBLIC_HOST} {
	import tls-{$JANUS_TLS:acme}
	encode zstd gzip
	request_body {
		max_size 25MB
	}
	reverse_proxy janus-upstream:8000 {
		transport http {
			read_timeout 120s
		}
	}
}
```

- [ ] **Step 4: creare `docker-compose.public.yml`**

```yaml
# Internet-facing multiplayer mode behind Caddy (automatic HTTPS):
#   docker compose -f docker-compose.yml -f docker-compose.public.yml up -d --build
#   (add -f docker-compose.hf.yml for Hugging Face, -f docker-compose.gpu.yml for a GPU)
#
# Required in .env: JANUS_PUBLIC_HOST (DNS name pointing here), JANUS_ACCESS_CODES.
# JANUS_TLS=internal uses Caddy's local CA instead of Let's Encrypt (tests, closed
# networks). Only Caddy is published; JANUS trusts X-Forwarded-* from this network only.
services:
  ollama:
    ports: !reset []

  janus:
    ports: !reset []
    environment:
      JANUS_ONLINE: "1"
      JANUS_PUBLIC_HOST: ${JANUS_PUBLIC_HOST:?set JANUS_PUBLIC_HOST in .env}
      JANUS_TRUSTED_PROXIES: ${JANUS_SUBNET:-172.30.57.0/24}
      JANUS_ACCESS_CODES: ${JANUS_ACCESS_CODES:?set JANUS_ACCESS_CODES in .env}

  caddy:
    image: caddy:${CADDY_VERSION:-2}
    restart: unless-stopped
    depends_on:
      janus:
        condition: service_healthy
    environment:
      JANUS_PUBLIC_HOST: ${JANUS_PUBLIC_HOST}
      JANUS_TLS: ${JANUS_TLS:-acme}
    ports:
      - "${JANUS_HTTP_PORT:-80}:80"
      - "${JANUS_HTTPS_PORT:-443}:443"
      - "${JANUS_HTTPS_PORT:-443}:443/udp"
    volumes:
      - ./docker/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config

networks:
  default:
    ipam:
      config:
        - subnet: ${JANUS_SUBNET:-172.30.57.0/24}

volumes:
  caddy-data:
  caddy-config:
```

- [ ] **Step 5: validare tutte le combinazioni**

Run:

```bash
export JANUS_PUBLIC_HOST=janus.localhost JANUS_ACCESS_CODES=test-code-2026
docker compose config -q && echo base
docker compose -f docker-compose.yml -f docker-compose.hf.yml config -q && echo hf
docker compose -f docker-compose.yml -f docker-compose.public.yml config -q && echo public
docker compose -f docker-compose.yml -f docker-compose.hf.yml -f docker-compose.public.yml config -q && echo hf+public
docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.public.yml config -q && echo gpu+public
JANUS_ACCESS_CODES= docker compose -f docker-compose.yml -f docker-compose.public.yml config -q 2>&1 | tail -1
docker run --rm -e JANUS_PUBLIC_HOST=janus.localhost -e JANUS_TLS=internal \
  -v "$PWD/docker/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2 \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker run --rm -e JANUS_PUBLIC_HOST=janus.localhost \
  -v "$PWD/docker/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2 \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Expected: `base`, `hf`, `public`, `hf+public`, `gpu+public`; poi l'errore `required variable JANUS_ACCESS_CODES is missing a value`; infine due `Valid configuration`.

- [ ] **Step 6: build e avvio rapido in locale (modalità non online invariata)**

Run: `docker compose build -q janus && docker compose up -d --wait && curl -s http://127.0.0.1:8000/api/config | grep -o '"online":false'`
Expected: `"online":false`.

- [ ] **Step 7: commit**

```bash
git add docker/entrypoint.sh docker-compose.yml docker-compose.hf.yml docker-compose.public.yml docker/Caddyfile
git commit -m "Add Caddy-based public deployment and online variables to the Docker stack"
```

---

### Task 13: script di carico e isolamento per l'istanza online

**Files:**
- Create: `scripts/online_load_test.py`

**Interfaces:**
- Consumes: l'API online (Task 7–8).
- Produces: `python scripts/online_load_test.py --base-url URL [--host-header H] [--ca-file PEM] [--players N] [--turns N]`; codice d'uscita 0 solo se non ci sono contaminazioni e tutti i giocatori completano. La verifica TLS resta sempre attiva: per la CA locale di Caddy si passa `--ca-file`.

- [ ] **Step 1: creare lo script**

```python
"""Concurrent online players against a running JANUS: isolation and latency report.

Each player joins with the event code (JANUS_ACCESS_CODES or --access-code), opens a
session, states a personal word, then asks for it back. Exit status is non-zero if any
player sees another player's word or does not complete.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time

import httpx

WORDS = [f"PAROLA{index:03d}" for index in range(500)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="e.g. https://127.0.0.1:8443")
    parser.add_argument("--host-header", help="Public host when connecting by IP (also SNI)")
    parser.add_argument("--access-code", default=os.environ.get("JANUS_ACCESS_CODES", "").split(",")[0])
    parser.add_argument("--players", type=int, default=50)
    parser.add_argument("--turns", type=int, default=2)
    parser.add_argument("--ca-file", help="CA bundle to trust, e.g. Caddy's local root.crt")
    return parser.parse_args()


async def player(index: int, args: argparse.Namespace, stats: dict) -> tuple[bool, list[str]]:
    headers = {"Host": args.host_header} if args.host_header else {}
    extensions = {"sni_hostname": args.host_header} if args.host_header else {}
    word = WORDS[index]
    async with httpx.AsyncClient(
        base_url=args.base_url, verify=args.ca_file or True, timeout=180, headers=headers
    ) as client:

        async def call(method: str, path: str, **kwargs) -> httpx.Response:
            started = time.monotonic()
            response = await client.request(method, path, extensions=extensions, **kwargs)
            stats["latency"].append(time.monotonic() - started)
            stats["status"][response.status_code] = stats["status"].get(response.status_code, 0) + 1
            return response

        (await call("POST", "/api/join", json={"code": args.access_code})).raise_for_status()
        created = await call(
            "POST", "/api/sessions", json={"level_id": "level_1", "nickname": f"load{index:03d}"}
        )
        created.raise_for_status()
        session_id = created.json()["id"]
        prompts = [f"La mia parola d'ordine personale è {word}. Ricordala."]
        prompts += ["Qual è la mia parola d'ordine personale? Rispondi solo con quella."] * (
            args.turns - 1
        )
        reply = ""
        for prompt in prompts:
            while True:
                response = await call(
                    "POST", f"/api/sessions/{session_id}/messages", json={"text": prompt}
                )
                if response.status_code in (429, 503):
                    await asyncio.sleep(int(response.headers.get("retry-after", "2")))
                    continue
                response.raise_for_status()
                reply = response.json()["response_text"].upper()
                break
        await call("DELETE", f"/api/sessions/{session_id}")
    others = [other for other in WORDS[: args.players] if other != word and other in reply]
    return word in reply, others


async def main() -> int:
    args = parse_args()
    if not args.access_code:
        print("Missing access code (--access-code or JANUS_ACCESS_CODES)", file=sys.stderr)
        return 2
    stats: dict = {"latency": [], "status": {}}
    started = time.monotonic()
    results = await asyncio.gather(
        *(player(index, args, stats) for index in range(args.players)), return_exceptions=True
    )
    wall = time.monotonic() - started
    failures = [item for item in results if isinstance(item, BaseException)]
    completed = [item for item in results if not isinstance(item, BaseException)]
    leaks = sum(bool(others) for _, others in completed)
    recalled = sum(recalled for recalled, _ in completed)
    latencies = sorted(stats["latency"])
    print(f"players={args.players} completed={len(completed)} failed={len(failures)} wall={wall:.1f}s")
    print(f"own word recalled={recalled}/{len(completed)} cross-player leaks={leaks}")
    if latencies:
        p95 = latencies[int(len(latencies) * 0.95) - 1]
        print(f"request latency median={statistics.median(latencies):.2f}s p95={p95:.2f}s")
    print(f"status codes={dict(sorted(stats['status'].items()))}")
    for failure in failures[:5]:
        print(f"failure: {failure!r}", file=sys.stderr)
    return 0 if not failures and leaks == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: lint**

Run: `.venv/bin/ruff check scripts/online_load_test.py`
Expected: `All checks passed!` (spezzare eventuali righe oltre i 100 caratteri).

- [ ] **Step 3: commit**

```bash
git add scripts/online_load_test.py
git commit -m "Add concurrent online load and isolation test script"
```

---

### Task 14: documentazione

**Files:**
- Modify: `docs/SECURITY.md`, `docs/DOCKER.md`, `docs/ARCHITECTURE.md`, `docs/EVENT_RUNBOOK.md`, `README.md`, `.env.example`

- [ ] **Step 1: `docs/DOCKER.md`.** Aggiungere la sezione "Modalità online su Internet" dopo "Scelta del backend LLM". Contenuto:
  - comando con `docker-compose.public.yml` (anche combinato con HF/GPU);
  - variabili obbligatorie (`JANUS_PUBLIC_HOST`, `JANUS_ACCESS_CODES`) e opzionali (`JANUS_TLS=internal`, `JANUS_HTTP_PORT`, `JANUS_HTTPS_PORT`, `JANUS_SUBNET`, `CADDY_VERSION`);
  - requisiti DNS e porte 80/443;
  - schema `browser → Caddy :443 → janus-upstream:8000`;
  - variante proxy esterno: `JANUS_BIND_ADDRESS` interno, `JANUS_ONLINE=1`, `JANUS_PUBLIC_HOST`, `JANUS_TRUSTED_PROXIES`; requisiti `Host` e `X-Forwarded-For/Proto`, body ≥ 20 MB, timeout ≥ 120 s;
  - esempio nginx (`proxy_set_header Host $host; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto https; client_max_body_size 25m; proxy_read_timeout 120s;`);
  - tabella di capacità: HF `llm.max_concurrent` 8–16 con le misure (8 giocatori in 4,6 s); Ollama con GPU `OLLAMA_NUM_PARALLEL` = `max_concurrent`; CPU sconsigliato oltre 2–3 giocatori (misura: turni fino a 38,7 s con 4 giocatori).
  - Le nuove variabili vanno anche nella tabella "Configurazione".

- [ ] **Step 2: `docs/SECURITY.md`.** Aggiungere la sezione "Modalità online (Internet)" con:
  - perimetro (Internet, TLS al proxy);
  - cookie firmato e suoi attributi;
  - codici evento solo da env, confronto a tempo costante, rotazione con riavvio;
  - codice di recupero (80 bit, solo hash, nessun prolungamento della scadenza);
  - proprietà delle risorse con 404 indistinguibile;
  - limiti (con la precisazione sui soli tentativi falliti);
  - cancello LLM e 503;
  - HTTPS obbligatorio e HSTS;
  - health ridotto;
  - log d'accesso con IP e percorsi (come ridurli: `--no-access-log` di uvicorn, `log` di Caddy);
  - divieto di ruotare la chiave master a evento in corso.

  Aggiornare la riga "La CLI accetta soltanto 127.0.0.1 o localhost" per citare `0.0.0.0` solo in container o dietro proxy.

- [ ] **Step 3: `docs/ARCHITECTURE.md`.**
  - Aggiungere alla tabella API: `POST /api/join`, `POST /api/recover`, `GET /api/players/me`, `POST /api/logout`, `GET /api/sessions/{id}/messages`.
  - Aggiungere la tabella `players` e la colonna `owner_id` alla persistenza.
  - Aggiungere le sezioni `GatedLLMProvider` e `SessionSweeper`.
  - In "Limiti noti": sostituire "non esiste uno scheduler globale fra sessioni concorrenti" con la descrizione del cancello FIFO; aggiungere "una sola istanza (lock e limiti in memoria)".

- [ ] **Step 4: `docs/EVENT_RUNBOOK.md`.** Aggiungere la checklist "Evento online":
  - dominio e record DNS;
  - porte 80/443 aperte;
  - `JANUS_ACCESS_CODES` generati (`python -c "import secrets; print(secrets.token_urlsafe(9))"`);
  - limiti rivisti;
  - prova `scripts/online_load_test.py` con il numero di giocatori atteso;
  - verifica di `docker compose ps` e dei log;
  - procedura di rotazione del codice (aggiornare `.env`, `docker compose up -d`);
  - cosa dire ai giocatori sul codice di recupero.

- [ ] **Step 5: `README.md` e `.env.example`.**
  - README: aggiungere una sotto-sezione "Gioco online multi-giocatore" nella sezione Docker Compose, con il comando e un rimando a `docs/DOCKER.md`.
  - `.env.example`: aggiungere un blocco commentato con `JANUS_PUBLIC_HOST`, `JANUS_ACCESS_CODES`, `JANUS_TLS`, `JANUS_HTTP_PORT`, `JANUS_HTTPS_PORT`, `CADDY_VERSION`.

- [ ] **Step 6: commit**

```bash
git add docs README.md .env.example
git commit -m "Document the online multiplayer mode, deployment and event checklist"
```

---

### Task 15: verifica dal vivo e prontezza al merge

**Files:**
- Nessun file di prodotto. Script temporanei nella scratchpad della sessione, non nel repository.

- [ ] **Step 1: suite completa e lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests scripts`
Expected: tutti i test PASS (52 esistenti + nuovi), `All checks passed!`.

- [ ] **Step 2: stack online HF con Caddy (TLS interno)**

Aggiungere a `.env` (già ignorato da git):

```bash
JANUS_PUBLIC_HOST=janus.localhost
JANUS_ACCESS_CODES=<generato con secrets.token_urlsafe(9)>
JANUS_TLS=internal
JANUS_HTTP_PORT=8080
JANUS_HTTPS_PORT=8443
```

Run:

```bash
docker compose down
docker compose -f docker-compose.yml -f docker-compose.hf.yml -f docker-compose.public.yml up -d --build --wait
docker compose -f docker-compose.yml -f docker-compose.hf.yml -f docker-compose.public.yml ps
curl -sk --resolve janus.localhost:8443:127.0.0.1 https://janus.localhost:8443/api/config | grep -o '"online":true'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/config || true
```

Expected:
- `janus` e `caddy` attivi, `janus` healthy;
- `"online":true`;
- porta 8000 non raggiungibile dall'host (connessione rifiutata).

- [ ] **Step 3: carico con 50 giocatori su HF**

Run:

```bash
docker compose -f docker-compose.yml -f docker-compose.hf.yml -f docker-compose.public.yml \
  cp caddy:/data/caddy/pki/authorities/local/root.crt "$SCRATCH/caddy-root.crt"
set -a; . ./.env; set +a
.venv/bin/python scripts/online_load_test.py --base-url https://janus.localhost:8443 \
  --ca-file "$SCRATCH/caddy-root.crt" --players 50
```

(`$SCRATCH` è la scratchpad della sessione. `janus.localhost` si risolve su loopback; se il resolver locale non lo fa, usare `--base-url https://127.0.0.1:8443 --host-header janus.localhost`, che imposta anche l'SNI.)
Expected:
- `completed=50 failed=0`;
- `cross-player leaks=0`;
- distribuzione dei codici di stato con eventuali 503 o 429 gestiti dai ritentativi;
- latenze da riportare.

- [ ] **Step 4: furto di sessione e header falsificati**

Con uno script nella scratchpad (httpx con `verify="$SCRATCH/caddy-root.crt"`):
1. Il giocatore A entra e crea una sessione; il giocatore B entra e prova GET, POST messages, submit, reset e DELETE sulla sessione di A. Expected: tutti 404, sessione di A invariata.
2. **Spoofing dell'IP attraverso Caddy:** 11 `POST /api/join` con codice errato, ciascuno con un `X-Forwarded-For` falsificato diverso (`1.2.3.N`). Expected: l'undicesimo riceve 429, perché Caddy non si fida degli `X-Forwarded-For` del client e JANUS vede l'IP reale. Il limite per IP non si aggira falsificando gli header.
3. **Porta interna:** JANUS non è raggiungibile dall'host senza passare da Caddy (già verificato allo step 2). L'intera subnet compose è fidata per scelta progettuale, e lo si documenta in `SECURITY.md`.

- [ ] **Step 5: stack online Ollama (4 giocatori)**

Run:

```bash
docker compose -f docker-compose.yml -f docker-compose.hf.yml -f docker-compose.public.yml down
docker compose -f docker-compose.yml -f docker-compose.public.yml up -d --wait
.venv/bin/python scripts/online_load_test.py --base-url https://janus.localhost:8443 --ca-file "$SCRATCH/caddy-root.crt" --players 4
```

Expected: `completed=4 failed=0 cross-player leaks=0`. Le latenze CPU elevate sono attese e vanno riportate.

- [ ] **Step 6: Playwright: accesso, recupero, ripresa, voce** (script Python nella scratchpad)

Preparazione: `uv run --no-project --with playwright python -m playwright install chromium`. Avviare Chromium con `args=["--host-resolver-rules=MAP janus.localhost 127.0.0.1", "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]` e `viewport={"width": 390, "height": 844}`. Per il certificato della CA locale di Caddy si usa `ignore_https_errors=True`: ammesso solo in questo test locale contro `tls internal`, e da dichiarare nel resoconto. Verificare:
1. `https://janus.localhost:8443/` mostra la schermata Accesso; codice evento errato → "Codice non valido."; codice corretto → codice di recupero visibile; "L'HO SALVATO" → attract.
2. Avvio della sfida (nickname, livello 1), invio di un messaggio, risposta visibile.
3. Chiusura del contesto e apertura di un nuovo contesto **con lo stesso storage state**: banner "Partita in corso" → "RIPRENDI" → cronologia ricaricata e timer coerente con `remaining_seconds`.
4. Nuovo contesto **pulito** (altro dispositivo): scheda "CODICE DI RECUPERO" con il codice in minuscolo → banner di ripresa → ripresa riuscita.
5. Push-to-talk: tenere premuto per 2 s con il microfono simulato → la richiesta `POST …/voice` parte (stato 200, oppure 502 "No speech was detected" accettabile con l'audio sintetico del dispositivo finto) e il microfono non viene bloccato per contesto non sicuro.
6. Screenshot mobile di Accesso, attract con banner, game: salvarli nella scratchpad e allegarli al resoconto.

- [ ] **Step 7: Playwright: errori non distruttivi** (stesso script, con `page.route`)
1. Rispondere una volta a `**/api/sessions/*/messages` con 409 `{"error":{"code":"turn_in_progress",...}}`. Expected: toast "JANUS sta ancora rispondendo", schermata `game` ancora attiva, testo ripristinato nella casella.
2. Rispondere una volta con 503 `llm_busy` e `Retry-After: 3`. Expected: messaggio "non inviato", invio disabilitato per circa 3 s, poi di nuovo abilitato.
3. Rispondere con 409 `invalid_session`. Expected: fine partita con "La sessione non è più attiva".

- [ ] **Step 8: ripristino dello stack di sviluppo**

Run:

```bash
docker compose -f docker-compose.yml -f docker-compose.public.yml down
docker compose up -d --wait
```

Rimuovere da `.env` le variabili `JANUS_PUBLIC_HOST`/`JANUS_TLS`/porte se non devono restare.

- [ ] **Step 9: revisione dell'intero branch**

Eseguire superpowers:requesting-code-review su `main..feat/multiplayer`, risolvere i rilievi confermati, rieseguire gli step 1 e 3.

- [ ] **Step 10: push**

```bash
git push origin feat/multiplayer
```

Riportare all'utente gli esiti degli step 2–7 (numeri, screenshot) e chiedere se fare il merge su `main` del fork.
