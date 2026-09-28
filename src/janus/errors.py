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

