from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from janus.config import load_config
from janus.engine import ChallengeEngine
from janus.providers.llm import MockLLMProvider
from janus.providers.stt import DisabledSTTProvider
from janus.providers.tts import DisabledTTSProvider
from janus.repository import SQLiteRepository
from janus.security import FlagService


@pytest.fixture
def loaded_config(tmp_path: Path):
    config = load_config(ROOT / "configs")
    app = config.app.model_copy(
        update={
            "database_path": tmp_path / "janus.sqlite3",
            "secret_key_path": tmp_path / "janus.key",
            "speech": config.app.speech.model_copy(
                update={"stt_provider": "disabled", "tts_provider": "disabled"}
            ),
            "llm": config.app.llm.model_copy(update={"provider": "mock"}),
        }
    )
    return config.model_copy(update={"app": app})


@pytest.fixture
def repository(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "test.sqlite3")
    yield repo
    repo.close()


@pytest.fixture
def flag_service():
    return FlagService(b"test-key-" * 4, "RH26")


@pytest.fixture
def mock_llm():
    return MockLLMProvider()


@pytest.fixture
def engine(loaded_config, repository, flag_service, mock_llm):
    return ChallengeEngine(
        loaded_config,
        repository,
        flag_service,
        mock_llm,
        DisabledSTTProvider(),
        DisabledTTSProvider(),
    )
