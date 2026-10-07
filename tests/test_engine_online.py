from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from fakes import FileTTS

from janus.config import OnlineSettings
from janus.domain import (
    HealthComponent,
    SessionRecord,
    SessionStatus,
    utc_now,
)
from janus.engine import ChallengeEngine
from janus.errors import CapacityError, ConflictError, InvalidSessionError, NotFoundError
from janus.providers.llm import GatedLLMProvider, MockLLMProvider
from janus.providers.stt import DisabledSTTProvider
from janus.providers.tts import DisabledTTSProvider


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
        assert repository.get_session(session.id).turn_count == 2

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


def test_capacity_error_does_not_cost_a_turn_or_leave_an_unanswered_message(
    online_config, repository, flag_service
):
    llm = BlockingLLM()
    gated = GatedLLMProvider(llm, max_concurrent=1, max_queue=0)
    engine = _engine(online_config, repository, flag_service, llm=gated)

    async def exercise():
        session_a = await engine.open_session(nickname="Ada", owner_id="p1")
        session_b = await engine.open_session(nickname="Bob", owner_id="p2")
        first = asyncio.create_task(engine.message(session_a.id, "one", owner_id="p1"))
        await llm.started.wait()
        with pytest.raises(CapacityError):
            await engine.message(session_b.id, "two", owner_id="p2")
        assert repository.get_session(session_b.id).turn_count == 0
        assert repository.list_messages(session_b.id) == []
        llm.release.set()
        turn = await first
        assert turn.response_text
        assert repository.get_session(session_a.id).turn_count == 1

    asyncio.run(exercise())


class ExplodingSTT:
    async def transcribe(self, audio_path, language=None):
        raise AssertionError("STT must not run for a foreign session")

    async def health(self):
        return HealthComponent(available=True, detail="exploding")


def _aged_session(engine, repository, owner_id, *, seconds_left, nickname="Ada"):
    limit = engine._time_limit_seconds(
        SessionRecord(id="x", mode_id="score", level_id="level_1", nickname=nickname)
    )
    return repository.create_session(
        SessionRecord(
            id=str(uuid.uuid4()),
            mode_id="score",
            level_id="level_1",
            nickname=nickname,
            owner_id=owner_id,
            started_at=utc_now() - timedelta(seconds=limit - seconds_left),
        )
    )


def test_foreign_voice_never_reaches_stt(online_config, repository, flag_service, tmp_path):
    engine = ChallengeEngine(
        online_config,
        repository,
        flag_service,
        MockLLMProvider(),
        ExplodingSTT(),
        DisabledTTSProvider(),
    )
    session = asyncio.run(engine.open_session(nickname="Ada", owner_id="p1"))

    with pytest.raises(NotFoundError, match="^Session not found$"):
        asyncio.run(engine.voice(session.id, tmp_path / "a.wav", owner_id="p2"))


def test_foreign_read_of_an_overdue_session_has_no_expiry_side_effect(
    online_config, repository, flag_service
):
    engine = _engine(online_config, repository, flag_service)
    session = _aged_session(engine, repository, "p1", seconds_left=-5)

    with pytest.raises(NotFoundError, match="^Session not found$"):
        asyncio.run(engine.get_session_serialized(session.id, owner_id="p2"))

    assert repository.get_session(session.id).status is SessionStatus.ACTIVE


def test_unlocked_readers_do_not_expire_a_session_during_a_running_turn(
    online_config, repository, flag_service
):
    llm = BlockingLLM()
    engine = _engine(online_config, repository, flag_service, llm=llm)
    session = _aged_session(engine, repository, "p1", seconds_left=1)

    async def exercise():
        turn = asyncio.create_task(engine.message(session.id, "one", owner_id="p1"))
        await llm.started.wait()
        await asyncio.sleep(1.2)  # the limit passes while the model is thinking
        overview = engine.player_overview("p1")
        engine.history(session.id, owner_id="p1")
        assert repository.get_session(session.id).status is SessionStatus.ACTIVE
        assert [item["id"] for item in overview["active_sessions"]] == [session.id]
        llm.release.set()
        await turn

    asyncio.run(exercise())
    assert [item.role for item in repository.list_messages(session.id)] == ["user", "assistant"]
    assert repository.get_session(session.id).status is SessionStatus.ACTIVE


