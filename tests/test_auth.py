"""Login, registration, roles and session behaviour."""
from fastapi.testclient import TestClient
from app import main, auth
from tests.conftest import ADMIN_USER, ADMIN_PW, login


def test_anonymous_is_unauthenticated(env):
    with TestClient(main.app) as c:
        body = c.get('/api/me').json()
        assert body['authenticated'] is False
        assert 'tutor' in body


def test_admin_seeded_and_login(env):
    with TestClient(main.app) as c:
        r = c.post('/api/login', json={'username': ADMIN_USER, 'password': ADMIN_PW})
        assert r.status_code == 200
        assert r.json()['role'] == 'admin'
        assert c.get('/api/me').json() == {**c.get('/api/me').json()}  # stable
        assert c.get('/api/me').json()['role'] == 'admin'


def test_wrong_password_rejected(env):
    with TestClient(main.app) as c:
        assert c.post('/api/login', json={'username': ADMIN_USER, 'password': 'nope'}).status_code == 401


def test_client_self_registration_and_role(env):
    with TestClient(main.app) as c:
        r = c.post('/api/register', json={'username': 'newbie', 'password': 'longenough1'})
        assert r.status_code == 200 and r.json()['role'] == 'client'
        assert c.get('/api/me').json()['username'] == 'newbie'


def test_registration_validation(env):
    with TestClient(main.app) as c:
        assert c.post('/api/register', json={'username': 'ok', 'password': 'longenough1'}).status_code == 400  # username too short
        assert c.post('/api/register', json={'username': 'gooduser', 'password': 'short'}).status_code == 400  # password too short
        c.post('/api/register', json={'username': 'dup', 'password': 'longenough1'})
        assert c.post('/api/register', json={'username': 'dup', 'password': 'longenough1'}).status_code == 409


def test_logout_clears_session(env):
    with TestClient(main.app) as c:
        login(c, ADMIN_USER, ADMIN_PW)
        assert c.get('/api/me').json()['authenticated'] is True
        assert c.post('/api/logout').status_code == 204
        assert c.get('/api/me').json()['authenticated'] is False


def test_password_hash_roundtrip():
    h = auth.hash_password('correct horse battery')
    assert auth.verify_password('correct horse battery', h)
    assert not auth.verify_password('wrong', h)
    assert h != auth.hash_password('correct horse battery')  # salted
