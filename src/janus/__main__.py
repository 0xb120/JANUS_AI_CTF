"""Local-only JANUS server entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .api import create_app
from .config import AppSettings, LLMSettings, SpeechSettings, load_config


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
        choices=["mock", "openai_compatible", "ollama"],
        default=None,
        help="Override the configured local inference provider.",
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
        app_updates["llm"] = LLMSettings.model_validate(
            {**config.app.llm.model_dump(), **llm_updates}
        )
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
    if app_updates:
        validated_app = AppSettings.model_validate(
            {**config.app.model_dump(), **app_updates}
        )
        config = config.model_copy(update={"app": validated_app})
    uvicorn.run(create_app(loaded_config=config), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
