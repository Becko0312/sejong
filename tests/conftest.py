"""Shared fixtures: an admin-authenticated client and a self-registered client."""
import io
import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from app import main, store

ADMIN_USER, ADMIN_PW = 'admin', 'admin-password-123'


def sample():
    buffer = io.BytesIO()
    doc = canvas.Canvas(buffer)
    doc.drawString(40, 740, '<script>alert(1)</script> Korean textbook')
    doc.showPage()
    doc.showPage()
    doc.save()
    return buffer.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setenv('ADMIN_USERNAME', ADMIN_USER)
    monkeypatch.setenv('ADMIN_PASSWORD', ADMIN_PW)
    return tmp_path


def login(client, username, password):
    res = client.post('/api/login', json={'username': username, 'password': password})
    assert res.status_code == 200, res.text
    return {'x-csrf-token': res.json()['csrf']}


@pytest.fixture
def admin(env):
    with TestClient(main.app) as client:      # lifespan seeds the admin from env
        yield client, login(client, ADMIN_USER, ADMIN_PW)


@pytest.fixture
def client_user(env):
    with TestClient(main.app) as client:
        res = client.post('/api/register', json={'username': 'student1', 'password': 'student-pass-1'})
        assert res.status_code == 200, res.text
        yield client, {'x-csrf-token': res.json()['csrf']}
