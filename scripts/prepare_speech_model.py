"""Download and validate one faster-whisper model for offline JANUS use."""

from __future__ import annotations

import argparse
from pathlib import Path

REQUIRED_FILES = ("config.json", "model.bin", "tokenizer.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("tiny", "base", "small", "medium"), required=True)
    parser.add_argument("--destination", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)

    try:
        from huggingface_hub import snapshot_download
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "huggingface-hub is missing. Run scripts\\Install-Janus.ps1 first."
        ) from exc

    snapshot_download(
        repo_id=f"Systran/faster-whisper-{args.model}",
        local_dir=destination,
    )

    missing = [name for name in REQUIRED_FILES if not (destination / name).is_file()]
    if missing:
        raise SystemExit(f"Incomplete Whisper model; missing: {', '.join(missing)}")

    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
