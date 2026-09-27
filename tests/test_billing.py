"""Prepaid Live minutes: free credit, per-session reservation/refund, PayLink top-ups (mocked)."""
import asyncio
import base64
import hashlib
import hmac
import types
from app import billing, live, main, store
from tests.test_tutor import seed_course


def enable_live(monkeypatch, session_seconds=None, usage=None):
    monkeypatch.setenv('TUTOR_PROVIDER', 'gemini')
    monkeypatch.setenv('GEMINI_API_KEY', 'AQ.test')

    async def fake_proxy(send, messages, title, page, used):
        await send({'type': 'ready'})
        used.update(usage or {'prompt': 1200, 'response': 300})
        if session_seconds is None:
            await asyncio.sleep(3600)          # "talks" until the credit timeout ends it
        await asyncio.sleep(session_seconds or 0)
    monkeypatch.setattr(live, 'proxy', fake_proxy)


def talk(c, job_id):
    with c.websocket_connect(f'/api/live/{job_id}') as ws:
        ws.send_json({'type': 'start', 'page': 1})
        out = []
        while True:
            m = ws.receive_json()
            out.append(m)
            if m['type'] in ('end', 'no_credit'):
                return out


def set_seconds(username, seconds):
    with store.connect() as db:
        db.execute('UPDATE users SET live_seconds=? WHERE username=?', (seconds, username))


def test_new_student_gets_free_minutes(client_user):
    c, _ = client_user
    info = c.get('/api/billing').json()
    assert info['seconds'] == billing.FREE_SECONDS == 600
    assert info['unlimited'] is False and info['packages'][0] == {'id': '30min', 'minutes': 30, 'price': 5000}


def test_live_session_ends_when_credit_runs_out(admin, client_user, monkeypatch):
    job_id = seed_course()
    enable_live(monkeypatch)
    set_seconds('student1', 2)
    c, _ = client_user
    msgs = talk(c, job_id)
    assert msgs[0] == {'type': 'credit', 'unlimited': False, 'session_seconds': 2, 'balance': 2}
    assert msgs[-1] == {'type': 'end', 'reason': 'credit', 'balance': 0}
    assert talk(c, job_id)[-1]['type'] == 'no_credit'           # paywall from now on
    with store.connect() as db:
        row = db.execute('SELECT * FROM live_sessions').fetchone()
    assert row['username'] == 'student1' and row['prompt_tokens'] == 1200


def test_unused_reserved_time_is_refunded(admin, client_user, monkeypatch):
    job_id = seed_course()
    enable_live(monkeypatch, session_seconds=0)
    c, _ = client_user
    msgs = talk(c, job_id)
    assert msgs[0]['session_seconds'] == live.MAX_SECONDS
    assert msgs[-1]['reason'] == 'done' and 598 <= msgs[-1]['balance'] < 600
    assert 598 <= billing.balance('student1') < 600


def test_admin_live_is_unlimited_and_can_grant(admin, client_user, monkeypatch):
    job_id = seed_course()
    enable_live(monkeypatch, session_seconds=0)
    a, ha = admin
    assert talk(a, job_id)[0]['unlimited'] is True
    res = a.post('/api/admin/live-credit', json={'username': 'student1', 'minutes': 30}, headers=ha)
    assert res.status_code == 200 and res.json()['seconds'] == 600 + 1800
    c, hc = client_user
    assert c.post('/api/admin/live-credit', json={'username': 'student1', 'minutes': 30}, headers=hc).status_code == 403


def test_signature_matches_paylink_formula():
    expected = base64.b64encode(hmac.new(b'TWVDb3JlRmliYUlLPQ==', b'secret', hashlib.sha512).digest()).decode()
    assert billing.signature('secret') == expected


def fake_paylink(monkeypatch, status='pending'):
    calls = []
    monkeypatch.setenv('PAYLINK_USERNAME', 'merchant')
    monkeypatch.setenv('PAYLINK_PASSWORD', 'pw')

    def post(url, json=None, headers=None, timeout=None):
        calls.append((headers['pc'], json, headers))
        if headers['pc'] == 'cu0900':
            body = {'invid': 'INV-1', 'payment_link': 'https://pay-link.fiba.mn/pay/INV-1'}
        else:
            body = {'status': status[0]}
        return types.SimpleNamespace(json=lambda: {'response_code': 'RC000000', 'response': body})
    import requests
    monkeypatch.setattr(requests, 'post', post)
    return calls, status


def test_paylink_checkout_credits_minutes_once(client_user, monkeypatch):
    status = ['pending']
    calls, _ = fake_paylink(monkeypatch, status)
    c, hc = client_user
    res = c.post('/api/billing/checkout', json={'package': '30min'}, headers=hc)
    assert res.status_code == 200, res.text
    assert res.json()['payment_link'].endswith('INV-1')
    assert calls[0][0] == 'cu0900' and calls[0][1]['amount'] == 5000
    assert calls[0][2]['X-SIGNATURE'] == billing.signature('pw')

    assert c.get('/api/billing').json()['seconds'] == 600             # not paid yet
    status[0] = 'paid'
    assert c.get('/api/billing').json()['seconds'] == 600 + 1800      # back from paylink.mn
    billing.poll_pending()                                            # poller must not credit twice
    assert c.get('/api/billing').json()['seconds'] == 600 + 1800
    assert billing.invoice_status('INV-1') == 'paid'


def test_student_only_reconciles_own_invoices(admin, client_user, monkeypatch):
    status = ['pending']
    calls, _ = fake_paylink(monkeypatch, status)
    c, hc = client_user
    c.post('/api/billing/checkout', json={'package': '30min'}, headers=hc)
    status[0] = 'paid'
    a, _ = admin
    a.get('/api/billing')                                  # admin page load: no PayLink calls
    assert [pc for pc, *_ in calls] == ['cu0900']


def test_checkout_without_paylink_config(client_user, monkeypatch):
    monkeypatch.delenv('PAYLINK_USERNAME', raising=False)
    monkeypatch.delenv('PAYLINK_PASSWORD', raising=False)
    c, hc = client_user
    assert c.get('/api/billing').json()['paylink'] is False
    assert c.post('/api/billing/checkout', json={'package': '30min'}, headers=hc).status_code == 503
    assert c.post('/api/billing/checkout', json={'package': 'nope'}, headers=hc).status_code == 400
