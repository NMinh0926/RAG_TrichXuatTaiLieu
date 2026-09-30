"""Password hashing (scrypt) and signed bearer tokens (HMAC-SHA256), standard library only."""
import base64
import hashlib
import hmac
import json
import secrets
import time

SCRYPT = {'n': 2 ** 14, 'r': 8, 'p': 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split('$')
        if scheme != 'scrypt':
            return False
        candidate = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt), dklen=len(digest) // 2,
                                   n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def issue_token(secret: str, user_id: str, token_version: int, ttl_seconds: int) -> str:
    """`<payload>.<signature>`; the payload carries the user id, a version (bumped to revoke) and expiry."""
    payload = _b64(json.dumps({'sub': user_id, 'ver': token_version, 'exp': int(time.time()) + ttl_seconds},
                              separators=(',', ':')).encode())
    signature = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f'{payload}.{signature}'


def read_token(secret: str, token: str) -> dict | None:
    """The payload of a valid, unexpired token; None otherwise."""
    try:
        payload, signature = token.split('.')
        expected = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature):
            return None
        data = json.loads(_unb64(payload))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get('exp', 0) < time.time():
        return None
    return data
