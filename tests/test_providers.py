from __future__ import annotations

import asyncio
import json
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
    HuggingFaceProvider,
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


HF_MODEL = "Qwen/Qwen3-4B-Instruct-2507"


def _whoami(*, role="fineGrained", scoped=None, global_permissions=(), orgs=()):
    return {
        "name": "player",
        "orgs": [{"name": name} for name in orgs],
        "auth": {
            "accessToken": {
                "role": role,
                "fineGrained": {
                    "global": list(global_permissions),
                    "scoped": [
                        {"entity": {"name": name}, "permissions": list(permissions)}
                        for name, permissions in (scoped or {}).items()
                    ],
                },
            }
        },
    }


def _router_models(providers=("nscale",)):
    return {
        "data": [
            {"id": HF_MODEL, "providers": [{"provider": name} for name in providers]},
            {"id": "other/model", "providers": [{"provider": "together"}]},
        ]
    }


def _hf_health(model, *, whoami=None, whoami_status=200, models=None):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "huggingface.co":
            assert request.url.path == "/api/whoami-v2"
            assert request.headers["Authorization"] == "Bearer hf_test"
            return httpx.Response(whoami_status, json=whoami or _whoami())
        assert request.url.host == "router.huggingface.co"
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json=models or _router_models())

    async def exercise():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = HuggingFaceProvider(
            LLMSettings(provider="huggingface", model=model), client=client
        )
        result = await provider.health()
        await client.aclose()
        return result

    return asyncio.run(exercise())


def test_huggingface_provider_uses_router_with_token_and_bill_to(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    monkeypatch.setenv("HF_BILL_TO", "acme-org")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://router.huggingface.co/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer hf_test"
        assert request.headers["X-HF-Bill-To"] == "acme-org"
        payload = json.loads(request.content)
        assert payload["model"] == f"{HF_MODEL}:nscale"
        assert "chat_template_kwargs" not in payload
        return httpx.Response(200, json={"choices": [{"message": {"content": " remote "}}]})

    async def exercise():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = HuggingFaceProvider(
            LLMSettings(provider="huggingface", model=f"{HF_MODEL}:nscale"), client=client
        )
        result = await provider.generate(
            [ChatMessage(role="user", content="hi")], temperature=0.2, max_tokens=64
        )
        await client.aclose()
        return result

    assert asyncio.run(exercise()) == "remote"


def test_huggingface_provider_omits_bill_to_header_when_unset(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    monkeypatch.delenv("HF_BILL_TO", raising=False)
    provider = HuggingFaceProvider(LLMSettings(provider="huggingface", model=HF_MODEL))

    assert "X-HF-Bill-To" not in provider._headers()


def test_huggingface_generate_without_token_fails_without_calling_the_router(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)

    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the router must not be called without a token")

    async def exercise():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = HuggingFaceProvider(
            LLMSettings(provider="huggingface", model=HF_MODEL), client=client
        )
        try:
            await provider.generate(
                [ChatMessage(role="user", content="hi")], temperature=0.2, max_tokens=64
            )
        finally:
            await client.aclose()

    with pytest.raises(ProviderError, match="HF_TOKEN is not set"):
        asyncio.run(exercise())
    health = asyncio.run(
        HuggingFaceProvider(LLMSettings(provider="huggingface", model=HF_MODEL)).health()
    )
    assert health.available is False
    assert "HF_TOKEN is not set" in health.detail


def test_huggingface_health_reports_ready_for_a_served_model(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    monkeypatch.setenv("HF_BILL_TO", "acme-org")
    whoami = _whoami(scoped={"acme-org": ["inference.serverless.write"]})

    for model in (HF_MODEL, f"{HF_MODEL}:nscale", f"{HF_MODEL}:fastest"):
        health = _hf_health(model, whoami=whoami)
        assert health.available is True, health.detail
        assert health.detail == f"{model} via Hugging Face"


def test_huggingface_health_rejects_an_invalid_token(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test")

    health = _hf_health(HF_MODEL, whoami_status=401)

    assert health.available is False
    assert "rejected" in health.detail


def test_huggingface_health_detects_a_token_that_cannot_bill_the_org(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    monkeypatch.setenv("HF_BILL_TO", "acme-org")
    whoami = _whoami(scoped={"other-org": ["inference.serverless.write"]})

    health = _hf_health(HF_MODEL, whoami=whoami)

    assert health.available is False
    assert "cannot bill acme-org" in health.detail


def test_huggingface_health_accepts_classic_tokens_of_org_members(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    monkeypatch.setenv("HF_BILL_TO", "acme-org")

    member = _hf_health(HF_MODEL, whoami=_whoami(role="write", orgs=["acme-org"]))
    outsider = _hf_health(HF_MODEL, whoami=_whoami(role="write", orgs=["other-org"]))

    assert member.available is True
    assert outsider.available is False


def test_huggingface_health_requires_the_model_and_pinned_provider(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    monkeypatch.delenv("HF_BILL_TO", raising=False)

    unknown = _hf_health("unknown/model")
    wrong_provider = _hf_health(f"{HF_MODEL}:together")

    assert unknown.available is False
    assert "not served" in unknown.detail
    assert wrong_provider.available is False
    assert "together" in wrong_provider.detail


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
