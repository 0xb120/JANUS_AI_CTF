"""Periodic cleanup so abandoned sessions do not keep transcripts, audio or memory."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from .domain import utc_now
from .engine import ChallengeEngine
from .ratelimit import RateLimiter

logger = logging.getLogger("janus.sweeper")


@dataclass
class SweepReport:
    expired: int = 0
    locks_pruned: int = 0
    audio_removed: int = 0
    limiter_keys_pruned: int = 0
    players_removed: int = 0


class SessionSweeper:
    def __init__(
        self,
        engine: ChallengeEngine,
        *,
        rate_limiter: RateLimiter,
        interval_seconds: float = 60.0,
        audio_max_age_seconds: float = 1800.0,
    ) -> None:
        self.engine = engine
        self.rate_limiter = rate_limiter
        self.interval_seconds = interval_seconds
        self.audio_max_age_seconds = audio_max_age_seconds
        self.running = False

    async def sweep_once(self) -> SweepReport:
        report = SweepReport()
        for session_id in self.engine.repository.active_session_ids():
            try:
                expired = await self.engine.expire_if_due(session_id)
            except Exception:
                # One bad session must not stop the rest of the pass; retried next interval.
                logger.exception("Could not expire session %s", session_id)
                continue
            if expired:
                report.expired += 1
        report.locks_pruned = self.engine.prune_locks()
        report.audio_removed = self.engine.sweep_orphan_audio(self.audio_max_age_seconds)
        report.limiter_keys_pruned = self.rate_limiter.prune(3600)
        online = self.engine.config.app.online
        if online.enabled:
            cutoff = utc_now() - timedelta(hours=online.player_ttl_hours)
            report.players_removed = self.engine.repository.delete_players_created_before(cutoff)
        return report

    async def run(self) -> None:
        self.running = True
        try:
            while True:
                await asyncio.sleep(self.interval_seconds)
                try:
                    await self.sweep_once()
                except Exception:
                    logger.exception("Session sweep failed")
        finally:
            self.running = False
