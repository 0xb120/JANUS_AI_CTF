"""Pydantic domain models shared by the engine, repository and API."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class Language(StrEnum):
    ITALIAN = "it"
    ENGLISH = "en"


class LanguagePreference(StrEnum):
    AUTO = "auto"
    ITALIAN = "it"
    ENGLISH = "en"


class InputModality(StrEnum):
    TEXT = "text"
    VOICE = "voice"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    WON = "won"
    RESET = "reset"
    EXPIRED = "expired"


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str
    content: str


class SessionRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    mode_id: str
    level_id: str
    nickname: str | None = None
    status: SessionStatus = SessionStatus.ACTIVE
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    turn_count: int = 0
    hints_used: int = 0
    processing_seconds: float = 0.0
    score: int | None = None
    last_language: Language | None = None
    # Online mode only. Never serialized: API clients must not learn player ids.
    owner_id: str | None = Field(default=None, exclude=True)


class MessageRecord(BaseModel):
    id: int | None = None
    session_id: str
    role: str
    content: str
    language: Language
    modality: InputModality = InputModality.TEXT
    created_at: datetime = Field(default_factory=utc_now)


class AudioArtifact(BaseModel):
    id: str
    mime_type: str = "audio/wav"


class TurnResult(BaseModel):
    session: SessionRecord
    user_text: str
    response_text: str
    language: Language
    transcript: str | None = None
    audio_url: str | None = None


class SubmissionResult(BaseModel):
    correct: bool
    session: SessionRecord
    score: int | None = None


class HintResult(BaseModel):
    hint: str
    hint_number: int
    session: SessionRecord


class LeaderboardEntry(BaseModel):
    rank: int
    nickname: str
    level_id: str
    score: int
    turns: int
    hints_used: int
    completed_at: datetime


class HealthComponent(BaseModel):
    available: bool
    detail: str | None = None
