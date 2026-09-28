from __future__ import annotations

import asyncio

import httpx
import pytest

from janus.api import create_app
from janus.domain import HealthComponent
from janus.errors import NotFoundError
from janus.providers.stt import DisabledSTTProvider
from janus.providers.tts import DisabledTTSProvider


class BlockingLLM:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    async def generate(self, messages, *, temperature, max_tokens):
        del temperature, max_tokens
        self.calls.append(list(messages))
        call_number = len(self.calls)
        if call_number == 1:
            self.started.set()
            await self.release.wait()
        return f"reply-{call_number}"

    async def health(self):
        return HealthComponent(available=True, detail="blocking test provider")


def _app(loaded_config, repository, flag_service, provider):
    return create_app(
        loaded_config=loaded_config,
        repository=repository,
        flag_service=flag_service,
        llm_provider=provider,
        stt_provider=DisabledSTTProvider(),
        tts_provider=DisabledTTSProvider(),
    )


def test_two_concurrent_message_posts_are_serialized(
    loaded_config, repository, flag_service
):
    async def scenario():
        provider = BlockingLLM()
        transport = httpx.ASGITransport(
            app=_app(loaded_config, repository, flag_service, provider)
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            session_id = (await client.post("/api/sessions", json={})).json()["id"]
            first = asyncio.create_task(
                client.post(
                    f"/api/sessions/{session_id}/messages",
                    json={"text": "first", "language": "en"},
                )
            )
            await asyncio.wait_for(provider.started.wait(), timeout=1)
            second = asyncio.create_task(
                client.post(
                    f"/api/sessions/{session_id}/messages",
                    json={"text": "second", "language": "en"},
                )
            )
            await asyncio.sleep(0.05)

            assert len(provider.calls) == 1
            assert not second.done()
            provider.release.set()
            first_response, second_response = await asyncio.gather(first, second)

        assert first_response.status_code == 200
        assert second_response.status_code == 200
        assert len(provider.calls) == 2
        assert [(item.role, item.content) for item in provider.calls[1][1:]] == [
            ("user", "first"),
            ("assistant", "reply-1"),
            ("user", "second"),
        ]

    asyncio.run(scenario())


def test_submit_waits_for_inflight_message_then_cleans_transcript(
    loaded_config, repository, flag_service
):
    async def scenario():
        provider = BlockingLLM()
        transport = httpx.ASGITransport(
            app=_app(loaded_config, repository, flag_service, provider)
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            session_id = (await client.post("/api/sessions", json={})).json()["id"]
            turn = asyncio.create_task(
                client.post(
                    f"/api/sessions/{session_id}/messages",
                    json={"text": "in flight", "language": "en"},
                )
            )
            await asyncio.wait_for(provider.started.wait(), timeout=1)
            submit = asyncio.create_task(
                client.post(
                    f"/api/sessions/{session_id}/submit",
                    json={"flag": flag_service.derive(session_id, "level_1")},
                )
            )
            await asyncio.sleep(0.05)
            assert not submit.done()

            provider.release.set()
            turn_response, submit_response = await asyncio.gather(turn, submit)

        assert turn_response.status_code == 200
        assert submit_response.status_code == 200
        assert submit_response.json()["session"]["status"] == "won"
        assert repository.get_messages(session_id) == []

    asyncio.run(scenario())


def test_reset_waits_for_inflight_message_and_removes_old_stand_session(
    loaded_config, repository, flag_service
):
    async def scenario():
        provider = BlockingLLM()
        transport = httpx.ASGITransport(
            app=_app(loaded_config, repository, flag_service, provider)
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            session_id = (await client.post("/api/sessions", json={})).json()["id"]
            turn = asyncio.create_task(
                client.post(
                    f"/api/sessions/{session_id}/messages",
                    json={"text": "in flight", "language": "en"},
                )
            )
            await asyncio.wait_for(provider.started.wait(), timeout=1)
            reset = asyncio.create_task(
                client.post(f"/api/sessions/{session_id}/reset")
            )
            await asyncio.sleep(0.05)
            assert not reset.done()

            provider.release.set()
            turn_response, reset_response = await asyncio.gather(turn, reset)
            old_response = await client.get(f"/api/sessions/{session_id}")

        assert turn_response.status_code == 200
        assert reset_response.status_code == 200
        assert reset_response.json()["id"] != session_id
        assert old_response.status_code == 404

    asyncio.run(scenario())


def test_unknown_session_ids_do_not_allocate_persistent_locks(engine):
    with pytest.raises(NotFoundError):
        asyncio.run(engine.get_session_serialized("not-a-real-session"))

    assert engine._session_locks == {}
