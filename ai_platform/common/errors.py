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


class ProviderError(PlatformError):
    """Base class for model provider failures (Anthropic, OpenAI, ...).
    Lives in common/errors.py, not providers/, so it's on the same
    transport-agnostic hierarchy the Gateway already knows how to map to
    HTTP — a future Runtime can let these propagate unchanged."""


class ProviderAuthError(ProviderError):
    """The provider rejected our credentials."""


class ProviderRateLimitError(ProviderError):
    """The provider itself rate-limited or overloaded us."""


class ProviderTimeoutError(ProviderError):
    """The provider did not respond in time."""


class ToolNotFoundError(PlatformError):
    """Runtime (or the model) referenced a tool that isn't registered."""


class RuntimeToolLoopExceededError(PlatformError):
    """The tool-calling loop exceeded its iteration limit without the model
    producing a final, non-tool-call answer."""
