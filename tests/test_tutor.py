"""Tutor module and course endpoints. The Anthropic client is always mocked."""
import json
import types
import pytest
from fastapi.testclient import TestClient
from app import main, store, tutor
from tests.test_converter import sample


@pytest.fixture
def course(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    with TestClient(main.app) as a, TestClient(main.app) as b:
        ha = {'x-csrf-token': a.get('/api/session').json()['csrf'], 'content-type': 'application/pdf'}
        hb = {'x-csrf-token': b.get('/api/session').json()['csrf'], 'content-type': 'application/pdf'}
        job = a.post('/api/jobs?name=Sejong.pdf', content=sample(), headers=ha).json()
        store.update(job['id'], status='completed')
        output = store.DATA / job['id'] / 'output'
        output.mkdir(parents=True, exist_ok=True)
        manifest = {'schema_version': '1.1', 'title': 'Sejong Korean 1', 'page_count': 2,
                    'review_required_pages': 1, 'pages': [
                        {'number': 1, 'text': '안녕하세요 hello', 'text_source': 'embedded', 'needs_ocr': False, 'needs_review': False},
                        {'number': 2, 'text': 'scanned page', 'text_source': 'ocr', 'needs_ocr': False, 'needs_review': True}]}
        (output / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        (output / 'page-1.png').write_bytes(b'\x89PNG\r\n')
        yield a, b, {'x-csrf-token': ha['x-csrf-token']}, {'x-csrf-token': hb['x-csrf-token']}, job['id']


class FakeMessage:
    def __init__(self, text):
        self.content = [types.SimpleNamespace(type='text', text=text)]
        self.model = 'claude-opus-5'


def fake_client(capture):
    class Messages:
        def create(self, **kwargs):
            capture.update(kwargs)
            return FakeMessage('안녕하세요! (annyeonghaseyo) means hello. 🌱')
    return types.SimpleNamespace(messages=Messages())


def enable_tutor(monkeypatch, capture=None):
    """Enable the Anthropic provider with a mocked client (no tokens spent)."""
    capture = {} if capture is None else capture
    monkeypatch.setenv('TUTOR_PROVIDER', 'anthropic')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test')
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    import anthropic
    monkeypatch.setattr(anthropic, 'Anthropic', lambda *a, **k: fake_client(capture))
    return capture


def enable_gemini(monkeypatch, capture=None):
    """Enable the Gemini provider with a mocked requests.post (no tokens spent)."""
    capture = {} if capture is None else capture
    monkeypatch.setenv('TUTOR_PROVIDER', 'gemini')
    monkeypatch.setenv('GEMINI_API_KEY', 'AQ.test')
    import requests

    def fake_post(url, params=None, json=None, timeout=None):
        capture.update(url=url, params=params, body=json)
        return types.SimpleNamespace(status_code=200, json=lambda: {
            'candidates': [{'content': {'parts': [{'text': '좋아요! (johayo) — good job. 🌱'}]}}],
            'modelVersion': 'gemini-2.5-flash'})
    monkeypatch.setattr(requests, 'post', fake_post)
    return capture


def test_enabled_reflects_credentials(monkeypatch):
    monkeypatch.delenv('TUTOR_PROVIDER', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_AUTH_TOKEN', raising=False)
    assert tutor.enabled() is False
    assert tutor.config() == {'enabled': False, 'provider': None, 'model': None}


def test_gemini_provider_grounds_and_maps_roles(monkeypatch):
    capture = enable_gemini(monkeypatch)
    assert tutor.enabled() is True
    assert tutor.config()['provider'] == 'gemini'
    page = {'number': 2, 'page_count': 5, 'text': '감사합니다 thank you', 'text_source': 'embedded'}
    history = [{'role': 'user', 'content': 'hi'}, {'role': 'assistant', 'content': 'earlier reply'}]
    result = tutor.answer('What is on this page?', 'Sejong Korean 1', page, history)
    assert '좋아요' in result['reply']
    contents = capture['body']['contents']
    assert contents[0]['role'] == 'user'
    assert contents[1]['role'] == 'model'  # assistant mapped to Gemini's "model"
    assert '감사합니다' in contents[-1]['parts'][0]['text'] and '<page_text>' in contents[-1]['parts'][0]['text']
    assert capture['body']['system_instruction']['parts'][0]['text'] == tutor.SYSTEM
    assert capture['params']['key'] == 'AQ.test'


def test_answer_grounds_in_page_and_bounds_history(monkeypatch):
    capture = enable_tutor(monkeypatch)
    page = {'number': 3, 'page_count': 10, 'text': 'lesson three 삼과', 'text_source': 'ocr'}
    history = [{'role': 'user', 'content': f'q{i}'} for i in range(30)]
    result = tutor.answer('How do I say hello?', 'Sejong Korean 1', page, history)
    assert '안녕하세요' in result['reply']
    assert len(capture['messages']) <= tutor.MAX_HISTORY + 1
    last = capture['messages'][-1]['content']
    assert 'Student question' in last and 'lesson three' in last and '<page_text>' in last
    assert capture['system'] == tutor.SYSTEM


def test_answer_requires_question_and_config(monkeypatch):
    monkeypatch.setattr(tutor, 'enabled', lambda: False)
    with pytest.raises(tutor.TutorError):
        tutor.answer('', 'Book', {'number': 1})
    with pytest.raises(tutor.TutorError):
        tutor.answer('hi', 'Book', {'number': 1})


def test_tutor_endpoint_owner_scoped_and_answers(course, monkeypatch):
    a, b, ha, hb, job_id = course
    capture = enable_tutor(monkeypatch)
    payload = {'question': 'What does page 1 say?', 'page': 1, 'history': []}
    # Cross-session access is denied.
    assert b.post(f'/api/tutor/{job_id}', json=payload, headers=hb).status_code == 404
    # CSRF is required.
    assert a.post(f'/api/tutor/{job_id}', json=payload).status_code == 403
    res = a.post(f'/api/tutor/{job_id}', json=payload, headers=ha)
    assert res.status_code == 200, res.text
    assert '안녕하세요' in res.json()['reply']
    assert '안녕하세요 hello' in capture['messages'][-1]['content']


def test_tutor_endpoint_rejects_unknown_page(course, monkeypatch):
    a, _, ha, _, job_id = course
    enable_tutor(monkeypatch)
    assert a.post(f'/api/tutor/{job_id}', json={'question': 'hi', 'page': 99}, headers=ha).status_code == 404


def test_tutor_endpoint_offline_is_graceful(course, monkeypatch):
    a, _, ha, _, job_id = course
    monkeypatch.setattr(tutor, 'enabled', lambda: False)
    res = a.post(f'/api/tutor/{job_id}', json={'question': 'hi', 'page': 1}, headers=ha)
    assert res.status_code == 503
    assert 'not configured' in res.json()['detail']


def test_course_reader_served(course):
    a, _, _, _, job_id = course
    res = a.get(f'/course/{job_id}')
    assert res.status_code == 200
    assert 'course.js' in res.text


def test_course_structure_endpoint(course):
    a, b, ha, hb, job_id = course
    assert b.get(f'/api/courses/{job_id}').status_code == 404  # cross-session denied
    data = a.get(f'/api/courses/{job_id}').json()
    assert data['title'] == 'Sejong Korean 1'
    assert data['page_count'] == 2
    assert [p['number'] for p in data['pages']] == [1, 2]
    assert 'words' not in data['pages'][0]  # heavy coordinates stripped for the reader
    assert 'lessons' in data and 'tutor' in data


def test_session_reports_tutor(course, monkeypatch):
    a, _, _, _, _ = course
    monkeypatch.setattr(tutor, 'enabled', lambda: False)
    assert a.get('/api/session').json()['tutor'] == {'enabled': False, 'provider': None, 'model': None}
