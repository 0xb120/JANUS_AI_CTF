"""Optional faster-whisper speech-to-text provider."""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from ..domain import HealthComponent, Language
from ..errors import FeatureUnavailableError, ProviderError


class Transcription(BaseModel):
    text: str
    language: Language


class STTProvider(Protocol):
    async def transcribe(self, audio_path: Path, language: Language | None = None) -> Transcription: ...

    async def health(self) -> HealthComponent: ...


class DisabledSTTProvider:
    def __init__(self, reason: str = "speech recognition is disabled") -> None:
        self.reason = reason

    async def transcribe(self, audio_path: Path, language: Language | None = None) -> Transcription:
        del audio_path, language
        raise FeatureUnavailableError(self.reason)

    async def health(self) -> HealthComponent:
        return HealthComponent(available=False, detail=self.reason)


class FasterWhisperProvider:
    """Loads the Whisper model lazily, avoiding startup failure on reduced hardware."""

    def __init__(
        self,
        model: str,
        device: str = "auto",
        compute_type: str = "int8",
        local_files_only: bool = True,
    ) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.local_files_only = local_files_only
        self._model = None
        self._model_lock = threading.Lock()

    def _load_model(self):
        if self._model is not None:
            return self._model
        if importlib.util.find_spec("faster_whisper") is None:
            raise FeatureUnavailableError(
                "faster-whisper is not installed; install JANUS with the speech extra"
            )
        with self._model_lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                    local_files_only=self.local_files_only,
                )
        return self._model

    def _transcribe_sync(self, audio_path: Path, language: Language | None) -> Transcription:
        try:
            model = self._load_model()
            segments, info = model.transcribe(
                str(audio_path),
                language=language.value if language else None,
                vad_filter=True,
                beam_size=5,
                condition_on_previous_text=False,
                initial_prompt=(
                    "JANUS, RomHack, MeetHack, flag, secret, prompt injection, jailbreak, "
                    "guardrail, Base64, sistema, istruzioni, segreto, autorizzazione."
                ),
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            detected = language or (Language.ITALIAN if info.language == "it" else Language.ENGLISH)
            if not text:
                raise ProviderError("No speech was detected in the recording")
            return Transcription(text=text, language=detected)
        except FeatureUnavailableError:
            raise
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Speech recognition failed: {exc}") from exc

    async def transcribe(self, audio_path: Path, language: Language | None = None) -> Transcription:
        return await asyncio.to_thread(self._transcribe_sync, audio_path, language)

    async def health(self) -> HealthComponent:
        if importlib.util.find_spec("faster_whisper") is None:
            return HealthComponent(available=False, detail="faster-whisper is not installed")
        try:
            # Preflight doubles as warm-up and proves that the offline model is
            # complete before the first participant reaches the microphone.
            await asyncio.to_thread(self._load_model)
            return HealthComponent(available=True, detail=self.model_name)
        except Exception as exc:  # noqa: BLE001 -- health reports third-party model failures.
            return HealthComponent(
                available=False, detail=f"faster-whisper model is not ready: {exc}"
            )
