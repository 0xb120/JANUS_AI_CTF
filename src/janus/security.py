"""Per-session deterministic flags backed by a local HMAC key."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigurationError


class SecretKeyStore:
    """Loads a key from the environment or creates a persistent local key."""

    def __init__(self, path: Path, environment_variable: str) -> None:
        self.path = path
        self.environment_variable = environment_variable

    @staticmethod
    def _decode(value: str) -> bytes:
        try:
            if value.startswith("hex:"):
                return bytes.fromhex(value[4:])
            if value.startswith("base64:"):
                return base64.b64decode(value[7:], validate=True)
            return value.encode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise ConfigurationError("The configured JANUS secret key is malformed") from exc

    def load_or_create(self) -> bytes:
        configured = os.environ.get(self.environment_variable)
        if configured:
            key = self._decode(configured)
            if len(key) < 32:
                raise ConfigurationError("JANUS secret key must contain at least 32 bytes")
            return key

        try:
            key = self.path.read_bytes()
        except FileNotFoundError:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            key = secrets.token_bytes(32)
            try:
                with self.path.open("xb") as handle:
                    handle.write(key)
                try:
                    self.path.chmod(0o600)
                except OSError:
                    pass  # Windows ACLs are managed outside Python's POSIX mode bits.
            except FileExistsError:
                key = self.path.read_bytes()
        except OSError as exc:
            raise ConfigurationError(f"Cannot read JANUS key at {self.path}") from exc

        if len(key) < 32:
            raise ConfigurationError("Persisted JANUS secret key is too short")
        return key


class FlagService:
    """Derives flags without storing plaintext secrets in SQLite."""

    def __init__(self, key: bytes, prefix: str = "RH26") -> None:
        if len(key) < 32:
            raise ValueError("HMAC key must contain at least 32 bytes")
        if not re.fullmatch(r"[A-Z0-9]{2,12}", prefix):
            raise ValueError("flag prefix must contain 2-12 uppercase letters or digits")
        self._key = key
        self.prefix = prefix

    def derive_subkey(self, label: bytes) -> bytes:
        """Independent key for another purpose; the master key never leaves this class."""

        return hmac.new(self._key, label, hashlib.sha256).digest()

    @staticmethod
    def _level_label(level_id: str) -> str:
        label = re.sub(r"[^A-Za-z0-9]", "", level_id).upper()
        return label[:12] or "LEVEL"

    def derive(self, session_id: str, level_id: str) -> str:
        context = f"janus-flag-v1\0{session_id}\0{level_id}".encode()
        raw = hmac.new(self._key, context, hashlib.sha256).digest()
        token = base64.b32encode(raw).decode("ascii").rstrip("=")[:16]
        grouped = "-".join(token[index : index + 4] for index in range(0, 16, 4))
        return f"{self.prefix}{{{self._level_label(level_id)}-{grouped}}}"

    def verify(self, candidate: str, session_id: str, level_id: str) -> bool:
        """Compare fixed-size keyed digests to avoid candidate-length timing leaks."""

        expected = self.derive(session_id, level_id)
        candidate_value = candidate.strip().upper()
        candidate_mac = hmac.new(
            self._key, b"janus-verify-v1\0" + candidate_value.encode("utf-8"), hashlib.sha256
        ).digest()
        expected_mac = hmac.new(
            self._key, b"janus-verify-v1\0" + expected.encode("utf-8"), hashlib.sha256
        ).digest()
        return hmac.compare_digest(candidate_mac, expected_mac)


PLAYER_TOKEN_LABEL = b"janus/player-token/v1"
RECOVERY_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32: no I, L, O, U
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass(frozen=True)
class PlayerToken:
    player_id: str
    expires_at: int


class PlayerTokenService:
    """Stateless signed player cookies: ``v1.<player_id>.<expires_unix>.<sig>``."""

    def __init__(self, key: bytes, clock: Callable[[], float] = time.time) -> None:
        if len(key) < 32:
            raise ValueError("player token key must contain at least 32 bytes")
        self._key = key
        self._clock = clock

    def _sign(self, player_id: str, expires_at: int) -> str:
        mac = hmac.new(
            self._key, f"v1|{player_id}|{expires_at}".encode("ascii"), hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")

    def issue(self, player_id: str, expires_at: int) -> str:
        return f"v1.{player_id}.{expires_at}.{self._sign(player_id, expires_at)}"

    def verify(self, token: str | None) -> PlayerToken | None:
        if not token:
            return None
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != "v1":
            return None
        _, player_id, expires_raw, signature = parts
        if not _UUID.fullmatch(player_id):
            return None
        # Strictly validate expires_raw: 1-12 digits, no leading zero, no Unicode digits
        if not re.fullmatch(r"[1-9][0-9]{0,11}", expires_raw):
            return None
        # Strictly validate signature: exactly 43 unpadded urlsafe base64 characters
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", signature):
            return None
        expires_at = int(expires_raw)
        if not hmac.compare_digest(self._sign(player_id, expires_at), signature):
            return None
        if expires_at <= self._clock():
            return None
        return PlayerToken(player_id=player_id, expires_at=expires_at)


class AccessCodeVerifier:
    def __init__(self, codes: Sequence[str]) -> None:
        cleaned = [code.strip() for code in codes if code.strip()]
        if not cleaned:
            raise ConfigurationError("Online mode requires at least one access code")
        if any(len(code) < 8 for code in cleaned):
            raise ConfigurationError("Access codes must contain at least 8 characters")
        self._codes = [code.encode("utf-8") for code in cleaned]

    @classmethod
    def from_env(cls, name: str) -> AccessCodeVerifier:
        return cls(os.environ.get(name, "").split(","))

    def verify(self, candidate: str) -> bool:
        encoded = candidate.strip().encode("utf-8")
        matched = False
        for code in self._codes:
            # Compare against every code so timing does not reveal which one matched.
            matched |= hmac.compare_digest(encoded, code)
        return matched


def generate_recovery_code() -> str:
    value = secrets.randbits(80)
    characters = []
    for _ in range(16):
        characters.append(RECOVERY_ALPHABET[value & 31])
        value >>= 5
    body = "".join(characters)
    return "RCV-" + "-".join(body[index : index + 4] for index in range(0, 16, 4))


def normalize_recovery_code(raw: str) -> str | None:
    cleaned = re.sub(r"[\s-]", "", raw).upper()
    # Strip leading "RCV" only when the cleaned string has 19 characters
    # (3 for "RCV" prefix + 16 for body), avoiding stripping RCV from body itself
    if len(cleaned) == 19:
        cleaned = cleaned.removeprefix("RCV")
    if len(cleaned) != 16 or any(character not in RECOVERY_ALPHABET for character in cleaned):
        return None
    return cleaned


def hash_recovery_code(normalized: str) -> str:
    # 80 random bits: an unsalted hash is not brute-forceable offline.
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()
