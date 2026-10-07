"""Small, thread-safe SQLite repository for sessions, messages and scores."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .domain import (
    InputModality,
    Language,
    LeaderboardEntry,
    MessageRecord,
    SessionRecord,
    SessionStatus,
    utc_now,
)
from .errors import NotFoundError


class SQLiteRepository:
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._memory_connection: sqlite3.Connection | None = None
        if self.database_path == ":memory:":
            self._memory_connection = self._new_connection()
        self.initialize()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _connect(self) -> sqlite3.Connection:
        return self._memory_connection or self._new_connection()

    def _close(self, connection: sqlite3.Connection) -> None:
        if connection is not self._memory_connection:
            connection.close()

    def initialize(self) -> None:
        connection = self._connect()
        try:
            if self.database_path != ":memory:":
                connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    mode_id TEXT NOT NULL,
                    level_id TEXT NOT NULL,
                    nickname TEXT,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    turn_count INTEGER NOT NULL DEFAULT 0,
                    hints_used INTEGER NOT NULL DEFAULT 0,
                    processing_seconds REAL NOT NULL DEFAULT 0,
                    score INTEGER,
                    last_language TEXT,
                    owner_id TEXT
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    language TEXT NOT NULL,
                    modality TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_sessions_leaderboard
                    ON sessions(level_id, status, score DESC);
                CREATE TABLE IF NOT EXISTS players (
                    id TEXT PRIMARY KEY,
                    recovery_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "processing_seconds" not in columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN processing_seconds REAL NOT NULL DEFAULT 0"
                )
            if "owner_id" not in columns:
                connection.execute("ALTER TABLE sessions ADD COLUMN owner_id TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_id, status)"
            )
            # Only completed ranked results survive a process restart. Active
            # conversations, Stand sessions, and transcripts are ephemeral.
            connection.execute(
                "DELETE FROM sessions WHERE mode_id != 'score' OR status != 'won'"
            )
            connection.commit()
        finally:
            self._close(connection)

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            id=row["id"],
            mode_id=row["mode_id"],
            level_id=row["level_id"],
            nickname=row["nickname"],
            status=SessionStatus(row["status"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=(datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None),
            turn_count=row["turn_count"],
            hints_used=row["hints_used"],
            processing_seconds=row["processing_seconds"],
            score=row["score"],
            last_language=(Language(row["last_language"]) if row["last_language"] else None),
            owner_id=row["owner_id"],
        )

    def create_session(self, session: SessionRecord) -> SessionRecord:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO sessions
                    (id, mode_id, level_id, nickname, status, started_at, completed_at,
                     turn_count, hints_used, processing_seconds, score, last_language, owner_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.mode_id,
                    session.level_id,
                    session.nickname,
                    session.status.value,
                    session.started_at.isoformat(),
                    session.completed_at.isoformat() if session.completed_at else None,
                    session.turn_count,
                    session.hints_used,
                    session.processing_seconds,
                    session.score,
                    session.last_language.value if session.last_language else None,
                    session.owner_id,
                ),
            )
            connection.commit()
            return session
        finally:
            self._close(connection)

    def get_session(self, session_id: str) -> SessionRecord:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        finally:
            self._close(connection)
        if row is None:
            raise NotFoundError("Session not found")
        return self._session_from_row(row)

    def add_message(self, message: MessageRecord) -> MessageRecord:
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO messages (session_id, role, content, language, modality, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message.session_id,
                    message.role,
                    message.content,
                    message.language.value,
                    message.modality.value,
                    message.created_at.isoformat(),
                ),
            )
            connection.commit()
            return message.model_copy(update={"id": cursor.lastrowid})
        finally:
            self._close(connection)

    def get_messages(self, session_id: str, limit: int = 24) -> list[MessageRecord]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT * FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
                """,
                (session_id, limit),
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

    def increment_turn(self, session_id: str, language: Language) -> SessionRecord:
        connection = self._connect()
        try:
            connection.execute(
                """
                UPDATE sessions
                SET turn_count = turn_count + 1, last_language = ?
                WHERE id = ?
                """,
                (language.value, session_id),
            )
            connection.commit()
        finally:
            self._close(connection)
        return self.get_session(session_id)

    def increment_hints(self, session_id: str) -> SessionRecord:
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE sessions SET hints_used = hints_used + 1 WHERE id = ?",
                (session_id,),
            )
            connection.commit()
        finally:
            self._close(connection)
        return self.get_session(session_id)

    def add_processing_time(self, session_id: str, seconds: float) -> SessionRecord:
        connection = self._connect()
        try:
            connection.execute(
                """
                UPDATE sessions
                SET processing_seconds = processing_seconds + ?
                WHERE id = ?
                """,
                (max(0.0, float(seconds)), session_id),
            )
            connection.commit()
        finally:
            self._close(connection)
        return self.get_session(session_id)

    def set_status(
        self,
        session_id: str,
        status: SessionStatus,
        *,
        score: int | None = None,
        completed_at: datetime | None = None,
    ) -> SessionRecord:
        finished = completed_at or (utc_now() if status is not SessionStatus.ACTIVE else None)
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE sessions SET status = ?, score = ?, completed_at = ? WHERE id = ?",
                (
                    status.value,
                    score,
                    finished.isoformat() if finished else None,
                    session_id,
                ),
            )
            connection.commit()
        finally:
            self._close(connection)
        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> None:
        connection = self._connect()
        try:
            cursor = connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            connection.commit()
        finally:
            self._close(connection)
        if cursor.rowcount == 0:
            raise NotFoundError("Session not found")

    def delete_messages(self, session_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            connection.commit()
        finally:
            self._close(connection)

    def leaderboard(self, level_id: str | None = None, limit: int = 10) -> list[LeaderboardEntry]:
        query = """
            SELECT nickname, level_id, score, turn_count, hints_used, completed_at
            FROM sessions
            WHERE mode_id = 'score' AND status = 'won' AND nickname IS NOT NULL
              AND score IS NOT NULL
        """
        parameters: list[object] = []
        if level_id is not None:
            query += " AND level_id = ?"
            parameters.append(level_id)
        query += " ORDER BY level_id, score DESC, completed_at ASC, turn_count ASC"
        connection = self._connect()
        try:
            rows = connection.execute(query, parameters).fetchall()
        finally:
            self._close(connection)

        # One entry per nickname and difficulty: only that player's best run is retained.
        best: list[sqlite3.Row] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            identity = (row["level_id"], row["nickname"].casefold())
            if identity not in seen:
                best.append(row)
                seen.add(identity)

        if level_id is None:
            # Cross-level results remain grouped and ranked independently.
            ranks: dict[str, int] = {}
            entries: list[LeaderboardEntry] = []
            for row in best[:limit]:
                ranks[row["level_id"]] = ranks.get(row["level_id"], 0) + 1
                entries.append(self._leaderboard_entry(row, ranks[row["level_id"]]))
            return entries
        return [self._leaderboard_entry(row, index + 1) for index, row in enumerate(best[:limit])]

    @staticmethod
    def _leaderboard_entry(row: sqlite3.Row, rank: int) -> LeaderboardEntry:
        return LeaderboardEntry(
            rank=rank,
            nickname=row["nickname"],
            level_id=row["level_id"],
            score=row["score"],
            turns=row["turn_count"],
            hints_used=row["hints_used"],
            completed_at=datetime.fromisoformat(row["completed_at"]),
        )

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

    def close(self) -> None:
        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None

    def add_messages(self, messages: Iterable[MessageRecord]) -> None:
        for message in messages:
            self.add_message(message)
