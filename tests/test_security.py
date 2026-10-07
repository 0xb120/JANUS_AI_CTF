import re
import uuid
from pathlib import Path

import pytest

from janus.errors import ConfigurationError
from janus.security import (
    PLAYER_TOKEN_LABEL,
    AccessCodeVerifier,
    FlagService,
    PlayerTokenService,
    SecretKeyStore,
    generate_recovery_code,
    hash_recovery_code,
    normalize_recovery_code,
)


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


def _tokens(now=1_000_000.0):
    service = FlagService(b"k" * 32)
    return PlayerTokenService(service.derive_subkey(PLAYER_TOKEN_LABEL), clock=lambda: now)


def test_player_token_round_trip_and_expiry():
    player_id = str(uuid.uuid4())
    token = _tokens().issue(player_id, 1_000_100)

    assert token.startswith("v1.")
    verified = _tokens().verify(token)
    assert verified is not None and verified.player_id == player_id
    assert _tokens(now=1_000_100.0).verify(token) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t[:-2] + ("AA" if not t.endswith("AA") else "BB"),
        lambda t: t.replace(".1000100.", ".1999999."),
        lambda t: "v2" + t[2:],
        lambda t: t + ".extra",
        lambda t: "",
    ],
)
def test_player_token_rejects_tampering(mutate):
    token = _tokens().issue(str(uuid.uuid4()), 1_000_100)
    assert _tokens().verify(mutate(token)) is None


def test_player_token_key_is_domain_separated_from_flags():
    service = FlagService(b"k" * 32)
    other = PlayerTokenService(FlagService(b"x" * 32).derive_subkey(PLAYER_TOKEN_LABEL))
    token = PlayerTokenService(service.derive_subkey(PLAYER_TOKEN_LABEL)).issue(
        str(uuid.uuid4()), 4_000_000_000
    )

    assert other.verify(token) is None
    assert service.derive_subkey(PLAYER_TOKEN_LABEL) != service.derive_subkey(b"other")


def test_access_codes_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("JANUS_ACCESS_CODES", " event-code-1 ,second-code ")
    verifier = AccessCodeVerifier.from_env("JANUS_ACCESS_CODES")

    assert verifier.verify("event-code-1")
    assert verifier.verify(" second-code ")
    assert not verifier.verify("event-code")


@pytest.mark.parametrize("raw", ["", " , ", "short"])
def test_access_codes_must_exist_and_be_long_enough(monkeypatch, raw):
    monkeypatch.setenv("JANUS_ACCESS_CODES", raw)
    with pytest.raises(ConfigurationError):
        AccessCodeVerifier.from_env("JANUS_ACCESS_CODES")


def test_recovery_codes_are_formatted_and_unique():
    codes = {generate_recovery_code() for _ in range(200)}

    assert len(codes) == 200
    for code in codes:
        assert re.fullmatch(r"RCV-[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}", code)


def test_recovery_code_normalization_accepts_human_input():
    code = generate_recovery_code()
    expected = normalize_recovery_code(code)

    assert expected is not None and len(expected) == 16
    assert normalize_recovery_code(code.lower()) == expected
    assert normalize_recovery_code(code.replace("-", " ")) == expected
    assert normalize_recovery_code(code.removeprefix("RCV-")) == expected
    assert normalize_recovery_code("RCV-ILOU-0000-0000-0000") is None
    assert normalize_recovery_code("RCV-1234") is None
    assert hash_recovery_code(expected) == hash_recovery_code(normalize_recovery_code(code.lower()))
    assert len(hash_recovery_code(expected)) == 64
