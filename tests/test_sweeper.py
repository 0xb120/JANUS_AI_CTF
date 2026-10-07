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


def test_sweep_expires_abandoned_sessions_and_prunes_locks(
    loaded_config, repository, flag_service
):
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

    local_sweeper = SessionSweeper(
        _engine(loaded_config, repository, flag_service), rate_limiter=RateLimiter()
    )
    online_sweeper = SessionSweeper(
        _engine(online, repository, flag_service), rate_limiter=RateLimiter()
    )
    local_report = asyncio.run(local_sweeper.sweep_once())
    online_report = asyncio.run(online_sweeper.sweep_once())

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


def test_prune_locks_removes_idle_owner_locks(loaded_config, repository, flag_service):
    engine = _engine(loaded_config, repository, flag_service)
    asyncio.run(engine.open_session(nickname="Ada", owner_id="p1"))
    assert "p1" in engine._owner_locks

    assert engine.prune_locks() == 1
    assert "p1" not in engine._owner_locks


def test_prune_locks_keeps_held_owner_lock(loaded_config, repository, flag_service):
    engine = _engine(loaded_config, repository, flag_service)

    async def scenario():
        lock = engine._owner_lock("p1")
        async with lock:
            assert engine.prune_locks() == 0
            assert engine._owner_locks["p1"] is lock

    asyncio.run(scenario())


def test_prune_locks_keeps_owner_lock_with_pending_waiter(
    loaded_config, repository, flag_service
):
    engine = _engine(loaded_config, repository, flag_service)

    async def scenario():
        lock = engine._owner_lock("p1")
        await lock.acquire()

        async def waiter():
            async with engine._owner_lock("p1") as _:
                return engine._owner_locks.get("p1")

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        lock.release()
        # The waiter has not resumed yet: locked() is False but it is still queued.
        assert not lock.locked()
        assert engine.prune_locks() == 0
        assert engine._owner_locks["p1"] is lock
        assert await task is lock

    asyncio.run(scenario())


def test_prune_locks_keeps_session_lock_with_pending_waiter(
    loaded_config, repository, flag_service
):
    engine = _engine(loaded_config, repository, flag_service)
    repository.create_session(
        SessionRecord(id="done", mode_id="score", level_id="level_1", nickname="Ada",
                      status=SessionStatus.EXPIRED)
    )

    async def scenario():
        lock = engine._session_lock("done")
        await lock.acquire()

        async def waiter():
            async with engine._session_lock("done"):
                return engine._session_locks.get("done")

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        lock.release()
        assert engine.prune_locks() == 0
        assert engine._session_locks["done"] is lock
        assert await task is lock

    asyncio.run(scenario())
