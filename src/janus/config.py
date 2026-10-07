"""Strict YAML configuration loading for JANUS."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ConfigurationError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocalizedText(StrictModel):
    it: str
    en: str


HUGGINGFACE_ROUTER_HOST = "router.huggingface.co"

DEFAULT_LLM_BASE_URLS = {
    "mock": "http://127.0.0.1:8080/v1",
    "openai_compatible": "http://127.0.0.1:8080/v1",
    "ollama": "http://127.0.0.1:11434",
    "huggingface": f"https://{HUGGINGFACE_ROUTER_HOST}/v1",
}


class LLMSettings(StrictModel):
    provider: Literal["mock", "openai_compatible", "ollama", "huggingface"] = (
        "openai_compatible"
    )
    base_url: str = DEFAULT_LLM_BASE_URLS["openai_compatible"]
    model: str = "local-model"
    api_key_env: str | None = None
    # Hugging Face only: names the variable holding the organization to bill.
    bill_to_env: str | None = None
    timeout_seconds: float = Field(default=90.0, ge=1, le=600)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=512, ge=16, le=8192)
    enable_thinking: bool = False
    fallback_to_mock: bool = True
    # Global gate in front of the provider: concurrent calls and bounded waiting queue.
    max_concurrent: int = Field(default=8, ge=1, le=256)
    max_queue: int = Field(default=16, ge=0, le=10_000)

    @model_validator(mode="before")
    @classmethod
    def provider_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        provider = data.get("provider", "openai_compatible")
        if not data.get("base_url") and provider in DEFAULT_LLM_BASE_URLS:
            data["base_url"] = DEFAULT_LLM_BASE_URLS[provider]
        if provider == "huggingface":
            # Secrets are read from the environment only, never from YAML.
            data["api_key_env"] = data.get("api_key_env") or "HF_TOKEN"
            data["bill_to_env"] = data.get("bill_to_env") or "HF_BILL_TO"
        return data

    @field_validator("base_url")
    @classmethod
    def http_urls_only(cls, value: str) -> str:
        if urlparse(value).scheme not in {"http", "https"}:
            raise ValueError("LLM base_url must use HTTP(S)")
        return value.rstrip("/")

    @model_validator(mode="after")
    def endpoint_matches_provider(self) -> LLMSettings:
        parsed = urlparse(self.base_url)
        if self.provider == "huggingface":
            # The only remote endpoint JANUS talks to, and only when chosen explicitly.
            if parsed.scheme != "https" or parsed.hostname != HUGGINGFACE_ROUTER_HOST:
                raise ValueError(
                    f"The Hugging Face provider only accepts https://{HUGGINGFACE_ROUTER_HOST}"
                )
        elif parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("LLM providers must be bound to the local machine")
        return self


class SpeechSettings(StrictModel):
    stt_provider: Literal["disabled", "faster_whisper"] = "disabled"
    stt_model: str = "small"
    stt_device: Literal["auto", "cpu", "cuda"] = "auto"
    stt_compute_type: str = "int8"
    stt_local_files_only: bool = True
    tts_provider: Literal["disabled", "sapi", "piper"] = "disabled"
    voice_output_dir: Path = Path("data/audio")
    piper_model_it: Path = Path("models/piper/it_IT-paola-medium.onnx")
    piper_model_en: Path = Path("models/piper/en_US-lessac-medium.onnx")
    piper_use_cuda: bool = False
    max_audio_bytes: int = Field(default=20_000_000, ge=1024, le=200_000_000)


_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")


class OnlineLimits(StrictModel):
    auth_attempts_per_minute: int = Field(default=10, ge=1, le=1000)
    turns_per_minute: int = Field(default=12, ge=1, le=1000)
    sessions_per_hour: int = Field(default=20, ge=1, le=10_000)
    max_active_sessions: int = Field(default=1, ge=1, le=10)


class OnlineSettings(StrictModel):
    """Internet-facing multiplayer mode; disabled keeps the local kiosk unchanged."""

    enabled: bool = False
    public_host: str | None = None
    access_codes_env: str = "JANUS_ACCESS_CODES"
    trusted_proxies: list[str] = Field(default_factory=list)
    player_ttl_hours: int = Field(default=12, ge=1, le=168)
    limits: OnlineLimits = Field(default_factory=OnlineLimits)

    @field_validator("public_host")
    @classmethod
    def bare_hostname(cls, value: str | None) -> str | None:
        if value is None:
            return None
        host = value.strip().lower()
        if not _HOSTNAME.fullmatch(host):
            raise ValueError("public_host must be a bare hostname such as ctf.example.com")
        return host

    @field_validator("trusted_proxies")
    @classmethod
    def ip_networks_only(cls, value: list[str]) -> list[str]:
        networks = []
        for item in value:
            try:
                networks.append(str(ipaddress.ip_network(item.strip(), strict=False)))
            except ValueError as exc:
                raise ValueError(f"trusted_proxies entries must be IPs or CIDRs: {item!r}") from exc
        return networks

    @model_validator(mode="after")
    def public_host_when_enabled(self) -> OnlineSettings:
        if self.enabled and not self.public_host:
            raise ValueError("online mode requires public_host")
        return self


class AppSettings(StrictModel):
    name: str = "JANUS // RomHack 2026"
    version: str = "0.1.0"
    api_prefix: str = "/api"
    default_mode: str = "stand"
    default_level: str = "level_1"
    default_language: Literal["it", "en"] = "it"
    default_hardware_profile: str = "a3000_6gb"
    database_path: Path = Path("data/janus.sqlite3")
    secret_key_path: Path = Path("data/janus.key")
    secret_key_env: str = "JANUS_SECRET_KEY"
    flag_prefix: str = "RH26"
    session_ttl_minutes: int = Field(default=20, ge=1, le=1440)
    history_limit: int = Field(default=24, ge=2, le=200)
    max_message_chars: int = Field(default=4000, ge=100, le=100_000)
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "localhost", "testserver"]
    )
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1", "http://localhost"]
    )
    llm: LLMSettings = Field(default_factory=LLMSettings)
    speech: SpeechSettings = Field(default_factory=SpeechSettings)
    online: OnlineSettings = Field(default_factory=OnlineSettings)

    def effective_allowed_hosts(self) -> list[str]:
        hosts = list(self.allowed_hosts)
        if self.online.enabled and self.online.public_host not in hosts:
            hosts.append(self.online.public_host)
        return hosts

    def effective_cors_origins(self) -> list[str]:
        if self.online.enabled:
            return [f"https://{self.online.public_host}"]
        return list(self.cors_origins)

    @field_validator("api_prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value.endswith("/"):
            raise ValueError("api_prefix must begin, but not end, with '/'")
        return value


class ModeConfig(StrictModel):
    id: str
    name: LocalizedText
    description: LocalizedText
    score_enabled: bool
    nickname_required: bool
    leaderboard_enabled: bool
    show_timer: bool = False

    @model_validator(mode="after")
    def score_mode_is_consistent(self) -> ModeConfig:
        if self.nickname_required and not self.score_enabled:
            raise ValueError("nickname_required requires score_enabled")
        if self.leaderboard_enabled and not self.score_enabled:
            raise ValueError("leaderboard_enabled requires score_enabled")
        return self


class ModesFile(StrictModel):
    modes: dict[str, ModeConfig]

    @model_validator(mode="after")
    def ids_match_keys(self) -> ModesFile:
        for key, value in self.modes.items():
            if key != value.id:
                raise ValueError(f"mode key {key!r} does not match id {value.id!r}")
        return self


class RuntimeRecommendation(StrictModel):
    llm_model: str
    llm_quantization: str
    context_tokens: int = Field(ge=512)
    stt_model: str
    llm_gpu_layers: int | str
    notes: LocalizedText


class HardwareProfile(StrictModel):
    id: str
    name: str
    minimum_vram_gb: float = Field(ge=0)
    minimum_free_ram_gb: float = Field(ge=1)
    recommendation: RuntimeRecommendation


class HardwareFile(StrictModel):
    profiles: dict[str, HardwareProfile]

    @model_validator(mode="after")
    def ids_match_keys(self) -> HardwareFile:
        for key, value in self.profiles.items():
            if key != value.id:
                raise ValueError(f"hardware key {key!r} does not match id {value.id!r}")
        return self


class ScoringRules(StrictModel):
    base_points: int = Field(default=1000, ge=0)
    time_bonus_max: int = Field(default=500, ge=0)
    time_bonus_window_seconds: int = Field(default=300, ge=1)
    turn_penalty: int = Field(default=25, ge=0)
    hint_penalty: int = Field(default=150, ge=0)
    minimum_score: int = Field(default=100, ge=0)


class GenerationSettings(StrictModel):
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=16, le=8192)


class OutputPolicy(StrictModel):
    """Deterministic application guardrail applied before UI, storage, and TTS."""

    redact_exact_secret: bool = False
    redaction: LocalizedText = Field(
        default_factory=lambda: LocalizedText(
            it="SENTINEL // OUTPUT CORROTTO: firma del segreto rilevata.",
            en="SENTINEL // OUTPUT CORRUPTED: secret signature detected.",
        )
    )


class SimulatedToolSettings(StrictModel):
    """A deliberately flawed, strictly local tool available to a challenge level."""

    id: Literal["diagnostics.collect"]
    enabled: bool = True


class ChallengeLevel(StrictModel):
    id: str
    order: int = Field(ge=1)
    difficulty: int = Field(ge=1, le=10)
    name: LocalizedText
    description: LocalizedText
    objective: LocalizedText
    time_limit_seconds: int = Field(default=600, ge=60, le=3600)
    system_prompt: str
    guardrails: list[str] = Field(default_factory=list)
    hints: list[LocalizedText] = Field(default_factory=list)
    output_policy: OutputPolicy = Field(default_factory=OutputPolicy)
    simulated_tool: SimulatedToolSettings | None = None
    generation: GenerationSettings = Field(default_factory=GenerationSettings)
    scoring: ScoringRules = Field(default_factory=ScoringRules)


class LoadedConfig(BaseModel):
    app: AppSettings
    modes: dict[str, ModeConfig]
    hardware: dict[str, HardwareProfile]
    levels: dict[str, ChallengeLevel]
    config_dir: Path = Field(exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @model_validator(mode="after")
    def validate_references(self) -> LoadedConfig:
        if self.app.default_mode not in self.modes:
            raise ValueError("default_mode is not configured")
        if self.app.default_level not in self.levels:
            raise ValueError("default_level is not configured")
        if self.app.default_hardware_profile not in self.hardware:
            raise ValueError("default_hardware_profile is not configured")
        return self

    def resolve_runtime_path(self, configured: Path) -> Path:
        if configured.is_absolute():
            return configured
        return (self.config_dir.parent / configured).resolve()


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Missing configuration file: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"Configuration root must be a mapping: {path}")
    return value


def load_config(config_dir: str | Path) -> LoadedConfig:
    """Load and cross-validate all JANUS configuration files."""

    directory = Path(config_dir).resolve()
    try:
        app = AppSettings.model_validate(_read_yaml(directory / "app.yaml"))
        modes = ModesFile.model_validate(_read_yaml(directory / "modes.yaml")).modes
        hardware = HardwareFile.model_validate(_read_yaml(directory / "hardware.yaml")).profiles
        levels: dict[str, ChallengeLevel] = {}
        for path in sorted((directory / "levels").glob("*.yaml")):
            level = ChallengeLevel.model_validate(_read_yaml(path))
            if level.id in levels:
                raise ConfigurationError(f"Duplicate challenge level id: {level.id}")
            levels[level.id] = level
        if not levels:
            raise ConfigurationError("At least one challenge level is required")
        return LoadedConfig(
            app=app,
            modes=modes,
            hardware=hardware,
            levels=levels,
            config_dir=directory,
        )
    except ConfigurationError:
        raise
    except Exception as exc:
        raise ConfigurationError(f"Configuration validation failed: {exc}") from exc
