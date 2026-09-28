from __future__ import annotations

import asyncio
import sys
import types
import wave
from typing import ClassVar

import httpx
import pytest

from janus.config import LLMSettings
from janus.domain import ChatMessage, Language
from janus.errors import ProviderError
from janus.providers.llm import (
    FallbackLLMProvider,
    MockLLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from janus.providers.tts import PiperTTSProvider, SapiTTSProvider


def test_openai_compatible_provider_uses_local_chat_completions_contract():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert b'"enable_thinking":false' in request.content
        return httpx.Response(200, json={"choices": [{"message": {"content": "  hello  "}}]})

    async def exercise():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
        provider = OpenAICompatibleProvider(
            LLMSettings(base_url="http://127.0.0.1:8080/v1", model="test"), client=client
        )
        result = await provider.generate(
            [ChatMessage(role="user", content="hi")], temperature=0.2, max_tokens=64
        )
        await client.aclose()
        return result

    result = asyncio.run(exercise())
    assert result == "hello"


def test_ollama_provider_uses_local_chat_contract():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        return httpx.Response(200, json={"message": {"content": "vault closed"}})

    async def exercise():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
        provider = OllamaProvider(
            LLMSettings(
                provider="ollama", base_url="http://localhost:11434", model="qwen-test"
            ),
            client=client,
        )
        result = await provider.generate(
            [ChatMessage(role="user", content="hi")], temperature=0.2, max_tokens=64
        )
        await client.aclose()
        return result

    result = asyncio.run(exercise())
    assert result == "vault closed"


def test_ollama_health_requires_the_configured_model():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen3:4b-instruct"}]})

    async def exercise(model):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
        provider = OllamaProvider(
            LLMSettings(provider="ollama", base_url="http://localhost:11434", model=model),
            client=client,
        )
        result = await provider.health()
        await client.aclose()
        return result

    assert asyncio.run(exercise("qwen3:4b-instruct")).available is True
    missing = asyncio.run(exercise("qwen3:8b"))
    assert missing.available is False
    assert "not installed" in missing.detail


def test_explicit_mock_fallback_keeps_text_ui_usable():
    class FailingProvider:
        async def generate(self, messages, *, temperature, max_tokens):
            raise ProviderError("llama-server offline")

        async def health(self):
            from janus.domain import HealthComponent

            return HealthComponent(available=False, detail="offline")

    provider = FallbackLLMProvider(FailingProvider(), MockLLMProvider())
    result = asyncio.run(
        provider.generate(
            [ChatMessage(role="system", content="Answer in Italian")],
            temperature=0.2,
            max_tokens=64,
        )
    )
    health = asyncio.run(provider.health())

    assert "caveau" in result.lower()
    assert health.available is True
    assert "fallback mock active" in health.detail


def test_sapi_audio_resolution_rejects_path_traversal(tmp_path):
    provider = SapiTTSProvider(tmp_path)

    assert provider.resolve("../secret") is None
    assert provider.resolve("not-an-audio-id") is None


def test_sapi_removes_partial_wav_when_synthesis_fails(tmp_path, monkeypatch):
    provider = SapiTTSProvider(tmp_path)

    def fail_after_open(text, language, audio_id):
        del text, language
        provider._artifact_path(audio_id).write_bytes(b"partial")
        raise ProviderError("voice unavailable")

    monkeypatch.setattr(provider, "_synthesize_sync", fail_after_open)
    with pytest.raises(ProviderError, match="voice unavailable"):
        asyncio.run(provider.synthesize("ciao", Language.ITALIAN))

    assert list(tmp_path.glob("*.wav")) == []


def test_piper_generates_bilingual_local_wav_and_rejects_traversal(tmp_path, monkeypatch):
    class FakeVoice:
        def synthesize_wav(self, text, wav_file):
            del text
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22_050)
            wav_file.writeframes(b"\x00\x00" * 200)

    class FakePiperVoice:
        loaded: ClassVar[list[tuple[str, bool]]] = []

        @classmethod
        def load(cls, path, use_cuda=False):
            cls.loaded.append((path, use_cuda))
            return FakeVoice()

    fake_module = types.ModuleType("piper")
    fake_module.PiperVoice = FakePiperVoice
    monkeypatch.setitem(sys.modules, "piper", fake_module)
    monkeypatch.setattr(PiperTTSProvider, "_dependency_available", staticmethod(lambda: True))

    model_it = tmp_path / "it.onnx"
    model_en = tmp_path / "en.onnx"
    for model in (model_it, model_en):
        model.write_bytes(b"model")
        (tmp_path / f"{model.name}.json").write_text("{}", encoding="utf-8")

    provider = PiperTTSProvider(
        tmp_path / "audio", model_it=model_it, model_en=model_en, use_cuda=False
    )
    artifact_it = asyncio.run(provider.synthesize("ciao", Language.ITALIAN))
    artifact_en = asyncio.run(provider.synthesize("hello", Language.ENGLISH))

    for artifact in (artifact_it, artifact_en):
        path = provider.resolve(artifact.id)
        assert path is not None
        with wave.open(str(path), "rb") as wav_file:
            assert wav_file.getnframes() == 200
    assert len(FakePiperVoice.loaded) == 2
    assert provider.resolve("../secret") is None
