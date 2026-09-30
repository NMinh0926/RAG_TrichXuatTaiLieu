"""Login with accounts created by an admin (no self sign-up) and admin user management."""
import logging
import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import COOKIE_NAME, get_current_user, require_admin
from app.core.config import Settings, get_settings
from app.core.dependencies import get_rate_limiter
from app.core.errors import PipelineError
from app.core.ratelimit import RateLimiter, client_ip, rate_limited
from app.core.security import hash_password, issue_token, verify_password
from app.db.models import User, UserRole
from app.db.repositories import UserRepository
from app.db.session import get_db
from app.models.schemas import LoginRequest, PasswordChange, SessionOut, UserCreate, UserOut, UserUpdate

auth_router = APIRouter(prefix='/api/auth', tags=['auth'])
users_router = APIRouter(prefix='/api/users', tags=['users'])
logger = logging.getLogger(__name__)
# Compared against when the username does not exist, so both cases take the same time.
_DUMMY_HASH = hash_password(uuid.uuid4().hex)


def start_session(response: Response, user: User, settings: Settings) -> SessionOut:
    ttl = settings.session_hours * 3600
    token = issue_token(settings.signing_secret(), str(user.id), user.token_version, ttl)
    response.set_cookie(COOKIE_NAME, token, max_age=ttl, httponly=True, secure=settings.cookie_secure,
                        samesite='strict', path='/')
    return SessionOut(user=UserOut.model_validate(user), access_token=token, expires_in=ttl)


@auth_router.post('/login', response_model=SessionOut)
def login(body: LoginRequest, request: Request, response: Response, session: Session = Depends(get_db),
          settings: Settings = Depends(get_settings), limiter: RateLimiter = Depends(get_rate_limiter)):
    """Limits: every attempt per IP, and failed attempts per (username, IP) — so nobody can lock another
    user out from their own machine, while guessing one password from one IP stays capped."""
    limit, ip = settings.rate_limit_login_per_minute, client_ip(request)
    username = body.username.strip().lower()
    limiter.check('login-ip', ip, limit)
    failures = f'{username}:{ip}'
    if limiter.blocked('login-fail', failures, limit):
        raise rate_limited()
    user = UserRepository(session).by_username(username)
    valid = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not valid or not user.is_active:
        limiter.hit('login-fail', failures)
        logger.warning('login failed')
        raise PipelineError('invalid_credentials', 'Sai tên đăng nhập hoặc mật khẩu.', 401)
    logger.info('login user_id=%s', user.id)
    return start_session(response, user, settings)


@auth_router.post('/logout', status_code=204)
def logout(settings: Settings = Depends(get_settings)):
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path='/', httponly=True, secure=settings.cookie_secure, samesite='strict')
    return response


@auth_router.get('/me', response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@auth_router.post('/password', response_model=SessionOut)
def change_password(body: PasswordChange, response: Response, user: User = Depends(get_current_user),
                    session: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                    limiter: RateLimiter = Depends(get_rate_limiter)):
    """Signs out every other session (token version bump) and returns a fresh one for this browser.
    Rate limited like logins: a stolen session must not be able to brute-force the current password."""
    limiter.check('password', str(user.id), settings.rate_limit_login_per_minute)
    if not verify_password(body.current_password, user.password_hash):
        raise PipelineError('invalid_credentials', 'Mật khẩu hiện tại không đúng.', 400)
    user.password_hash, user.token_version = hash_password(body.new_password), user.token_version + 1
    session.commit()
    return start_session(response, user, settings)


@users_router.get('', response_model=list[UserOut])
def list_users(session: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return UserRepository(session).list()


@users_router.post('', response_model=UserOut, status_code=201)
def create_user(body: UserCreate, session: Session = Depends(get_db), admin: User = Depends(require_admin)):
    repository = UserRepository(session)
    if repository.by_username(body.username) is not None:
        raise PipelineError('username_taken', 'Tên đăng nhập đã tồn tại.', 409)
    user = User(username=body.username.lower(), password_hash=hash_password(body.password), role=UserRole(body.role))
    try:
        repository.add(user)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise PipelineError('username_taken', 'Tên đăng nhập đã tồn tại.', 409) from exc
    logger.info('user created user_id=%s role=%s by=%s', user.id, user.role.value, admin.id)
    return user


@users_router.patch('/{user_id}', response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdate, session: Session = Depends(get_db),
                admin: User = Depends(require_admin)):
    """Reset password, change role or (de)activate. Password reset and deactivation sign the user out.

    Note: an admin who resets someone's password can sign in as them; every change is logged with its fields.
    """
    user = UserRepository(session).get(user_id)
    if user is None:
        raise PipelineError('user_not_found', 'Không tìm thấy người dùng.', 404)
    if user.id == admin.id and (body.is_active is False or body.role == UserRole.user.value):
        raise PipelineError('cannot_demote_self', 'Không thể tự khóa hoặc bỏ quyền quản trị của chính mình.', 409)
    if user.id == admin.id and body.password is not None:
        raise PipelineError('use_password_change', 'Đổi mật khẩu của chính bạn bằng mục "Đổi mật khẩu".', 409)
    removes_admin = user.role == UserRole.admin and user.is_active and (
        body.is_active is False or body.role == UserRole.user.value)
    if removes_admin:
        # Row locks serialize concurrent demotions, so two admins cannot remove each other at once.
        others = session.scalars(select(User.id).where(User.role == UserRole.admin, User.is_active.is_(True),
                                                        User.id != user.id).with_for_update()).all()
        if not others:
            raise PipelineError('last_admin', 'Phải còn ít nhất một quản trị viên đang hoạt động.', 409)
    changed = [field for field in ('password', 'role', 'is_active') if getattr(body, field) is not None]
    if body.password is not None:
        user.password_hash, user.token_version = hash_password(body.password), user.token_version + 1
    if body.role is not None:
        user.role = UserRole(body.role)
    if body.is_active is not None:
        if not body.is_active and user.is_active:
            user.token_version += 1
        user.is_active = body.is_active
    session.commit()
    logger.info('user updated user_id=%s by=%s changed=%s', user.id, admin.id, ','.join(changed))
    return user
