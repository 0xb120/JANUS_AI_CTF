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
