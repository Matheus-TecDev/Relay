import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

security = HTTPBearer(auto_error=False)


class InvalidTokenError(Exception):
    pass


def authenticate_admin(username: str, password: str) -> bool:
    return hmac.compare_digest(username, settings.auth_admin_username) and hmac.compare_digest(
        password,
        settings.auth_admin_password,
    )


def create_access_token(subject: str) -> str:
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.auth_token_expire_minutes)
    payload = {
        "sub": subject,
        "exp": int(expires_at.timestamp()),
        "iat": int(datetime.now(UTC).timestamp()),
    }
    return _encode_jwt(payload)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".")
    except ValueError as exc:
        raise InvalidTokenError("Invalid token format") from exc

    signed_content = f"{encoded_header}.{encoded_payload}".encode()
    expected_signature = _sign(signed_content)
    try:
        provided_signature = _base64url_decode(encoded_signature)
    except Exception as exc:
        raise InvalidTokenError("Invalid token signature") from exc
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise InvalidTokenError("Invalid token signature")

    try:
        header = _decode_json(encoded_header)
    except Exception as exc:
        raise InvalidTokenError("Invalid token header") from exc
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise InvalidTokenError("Invalid token header")

    try:
        payload = _decode_json(encoded_payload)
    except Exception as exc:
        raise InvalidTokenError("Invalid token payload") from exc
    subject = payload.get("sub")
    expires_at = payload.get("exp")
    if subject != settings.auth_admin_username or not isinstance(expires_at, int):
        raise InvalidTokenError("Invalid token payload")
    if expires_at < int(datetime.now(UTC).timestamp()):
        raise InvalidTokenError("Token expired")

    return payload


def require_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()

    try:
        payload = decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise _unauthorized() from exc

    return str(payload["sub"])


def _encode_jwt(payload: dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = _base64url_encode(json.dumps(header, separators=(",", ":")).encode())
    encoded_payload = _base64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = _base64url_encode(_sign(f"{encoded_header}.{encoded_payload}".encode()))
    return f"{encoded_header}.{encoded_payload}.{signature}"


def _sign(value: bytes) -> bytes:
    return hmac.new(settings.auth_jwt_secret.encode(), value, hashlib.sha256).digest()


def _decode_json(value: str) -> dict[str, Any]:
    decoded = _base64url_decode(value)
    parsed = json.loads(decoded)
    if not isinstance(parsed, dict):
        raise InvalidTokenError("Invalid JSON payload")
    return parsed


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
