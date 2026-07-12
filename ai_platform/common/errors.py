class PlatformError(Exception):
    """Base class for all platform errors. Transport-agnostic on purpose:
    this module must not import anything HTTP-specific, so the same
    exception hierarchy can be raised from Runtime, Tools, or Gateway."""


class AuthenticationError(PlatformError):
    """Caller could not be identified (missing/invalid API key)."""


class RateLimitExceededError(PlatformError):
    """Caller has exceeded their allotted request quota."""


class ValidationError(PlatformError):
    """Request failed platform-level validation."""


class RuntimeUnavailableError(PlatformError):
    """The Runtime backing the platform could not process the request."""
