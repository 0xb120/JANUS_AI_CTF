import sys
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from janus.__main__ import main
from janus.config import LLMSettings, load_config

ROOT = Path(__file__).resolve().parents[1]


def test_project_configuration_is_complete_and_cross_validated():
    config = load_config(ROOT / "configs")

    assert set(config.modes) == {"stand", "score"}
    assert [level.id for level in sorted(config.levels.values(), key=lambda item: item.order)] == [
        "level_1",
        "level_2",
        "level_3",
    ]
    assert config.modes["stand"].score_enabled is False
    assert config.modes["score"].nickname_required is True
    assert config.app.llm.fallback_to_mock is False
    assert config.app.speech.tts_provider == "piper"


def test_remote_model_servers_are_rejected():
    with pytest.raises(PydanticValidationError, match="local machine"):
        LLMSettings(base_url="https://models.example.com/v1")


def test_cli_cannot_bypass_local_llm_url_validation(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["janus", "--llm-base-url", "https://models.example.com/v1"],
    )
    with pytest.raises(PydanticValidationError, match="local machine"):
        main()


def test_huggingface_provider_defaults_to_router_and_hf_environment():
    settings = LLMSettings(provider="huggingface", model="Qwen/Qwen3-4B-Instruct-2507")

    assert settings.base_url == "https://router.huggingface.co/v1"
    assert settings.api_key_env == "HF_TOKEN"
    assert settings.bill_to_env == "HF_BILL_TO"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://models.example.com/v1",
        "http://router.huggingface.co/v1",
        "http://127.0.0.1:8080/v1",
    ],
)
def test_huggingface_provider_only_accepts_the_https_router(base_url):
    with pytest.raises(PydanticValidationError, match="Hugging Face provider only accepts"):
        LLMSettings(provider="huggingface", base_url=base_url)


@pytest.mark.parametrize("provider", ["ollama", "openai_compatible"])
def test_local_providers_still_reject_the_remote_router(provider):
    with pytest.raises(PydanticValidationError, match="local machine"):
        LLMSettings(provider=provider, base_url="https://router.huggingface.co/v1")


def _run_main_and_capture_config(monkeypatch, argv):
    import janus.__main__ as entrypoint

    captured = {}
    monkeypatch.setattr(sys, "argv", ["janus", *argv])
    monkeypatch.setattr(
        entrypoint, "create_app", lambda loaded_config: captured.setdefault("config", loaded_config)
    )
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *args, **kwargs: None)
    main()
    return captured["config"].app.llm


def test_cli_provider_switch_uses_the_new_provider_default_url(monkeypatch):
    llm = _run_main_and_capture_config(
        monkeypatch, ["--llm-provider", "huggingface", "--llm-model", "Qwen/Qwen3-4B-Instruct-2507"]
    )

    assert llm.provider == "huggingface"
    assert llm.base_url == "https://router.huggingface.co/v1"
    assert llm.api_key_env == "HF_TOKEN"


def test_cli_keeps_the_configured_url_when_the_provider_is_unchanged(monkeypatch):
    llm = _run_main_and_capture_config(monkeypatch, ["--llm-provider", "ollama"])

    assert llm.base_url == "http://127.0.0.1:11434"
