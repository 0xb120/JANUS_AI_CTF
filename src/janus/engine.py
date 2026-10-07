"""Challenge orchestration independent from FastAPI and the graphical interface."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from pathlib import Path

from .config import ChallengeLevel, LoadedConfig
from .domain import (
    ChatMessage,
    HintResult,
    InputModality,
    Language,
    LanguagePreference,
    MessageRecord,
    SessionRecord,
    SessionStatus,
    SubmissionResult,
    TurnResult,
    utc_now,
)
from .errors import (
    CapacityError,
    ConflictError,
    InvalidSessionError,
    NotFoundError,
    ValidationError,
)
from .providers.llm import LLMProvider
from .providers.stt import STTProvider
from .providers.tts import TTSProvider
from .repository import SQLiteRepository
from .scoring import ScoreCalculator
from .security import FlagService
from .tool_runtime import SimulatedToolRuntime

_ITALIAN_WORDS = {
    "ciao",
    "buongiorno",
    "grazie",
    "sono",
    "come",
    "cosa",
    "perché",
    "perche",
    "questo",
    "questa",
    "segreto",
    "bandiera",
    "dimmi",
    "mostra",
    "puoi",
    "voglio",
    "istruzioni",
    "sistema",
    "non",
    "con",
    "per",
    "una",
    "gli",
    "della",
}
_ENGLISH_WORDS = {
    "hello",
    "hi",
    "thanks",
    "thank",
    "what",
    "why",
    "this",
    "secret",
    "flag",
    "tell",
    "show",
    "please",
    "can",
    "want",
    "instructions",
    "system",
    "the",
    "and",
    "with",
    "not",
    "your",
}


def detect_language(text: str, default: Language = Language.ITALIAN) -> Language:
    """A deterministic fallback detector for IT/EN; Whisper handles voice when available."""

    tokens = re.findall(r"[^\W\d_]+", text.casefold(), flags=re.UNICODE)
    italian_score = sum(token in _ITALIAN_WORDS for token in tokens)
    english_score = sum(token in _ENGLISH_WORDS for token in tokens)
    if italian_score > english_score:
        return Language.ITALIAN
    if english_score > italian_score:
        return Language.ENGLISH
    if re.search(r"[àèéìòù]", text.casefold()):
        return Language.ITALIAN
    return default


class ChallengeEngine:
    def __init__(
        self,
        config: LoadedConfig,
        repository: SQLiteRepository,
        flag_service: FlagService,
        llm: LLMProvider,
        stt: STTProvider,
        tts: TTSProvider,
        score_calculator: ScoreCalculator | None = None,
        tool_runtime: SimulatedToolRuntime | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.flag_service = flag_service
        self.llm = llm
        self.stt = stt
        self.tts = tts
        self.score_calculator = score_calculator or ScoreCalculator()
        self.tool_runtime = tool_runtime or SimulatedToolRuntime()
        self._audio_by_session: dict[str, set[str]] = {}
        self._audio_session: dict[str, str] = {}
        # Session ids are never reused. Keeping their locks for the process
        # avoids replacing a lock while an older waiter still references it.
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._owner_locks: dict[str, asyncio.Lock] = {}

    @property
    def default_language(self) -> Language:
        return Language(self.config.app.default_language)

    @staticmethod
    def _normalize_nickname(value: str) -> str:
        nickname = " ".join(value.strip().split())
        if not 2 <= len(nickname) <= 24:
            raise ValidationError("Nickname must contain between 2 and 24 characters")
        if not all(character.isalnum() or character in " _.-" for character in nickname):
            raise ValidationError("Nickname contains unsupported characters")
        return nickname

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
        if owner_id is None:
            return self.repository.create_session(record)
        # Per-owner serialization: concurrent opens cannot all pass the limit check.
        async with self._owner_lock(owner_id):
            # One player cannot multiply their LLM share by opening parallel sessions.
            limit = self.config.app.online.limits.max_active_sessions
            active = self.repository.active_sessions_for_owner(owner_id)
            for stale in active[: max(0, len(active) - limit + 1)]:
                try:
                    async with self._session_lock(stale.id):
                        self._retire(stale.id)
                except NotFoundError:
                    continue
            # No await from here to the insert: the nickname check and insert are atomic.
            if record.nickname is not None and self.repository.nickname_taken_by_other(
                record.nickname, owner_id
            ):
                raise ConflictError("Nickname already in use", code="nickname_taken")
            return self.repository.create_session(record)

    def _owner_lock(self, owner_id: str) -> asyncio.Lock:
        lock = self._owner_locks.get(owner_id)
        if lock is None:
            lock = asyncio.Lock()
            self._owner_locks[owner_id] = lock
        return lock

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
        if (
            session.status is SessionStatus.ACTIVE
            and self._effective_elapsed(session) >= self._time_limit_seconds(session)
        ):
            expired = self.repository.set_status(session.id, SessionStatus.EXPIRED)
            self.repository.delete_messages(session.id)
            self._purge_audio(session.id)
            return expired
        return session

    def _owned(
        self, session_id: str, owner_id: str | None, *, locked: bool = False
    ) -> SessionRecord:
        raw = self.repository.get_session(session_id)
        if owner_id is not None and raw.owner_id != owner_id:
            # Same error as a missing session, and no side effect for foreign callers.
            raise NotFoundError("Session not found")
        lock = self._session_locks.get(session_id)
        if locked or lock is None or not lock.locked():
            return self.get_session(session_id)
        # A turn is in flight: its processing time is not booked yet, so expiring the
        # session from an unlocked reader would corrupt the running turn.
        return raw

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

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._session_locks.get(session_id)
        if lock is None:
            # Do not allocate a permanent lock for arbitrary, nonexistent ids:
            # GET is cross-origin "simple" traffic and must not become an
            # unauthenticated memory-amplification primitive on localhost.
            self.repository.get_session(session_id)
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    async def get_session_serialized(
        self, session_id: str, owner_id: str | None = None
    ) -> SessionRecord:
        async with self._session_lock(session_id):
            return self._owned(session_id, owner_id, locked=True)

    def _purge_audio(self, session_id: str) -> None:
        for audio_id in self._audio_by_session.pop(session_id, set()):
            self._audio_session.pop(audio_id, None)
            self.tts.delete(audio_id)

    def _require_active(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        if session.status is not SessionStatus.ACTIVE:
            raise InvalidSessionError(
                f"Session is {session.status.value}", details={"status": session.status.value}
            )
        return session

    def _select_language(
        self,
        text: str,
        preference: LanguagePreference,
        session: SessionRecord,
    ) -> Language:
        if preference is LanguagePreference.ITALIAN:
            return Language.ITALIAN
        if preference is LanguagePreference.ENGLISH:
            return Language.ENGLISH
        return detect_language(text, session.last_language or self.default_language)

    def _render_system_prompt(
        self, level: ChallengeLevel, session: SessionRecord, language: Language
    ) -> str:
        flag = self.flag_service.derive(session.id, session.level_id)
        language_name = "Italian" if language is Language.ITALIAN else "English"
        prompt = level.system_prompt.replace("{{flag}}", flag).replace(
            "{{language_name}}", language_name
        )
        if level.guardrails:
            prompt += "\n\nAdditional security rules, in descending priority:\n"
            prompt += "\n".join(f"- {rule}" for rule in level.guardrails)
        return prompt

    @staticmethod
    def _apply_output_policy(
        level: ChallengeLevel,
        response: str,
        flag: str,
        language: Language,
    ) -> str:
        policy = level.output_policy
        if not policy.redact_exact_secret:
            return response
        redacted, matches = re.subn(re.escape(flag), "[REDACTED]", response, flags=re.IGNORECASE)
        if not matches:
            return response
        notice = policy.redaction.it if language is Language.ITALIAN else policy.redaction.en
        return f"{notice}\n{redacted}"

    @staticmethod
    def _redact_for_transient_history(text: str, flag: str) -> str:
        return re.sub(
            re.escape(flag), "[REDACTED_SESSION_FLAG]", text, flags=re.IGNORECASE
        )

    async def _generate_model_response(
        self,
        *,
        level: ChallengeLevel,
        messages: list[ChatMessage],
        flag: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        response = await self.llm.generate(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if level.simulated_tool is None or not level.simulated_tool.enabled:
            return response

        request = self.tool_runtime.parse(response)
        if request is None:
            return response
        result = self.tool_runtime.execute(request, secret=flag)
        if not result.authorized or result.trusted_context is None:
            return result.public_message

        # A second model pass receives a trusted result generated entirely by
        # the in-memory simulator. No shell, filesystem, or network capability
        # is reachable from this mechanism.
        return await self.llm.generate(
            [
                *messages,
                ChatMessage(role="assistant", content=response),
                ChatMessage(role="system", content=result.trusted_context),
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )

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
            self._owned(session_id, owner_id, locked=True)
            return await self._message_locked(
                session_id,
                text,
                language=language,
                modality=modality,
                speak=speak,
                transcript=transcript,
            )

    async def _message_locked(
        self,
        session_id: str,
        text: str,
        *,
        language: LanguagePreference,
        modality: InputModality,
        speak: bool,
        transcript: str | None,
    ) -> TurnResult:
        session = self._require_active(session_id)
        cleaned = text.strip()
        if not cleaned:
            raise ValidationError("Message cannot be empty")
        if len(cleaned) > self.config.app.max_message_chars:
            raise ValidationError(
                f"Message exceeds {self.config.app.max_message_chars} characters"
            )
        selected_language = self._select_language(cleaned, language, session)
        flag = self.flag_service.derive(session.id, session.level_id)
        prior_history = self.repository.get_messages(
            session.id, max(1, self.config.app.history_limit - 1)
        )
        user_message = self.repository.add_message(
            MessageRecord(
                session_id=session.id,
                role="user",
                content=self._redact_for_transient_history(cleaned, flag),
                language=selected_language,
                modality=modality,
            )
        )
        session = self.repository.increment_turn(session.id, selected_language)
        level = self.config.levels[session.level_id]
        messages = [
            ChatMessage(
                role="system",
                content=self._render_system_prompt(level, session, selected_language),
            ),
            *[ChatMessage(role=item.role, content=item.content) for item in prior_history],
            ChatMessage(role="user", content=cleaned),
        ]
        temperature = (
            level.generation.temperature
            if level.generation.temperature is not None
            else self.config.app.llm.temperature
        )
        max_tokens = (
            level.generation.max_tokens
            if level.generation.max_tokens is not None
            else self.config.app.llm.max_tokens
        )
        audio_url: str | None = None
        processing_started = time.perf_counter()
        try:
            response = await self._generate_model_response(
                level=level,
                messages=messages,
                flag=flag,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            response = response.strip()
            if not response:
                raise ValidationError("The local model returned an empty response")
            response = self._apply_output_policy(level, response, flag, selected_language)
            self.repository.add_message(
                MessageRecord(
                    session_id=session.id,
                    role="assistant",
                    content=self._redact_for_transient_history(response, flag),
                    language=selected_language,
                    modality=modality,
                )
            )

            if speak:
                try:
                    artifact = await self.tts.synthesize(response, selected_language)
                    self._audio_by_session.setdefault(session.id, set()).add(artifact.id)
                    self._audio_session[artifact.id] = session.id
                    audio_url = f"{self.config.app.api_prefix}/audio/{artifact.id}"
                except Exception:  # noqa: BLE001 -- optional TTS must not break text gameplay.
                    # Text gameplay remains available if an optional voice or driver fails.
                    audio_url = None
        except CapacityError:
            # The gate was full (possibly on a later pass of a tool-using level): the
            # player keeps the turn and the unanswered message is dropped.
            assert user_message.id is not None
            self.repository.delete_message(user_message.id)
            self.repository.decrement_turn(session.id)
            raise
        finally:
            self.repository.add_processing_time(
                session.id, time.perf_counter() - processing_started
            )
        return TurnResult(
            session=self.get_session(session.id),
            user_text=cleaned,
            response_text=response,
            language=selected_language,
            transcript=transcript,
            audio_url=audio_url,
        )

    async def voice(
        self,
        session_id: str,
        audio_path: Path,
        *,
        language: LanguagePreference = LanguagePreference.AUTO,
        speak: bool = True,
        owner_id: str | None = None,
    ) -> TurnResult:
        async with self._reject_if_turn_running(session_id, owner_id):
            self._owned(session_id, owner_id, locked=True)
            self._require_active(session_id)
            forced = None if language is LanguagePreference.AUTO else Language(language.value)
            processing_started = time.perf_counter()
            try:
                transcription = await self.stt.transcribe(audio_path, forced)
            finally:
                self.repository.add_processing_time(
                    session_id, time.perf_counter() - processing_started
                )
            preference = (
                LanguagePreference(transcription.language.value)
                if language is LanguagePreference.AUTO
                else language
            )
            return await self._message_locked(
                session_id,
                transcription.text,
                language=preference,
                modality=InputModality.VOICE,
                speak=speak,
                transcript=transcription.text,
            )

    async def submit_serialized(
        self, session_id: str, candidate: str, owner_id: str | None = None
    ) -> SubmissionResult:
        async with self._session_lock(session_id):
            self._owned(session_id, owner_id, locked=True)
            return self.submit(session_id, candidate)

    async def hint_serialized(
        self,
        session_id: str,
        language: LanguagePreference = LanguagePreference.AUTO,
        owner_id: str | None = None,
    ) -> HintResult:
        async with self._session_lock(session_id):
            self._owned(session_id, owner_id, locked=True)
            return self.hint(session_id, language)

    async def reset_serialized(self, session_id: str, owner_id: str | None = None) -> SessionRecord:
        async with self._session_lock(session_id):
            self._owned(session_id, owner_id, locked=True)
            return self.reset(session_id)

    async def delete_serialized(self, session_id: str, owner_id: str | None = None) -> None:
        async with self._session_lock(session_id):
            self._owned(session_id, owner_id, locked=True)
            self.delete(session_id)

    def submit(self, session_id: str, candidate: str) -> SubmissionResult:
        session = self.get_session(session_id)
        if session.status is SessionStatus.WON:
            correct = self.flag_service.verify(candidate, session.id, session.level_id)
            return SubmissionResult(correct=correct, session=session, score=session.score)
        if session.status is not SessionStatus.ACTIVE:
            raise InvalidSessionError(f"Session is {session.status.value}")
        correct = self.flag_service.verify(candidate, session.id, session.level_id)
        if not correct:
            return SubmissionResult(correct=False, session=session, score=None)

        mode = self.config.modes[session.mode_id]
        completed_at = utc_now()
        score = None
        if mode.score_enabled:
            score = self.score_calculator.calculate(
                session, self.config.levels[session.level_id].scoring, completed_at
            )
        won = self.repository.set_status(
            session.id,
            SessionStatus.WON,
            score=score,
            completed_at=completed_at,
        )
        self.repository.delete_messages(session.id)
        self._purge_audio(session.id)
        return SubmissionResult(correct=True, session=won, score=score)

    def hint(
        self,
        session_id: str,
        language: LanguagePreference = LanguagePreference.AUTO,
    ) -> HintResult:
        session = self._require_active(session_id)
        level = self.config.levels[session.level_id]
        if not level.hints:
            raise NotFoundError("No hints are configured for this level")
        hint_index = min(session.hints_used, len(level.hints) - 1)
        if session.hints_used < len(level.hints):
            session = self.repository.increment_hints(session.id)
        selected = (
            Language(language.value)
            if language is not LanguagePreference.AUTO
            else session.last_language or self.default_language
        )
        localized = level.hints[hint_index]
        return HintResult(
            hint=localized.it if selected is Language.ITALIAN else localized.en,
            hint_number=hint_index + 1,
            session=session,
        )

    def reset(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        self._purge_audio(session.id)
        replacement = self.create_session(
            mode_id=session.mode_id,
            level_id=session.level_id,
            nickname=session.nickname,
            owner_id=session.owner_id,
        )
        if session.mode_id == "stand":
            self.repository.delete_session(session.id)
        elif session.status is SessionStatus.ACTIVE:
            self.repository.set_status(session.id, SessionStatus.RESET)
            self.repository.delete_messages(session.id)
        return replacement

    def delete(self, session_id: str) -> None:
        session = self.get_session(session_id)
        if session.mode_id == "score" and session.status is SessionStatus.WON:
            raise InvalidSessionError("Completed ranked sessions are retained for the leaderboard")
        self._purge_audio(session.id)
        self.repository.delete_session(session.id)

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
            session = self._owned(stored.id, owner_id)
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

    async def expire_if_due(self, session_id: str) -> bool:
        try:
            async with self._session_lock(session_id):
                if self.repository.get_session(session_id).status is not SessionStatus.ACTIVE:
                    return False
                return self.get_session(session_id).status is SessionStatus.EXPIRED
        except NotFoundError:
            return False

    @staticmethod
    def _lock_is_idle(lock: asyncio.Lock) -> bool:
        # locked() is False between release() and the woken waiter resuming, so also
        # require an empty waiter queue. Relies on CPython's asyncio.Lock._waiters.
        return not lock.locked() and not getattr(lock, "_waiters", None)

    def prune_locks(self) -> int:
        removed = 0
        for session_id, lock in list(self._session_locks.items()):
            if not self._lock_is_idle(lock):
                continue
            try:
                active = self.repository.get_session(session_id).status is SessionStatus.ACTIVE
            except NotFoundError:
                active = False
            if not active:
                # Safe: an idle lock is free with no queued waiters, and acquiring
                # never yields between lookup and acquire.
                del self._session_locks[session_id]
                removed += 1
        # Owner locks are recreated on demand when idle.
        for owner_id, lock in list(self._owner_locks.items()):
            if self._lock_is_idle(lock):
                del self._owner_locks[owner_id]
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
