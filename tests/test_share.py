"""Publishing a course to a public /learn link, and rate-limited public tutoring."""
import json
import types
import pytest
from fastapi.testclient import TestClient
from app import main, store, tutor
from tests.test_converter import sample


@pytest.fixture
def published(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    # owner = a, student/visitor = b (a different, non-owner session)
    with TestClient(main.app) as a, TestClient(main.app) as b:
        ha = {'x-csrf-token': a.get('/api/session').json()['csrf'], 'content-type': 'application/pdf'}
        hb = {'x-csrf-token': b.get('/api/session').json()['csrf']}
        job = a.post('/api/jobs?name=Course.pdf', content=sample(), headers=ha).json()
        store.update(job['id'], status='completed')
        out = store.DATA / job['id'] / 'output'
        out.mkdir(parents=True, exist_ok=True)
        (out / 'manifest.json').write_text(json.dumps({'schema_version': '1.1', 'title': 'Course', 'page_count': 1,
            'pages': [{'number': 1, 'text': '안녕하세요 hello', 'text_source': 'ocr', 'needs_ocr': False, 'needs_review': True}]}), encoding='utf-8')
        (out / 'page-1.png').write_bytes(b'\x89PNG\r\n')
        yield a, b, {'x-csrf-token': ha['x-csrf-token']}, hb, job['id']


def enable_tutor(monkeypatch):
    monkeypatch.setenv('TUTOR_PROVIDER', 'anthropic')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test')
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    import anthropic

    class Messages:
        def create(self, **kwargs):
            return types.SimpleNamespace(content=[types.SimpleNamespace(type='text', text='좋아요 (johayo)')], model='m')
    monkeypatch.setattr(anthropic, 'Anthropic', lambda *a, **k: types.SimpleNamespace(messages=Messages()))


def test_publish_is_owner_only_and_visitor_can_learn(published, monkeypatch):
    a, b, ha, hb, job_id = published
    # A non-owner cannot publish.
    assert b.post(f'/api/jobs/{job_id}/share', headers=hb).status_code == 404
    # Publishing needs CSRF.
    assert a.post(f'/api/jobs/{job_id}/share').status_code == 403
    result = a.post(f'/api/jobs/{job_id}/share', headers=ha).json()
    token = result['token']
    assert result['url'].endswith(f'/learn/{token}')
    # The owner's job now reports the share token.
    assert a.get(f'/api/jobs/{job_id}').json()['shared'] == token
    # A brand-new visitor (no ownership) can read the course and its pages.
    with TestClient(main.app) as visitor:
        visitor.get('/api/session')
        course = visitor.get(f'/api/shared/{token}')
        assert course.status_code == 200
        assert course.json()['title'] == 'Course' and course.json()['shared'] is True
        assert visitor.get(f'/shared/{token}/page-1.png').status_code == 200
        assert visitor.get(f'/shared/{token}/manifest.json').status_code == 404   # only page images are public
        assert visitor.get(f'/learn/{token}').status_code == 200
        # The visitor cannot reach the private owner endpoints.
        assert visitor.get(f'/api/courses/{job_id}').status_code in (401, 404)


def test_unpublish_revokes_the_link(published):
    a, b, ha, hb, job_id = published
    token = a.post(f'/api/jobs/{job_id}/share', headers=ha).json()['token']
    assert b.get(f'/api/shared/{token}').status_code == 200
    assert a.request('DELETE', f'/api/jobs/{job_id}/share', headers=ha).status_code == 204
    assert b.get(f'/api/shared/{token}').status_code == 404
    assert b.get(f'/learn/{token}').status_code == 200          # page shell still serves; data is gone
    assert b.get(f'/api/shared/{token}/tutor').status_code in (404, 405)


def test_unknown_token_is_not_found(published):
    a, b, ha, hb, job_id = published
    assert b.get('/api/shared/nope-not-real').status_code == 404
    assert b.get('/shared/nope/page-1.png').status_code == 404


def test_public_tutor_works_for_visitor_and_is_rate_limited(published, monkeypatch):
    a, b, ha, hb, job_id = published
    enable_tutor(monkeypatch)
    monkeypatch.setattr(store, 'SHARE_TUTOR_PER_IP', 2)
    token = a.post(f'/api/jobs/{job_id}/share', headers=ha).json()['token']
    payload = {'page': 1, 'mode': 'explain'}
    # A non-owner visitor can use the tutor (with their own CSRF), up to the per-visitor cap.
    assert b.post(f'/api/shared/{token}/tutor', json=payload, headers=hb).status_code == 200
    assert b.post(f'/api/shared/{token}/tutor', json={'page': 1, 'question': 'hi'}, headers=hb).status_code == 200
    assert b.post(f'/api/shared/{token}/tutor', json=payload, headers=hb).status_code == 429
    # Public tutor still requires CSRF (blocks blind cross-site calls).
    assert b.post(f'/api/shared/{token}/tutor', json=payload).status_code == 403


def test_publishing_extends_retention(published, monkeypatch):
    import time
    a, b, ha, hb, job_id = published
    store.update(job_id, expires=time.time() + 100)   # about to expire
    a.post(f'/api/jobs/{job_id}/share', headers=ha)
    with store.connect() as db:
        expires = db.execute('SELECT expires FROM jobs WHERE id=?', (job_id,)).fetchone()['expires']
    assert expires > time.time() + 300 * 86400
