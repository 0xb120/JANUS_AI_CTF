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
