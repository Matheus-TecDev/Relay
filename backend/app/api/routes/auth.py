from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import authenticate_admin, create_access_token, require_current_user
from app.core.config import settings
from app.schemas.auth import CurrentUserResponse, LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    if not authenticate_admin(payload.username, payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")

    return TokenResponse(
        access_token=create_access_token(payload.username),
        expires_in=settings.auth_token_expire_minutes * 60,
    )


@router.get("/me", response_model=CurrentUserResponse)
def me(username: str = Depends(require_current_user)) -> CurrentUserResponse:
    return CurrentUserResponse(username=username)
