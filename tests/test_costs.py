"""Usage & cost meter: AI token pricing, Gemini call recording, Azure parsing, admin endpoint."""
import time
import types
import pytest
from app import costs, enrich, store


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(costs, '_cache', {'at': 0.0, 'data': None})


def test_price_uses_per_million_rates():
    assert costs.price('gemini-2.5-flash', 1_000_000, 1_000_000) == pytest.approx(2.80)
    assert costs.price('unknown-model', 1_000_000, 0) == pytest.approx(costs.DEFAULT_PRICE[0])


def test_gemini_calls_are_recorded_with_token_counts(env, monkeypatch):
    store.initialize()
    monkeypatch.setenv('GEMINI_API_KEY', 'AQ.test')
    import requests
    reply = {'candidates': [{'content': {'parts': [{'text': '{}'}]}}],
             'usageMetadata': {'promptTokenCount': 1000, 'candidatesTokenCount': 200, 'thoughtsTokenCount': 50}}
    monkeypatch.setattr(requests, 'post', lambda *a, **k: types.SimpleNamespace(status_code=200, json=lambda: reply))
    enrich.call_gemini('prompt', 100, 'failed', 'summary')
    summary = costs.ai_summary()
    row = next(f for f in summary['features'] if f['key'] == 'summary')
    assert (row['calls'], row['input_tokens'], row['output_tokens']) == (1, 1000, 250)
    assert row['month_usd'] == pytest.approx(costs.price(enrich.GEMINI_MODEL, 1000, 250), abs=1e-4)


def test_live_sessions_are_priced_from_the_existing_log(env):
    store.initialize()
    with store.connect() as db:
        db.execute('INSERT INTO live_sessions VALUES(?,?,?,?,?)', ('stu', 120, 100_000, 10_000, time.time()))
    summary = costs.ai_summary()
    live = next(f for f in summary['features'] if f['key'] == 'live')
    assert live['month_usd'] == pytest.approx(costs.price(costs.LIVE_MODEL, 100_000, 10_000), abs=1e-4)
    assert summary['totals']['live_minutes_month'] == pytest.approx(2)
    assert summary['daily'] and summary['totals']['today_usd'] > 0


def test_recording_never_breaks_the_feature(monkeypatch):
    def broken():
        raise RuntimeError('disk full')
    monkeypatch.setattr(store, 'connect', broken)
    assert costs.record('tutor', 'gemini-2.5-flash', 10, 10) > 0


def fake_azure(monkeypatch):
    """Answer the Azure REST calls costs.py makes; returns the list of called URLs."""
    calls = []
    monkeypatch.setattr(costs, '_token', lambda: 'tok')
    monkeypatch.setenv('AZURE_SUBSCRIPTION_ID', 'sub1')
    monkeypatch.delenv('AZURE_BILLING_PROFILE_ID', raising=False)

    def http(method, url, token=None, json_body=None, headers=None, timeout=20):
        calls.append(url)
        if 'billingProperty' in url:
            return {'properties': {'billingProfileId': '/providers/Microsoft.Billing/billingAccounts/a:b/billingProfiles/P1'}}
        if 'balanceSummary' in url:
            return {'properties': {'creditCurrency': 'USD', 'pendingEligibleCharges': {'value': -32.25},
                                   'balanceSummary': {'currentBalance': {'value': 78.83},
                                                      'estimatedBalance': {'value': 46.58}}}}
        if url.endswith('/lots?api-version=2023-05-01'):
            return {'value': [{'properties': {'originalAmount': {'value': 100.0}, 'source': 'Azure for students credit',
                                              'expirationDate': '2027-09-18T05:23:43Z'}}]}
        if json_body['timeframe'] == 'MonthToDate':
            return {'properties': {'columns': [{'name': 'Cost'}, {'name': 'CostUSD'}, {'name': 'ServiceName'}, {'name': 'Currency'}],
                                   'rows': [[1753.7, 1.285, 'Virtual Network', 'KRW'], [34102.8, 24.98, 'Virtual Machines', 'KRW']]}}
        return {'properties': {'columns': [{'name': 'Cost'}, {'name': 'CostUSD'}, {'name': 'UsageDate'}, {'name': 'Currency'}],
                               'rows': [[1300, 0.95, 20261010, 'KRW'], [1400, 1.05, 20261011, 'KRW']]}}

    monkeypatch.setattr(costs, '_http', http)
    return calls


