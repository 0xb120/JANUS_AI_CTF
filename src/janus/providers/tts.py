"""Local text-to-speech providers with session-ephemeral WAV artifacts."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import threading
import uuid
import wave
from pathlib import Path
from typing import Protocol

from ..domain import AudioArtifact, HealthComponent, Language
from ..errors import FeatureUnavailableError, ProviderError


class TTSProvider(Protocol):
    async def synthesize(self, text: str, language: Language) -> AudioArtifact: ...

    def resolve(self, audio_id: str) -> Path | None: ...

    def delete(self, audio_id: str) -> None: ...

    async def health(self) -> HealthComponent: ...


class DisabledTTSProvider:
    def __init__(self, reason: str = "speech synthesis is disabled") -> None:
        self.reason = reason

    async def synthesize(self, text: str, language: Language) -> AudioArtifact:
        del text, language
        raise FeatureUnavailableError(self.reason)

    def resolve(self, audio_id: str) -> Path | None:
        del audio_id
        return None

    def delete(self, audio_id: str) -> None:
        del audio_id

    async def health(self) -> HealthComponent:
        return HealthComponent(available=False, detail=self.reason)


class _LocalWavStore:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Audio is session-ephemeral. A clean process start has no valid owner
        # for artifacts left behind by a previous crash.
        for path in self.output_dir.glob("*.wav"):
            if re.fullmatch(r"[0-9a-f]{32}\.wav", path.name):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _artifact_path(self, audio_id: str) -> Path:
        return self.output_dir / f"{audio_id}.wav"

    def _validate_artifact(self, audio_id: str) -> None:
        path = self._artifact_path(audio_id)
        if not path.is_file() or path.stat().st_size <= 44:
            raise ProviderError("Speech synthesis did not produce a valid WAV artifact")

    def resolve(self, audio_id: str) -> Path | None:
        if not re.fullmatch(r"[0-9a-f]{32}", audio_id):
            return None
        path = self._artifact_path(audio_id)
        return path if path.is_file() else None

    def delete(self, audio_id: str) -> None:
        if re.fullmatch(r"[0-9a-f]{32}", audio_id):
            try:
                self._artifact_path(audio_id).unlink(missing_ok=True)
            except OSError:
                pass


class SapiTTSProvider(_LocalWavStore):
    def __init__(self, output_dir: Path) -> None:
        super().__init__(output_dir)
        self._health_cache: HealthComponent | None = None
        self._health_lock = asyncio.Lock()

    @staticmethod
    def _dependency_available() -> bool:
        return os.name == "nt" and importlib.util.find_spec("win32com") is not None

    @staticmethod
    def _pick_voice(voice, language: Language):
        target = "410" if language is Language.ITALIAN else "409"
        voices = voice.GetVoices()
        for index in range(voices.Count):
            token = voices.Item(index)
            try:
                installed_languages = token.GetAttribute("Language").lower().split(";")
            except Exception:  # noqa: BLE001, S112 -- skip malformed third-party voice tokens.
                continue
            if target in installed_languages:
                return token
        return None

    def _synthesize_sync(self, text: str, language: Language, audio_id: str) -> None:
        if not self._dependency_available():
            raise FeatureUnavailableError(
                "Windows SAPI requires Windows and pywin32; install JANUS with the speech extra"
            )
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            try:
                voice = win32com.client.Dispatch("SAPI.SpVoice")
                selected = self._pick_voice(voice, language)
                if selected is None:
                    raise FeatureUnavailableError(
                        f"No Windows SAPI voice is available for {language.value}"
                    )
                voice.Voice = selected
                stream = win32com.client.Dispatch("SAPI.SpFileStream")
                try:
                    stream.Format.Type = 22  # 22 kHz, 16-bit, mono PCM
                    stream.Open(str(self._artifact_path(audio_id)), 3, False)
                    voice.AudioOutputStream = stream
                    voice.Speak(text, 0)
                finally:
                    try:
                        stream.Close()
                    except Exception:  # noqa: BLE001, S110 -- best-effort COM cleanup.
                        pass
                    del stream
                    del voice
                    if selected is not None:
                        del selected
            finally:
                pythoncom.CoUninitialize()
        except FeatureUnavailableError:
            raise
        except Exception as exc:
            raise ProviderError(f"Windows speech synthesis failed: {exc}") from exc

    async def synthesize(self, text: str, language: Language) -> AudioArtifact:
        audio_id = uuid.uuid4().hex
        try:
            await asyncio.to_thread(self._synthesize_sync, text, language, audio_id)
            self._validate_artifact(audio_id)
        except Exception:
            self.delete(audio_id)
            raise
        return AudioArtifact(id=audio_id)

    async def health(self) -> HealthComponent:
        if self._health_cache is not None:
            return self._health_cache
        async with self._health_lock:
            if self._health_cache is not None:
                return self._health_cache
            if not self._dependency_available():
                self._health_cache = HealthComponent(
                    available=False, detail="Windows SAPI/pywin32 unavailable"
                )
                return self._health_cache
            artifacts: list[AudioArtifact] = []
            try:
                # A real file probe catches machines where SAPI is installed but
                # voices are unavailable to the kiosk security context.
                for text, language in (
                    ("Controllo audio JANUS.", Language.ITALIAN),
                    ("JANUS audio check.", Language.ENGLISH),
                ):
                    artifacts.append(await self.synthesize(text, language))
                self._health_cache = HealthComponent(
                    available=True, detail="Windows SAPI, Italian and English probe passed"
                )
            except Exception as exc:  # noqa: BLE001 -- health reports COM/driver failures.
                self._health_cache = HealthComponent(
                    available=False, detail=f"Windows SAPI probe failed: {exc}"
                )
            finally:
                for artifact in artifacts:
                    self.delete(artifact.id)
            return self._health_cache


class PiperTTSProvider(_LocalWavStore):
    """CPU-first bilingual neural TTS using two explicitly provisioned Piper voices."""

    def __init__(
        self,
        output_dir: Path,
        *,
        model_it: Path,
        model_en: Path,
        use_cuda: bool = False,
    ) -> None:
        super().__init__(output_dir)
        self.models = {
            Language.ITALIAN: model_it.resolve(),
            Language.ENGLISH: model_en.resolve(),
        }
        self.use_cuda = use_cuda
        self._voices: dict[Language, object] = {}
        self._voice_lock = threading.Lock()

    @staticmethod
    def _dependency_available() -> bool:
        return importlib.util.find_spec("piper") is not None

    @staticmethod
    def _config_path(model_path: Path) -> Path:
        return Path(f"{model_path}.json")

    def _voice_for(self, language: Language):
        voice = self._voices.get(language)
        if voice is not None:
            return voice
        from piper import PiperVoice

        model_path = self.models[language]
        voice = PiperVoice.load(str(model_path), use_cuda=self.use_cuda)
        self._voices[language] = voice
        return voice

    def _synthesize_sync(self, text: str, language: Language, audio_id: str) -> None:
        if not self._dependency_available():
            raise FeatureUnavailableError(
                "Piper is not installed; install JANUS with the speech extra"
            )
        model_path = self.models[language]
        config_path = self._config_path(model_path)
        if not model_path.is_file() or not config_path.is_file():
            raise FeatureUnavailableError(
                f"Piper voice is incomplete for {language.value}; prepare its ONNX and JSON files"
            )
        try:
            # Piper voice objects are cached, and access is serialized because
            # the underlying ONNX session is not treated as re-entrant here.
            with self._voice_lock:
                voice = self._voice_for(language)
                with wave.open(str(self._artifact_path(audio_id)), "wb") as wav_file:
                    voice.synthesize_wav(text, wav_file)
        except FeatureUnavailableError:
            raise
        except Exception as exc:
            raise ProviderError(f"Piper speech synthesis failed: {exc}") from exc

    async def synthesize(self, text: str, language: Language) -> AudioArtifact:
        audio_id = uuid.uuid4().hex
        try:
            await asyncio.to_thread(self._synthesize_sync, text, language, audio_id)
            self._validate_artifact(audio_id)
        except Exception:
            self.delete(audio_id)
            raise
        return AudioArtifact(id=audio_id)

    async def health(self) -> HealthComponent:
        if not self._dependency_available():
            return HealthComponent(available=False, detail="Piper is not installed")
        missing: list[str] = []
        for language, model_path in self.models.items():
            if not model_path.is_file() or not self._config_path(model_path).is_file():
                missing.append(language.value)
        if missing:
            return HealthComponent(
                available=False,
                detail=f"Piper voice files missing or incomplete: {', '.join(missing)}",
            )
        return HealthComponent(
            available=True,
            detail="Piper CPU voices ready" if not self.use_cuda else "Piper CUDA voices ready",
        )
