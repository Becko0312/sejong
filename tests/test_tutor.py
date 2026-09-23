"""Tutor module (provider-agnostic) and role-based tutor access. Claude/Gemini are mocked."""
import json
import time
import types
import uuid
import pytest
from app import main, store, tutor


# ---------- direct tutor-module tests (no API) ----------

def fake_anthropic(capture):
    class Messages:
        def create(self, **kwargs):
            capture.update(kwargs)
            return types.SimpleNamespace(content=[types.SimpleNamespace(type='text', text='안녕하세요! (annyeonghaseyo)')], model='claude-opus-5')
    return types.SimpleNamespace(messages=Messages())


def enable_tutor(monkeypatch, capture=None):
    capture = {} if capture is None else capture
    monkeypatch.setenv('TUTOR_PROVIDER', 'anthropic')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test')
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    import anthropic
    monkeypatch.setattr(anthropic, 'Anthropic', lambda *a, **k: fake_anthropic(capture))
    return capture


def test_enabled_reflects_credentials(monkeypatch):
    for name in ('TUTOR_PROVIDER', 'GEMINI_API_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'):
        monkeypatch.delenv(name, raising=False)
    assert tutor.enabled() is False
    assert tutor.config() == {'enabled': False, 'provider': None, 'model': None}


def test_answer_grounds_in_page_and_bounds_history(monkeypatch):
    capture = enable_tutor(monkeypatch)
    page = {'number': 3, 'page_count': 10, 'text': 'lesson three 삼과', 'text_source': 'ocr'}
    tutor.answer('How do I say hello?', 'Sejong Korean 1', page, [{'role': 'user', 'content': f'q{i}'} for i in range(30)])
    assert len(capture['messages']) <= tutor.MAX_HISTORY + 1
    assert 'lesson three' in capture['messages'][-1]['content'] and '<page_text>' in capture['messages'][-1]['content']


def test_teaching_mode_needs_no_question(monkeypatch):
    capture = enable_tutor(monkeypatch)
    tutor.answer('', 'Book', {'number': 1, 'text': '안녕'}, mode='vocab')
    assert tutor.MODES['vocab'] in capture['messages'][-1]['content']
    with pytest.raises(tutor.TutorError):
        tutor.answer('', 'Book', {'number': 1}, mode='sudo')


def test_gemini_provider_maps_roles(monkeypatch):
    capture = {}
    monkeypatch.setenv('TUTOR_PROVIDER', 'gemini')
    monkeypatch.setenv('GEMINI_API_KEY', 'AQ.test')
    import requests
    monkeypatch.setattr(requests, 'post', lambda url, params=None, json=None, timeout=None: capture.update(url=url, params=params, body=json) or types.SimpleNamespace(status_code=200, json=lambda: {'candidates': [{'content': {'parts': [{'text': '좋아요'}]}}], 'modelVersion': 'gemini-2.5-flash'}))
    out = tutor.answer('hi', 'Book', {'number': 1, 'text': '안녕'}, [{'role': 'assistant', 'content': 'x'}])
    assert out['reply'] == '좋아요'
    assert capture['body']['contents'][0]['role'] == 'user'
    assert capture['params']['key'] == 'AQ.test'


# ---------- role-based tutor + course access ----------

def seed_course(available=1, title='Sejong Korean 1'):
    job_id = uuid.uuid4().hex
    with store.connect() as db:
        db.execute('INSERT INTO jobs(id,name,status,created,owner,expires,available,title) VALUES(?,?,?,?,?,?,?,?)',
                   (job_id, 'c.pdf', 'completed', time.time(), 'admin', time.time() + 99999, available, title))
    out = store.DATA / job_id / 'output'
    out.mkdir(parents=True, exist_ok=True)
    manifest = {'schema_version': '1.1', 'title': title, 'page_count': 2, 'pages': [
        {'number': 1, 'text': '안녕하세요 hello', 'text_source': 'ocr', 'needs_ocr': False, 'needs_review': False},
        {'number': 2, 'text': 'scanned', 'text_source': 'ocr', 'needs_ocr': False, 'needs_review': True}]}
    (out / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (out / 'page-1.png').write_bytes(b'\x89PNG\r\n')
    return job_id


def test_client_studies_available_course_with_tutor(admin, client_user, monkeypatch):
    job_id = seed_course()
    capture = enable_tutor(monkeypatch)
    c, hc = client_user
    assert c.get(f'/course/{job_id}').status_code == 200          # reader shell
    assert c.get(f'/api/courses/{job_id}').json()['title'] == 'Sejong Korean 1'
    res = c.post(f'/api/tutor/{job_id}', json={'page': 1, 'mode': 'explain'}, headers=hc)
    assert res.status_code == 200, res.text
    assert '안녕하세요' in res.json()['reply']
    assert '안녕하세요 hello' in capture['messages'][-1]['content']


def test_tutor_requires_sign_in_and_csrf(admin, client_user, monkeypatch):
    job_id = seed_course()
    enable_tutor(monkeypatch)
    c, hc = client_user
    assert c.post(f'/api/tutor/{job_id}', json={'page': 1, 'question': 'hi'}).status_code == 403  # no CSRF
    from fastapi.testclient import TestClient
    with TestClient(main.app) as anon:
        assert anon.post(f'/api/tutor/{job_id}', json={'page': 1, 'question': 'hi'}).status_code == 401


def test_client_blocked_from_unavailable_course(admin, client_user, monkeypatch):
    job_id = seed_course(available=0)
    enable_tutor(monkeypatch)
    c, hc = client_user
    assert c.get(f'/api/courses/{job_id}').status_code == 404
    assert c.post(f'/api/tutor/{job_id}', json={'page': 1, 'question': 'hi'}, headers=hc).status_code == 404


def test_client_daily_tutor_limit(admin, client_user, monkeypatch):
    job_id = seed_course()
    enable_tutor(monkeypatch)
    monkeypatch.setattr(store, 'CLIENT_TUTOR_PER_DAY', 2)
    c, hc = client_user
    assert c.post(f'/api/tutor/{job_id}', json={'page': 1, 'mode': 'explain'}, headers=hc).status_code == 200
    assert c.post(f'/api/tutor/{job_id}', json={'page': 1, 'mode': 'vocab'}, headers=hc).status_code == 200
    assert c.post(f'/api/tutor/{job_id}', json={'page': 1, 'mode': 'quiz'}, headers=hc).status_code == 429


def test_admin_exempt_from_tutor_limit(admin, monkeypatch):
    job_id = seed_course()
    enable_tutor(monkeypatch)
    monkeypatch.setattr(store, 'CLIENT_TUTOR_PER_DAY', 1)
    a, ha = admin
    for _ in range(3):
        assert a.post(f'/api/tutor/{job_id}', json={'page': 1, 'mode': 'explain'}, headers=ha).status_code == 200
