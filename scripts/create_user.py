"""Create an account or reset its password (accounts are only created by admins; there is no sign-up).

The first account ever created also takes over conversations from before accounts existed.
Run: docker compose run --rm -it api python -m scripts.create_user <username> [--admin | --user]
Resetting an existing account keeps its role unless --admin or --user is given.
The password is asked interactively, or read from the CREATE_USER_PASSWORD environment variable.
"""
import argparse
import getpass
import os
import re
import sys

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.models import Conversation, User, UserRole
from app.db.repositories import UserRepository
from app.models.schemas import USERNAME_PATTERN

MIN_PASSWORD = 8


def create_or_reset(session: Session, username: str, password: str,
                    role: UserRole | None = None) -> tuple[User, bool]:
    """Returns (user, created). New accounts get `role` (default user); an existing one gets the new
    password, is re-activated and keeps its role unless `role` is given. Usernames are stored lower-case."""
    username = username.strip().lower()
    if not re.fullmatch(USERNAME_PATTERN, username):
        raise ValueError('Username: 3-64 letters, digits, dot, underscore or hyphen.')
    if len(password) < MIN_PASSWORD:
        raise ValueError(f'Password must have at least {MIN_PASSWORD} characters.')
    repository = UserRepository(session)
    first = repository.count() == 0
    user = repository.by_username(username)
    created = user is None
    if created:
        user = repository.add(User(username=username, password_hash=hash_password(password),
                                   role=role or UserRole.user))
    else:
        user.password_hash, user.is_active = hash_password(password), True
        user.role = role or user.role
        user.token_version += 1
    if first:
        session.execute(update(Conversation).where(Conversation.owner_id.is_(None)).values(owner_id=user.id))
    session.commit()
    return user, created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('username')
    roles = parser.add_mutually_exclusive_group()
    roles.add_argument('--admin', action='store_const', const=UserRole.admin, dest='role', help='grant the admin role')
    roles.add_argument('--user', action='store_const', const=UserRole.user, dest='role', help='make a regular user')
    args = parser.parse_args()
    from app.db.session import get_sessionmaker
    password = os.environ.get('CREATE_USER_PASSWORD') or getpass.getpass('Password: ')
    if not os.environ.get('CREATE_USER_PASSWORD') and getpass.getpass('Repeat password: ') != password:
        parser.exit(1, 'Passwords do not match.\n')
    with get_sessionmaker()() as session:
        try:
            user, created = create_or_reset(session, args.username, password, args.role)
        except ValueError as exc:
            parser.exit(1, f'{exc}\n')
    print(f"{'Created' if created else 'Updated'} {user.role.value} account '{user.username}'.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