def test_azure_snapshot_reads_costs_in_usd_and_the_student_credit(monkeypatch):
    calls = fake_azure(monkeypatch)
    snap = costs.azure_snapshot()
    assert snap['errors'] == []
    assert snap['costs']['currency'] == 'USD'
    assert snap['costs']['services'][0] == {'service': 'Virtual Machines', 'usd': 24.98}
    assert snap['costs']['month_usd'] == pytest.approx(26.265)
    assert snap['costs']['daily'] == [{'date': '2026-10-10', 'usd': 0.95}, {'date': '2026-10-11', 'usd': 1.05}]
    assert snap['credit'] == {'currency': 'USD', 'original_usd': 100.0, 'balance_usd': 78.83, 'estimated_usd': 46.58,
                              'pending_usd': 32.25, 'expires': '2027-09-18T05:23:43Z', 'source': 'Azure for students credit'}
    assert any('a%3Ab/billingProfiles/P1' in url for url in calls)      # ':' in the account id is escaped
    count = len(calls)
    costs.azure_snapshot()
    assert len(calls) == count                                            # served from the cache
    costs.azure_snapshot(force=True)
    assert len(calls) > count


def test_azure_errors_are_reported_not_raised(monkeypatch):
    def no_identity():
        raise costs.AzureError('No Azure identity is set up for this server.')
    monkeypatch.setattr(costs, '_token', no_identity)
    snap = costs.azure_snapshot()
    assert snap['costs'] is None and snap['credit'] is None
    assert 'No Azure identity' in snap['errors'][0]


def test_costs_endpoint_is_admin_only(admin, monkeypatch):
    fake_azure(monkeypatch)
    client, _ = admin
    data = client.get('/api/admin/costs').json()
    assert data['azure']['credit']['balance_usd'] == 78.83
    assert data['month_total_usd'] == pytest.approx(26.265 + data['ai']['totals']['month_usd'], abs=1e-3)
    client.post('/api/logout')
    assert client.get('/api/admin/costs').status_code == 401
    client.post('/api/register', json={'username': 'student1', 'password': 'student-password-1'})
    assert client.get('/api/admin/costs').status_code == 403


def test_a_failed_refresh_keeps_the_last_good_numbers(monkeypatch):
    fake_azure(monkeypatch)
    good = costs.azure_snapshot(now=1000.0)
    def limited(*a, **k):
        raise costs.AzureError('Azure is rate-limiting cost queries; try again in a minute.')
    monkeypatch.setattr(costs, '_http', limited)
    snap = costs.azure_snapshot(force=True, now=2000.0)
    assert snap['costs'] == good['costs'] and snap['credit'] == good['credit']
    assert len(snap['errors']) == 2
    calls = []
    monkeypatch.setattr(costs, '_http', lambda *a, **k: calls.append(1) or limited())
    costs.azure_snapshot(now=2030.0)
    assert calls == []                                  # still within the 60 s retry window
    costs.azure_snapshot(now=2061.0)
    assert calls                                        # failures are retried after a minute


def test_rate_limited_queries_wait_and_retry(monkeypatch):
    import requests
    replies = [types.SimpleNamespace(status_code=429, headers={'x-ms-ratelimit-microsoft.costmanagement-clienttype-retry-after': '2'}, content=b''),
               types.SimpleNamespace(status_code=200, headers={}, content=b'{}', json=lambda: {'ok': True})]
    sent, slept = [], []
    monkeypatch.setattr(requests, 'request', lambda method, url, json=None, headers=None, timeout=None: sent.append(headers) or replies.pop(0))
    monkeypatch.setattr(costs.time, 'sleep', slept.append)
    assert costs._http('POST', 'https://x', 'tok', {}) == {'ok': True}
    assert slept == [2.0] and sent[0]['ClientType'] == 'book2course-cost-meter'
