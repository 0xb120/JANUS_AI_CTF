"""Local-only JANUS server entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .api import create_app
from .config import (
    AppSettings,
    LLMSettings,
    LoadedConfig,
    OnlineSettings,
    SpeechSettings,
    load_config,
)


def _proxy_options(config: LoadedConfig) -> dict[str, object]:
    """Trust X-Forwarded-* only from the configured proxies, never by default."""

    proxies = config.app.online.trusted_proxies
    if not proxies:
        return {"proxy_headers": False}
    return {"proxy_headers": True, "forwarded_allow_ips": ",".join(proxies)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the JANUS kiosk backend")
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument(
        "--mode",
        choices=["stand", "score"],
        default=None,
        help="Lock the kiosk to Stand or Score mode for this process.",
    )
    parser.add_argument(
        "--llm-provider",
        choices=["mock", "openai_compatible", "ollama", "huggingface"],
        default=None,
        help=(
            "Override the configured inference provider. huggingface is the only remote "
            "option and reads its token from HF_TOKEN."
        ),
    )
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--stt-model", default=None)
    parser.add_argument(
        "--stt-provider", choices=["disabled", "faster_whisper"], default=None
    )
    parser.add_argument("--tts-provider", choices=["disabled", "sapi", "piper"], default=None)
    parser.add_argument("--piper-model-it", type=Path, default=None)
    parser.add_argument("--piper-model-en", type=Path, default=None)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory for the score database, HMAC key, and ephemeral audio.",
    )
    parser.add_argument(
        "--host",
        choices=["127.0.0.1", "localhost", "0.0.0.0"],
        default="127.0.0.1",
        help=(
            "Bind address. Use 0.0.0.0 only inside a container whose published "
            "port is itself bound to the host loopback (see docker-compose.yml)."
        ),
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--online",
        action="store_true",
        help="Enable the Internet-facing multiplayer mode (requires --public-host and "
        "JANUS_ACCESS_CODES).",
    )
    parser.add_argument("--public-host", default=None, help="Public DNS name, e.g. ctf.example.com")
    parser.add_argument(
        "--trusted-proxies",
        default=None,
        help="Comma-separated IPs/CIDRs whose X-Forwarded-* headers are trusted.",
    )
    parser.add_argument(
        "--no-access-log",
        action="store_true",
        help="Disable the uvicorn per-request access log (client IPs and paths).",
    )
    args = parser.parse_args()
    config_dir = args.config_dir or (Path(__file__).resolve().parents[2] / "configs")
    config = load_config(config_dir)
    app_updates: dict[str, object] = {}
    if args.mode is not None:
        app_updates["default_mode"] = args.mode
    llm_updates: dict[str, object] = {}
    if args.llm_provider is not None:
        llm_updates["provider"] = args.llm_provider
    if args.llm_base_url is not None:
        llm_updates["base_url"] = args.llm_base_url
    if args.llm_model is not None:
        llm_updates["model"] = args.llm_model
    if llm_updates:
        current_llm = config.app.llm.model_dump()
        if (
            args.llm_provider not in (None, config.app.llm.provider)
            and args.llm_base_url is None
        ):
            # The configured URL belongs to the previous provider: use the new one's default.
            del current_llm["base_url"]
        app_updates["llm"] = LLMSettings.model_validate({**current_llm, **llm_updates})
    speech_updates: dict[str, object] = {}
    if args.stt_model is not None:
        speech_updates["stt_model"] = args.stt_model
    if args.stt_provider is not None:
        speech_updates["stt_provider"] = args.stt_provider
    if args.tts_provider is not None:
        speech_updates["tts_provider"] = args.tts_provider
    if args.piper_model_it is not None:
        speech_updates["piper_model_it"] = args.piper_model_it.resolve()
    if args.piper_model_en is not None:
        speech_updates["piper_model_en"] = args.piper_model_en.resolve()
    if args.data_dir is not None:
        data_dir = args.data_dir.resolve()
        app_updates.update(
            {
                "database_path": data_dir / "janus.sqlite3",
                "secret_key_path": data_dir / "janus.key",
            }
        )
        speech_updates["voice_output_dir"] = data_dir / "audio"
    if speech_updates:
        app_updates["speech"] = SpeechSettings.model_validate(
            {**config.app.speech.model_dump(), **speech_updates}
        )
    online_updates: dict[str, object] = {}
    if args.online:
        online_updates["enabled"] = True
    if args.public_host is not None:
        online_updates["public_host"] = args.public_host
    if args.trusted_proxies is not None:
        online_updates["trusted_proxies"] = [
            item for item in args.trusted_proxies.split(",") if item.strip()
        ]
    if online_updates:
        app_updates["online"] = OnlineSettings.model_validate(
            {**config.app.online.model_dump(), **online_updates}
        )
    if app_updates:
        validated_app = AppSettings.model_validate(
            {**config.app.model_dump(), **app_updates}
        )
        config = config.model_copy(update={"app": validated_app})
    uvicorn.run(
        create_app(loaded_config=config),
        host=args.host,
        port=args.port,
        access_log=not args.no_access_log,
        **_proxy_options(config),
    )


if __name__ == "__main__":
    main()
