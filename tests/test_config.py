import sys
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from janus.__main__ import main
from janus.config import AppSettings, LLMSettings, OnlineLimits, OnlineSettings, load_config

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


def test_online_mode_defaults_to_disabled_and_keeps_local_hosts():
    config = load_config(ROOT / "configs")

    assert config.app.online.enabled is False
    assert config.app.online.limits == OnlineLimits()
    assert config.app.effective_allowed_hosts() == config.app.allowed_hosts
    assert config.app.effective_cors_origins() == config.app.cors_origins
    assert (config.app.llm.max_concurrent, config.app.llm.max_queue) == (8, 16)


def test_online_mode_requires_a_bare_public_host():
    with pytest.raises(PydanticValidationError, match="requires public_host"):
        OnlineSettings(enabled=True)
    with pytest.raises(PydanticValidationError, match="bare hostname"):
        OnlineSettings(enabled=True, public_host="https://ctf.example.com")


def test_online_mode_exposes_only_the_public_origin():
    app = AppSettings(online=OnlineSettings(enabled=True, public_host="CTF.Example.com"))

    assert "ctf.example.com" in app.effective_allowed_hosts()
    assert "127.0.0.1" in app.effective_allowed_hosts()
    assert app.effective_cors_origins() == ["https://ctf.example.com"]


def test_trusted_proxies_must_be_ip_networks():
    online = OnlineSettings(trusted_proxies=["172.30.57.0/24", "10.0.0.5"])

    assert online.trusted_proxies == ["172.30.57.0/24", "10.0.0.5/32"]
    with pytest.raises(PydanticValidationError, match="trusted_proxies"):
        OnlineSettings(trusted_proxies=["proxy.local"])


def test_cli_enables_online_mode_and_wires_proxy_headers(monkeypatch):
    import janus.__main__ as entrypoint

    captured = {}
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "janus",
            "--online",
            "--public-host",
            "ctf.example.com",
            "--trusted-proxies",
            "172.30.57.0/24",
            "--host",
            "0.0.0.0",
        ],
    )
    monkeypatch.setattr(
        entrypoint, "create_app", lambda loaded_config: captured.setdefault("config", loaded_config)
    )
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda app, **kwargs: captured.setdefault("run", kwargs)
    )
    main()

    online = captured["config"].app.online
    assert online.enabled is True
    assert online.public_host == "ctf.example.com"
    assert online.trusted_proxies == ["172.30.57.0/24"]
    assert captured["run"]["proxy_headers"] is True
    assert captured["run"]["forwarded_allow_ips"] == "172.30.57.0/24"


def test_cli_ignores_forwarded_headers_without_trusted_proxies(monkeypatch):
    import janus.__main__ as entrypoint

    captured = {}
    monkeypatch.setattr(sys, "argv", ["janus"])
    monkeypatch.setattr(entrypoint, "create_app", lambda loaded_config: object())
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda app, **kwargs: captured.setdefault("run", kwargs)
    )
    main()

    assert captured["run"]["proxy_headers"] is False
    assert "forwarded_allow_ips" not in captured["run"]


@pytest.mark.parametrize(("argv", "expected"), [([], True), (["--no-access-log"], False)])
def test_cli_can_disable_the_access_log(monkeypatch, argv, expected):
    import janus.__main__ as entrypoint

    captured = {}
    monkeypatch.setattr(sys, "argv", ["janus", *argv])
    monkeypatch.setattr(entrypoint, "create_app", lambda loaded_config: object())
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda app, **kwargs: captured.setdefault("run", kwargs)
    )
    main()

    assert captured["run"]["access_log"] is expected
