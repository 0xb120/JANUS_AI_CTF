from __future__ import annotations

import uuid
from pathlib import Path

from janus.domain import AudioArtifact, HealthComponent


class FileTTS:
    def __init__(self, directory: Path) -> None:
        self.output_dir = directory

    async def synthesize(self, text, language):
        audio_id = uuid.uuid4().hex
        (self.output_dir / f"{audio_id}.wav").write_bytes(b"RIFF" + b"\0" * 100)
        return AudioArtifact(id=audio_id)

    def resolve(self, audio_id):
        path = self.output_dir / f"{audio_id}.wav"
        return path if path.is_file() else None

    def delete(self, audio_id):
        (self.output_dir / f"{audio_id}.wav").unlink(missing_ok=True)

    async def health(self):
        return HealthComponent(available=True, detail="file tts")
