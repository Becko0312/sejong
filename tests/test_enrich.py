"""Interactive-page reorganization: estimates, batch build, endpoint guards. Gemini mocked."""
import json
import time
import types
import pytest
from app import enrich, main, store

PAYLOAD = {'sentences': [{'ko': '안녕하세요.', 'rom': 'annyeonghaseyo', 'tr': 'Сайн уу.'},
                         {'ko': '', 'rom': 'noise', 'tr': ''},
                         {'ko': '감사합니다.', 'rom': '', 'tr': 'Баярлалаа.'}]}


def fake_gemini(monkeypatch, payload=None):
    """Point enrich at a fake Gemini; returns the list of request bodies."""
    calls = []
    text = json.dumps(PAYLOAD if payload is None else payload)
    monkeypatch.setenv('GEMINI_API_KEY', 'AQ.test')
    import requests

    def fake_post(url, params=None, json=None, timeout=None):
        calls.append(json)
        return types.SimpleNamespace(status_code=200,
                                     json=lambda: {'candidates': [{'content': {'parts': [{'text': text}]}}]})

    monkeypatch.setattr(requests, 'post', fake_post)
    return calls


def manifest():
    return {'title': 'T', 'page_count': 2, 'pages': [
        {'number': 1, 'text': '안녕하세요. 감사합니다.', 'text_source': 'extracted'},
        {'number': 2, 'text': '   ', 'text_source': 'none'}]}


def seed_job(env, job_id='job1'):
    out = env / job_id / 'output'
    out.mkdir(parents=True)
    (out / 'manifest.json').write_text(json.dumps(manifest()), encoding='utf-8')
    with store.connect() as db:
        db.execute("INSERT INTO jobs(id, name, status, done, total, created, owner, expires, available, title)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (job_id, 'doc.pdf', 'completed', 2, 2, time.time(), 'admin', time.time() + 3600, 1, 'T'))


# ---------- module ----------

def test_estimate_counts_only_text_pages_and_prices():
    est = enrich.estimate(manifest())
    assert est['pages'] == 1
    chars = len('안녕하세요. 감사합니다.')
    assert est['input_tokens'] == chars // enrich.CHARS_PER_TOKEN + enrich.SYSTEM_TOKENS
    assert est['output_tokens'] == enrich.OUTPUT_TOKENS_PER_PAGE
    assert est['total_tokens'] == est['input_tokens'] + est['output_tokens']
    assert est['cost_usd'] > 0


def test_build_all_writes_pages_and_status(env, monkeypatch):
    calls = fake_gemini(monkeypatch)
    status = enrich.build_all(env, manifest())
    assert status['state'] == 'done' and status['done'] == 1 and status['total'] == 1
    assert len(calls) == 1
    saved = json.loads((env / 'output' / 'interactive-1.json').read_text(encoding='utf-8'))
    assert [s['ko'] for s in saved['sentences']] == ['안녕하세요.', '감사합니다.']
    assert enrich.built_count(env) == 1


def test_generate_rejects_garbage(monkeypatch):
    fake_gemini(monkeypatch, payload={'nope': True})
    with pytest.raises(enrich.EnrichError):
        enrich.generate(manifest()['pages'][0])
    fake_gemini(monkeypatch, payload='not json at all')
    with pytest.raises(enrich.EnrichError):
        enrich.generate(manifest()['pages'][0])


def test_start_batch_runs_once(env, monkeypatch):
    fake_gemini(monkeypatch)
    assert enrich.start_batch(env, manifest()) is True
    assert enrich.start_batch(env, manifest()) is False
    for _ in range(100):
        if enrich.read_status(env)['state'] in ('done', 'partial', 'error'):
            break
        time.sleep(0.05)
    assert enrich.read_status(env)['state'] == 'done'
    assert enrich.built_count(env) == 1


# ---------- API ----------

def test_admin_enrich_estimate_and_build(admin, env, monkeypatch):
    client, headers = admin
    seed_job(env)
    calls = fake_gemini(monkeypatch)
    info = client.get('/api/admin/courses/job1/enrich', headers=headers).json()
    assert info['pages'] == 1 and info['built'] == 0 and info['status']['state'] == 'idle'
    res = client.post('/api/admin/courses/job1/enrich', headers=headers)
    assert res.status_code == 200, res.text
    for _ in range(100):
        status = client.get('/api/admin/courses/job1/enrich', headers=headers).json()['status']
        if status['state'] in ('done', 'partial', 'error'):
            break
        time.sleep(0.05)
    assert status['state'] == 'done' and len(calls) == 1


def test_interactive_page_guards(admin, client_user, env, monkeypatch):
    admin_client, admin_headers = admin
    client, headers = client_user
    seed_job(env)
    fake_gemini(monkeypatch)
    # Not built yet: students are refused, admins generate on demand.
    assert client.get('/api/courses/job1/pages/1/interactive', headers=headers).status_code == 409
    got = admin_client.get('/api/courses/job1/pages/1/interactive', headers=admin_headers)
    assert got.status_code == 200 and got.json()['sentences'][0]['ko'] == '안녕하세요.'
    # Once built, students read the cached page without spending more tokens.
    assert client.get('/api/courses/job1/pages/1/interactive', headers=headers).status_code == 200
    assert client.get('/api/courses/job1/pages/2/interactive', headers=headers).status_code == 409
    client.cookies.clear()
    assert client.get('/api/courses/job1/pages/1/interactive', headers=headers).status_code == 401


def test_course_payload_reports_enrich(admin, env, monkeypatch):
    client, headers = admin
    seed_job(env)
    fake_gemini(monkeypatch)
    course = client.get('/api/courses/job1', headers=headers).json()
    assert course['enrich'] == {'enabled': True, 'built': 0}
