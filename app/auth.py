"""Accounts, roles and sessions for the Book2Course platform.

Two roles: 'admin' (adds and manages courses) and 'client' (studies them).
Admins are seeded from ADMIN_USERNAME/ADMIN_PASSWORD; clients self-register.
Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes (stdlib, no deps).
Sessions are opaque random tokens in an HTTP-only cookie, backed by a table.
"""
import hashlib
import hmac
import os
import re
import secrets
import time
from app import languages, store

COOKIE = 'b2c_session'
USERNAME_RE = re.compile(r'[A-Za-z0-9_.-]{3,32}')
PBKDF2_ROUNDS = 200_000


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PBKDF2_ROUNDS)
    return f'pbkdf2$sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}'


def verify_password(password, stored):
    try:
        _, _, rounds, salt_hex, digest_hex = stored.split('$')
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt_hex), int(rounds))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(actual, expected)


def valid_username(username):
    return bool(username and USERNAME_RE.fullmatch(username))


def seed_admin():
    """Create the configured admin account if it does not exist yet."""
    username, password = os.getenv('ADMIN_USERNAME'), os.getenv('ADMIN_PASSWORD')
    if not username or not password:
        return
    with store.connect() as db:
        row = db.execute('SELECT role FROM users WHERE username=?', (username,)).fetchone()
        if row is None:
            db.execute('INSERT INTO users(username, password_hash, role, created) VALUES(?,?,?,?)',
                       (username, hash_password(password), 'admin', time.time()))
        elif row['role'] != 'admin':
            db.execute('UPDATE users SET role=? WHERE username=?', ('admin', username))


def register(username, password, role='client', native_lang=None):
    """native_lang: the student's mother tongue (a languages.LANGUAGES name); the Live tutor speaks it."""
    username = (username or '').strip()
    if not valid_username(username):
        raise AuthError(400, 'Username must be 3-32 letters, numbers, dot, dash or underscore.')
    if not password or len(password) < 8:
        raise AuthError(400, 'Password must be at least 8 characters.')
    native = languages.normalize(native_lang) if native_lang else languages.DEFAULT_NATIVE
    if not native:
        raise AuthError(400, 'Please choose your native language from the list.')
    with store.connect() as db:
        if db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
            raise AuthError(409, 'That username is taken.')
        db.execute('INSERT INTO users(username, password_hash, role, created, native_lang) VALUES(?,?,?,?,?)',
                   (username, hash_password(password), role, time.time(), native))


def native_language(username):
    """The student's mother tongue; Mongolian for accounts made before sign-up asked for it."""
    with store.connect() as db:
        row = db.execute('SELECT native_lang FROM users WHERE username=?', (username,)).fetchone()
    return languages.normalize(row and row['native_lang']) or languages.DEFAULT_NATIVE


def login(username, password):
    username = (username or '').strip()
    with store.connect() as db:
        row = db.execute('SELECT password_hash, role FROM users WHERE username=?', (username,)).fetchone()
    if not row or not verify_password(password or '', row['password_hash']):
        raise AuthError(401, 'Wrong username or password.')
    token = secrets.token_urlsafe(32)
    now = time.time()
    with store.connect() as db:
        db.execute('INSERT INTO sessions(token, username, role, created, expires) VALUES(?,?,?,?,?)',
                   (token, username, row['role'], now, now + store.SESSION_TTL))
    return token, row['role']


def logout(token):
    if token:
        with store.connect() as db:
            db.execute('DELETE FROM sessions WHERE token=?', (token,))


def session(request):
    """The signed-in user for this request as {username, role, token, csrf}, or None."""
    token = request.cookies.get(COOKIE, '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', token):
        return None
    with store.connect() as db:
        row = db.execute('SELECT username, role, expires FROM sessions WHERE token=?', (token,)).fetchone()
    if not row or row['expires'] < time.time():
        return None
    return {'username': row['username'], 'role': row['role'], 'token': token,
            'csrf': hashlib.sha256(token.encode()).hexdigest()}


def csrf_ok(request, user):
    return bool(user) and hmac.compare_digest(request.headers.get('x-csrf-token', ''), user['csrf'])


class AuthError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message
