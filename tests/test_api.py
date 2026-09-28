from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from janus.api import create_app
from janus.domain import HealthComponent, Language
from janus.providers.llm import MockLLMProvider
from janus.providers.stt import Transcription
from janus.providers.tts import DisabledTTSProvider


class FakeSTT:
    async def transcribe(self, audio_path: Path, language=None):
        assert audio_path.read_bytes() == b"fake-wave"
        return Transcription(text="Hello from the microphone", language=language or Language.ENGLISH)

    async def health(self):
        return HealthComponent(available=True, detail="fake STT")


def test_full_text_and_score_api_flow(loaded_config, repository, flag_service):
    loaded_config = loaded_config.model_copy(
        update={
            "app": loaded_config.app.model_copy(update={"default_mode": "score"}),
        }
    )
    app = create_app(
        loaded_config=loaded_config,
        repository=repository,
        flag_service=flag_service,
        llm_provider=MockLLMProvider(),
        stt_provider=FakeSTT(),
        tts_provider=DisabledTTSProvider(),
    )
    client = TestClient(app)

    public = client.get("/api/config")
    assert public.status_code == 200
    assert {mode["id"] for mode in public.json()["modes"]} == {"stand", "score"}
    assert [level["time_limit_seconds"] for level in public.json()["levels"]] == [
        480,
        600,
        720,
    ]
    assert public.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in public.headers["content-security-policy"]
    assert public.headers["x-frame-options"] == "DENY"
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["components"]["llm"]["available"] is True

    created = client.post(
        "/api/sessions",
        json={"mode_id": "score", "level_id": "level_1", "nickname": "Ada"},
    )
    assert created.status_code == 201
    session = created.json()

    turn = client.post(
        f"/api/sessions/{session['id']}/messages",
        json={"text": "Hello JANUS", "language": "auto", "speak": False},
    )
    assert turn.status_code == 200
    assert turn.json()["language"] == "en"

    wrong = client.post(
        f"/api/sessions/{session['id']}/submit", json={"flag": "RH26{WRONG}"}
    )
    assert wrong.json()["correct"] is False
    flag = flag_service.derive(session["id"], "level_1")
    solved = client.post(f"/api/sessions/{session['id']}/submit", json={"flag": flag})
    assert solved.json()["correct"] is True
    assert solved.json()["score"] > 0

    board = client.get("/api/leaderboard", params={"level_id": "level_1"})
    assert board.status_code == 200
    assert board.json()["entries"][0]["nickname"] == "Ada"


def test_api_mode_is_locked_by_operator(loaded_config, repository, flag_service):
    app = create_app(
        loaded_config=loaded_config,
        repository=repository,
        flag_service=flag_service,
        llm_provider=MockLLMProvider(),
        stt_provider=FakeSTT(),
        tts_provider=DisabledTTSProvider(),
    )
    response = TestClient(app).post(
        "/api/sessions",
        json={"mode_id": "score", "level_id": "level_1", "nickname": "Eve"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["active_mode"] == "stand"


def test_raw_audio_voice_endpoint_and_anonymous_stand_mode(
    loaded_config, repository, flag_service
):
    app = create_app(
        loaded_config=loaded_config,
        repository=repository,
        flag_service=flag_service,
        llm_provider=MockLLMProvider(),
        stt_provider=FakeSTT(),
        tts_provider=DisabledTTSProvider(),
    )
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={"mode_id": "stand", "level_id": "level_1", "nickname": "Discard me"},
    ).json()
    assert created["nickname"] is None

    response = client.post(
        f"/api/sessions/{created['id']}/voice?language=auto&speak=true",
        content=b"fake-wave",
        headers={"content-type": "audio/wav"},
    )

    assert response.status_code == 200
    assert response.json()["transcript"] == "Hello from the microphone"
    assert response.json()["language"] == "en"
    assert response.json()["audio_url"] is None


def test_api_errors_have_stable_machine_readable_shape(
    loaded_config, repository, flag_service
):
    app = create_app(
        loaded_config=loaded_config,
        repository=repository,
        flag_service=flag_service,
        llm_provider=MockLLMProvider(),
        stt_provider=FakeSTT(),
        tts_provider=DisabledTTSProvider(),
    )
    response = TestClient(app).post(
        "/api/sessions", json={"mode_id": "score", "level_id": "level_1"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_delete_removes_anonymous_session(loaded_config, repository, flag_service):
    app = create_app(
        loaded_config=loaded_config,
        repository=repository,
        flag_service=flag_service,
        llm_provider=MockLLMProvider(),
        stt_provider=FakeSTT(),
        tts_provider=DisabledTTSProvider(),
    )
    client = TestClient(app)
    session_id = client.post("/api/sessions", json={"mode_id": "stand"}).json()["id"]

    assert client.delete(f"/api/sessions/{session_id}").status_code == 204
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


def test_raw_audio_limit_is_enforced_while_streaming(
    loaded_config, repository, flag_service
):
    speech = loaded_config.app.speech.model_copy(update={"max_audio_bytes": 1024})
    config = loaded_config.model_copy(
        update={"app": loaded_config.app.model_copy(update={"speech": speech})}
    )
    app = create_app(
        loaded_config=config,
        repository=repository,
        flag_service=flag_service,
        llm_provider=MockLLMProvider(),
        stt_provider=FakeSTT(),
        tts_provider=DisabledTTSProvider(),
    )
    client = TestClient(app)
    session_id = client.post("/api/sessions", json={}).json()["id"]

    response = client.post(
        f"/api/sessions/{session_id}/voice",
        content=b"x" * 1025,
        headers={"content-type": "audio/wav"},
    )

    assert response.status_code == 422
    assert "Audio exceeds" in response.json()["detail"]
