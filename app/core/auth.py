"""Request authentication: the HttpOnly session cookie (browser) or `Authorization: Bearer` (API clients)."""
import uuid

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import PipelineError
from app.core.security import read_token
from app.db.models import User, UserRole
from app.db.session import get_db

COOKIE_NAME = 'rag_session'


def request_token(request: Request) -> str | None:
    header = request.headers.get('authorization', '')
    if header[:7].lower() == 'bearer ':
        return header[7:].strip() or None
    return request.cookies.get(COOKIE_NAME)


def get_current_user(request: Request, session: Session = Depends(get_db),
                     settings: Settings = Depends(get_settings)) -> User:
    token = request_token(request)
    payload = read_token(settings.signing_secret(), token) if token else None
    user = None
    if payload is not None:
        try:
            user = session.get(User, uuid.UUID(str(payload.get('sub'))))
        except ValueError:
            user = None
    # A bumped token_version (password change, deactivation) revokes every earlier token.
    if user is None or not user.is_active or user.token_version != payload.get('ver'):
        raise PipelineError('unauthorized', 'Vui lòng đăng nhập.', 401)
    request.state.user_id = str(user.id)
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.admin:
        raise PipelineError('forbidden', 'Chỉ quản trị viên được thực hiện thao tác này.', 403)
    return user
