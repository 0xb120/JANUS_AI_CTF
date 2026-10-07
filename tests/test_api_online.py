from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from janus.api import create_app
from janus.config import OnlineLimits, OnlineSettings
from janus.domain import utc_now
from janus.errors import ConfigurationError
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
