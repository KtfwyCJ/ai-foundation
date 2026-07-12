from fastapi import Depends, Header

from ai_platform.common.config import Settings, get_settings
from ai_platform.common.errors import AuthenticationError


def verify_api_key(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    """Extracts and validates the caller's API key from the Authorization
    header. Returns the validated key so downstream dependencies (e.g. rate
    limiting) can key off the same caller identity."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise AuthenticationError("Missing or malformed Authorization header")

    api_key = authorization.removeprefix("Bearer ").strip()
    if api_key not in settings.api_key_set:
        raise AuthenticationError("Invalid API key")

    return api_key
