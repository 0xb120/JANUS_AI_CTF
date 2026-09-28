"""Optional local model and speech provider adapters."""

from .llm import (
    FallbackLLMProvider,
    LLMProvider,
    MockLLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from .stt import DisabledSTTProvider, FasterWhisperProvider, STTProvider, Transcription
from .tts import DisabledTTSProvider, PiperTTSProvider, SapiTTSProvider, TTSProvider

__all__ = [
    "DisabledSTTProvider",
    "DisabledTTSProvider",
    "FallbackLLMProvider",
    "FasterWhisperProvider",
    "LLMProvider",
    "MockLLMProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "PiperTTSProvider",
    "STTProvider",
    "SapiTTSProvider",
    "TTSProvider",
    "Transcription",
]
