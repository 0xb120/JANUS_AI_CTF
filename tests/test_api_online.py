from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fakes import FileTTS
from fastapi.testclient import TestClient

from janus.api import create_app
from janus.config import OnlineLimits, OnlineSettings
from janus.domain import utc_now
from janus.errors import ConfigurationError
from janus.providers.llm import MockLLMProvider
from janus.providers.stt import DisabledSTTProvider
from janus.providers.tts import DisabledTTSProvider
from janus.repository import SQLiteRepository
from janus.security import hash_recovery_code

ACCESS_CODE = "event-code-2026"


@pytest.fixture
def make_app(monkeypatch, loaded_config, repository, flag_service):
    monkeypatch.setenv("JANUS_ACCESS_CODES", ACCESS_CODE)

    def factory(*, limits=None, llm=None, tts=None, repo=None, **extra):
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
            **extra,
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
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1


def test_successful_joins_from_one_ip_are_not_rate_limited(make_app):
    app = make_app(limits=OnlineLimits(auth_attempts_per_minute=3))

    for _ in range(15):
        join(https_client(app))


def test_recover_rejects_unknown_and_expired_codes(make_app, repository):
    client = https_client(make_app())
    unknown = client.post("/api/recover", json={"recovery_code": "RCV-0000-0000-0000-0000"})
    assert unknown.status_code == 401

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


def loopback_client(app, scheme: str = "http") -> TestClient:
    return TestClient(app, base_url=f"{scheme}://testserver", client=("127.0.0.1", 50000))


def test_recover_from_a_new_client_keeps_the_original_expiry(make_app):
    app = make_app()
    first = https_client(app)
    response = first.post("/api/join", json={"code": ACCESS_CODE})
    code = response.json()["recovery_code"]
    original_expiry = response.json()["expires_at"]

    second = https_client(app)
    recovered = second.post("/api/recover", json={"recovery_code": code})

    assert recovered.status_code == 200
    assert recovered.json()["expires_at"] == original_expiry
    me = second.get("/api/players/me")
    assert me.status_code == 200
    assert me.json()["nickname"] is None
    # The cookie carries whole seconds, so /players/me truncates the microseconds.
    expected = datetime.fromisoformat(original_expiry).replace(microsecond=0)
    assert datetime.fromisoformat(me.json()["expires_at"]) == expected


def test_join_and_recover_failures_share_one_bucket(make_app):
    app = make_app(limits=OnlineLimits(auth_attempts_per_minute=3))
    code = join(https_client(app))
    client = https_client(app)
    for _ in range(2):
        assert client.post("/api/join", json={"code": "wrong-code"}).status_code == 401
    bad = client.post("/api/recover", json={"recovery_code": "RCV-0000-0000-0000-0000"})
    assert bad.status_code == 401

    blocked = client.post("/api/recover", json={"recovery_code": code})

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"


