"""Domain errors mapped to stable API error codes."""


class JanusError(Exception):
    """Base class for expected application failures."""

    code = "janus_error"
    status_code = 400

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(JanusError):
    code = "configuration_error"
    status_code = 500


class NotFoundError(JanusError):
    code = "not_found"
    status_code = 404


class InvalidSessionError(JanusError):
    code = "invalid_session"
    status_code = 409


class ValidationError(JanusError):
    code = "validation_error"
    status_code = 422


class FeatureUnavailableError(JanusError):
    code = "feature_unavailable"
    status_code = 503


class ProviderError(JanusError):
    code = "provider_error"
    status_code = 502


class UnauthorizedError(JanusError):
    code = "unauthorized"
    status_code = 401


class ConflictError(JanusError):
    status_code = 409

    def __init__(self, message: str, *, code: str, details: dict | None = None) -> None:
        super().__init__(message, details=details)
        self.code = code


class RateLimitedError(JanusError):
    code = "rate_limited"
    status_code = 429

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            f"Too many requests; retry in {retry_after} s", details={"retry_after": retry_after}
        )


class CapacityError(JanusError):
    code = "llm_busy"
    status_code = 503

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            f"JANUS is at capacity; retry in {retry_after} s", details={"retry_after": retry_after}
        )
