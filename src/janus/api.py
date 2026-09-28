"""FastAPI composition root and local-only REST API."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .config import LoadedConfig, load_config
from .domain import LanguagePreference, SessionRecord
from .engine import ChallengeEngine
from .errors import JanusError, NotFoundError, ValidationError
from .providers.llm import (
    FallbackLLMProvider,
    LLMProvider,
    MockLLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from .providers.stt import DisabledSTTProvider, FasterWhisperProvider, STTProvider
from .providers.tts import DisabledTTSProvider, PiperTTSProvider, SapiTTSProvider, TTSProvider
from .repository import SQLiteRepository
from .security import FlagService, SecretKeyStore


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSessionRequest(APIModel):
    mode_id: str | None = None
    level_id: str | None = None
    nickname: str | None = None


class MessageRequest(APIModel):
    text: str
    language: LanguagePreference = LanguagePreference.AUTO
    speak: bool = False


class SubmitRequest(APIModel):
    flag: str = Field(min_length=1, max_length=256)


class HintRequest(APIModel):
    language: LanguagePreference = LanguagePreference.AUTO


class AppContainer:
    def __init__(self, config: LoadedConfig, engine: ChallengeEngine) -> None:
        self.config = config
        self.engine = engine


def _default_config_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "configs"


def _build_llm(config: LoadedConfig) -> LLMProvider:
    settings = config.app.llm
    if settings.provider == "mock":
        return MockLLMProvider()
    if settings.provider == "ollama":
        provider: LLMProvider = OllamaProvider(settings)
    else:
        provider = OpenAICompatibleProvider(settings)
    return FallbackLLMProvider(provider) if settings.fallback_to_mock else provider


def _build_stt(config: LoadedConfig) -> STTProvider:
    settings = config.app.speech
    if settings.stt_provider == "faster_whisper":
        return FasterWhisperProvider(
            settings.stt_model,
            device=settings.stt_device,
            compute_type=settings.stt_compute_type,
            local_files_only=settings.stt_local_files_only,
        )
    return DisabledSTTProvider()


def _build_tts(config: LoadedConfig) -> TTSProvider:
    settings = config.app.speech
    if settings.tts_provider == "sapi":
        return SapiTTSProvider(config.resolve_runtime_path(settings.voice_output_dir))
    if settings.tts_provider == "piper":
        return PiperTTSProvider(
            config.resolve_runtime_path(settings.voice_output_dir),
            model_it=config.resolve_runtime_path(settings.piper_model_it),
            model_en=config.resolve_runtime_path(settings.piper_model_en),
            use_cuda=settings.piper_use_cuda,
        )
    return DisabledTTSProvider()


def create_app(
    *,
    config_dir: str | Path | None = None,
    loaded_config: LoadedConfig | None = None,
    repository: SQLiteRepository | None = None,
    flag_service: FlagService | None = None,
    llm_provider: LLMProvider | None = None,
    stt_provider: STTProvider | None = None,
    tts_provider: TTSProvider | None = None,
) -> FastAPI:
    config = loaded_config or load_config(config_dir or _default_config_dir())
    repo = repository or SQLiteRepository(config.resolve_runtime_path(config.app.database_path))
    if flag_service is None:
        key = SecretKeyStore(
            config.resolve_runtime_path(config.app.secret_key_path), config.app.secret_key_env
        ).load_or_create()
        flag_service = FlagService(key, config.app.flag_prefix)
    engine = ChallengeEngine(
        config,
        repo,
        flag_service,
        llm_provider or _build_llm(config),
        stt_provider or _build_stt(config),
        tts_provider or _build_tts(config),
    )
    container = AppContainer(config, engine)
    app = FastAPI(
        title=config.app.name,
        version=config.app.version,
        docs_url=None,
        redoc_url=None,
        openapi_url=f"{config.app.api_prefix}/openapi.json",
    )
    app.state.janus = container
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.app.allowed_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.app.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(JanusError)
    async def janus_error_handler(request: Request, exc: JanusError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.message,
                "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            },
        )

    prefix = config.app.api_prefix

    @app.middleware("http")
    async def kiosk_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "media-src 'self' blob:; connect-src 'self'; font-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), geolocation=(), microphone=(self), payment=(), usb=()"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith(prefix):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get(f"{prefix}/config")
    async def public_config() -> dict[str, Any]:
        levels = sorted(config.levels.values(), key=lambda item: item.order)
        return {
            "app": {
                "name": config.app.name,
                "version": config.app.version,
                "default_mode": config.app.default_mode,
                "default_level": config.app.default_level,
                "default_language": config.app.default_language,
                "session_ttl_minutes": config.app.session_ttl_minutes,
                "max_message_chars": config.app.max_message_chars,
            },
            "modes": [mode.model_dump(mode="json") for mode in config.modes.values()],
            "levels": [
                {
                    "id": level.id,
                    "order": level.order,
                    "difficulty": level.difficulty,
                    "name": level.name.model_dump(),
                    "description": level.description.model_dump(),
                    "objective": level.objective.model_dump(),
                    "time_limit_seconds": level.time_limit_seconds,
                    "hint_count": len(level.hints),
                }
                for level in levels
            ],
            "hardware": {
                "active_profile": config.app.default_hardware_profile,
                "profiles": [item.model_dump(mode="json") for item in config.hardware.values()],
            },
            "capabilities": {
                "text": True,
                "voice": config.app.speech.stt_provider != "disabled",
                "tts": config.app.speech.tts_provider != "disabled",
                "hints": any(level.hints for level in levels),
                "voice_input_configured": config.app.speech.stt_provider != "disabled",
                "voice_output_configured": config.app.speech.tts_provider != "disabled",
                "languages": ["it", "en"],
            },
        }

    @app.get(f"{prefix}/health")
    async def health() -> dict[str, Any]:
        llm_health, stt_health, tts_health = await asyncio.gather(
            engine.llm.health(), engine.stt.health(), engine.tts.health()
        )
        fallback_active = bool(llm_health.detail and "fallback mock active" in llm_health.detail)
        configured_components_ready = (
            llm_health.available
            and (
                config.app.speech.stt_provider == "disabled" or stt_health.available
            )
            and (
                config.app.speech.tts_provider == "disabled" or tts_health.available
            )
        )
        status = "ok" if configured_components_ready and not fallback_active else "degraded"
        return {
            "status": status,
            "version": config.app.version,
            "components": {
                "database": {"available": True, "detail": "SQLite"},
                "llm": llm_health.model_dump(),
                "stt": stt_health.model_dump(),
                "tts": tts_health.model_dump(),
            },
        }

    @app.post(f"{prefix}/sessions", response_model=SessionRecord, status_code=201)
    async def create_session(payload: CreateSessionRequest) -> SessionRecord:
        requested_mode = payload.mode_id or config.app.default_mode
        if requested_mode != config.app.default_mode:
            raise ValidationError(
                "Game mode is fixed by the operator at startup",
                details={"active_mode": config.app.default_mode},
            )
        return engine.create_session(
            mode_id=config.app.default_mode,
            level_id=payload.level_id,
            nickname=payload.nickname,
        )

    @app.get(f"{prefix}/sessions/{{session_id}}", response_model=SessionRecord)
    async def get_session(session_id: str) -> SessionRecord:
        return await engine.get_session_serialized(session_id)

    @app.delete(f"{prefix}/sessions/{{session_id}}", status_code=204)
    async def delete_session(session_id: str) -> Response:
        await engine.delete_serialized(session_id)
        return Response(status_code=204)

    @app.post(f"{prefix}/sessions/{{session_id}}/messages")
    async def send_message(session_id: str, payload: MessageRequest) -> dict[str, Any]:
        return (
            await engine.message(
                session_id,
                payload.text,
                language=payload.language,
                speak=payload.speak,
            )
        ).model_dump(mode="json")

    @app.post(f"{prefix}/sessions/{{session_id}}/voice")
    async def send_voice(
        session_id: str,
        request: Request,
        language: Annotated[LanguagePreference, Query()] = LanguagePreference.AUTO,
        speak: Annotated[bool, Query()] = True,
    ) -> dict[str, Any]:
        max_bytes = config.app.speech.max_audio_bytes
        try:
            declared_length = int(request.headers.get("content-length", "0") or "0")
        except ValueError as exc:
            raise ValidationError("Invalid Content-Length header") from exc
        if declared_length < 0:
            raise ValidationError("Invalid Content-Length header")
        if declared_length > max_bytes:
            raise ValidationError(f"Audio exceeds the {max_bytes}-byte limit")

        content_type = request.headers.get("content-type", "application/octet-stream")
        filename = "recording.bin"
        if content_type.startswith("multipart/form-data"):
            form = await request.form(max_files=1, max_fields=3, max_part_size=max_bytes)
            upload = form.get("file") or form.get("audio")
            if upload is None or not hasattr(upload, "read"):
                raise ValidationError("Multipart voice requests require a file or audio field")
            raw = await upload.read(max_bytes + 1)
            filename = getattr(upload, "filename", filename) or filename
            form_language = form.get("language")
            if form_language:
                try:
                    language = LanguagePreference(str(form_language))
                except ValueError as exc:
                    raise ValidationError("language must be auto, it, or en") from exc
        else:
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > max_bytes:
                    raise ValidationError(f"Audio exceeds the {max_bytes}-byte limit")
                body.extend(chunk)
            raw = bytes(body)

        if not raw:
            raise ValidationError("Audio body cannot be empty")
        if len(raw) > max_bytes:
            raise ValidationError(f"Audio exceeds the {max_bytes}-byte limit")
        suffix_by_type = {
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/webm": ".webm",
            "audio/ogg": ".ogg",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
        }
        suffix = Path(filename).suffix.lower()
        allowed_suffixes = {".wav", ".webm", ".ogg", ".mp3", ".m4a", ".mp4"}
        if suffix not in allowed_suffixes:
            suffix = suffix_by_type.get(content_type.split(";", 1)[0].lower(), ".bin")
        temp_dir = config.resolve_runtime_path(config.app.speech.voice_output_dir) / "incoming"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=temp_dir, suffix=suffix, delete=False) as handle:
                handle.write(raw)
                temporary_path = Path(handle.name)
            result = await engine.voice(
                session_id, temporary_path, language=language, speak=speak
            )
            return result.model_dump(mode="json")
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @app.post(f"{prefix}/sessions/{{session_id}}/submit")
    async def submit_flag(session_id: str, payload: SubmitRequest) -> dict[str, Any]:
        return (await engine.submit_serialized(session_id, payload.flag)).model_dump(mode="json")

    @app.post(f"{prefix}/sessions/{{session_id}}/hint")
    async def request_hint(
        session_id: str,
        payload: HintRequest | None = None,
        language: Annotated[LanguagePreference, Query()] = LanguagePreference.AUTO,
    ) -> dict[str, Any]:
        selected_language = payload.language if payload is not None else language
        return (await engine.hint_serialized(session_id, selected_language)).model_dump(
            mode="json"
        )

    @app.post(f"{prefix}/sessions/{{session_id}}/reset", response_model=SessionRecord)
    async def reset_session(session_id: str) -> SessionRecord:
        return await engine.reset_serialized(session_id)

    @app.get(f"{prefix}/leaderboard")
    async def leaderboard(
        level_id: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 10,
    ) -> dict[str, Any]:
        if level_id is not None and level_id not in config.levels:
            raise ValidationError("Unknown challenge level", details={"level_id": level_id})
        return {
            "level_id": level_id,
            "entries": [
                entry.model_dump(mode="json")
                for entry in engine.repository.leaderboard(level_id=level_id, limit=limit)
            ],
        }

    @app.get(f"{prefix}/audio/{{audio_id}}")
    async def audio(audio_id: str) -> FileResponse:
        path = engine.tts.resolve(audio_id)
        if path is None:
            raise NotFoundError("Audio artifact not found")
        return FileResponse(path, media_type="audio/wav", filename=f"janus-{audio_id}.wav")

    web_dir = Path(__file__).resolve().parent / "web"
    index_path = web_dir / "index.html"
    if index_path.is_file():
        app.mount("/static", StaticFiles(directory=web_dir), name="static")

        @app.get("/", include_in_schema=False)
        async def kiosk_index() -> FileResponse:
            return FileResponse(index_path, media_type="text/html")

    return app
