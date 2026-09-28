"""Deterministic scoring policy for ranked sessions."""

from __future__ import annotations

from datetime import datetime

from .config import ScoringRules
from .domain import SessionRecord, utc_now


class ScoreCalculator:
    def calculate(
        self,
        session: SessionRecord,
        rules: ScoringRules,
        completed_at: datetime | None = None,
    ) -> int:
        finished = completed_at or utc_now()
        # Model/STT/TTS latency is not under the player's control and must not
        # reduce an Arena score. The UI pauses the same interval visually.
        elapsed = max(
            0.0,
            (finished - session.started_at).total_seconds() - session.processing_seconds,
        )
        remaining_ratio = max(0.0, 1.0 - elapsed / rules.time_bonus_window_seconds)
        time_bonus = int(rules.time_bonus_max * remaining_ratio)
        extra_turns = max(0, session.turn_count - 1)
        total = (
            rules.base_points
            + time_bonus
            - extra_turns * rules.turn_penalty
            - session.hints_used * rules.hint_penalty
        )
        return max(rules.minimum_score, total)
