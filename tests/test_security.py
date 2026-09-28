from pathlib import Path

import pytest

from janus.errors import ConfigurationError
from janus.security import SecretKeyStore


def test_flags_are_stable_per_session_and_distinct_between_sessions(flag_service):
    first = flag_service.derive("session-a", "level_1")
    repeated = flag_service.derive("session-a", "level_1")
    second = flag_service.derive("session-b", "level_1")

    assert first == repeated
    assert first != second
    assert first.startswith("RH26{LEVEL1-")
    assert flag_service.verify(first, "session-a", "level_1") is True
    assert flag_service.verify(first.lower(), "session-a", "level_1") is True
    assert flag_service.verify(first + "x", "session-a", "level_1") is False


def test_key_store_creates_and_reuses_a_32_byte_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JANUS_TEST_KEY", raising=False)
    store = SecretKeyStore(tmp_path / "runtime" / "key", "JANUS_TEST_KEY")

    first = store.load_or_create()
    second = store.load_or_create()

    assert len(first) == 32
    assert first == second


def test_short_environment_key_is_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JANUS_TEST_KEY", "too-short")
    with pytest.raises(ConfigurationError, match="at least 32 bytes"):
        SecretKeyStore(tmp_path / "unused", "JANUS_TEST_KEY").load_or_create()
