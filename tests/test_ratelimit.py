from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from janus.api import create_app
from janus.errors import CapacityError, ConflictError, RateLimitedError
from janus.providers.llm import MockLLMProvider
from janus.providers.stt import DisabledSTTProvider
from janus.providers.tts import DisabledTTSProvider
from janus.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_hit_allows_up_to_the_limit_then_reports_retry_after():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    for _ in range(3):
        limiter.hit("turn:a", limit=3, window_seconds=60)

    with pytest.raises(RateLimitedError) as raised:
        limiter.hit("turn:a", limit=3, window_seconds=60)
    assert raised.value.details["retry_after"] == 60

    clock.now += 30
    with pytest.raises(RateLimitedError) as raised:
        limiter.hit("turn:a", limit=3, window_seconds=60)
    assert raised.value.details["retry_after"] == 30

    clock.now += 31
    limiter.hit("turn:a", limit=3, window_seconds=60)


def test_keys_are_independent():
    limiter = RateLimiter(clock=FakeClock())
    limiter.hit("turn:a", limit=1, window_seconds=60)
    limiter.hit("turn:b", limit=1, window_seconds=60)
    with pytest.raises(RateLimitedError):
        limiter.hit("turn:a", limit=1, window_seconds=60)


def test_check_does_not_record_and_record_does_not_check():
    limiter = RateLimiter(clock=FakeClock())
    for _ in range(5):
        limiter.check("auth:ip", limit=2, window_seconds=60)
    limiter.record("auth:ip", window_seconds=60)
    limiter.record("auth:ip", window_seconds=60)
    with pytest.raises(RateLimitedError):
        limiter.check("auth:ip", limit=2, window_seconds=60)


def test_prune_drops_idle_keys():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    limiter.hit("a", limit=5, window_seconds=60)
    clock.now += 10
    limiter.hit("b", limit=5, window_seconds=60)
    clock.now += 3595

    assert limiter.prune(3600) == 1
    assert limiter.prune(3600) == 0


def test_error_classes_carry_codes_and_retry_after():
    assert ConflictError("busy", code="turn_in_progress").code == "turn_in_progress"
    assert ConflictError("busy", code="turn_in_progress").status_code == 409
    assert CapacityError(7).status_code == 503
    assert CapacityError(7).details == {"retry_after": 7}


def test_error_handler_adds_retry_after_header(loaded_config, repository, flag_service):
    app = create_app(
        loaded_config=loaded_config,
        repository=repository,
        flag_service=flag_service,
        llm_provider=MockLLMProvider(),
        stt_provider=DisabledSTTProvider(),
        tts_provider=DisabledTTSProvider(),
    )

    @app.get("/api/test-rate-limited")
    async def rate_limited():
        raise RateLimitedError(7)

    response = TestClient(app).get("/api/test-rate-limited")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"
    assert response.json()["error"]["code"] == "rate_limited"


def test_refund_gives_back_the_most_recent_hit():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    limiter.hit("session:p", limit=2, window_seconds=60)
    clock.now += 30
    limiter.hit("session:p", limit=2, window_seconds=60)

    limiter.refund("session:p")

    # The older hit (t=1000) remains, so a limit of 1 is still exhausted for 30 s.
    with pytest.raises(RateLimitedError) as raised:
        limiter.check("session:p", limit=1, window_seconds=60)
    assert raised.value.details["retry_after"] == 30
    limiter.hit("session:p", limit=2, window_seconds=60)
    with pytest.raises(RateLimitedError):
        limiter.hit("session:p", limit=2, window_seconds=60)


def test_refund_without_hits_is_a_no_op():
    limiter = RateLimiter(clock=FakeClock())

    limiter.refund("never-seen")
    limiter.hit("once", limit=1, window_seconds=60)
    limiter.refund("once")
    limiter.refund("once")

    limiter.hit("once", limit=1, window_seconds=60)
    assert limiter.prune(3600) == 0
