"""Per-session deterministic flags backed by a local HMAC key."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
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