def test_concurrent_opens_leave_a_single_active_session(online_config, repository, flag_service):
    llm = BlockingLLM()
    engine = _engine(online_config, repository, flag_service, llm=llm)

    async def exercise():
        first = await engine.open_session(nickname="Ada", owner_id="p1")
        turn = asyncio.create_task(engine.message(first.id, "one", owner_id="p1"))
        await llm.started.wait()
        opens = [
            asyncio.create_task(engine.open_session(nickname="Ada", owner_id="p1"))
            for _ in range(3)
        ]
        await asyncio.sleep(0.05)
        llm.release.set()
        await turn
        await asyncio.gather(*opens)

    asyncio.run(exercise())
    assert len(repository.active_sessions_for_owner("p1")) == 1


def test_concurrent_nickname_claim_has_exactly_one_winner(online_config, repository, flag_service):
    llm = BlockingLLM()
    engine = _engine(online_config, repository, flag_service, llm=llm)

    async def exercise():
        first = await engine.open_session(nickname="Ada", owner_id="p1")
        turn = asyncio.create_task(engine.message(first.id, "one", owner_id="p1"))
        await llm.started.wait()
        mine = asyncio.create_task(engine.open_session(nickname="Zed", owner_id="p1"))
        theirs = asyncio.create_task(engine.open_session(nickname="zed", owner_id="p2"))
        await asyncio.sleep(0.05)
        llm.release.set()
        await turn
        return await asyncio.gather(mine, theirs, return_exceptions=True)

    results = asyncio.run(exercise())
    failures = [item for item in results if isinstance(item, ConflictError)]
    assert len(failures) == 1
    assert failures[0].code == "nickname_taken"
    assert sum(isinstance(item, SessionRecord) for item in results) == 1


def test_reset_of_a_retired_session_respects_the_active_session_cap(
    online_config, repository, flag_service
):
    engine = _engine(online_config, repository, flag_service)
    first = asyncio.run(engine.open_session(nickname="Ada", owner_id="p1"))
    second = asyncio.run(engine.open_session(nickname="Ada", owner_id="p1"))

    replacement = asyncio.run(engine.reset_serialized(first.id, owner_id="p1"))
    again = asyncio.run(engine.reset_serialized(first.id, owner_id="p1"))

    assert [item.id for item in repository.active_sessions_for_owner("p1")] == [again.id]
    assert repository.get_session(second.id).status is SessionStatus.RESET
    assert repository.get_session(replacement.id).status is SessionStatus.RESET
    assert again.owner_id == "p1" and again.nickname == "Ada"


def test_reset_of_the_active_session_replaces_it(online_config, repository, flag_service):
    engine = _engine(online_config, repository, flag_service)
    session = asyncio.run(engine.open_session(nickname="Ada", level_id="level_2", owner_id="p1"))

    replacement = asyncio.run(engine.reset_serialized(session.id, owner_id="p1"))

    assert replacement.id != session.id
    assert replacement.level_id == "level_2"
    assert repository.get_session(session.id).status is SessionStatus.RESET
    assert [item.id for item in repository.active_sessions_for_owner("p1")] == [replacement.id]


def test_taken_nickname_does_not_retire_the_running_session(
    online_config, repository, flag_service
):
    engine = _engine(online_config, repository, flag_service)
    asyncio.run(engine.open_session(nickname="Ada", owner_id="p1"))
    mine = asyncio.run(engine.open_session(nickname="Carl", owner_id="p2"))

    with pytest.raises(ConflictError) as raised:
        asyncio.run(engine.open_session(nickname="ADA", owner_id="p2"))

    assert raised.value.code == "nickname_taken"
    assert repository.get_session(mine.id).status is SessionStatus.ACTIVE
    assert [item.id for item in repository.active_sessions_for_owner("p2")] == [mine.id]
