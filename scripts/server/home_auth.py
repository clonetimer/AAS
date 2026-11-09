"""Local administrator bootstrap and cookie sessions for the AAS web console.

Authentication is mandatory for business APIs and workspace documents. Credentials
are never shipped with the application; bootstrap must originate on loopback.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse

PASSWORD_ITERATIONS = 260_000
SESSION_LIFETIME = 8 * 60 * 60
PASSWORD_MIN_LENGTH = 10
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
COOKIE_NAME = "aas_session"


class AuthError(ValueError):
    """User-visible authentication or validation error."""


class HomeAuth:
    """One local administrator and short-lived in-memory sessions."""

    def __init__(self, project_root: Path):
        self.credentials_path = Path(project_root) / 'configs' / '.aas_admin.json'
        self._lock = threading.RLock()
        self._sessions: dict[str, tuple[str, float]] = {}
        self._failed: dict[str, tuple[int, float]] = {}

    def initialized(self) -> bool:
        return self.credentials_path.is_file()

    def _read(self) -> dict:
        return json.loads(self.credentials_path.read_text(encoding='utf-8'))

    @staticmethod
    def _hash(password: str, salt_hex: str, iterations: int) -> str:
        return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                   bytes.fromhex(salt_hex), iterations).hex()

    def setup(self, username: str, password: str, client_address: str) -> None:
        if client_address not in ('127.0.0.1', '::1'):
            raise AuthError('首次设置管理员仅允许在本机访问')
        if not isinstance(username, str) or not USERNAME_RE.fullmatch(username):
            raise AuthError('账号须为 3–32 位字母、数字、下划线、点或短横线')
        if not isinstance(password, str) or not PASSWORD_MIN_LENGTH <= len(password) <= 128:
            raise AuthError('密码长度须为 10–128 个字符')
        with self._lock:
            self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
            salt = secrets.token_hex(24)
            record = {'username': username, 'salt': salt, 'iterations': PASSWORD_ITERATIONS,
                      'password_hash': self._hash(password, salt, PASSWORD_ITERATIONS)}
            # Exclusive file creation prevents a race between two bootstrap attempts.
            try:
                fd = os.open(str(self.credentials_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError as exc:
                raise AuthError('管理员已初始化，不能重复创建') from exc
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump(record, stream, ensure_ascii=False)
                if os.name != 'nt':
                    self.credentials_path.chmod(0o600)
            except BaseException:
                self.credentials_path.unlink(missing_ok=True)
                raise

    def login(self, username: str, password: str, address: str) -> str:
        with self._lock:
            if not self.initialized():
                raise AuthError('请先在本机设置管理员账号')
            attempts, until = self._failed.get(address, (0, 0))
            if attempts >= 5 and time.time() < until:
                raise AuthError('尝试次数过多，请稍后再试')
            record = self._read()
            expected = record['password_hash']
            actual = self._hash(str(password), record['salt'], int(record['iterations']))
            if not (hmac.compare_digest(str(username), record['username'])
                    and hmac.compare_digest(expected, actual)):
                self._failed[address] = (attempts + 1, time.time() + 600)
                raise AuthError('账号或密码错误')
            self._failed.pop(address, None)
            token = secrets.token_urlsafe(36)
            self._sessions[hashlib.sha256(token.encode()).hexdigest()] = (
                record['username'], time.time() + SESSION_LIFETIME)
            return token

    @staticmethod
    def token_from_cookie(raw_cookie: str) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie or '')
        except Exception:  # pylint: disable=broad-exception-caught
            return ''
        item = cookie.get(COOKIE_NAME)
        return item.value if item else ''

    def user(self, cookie_header: str) -> str | None:
        token = self.token_from_cookie(cookie_header)
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            session = self._sessions.get(digest)
            if not session:
                return None
            if time.time() >= session[1]:
                self._sessions.pop(digest, None)
                return None
            return session[0]

    def logout(self, cookie_header: str) -> None:
        token = self.token_from_cookie(cookie_header)
        with self._lock:
            if token:
                self._sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)


def same_origin(origin: str, host: str) -> bool:
    """Disallow cross-site API writes when browsers send an Origin header."""
    if not origin:
        return True  # Direct CLI calls have no Origin; they still require auth.
    try:
        parsed = urlparse(origin)
        return parsed.scheme in {'http', 'https'} and parsed.netloc.lower() == host.lower()
    except ValueError:
        return False


def auth_cookie(token: str, *, secure: bool = False) -> str:
    suffix = '; Secure' if secure else ''
    return f'{COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_LIFETIME}; HttpOnly; SameSite=Strict{suffix}'


def expired_cookie() -> str:
    return f'{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict'