def test_direct_loopback_without_forwarding_headers_may_use_plain_http(make_app):
    client = loopback_client(make_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert "components" in response.json()


def test_forwarded_loopback_requests_are_not_exempt(make_app):
    app = make_app()
    forwarded = {"X-Forwarded-For": "203.0.113.9"}

    refused = loopback_client(app).get("/api/config", headers=forwarded)
    reduced = loopback_client(app, "https").get("/api/health", headers=forwarded)

    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "https_required"
    assert set(reduced.json()) == {"status", "version"}


def test_https_required_response_carries_security_headers(make_app):
    refused = TestClient(make_app()).get("/api/config")

    assert refused.status_code == 400
    assert refused.headers["x-frame-options"] == "DENY"
    assert refused.headers["cache-control"] == "no-store"


def test_warns_when_online_has_no_trusted_proxies(make_app, caplog):
    with caplog.at_level("WARNING", logger="janus.api"):
        make_app()

    assert any("trusted-proxies" in record.getMessage() for record in caplog.records)


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
        bob.post(
            f"/api/sessions/{sid}/voice", content=b"RIFF....", headers={"content-type": "audio/wav"}
        ),
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

    first = client.post("/api/sessions", json={"level_id": "level_1", "nickname": "Ada"})
    assert first.status_code == 201
    second = client.post("/api/sessions", json={"level_id": "level_1", "nickname": "Ada"})
    assert second.status_code == 429


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


class CountingLLM(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.health_calls = 0

    async def health(self):
        self.health_calls += 1
        return await super().health()


def test_anonymous_health_runs_no_provider_probes(make_app):
    llm = CountingLLM()
    client = https_client(make_app(llm=llm))

    body = client.get("/api/health").json()

    assert body == {"status": "unknown", "version": body["version"]}
    assert llm.health_calls == 0


def test_health_probes_are_cached_for_fifteen_seconds(make_app):
    llm = CountingLLM()
    now = [1000.0]
    app = make_app(llm=llm, clock=lambda: now[0])
    player = https_client(app)
    join(player)
    anonymous = https_client(app)

    first = player.get("/api/health").json()
    second = player.get("/api/health").json()
    assert llm.health_calls == 1
    assert first == second and "components" in first
    assert anonymous.get("/api/health").json() == {
        "status": first["status"],
        "version": first["version"],
    }
    assert llm.health_calls == 1

    now[0] += 16
    player.get("/api/health")
    assert llm.health_calls == 2


def test_local_health_keeps_its_shape_and_is_cached(loaded_config, repository, flag_service):
    llm = CountingLLM()
    client = TestClient(
        create_app(
            loaded_config=loaded_config,
            repository=repository,
            flag_service=flag_service,
            llm_provider=llm,
            stt_provider=DisabledSTTProvider(),
            tts_provider=DisabledTTSProvider(),
        )
    )

    bodies = [client.get("/api/health").json() for _ in range(2)]

    assert llm.health_calls == 1
    for body in bodies:
        assert set(body) == {"status", "version", "components"}
        assert set(body["components"]) == {"database", "llm", "stt", "tts"}


def test_resetting_a_retired_session_keeps_one_active_session(make_app):
    app = make_app()
    client, first = _player(app, "Ada")
    client.post("/api/sessions", json={"level_id": "level_1", "nickname": "Ada"})

    for _ in range(2):
        response = client.post(f"/api/sessions/{first['id']}/reset")
        assert response.status_code == 200
        replacement = response.json()

    active = client.get("/api/players/me").json()["active_sessions"]
    assert [item["id"] for item in active] == [replacement["id"]]


def test_nickname_conflicts_do_not_consume_the_session_quota(make_app):
    app = make_app(limits=OnlineLimits(sessions_per_hour=2))
    _player(app, "Ada")
    client, _ = _player(app, "Bob")

    for _ in range(3):
        taken = client.post("/api/sessions", json={"level_id": "level_1", "nickname": "Ada"})
        assert taken.status_code == 409
    again = client.post("/api/sessions", json={"level_id": "level_1", "nickname": "Bob"})
    assert again.status_code == 201
    limited = client.post("/api/sessions", json={"level_id": "level_1", "nickname": "Bob"})
    assert limited.status_code == 429


def test_failed_resets_do_not_consume_the_session_quota(make_app):
    app = make_app(limits=OnlineLimits(sessions_per_hour=2))
    _, foreign = _player(app, "Ada")
    client, mine = _player(app, "Bob")

    for _ in range(3):
        assert client.post(f"/api/sessions/{foreign['id']}/reset").status_code == 404
    reset = client.post(f"/api/sessions/{mine['id']}/reset")
    assert reset.status_code == 200
    limited = client.post(f"/api/sessions/{reset.json()['id']}/reset")
    assert limited.status_code == 429


def test_player_dependency_runs_on_the_event_loop(make_app):
    import inspect

    from fastapi.routing import APIRoute

    route = next(
        item
        for item in make_app().routes
        if isinstance(item, APIRoute) and item.path == "/api/sessions/{session_id}"
        and "GET" in item.methods
    )
    [dependency] = route.dependant.dependencies

    assert inspect.iscoroutinefunction(dependency.call)


def test_concurrent_health_misses_share_one_probe(loaded_config, repository, flag_service):
    import asyncio

    import httpx

    class SlowCountingLLM(CountingLLM):
        async def health(self):
            await asyncio.sleep(0.05)
            return await super().health()

    llm = SlowCountingLLM()
    app = create_app(
        loaded_config=loaded_config,
        repository=repository,
        flag_service=flag_service,
        llm_provider=llm,
        stt_provider=DisabledSTTProvider(),
        tts_provider=DisabledTTSProvider(),
    )

    async def burst():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await asyncio.gather(*(client.get("/api/health") for _ in range(5)))

    responses = asyncio.run(burst())

    assert [response.status_code for response in responses] == [200] * 5
    assert llm.health_calls == 1
