"""Usage & cost meter for the admin page, in USD.

Two sources:
- AI (Gemini): Google offers no spend API for AI Studio keys, so every model call records its
  token counts here and we price them with the published per-token rates below. Live voice
  talks are already logged per session in `live_sessions` and are priced from there.
- Azure: month-to-date cost from Cost Management, plus the Azure for Students credit balance
  (Microsoft Customer Agreement credits API). Auth is the VM's managed identity, or a service
  principal from AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET. The identity needs
  "Cost Management Reader" on the subscription and "Billing profile reader" for the credit.

Metering must never break the feature being metered, so recording failures are only logged.
"""
import logging
import os
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from app import store

log = logging.getLogger(__name__)

# USD per 1M tokens (input, output). Paid-tier list prices; a free-tier key is billed $0.
PRICES = {
    'gemini-2.5-flash': (0.30, 2.50),
    'gemini-2.5-flash-lite': (0.10, 0.40),
    'gemini-2.5-pro': (1.25, 10.00),
    # Live (native audio): mostly audio in ($3.00) and audio out ($12.00); text parts are cheaper,
    # so pricing every token at the audio rate keeps the estimate on the safe (high) side.
    'gemini-2.5-flash-native-audio-latest': (3.00, 12.00),
    'gpt-realtime-mini': (10.00, 20.00),
}
DEFAULT_PRICE = (0.30, 2.50)
LIVE_MODEL = os.getenv('GEMINI_LIVE_MODEL', 'gemini-2.5-flash-native-audio-latest')
FEATURES = {'tutor': 'Text tutor', 'live': 'Live voice', 'interactive': 'Interactive pages',
            'summary': 'Summaries & tests'}

MANAGEMENT = 'https://management.azure.com'
IMDS = 'http://169.254.169.254/metadata'
CACHE_SECONDS = int(os.getenv('COST_CACHE_SECONDS', '600'))
_cache = {'at': 0.0, 'data': None}
_lock = threading.Lock()


def price(model, input_tokens, output_tokens):
    price_in, price_out = PRICES.get(model, DEFAULT_PRICE)
    return input_tokens / 1_000_000 * price_in + output_tokens / 1_000_000 * price_out


def record(feature, model, input_tokens, output_tokens, username=None):
    """Log one paid model call; returns its USD cost."""
    input_tokens, output_tokens = int(input_tokens or 0), int(output_tokens or 0)
    cost = price(model, input_tokens, output_tokens)
    try:
        with store.connect() as db:
            db.execute('INSERT INTO ai_costs(created, feature, model, input_tokens, output_tokens, cost_usd, username) '
                       'VALUES(?,?,?,?,?,?,?)',
                       (time.time(), feature, model, input_tokens, output_tokens, cost, username))
    except Exception as exc:
        log.warning('Could not record AI usage: %s', exc)
    return cost


def record_gemini(feature, model, response_json, username=None):
    """Record a generateContent reply from its usageMetadata (thinking tokens bill as output)."""
    meta = (response_json or {}).get('usageMetadata') or {}
    return record(feature, model, meta.get('promptTokenCount'),
                  int(meta.get('candidatesTokenCount') or 0) + int(meta.get('thoughtsTokenCount') or 0), username)


def _month_start(now):
    return datetime.fromtimestamp(now, timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def ai_summary(now=None):
    """AI spend: totals for this month / last 30 days / all time, per feature, and per day."""
    now = now or time.time()
    month, recent = _month_start(now), now - 30 * 86400
    rows = []
    with store.connect() as db:
        rows += [dict(r) for r in db.execute(
            'SELECT created, feature, model, input_tokens, output_tokens, cost_usd FROM ai_costs')]
        rows += [{'created': r['created'], 'feature': 'live', 'model': LIVE_MODEL,
                  'input_tokens': r['prompt_tokens'] or 0, 'output_tokens': r['response_tokens'] or 0,
                  'cost_usd': price(LIVE_MODEL, r['prompt_tokens'] or 0, r['response_tokens'] or 0),
                  'seconds': r['seconds'] or 0}
                 for r in db.execute('SELECT created, seconds, prompt_tokens, response_tokens FROM live_sessions')]
    features = {key: {'key': key, 'label': label, 'calls': 0, 'input_tokens': 0, 'output_tokens': 0,
                      'month_usd': 0.0, 'total_usd': 0.0} for key, label in FEATURES.items()}
    daily = {}
    totals = {'month_usd': 0.0, 'last30_usd': 0.0, 'total_usd': 0.0, 'today_usd': 0.0, 'live_minutes_month': 0.0}
    today = now - (now % 86400)
    for r in rows:
        f = features.setdefault(r['feature'], {'key': r['feature'], 'label': r['feature'], 'calls': 0,
                                               'input_tokens': 0, 'output_tokens': 0, 'month_usd': 0.0, 'total_usd': 0.0})
        cost = r['cost_usd'] or 0.0
        f['total_usd'] += cost
        totals['total_usd'] += cost
        if r['created'] >= month:
            f['calls'] += 1
            f['input_tokens'] += r['input_tokens']
            f['output_tokens'] += r['output_tokens']
            f['month_usd'] += cost
            totals['month_usd'] += cost
            totals['live_minutes_month'] += r.get('seconds', 0) / 60
        if r['created'] >= recent:
            totals['last30_usd'] += cost
            day = datetime.fromtimestamp(r['created'], timezone.utc).date().isoformat()
            daily[day] = daily.get(day, 0.0) + cost
        if r['created'] >= today:
            totals['today_usd'] += cost
    return {'totals': {k: round(v, 4) for k, v in totals.items()},
            'features': [{**f, 'month_usd': round(f['month_usd'], 4), 'total_usd': round(f['total_usd'], 4)}
                         for f in features.values()],
            'daily': [{'date': d, 'usd': round(v, 4)} for d, v in sorted(daily.items())],
            'note': 'Estimated from token counts at Google\'s paid-tier prices; a free-tier key costs $0.'}


# ---------- Azure ----------

class AzureError(Exception):
    pass


def _http(method, url, token=None, json_body=None, headers=None, timeout=20):
    import requests
    hdrs = dict(headers or {})
    if token:
        hdrs['Authorization'] = f'Bearer {token}'
    try:
        response = requests.request(method, url, json=json_body, headers=hdrs, timeout=timeout)
    except requests.RequestException as exc:
        raise AzureError('Azure is not reachable right now.') from exc
    if response.status_code in (401, 403):
        raise AzureError('The server is not allowed to read Azure costs yet (needs Cost Management Reader / '
                         'Billing profile reader).')
    if response.status_code == 429:
        raise AzureError('Azure is rate-limiting cost queries; try again in a minute.')
    if response.status_code >= 400:
        raise AzureError(f'Azure returned HTTP {response.status_code}.')
    return response.json() if response.content else {}


def _token():
    tenant, client, secret = (os.getenv(n, '').strip() for n in ('AZURE_TENANT_ID', 'AZURE_CLIENT_ID', 'AZURE_CLIENT_SECRET'))
    import requests
    try:
        if tenant and client and secret:
            response = requests.post(f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token', timeout=20, data={
                'grant_type': 'client_credentials', 'client_id': client, 'client_secret': secret,
                'scope': f'{MANAGEMENT}/.default'})
        else:   # the VM's managed identity
            response = requests.get(f'{IMDS}/identity/oauth2/token', headers={'Metadata': 'true'}, timeout=5,
                                    params={'api-version': '2018-02-01', 'resource': f'{MANAGEMENT}/'})
        response.raise_for_status()
        return response.json()['access_token']
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise AzureError('No Azure identity is set up for this server (managed identity or service principal).') from exc


def _subscription(token):
    sub = os.getenv('AZURE_SUBSCRIPTION_ID', '').strip()
    if sub:
        return sub
    import requests
    try:
        response = requests.get(f'{IMDS}/instance/compute/subscriptionId', headers={'Metadata': 'true'}, timeout=5,
                                params={'api-version': '2021-02-01', 'format': 'text'})
        response.raise_for_status()
        return response.text.strip()
    except requests.RequestException as exc:
        raise AzureError('Set AZURE_SUBSCRIPTION_ID so the server knows which subscription to report.') from exc


def _rows(response):
    """Cost Management query rows as dicts keyed by lower-case column name."""
    props = response.get('properties') or {}
    cols = [c.get('name', '').lower() for c in props.get('columns') or []]
    return [dict(zip(cols, row)) for row in props.get('rows') or []]


def _usd(row):
    # MCA accounts return CostUSD next to the billing-currency Cost; prefer it.
    if row.get('costusd') is not None:
        return float(row['costusd']), 'USD'
    return float(row.get('cost') or 0), row.get('currency') or ''


def _query(token, scope, body):
    url = f'{MANAGEMENT}{scope}/providers/Microsoft.CostManagement/query?api-version=2023-11-01'
    return _rows(_http('POST', url, token, body))


def _azure_costs(token, sub, now):
    scope = os.getenv('COST_SCOPE', '').strip() or f'/subscriptions/{sub}'
    aggregation = {'cost': {'name': 'Cost', 'function': 'Sum'}, 'costUSD': {'name': 'CostUSD', 'function': 'Sum'}}
    services, currency = [], 'USD'
    for row in _query(token, scope, {'type': 'ActualCost', 'timeframe': 'MonthToDate', 'dataset': {
            'granularity': 'None', 'aggregation': aggregation,
            'grouping': [{'type': 'Dimension', 'name': 'ServiceName'}]}}):
        amount, currency = _usd(row)
        services.append({'service': row.get('servicename') or 'Other', 'usd': round(amount, 4)})
    services.sort(key=lambda s: -s['usd'])
    end = datetime.fromtimestamp(now, timezone.utc)
    start = end - timedelta(days=29)
    daily = {}
    for row in _query(token, scope, {'type': 'ActualCost', 'timeframe': 'Custom', 'timePeriod': {
            'from': start.strftime('%Y-%m-%dT00:00:00Z'), 'to': end.strftime('%Y-%m-%dT23:59:59Z')},
            'dataset': {'granularity': 'Daily', 'aggregation': aggregation}}):
        raw = str(row.get('usagedate') or row.get('date') or '')[:10].replace('-', '')
        if len(raw) == 8:
            day = f'{raw[:4]}-{raw[4:6]}-{raw[6:8]}'
            daily[day] = daily.get(day, 0.0) + _usd(row)[0]
    return {'currency': currency, 'month_usd': round(sum(s['usd'] for s in services), 4), 'services': services,
            'daily': [{'date': d, 'usd': round(v, 4)} for d, v in sorted(daily.items())]}


def _billing_profile(token, sub):
    profile = os.getenv('AZURE_BILLING_PROFILE_ID', '').strip()
    if profile:
        return profile
    data = _http('GET', f'{MANAGEMENT}/subscriptions/{sub}/providers/Microsoft.Billing/billingProperty/default'
                        '?api-version=2024-04-01', token)
    profile = (data.get('properties') or {}).get('billingProfileId')
    if not profile:
        raise AzureError('This subscription has no billing profile with credits.')
    return profile


def _azure_credit(token, sub):
    """Azure for Students (MCA) credit: original amount, what is left, and when it expires."""
    base = MANAGEMENT + urllib.parse.quote(_billing_profile(token, sub), safe='/') + '/providers/Microsoft.Consumption'
    summary = _http('GET', f'{base}/credits/balanceSummary?api-version=2023-05-01', token)
    props = summary.get('properties') or {}
    balance = props.get('balanceSummary') or {}
    current = (balance.get('currentBalance') or {}).get('value')
    estimated = (balance.get('estimatedBalance') or {}).get('value')
    lots = _http('GET', f'{base}/lots?api-version=2023-05-01', token).get('value') or []
    active = [l.get('properties') or {} for l in lots]
    original = sum(float((l.get('originalAmount') or {}).get('value') or 0) for l in active)
    expiry = min((l['expirationDate'] for l in active if l.get('expirationDate')), default=None)
    return {'currency': props.get('creditCurrency') or 'USD',
            'original_usd': round(original, 2) if original else None,
            'balance_usd': current,
            # The balance after usage Azure has metered but not yet billed against the credit.
            'estimated_usd': estimated,
            'pending_usd': abs(float((props.get('pendingEligibleCharges') or {}).get('value') or 0)),
            'expires': expiry,
            'source': next((l.get('source') for l in active if l.get('source')), None)}


def azure_snapshot(force=False, now=None):
    """Azure costs + credit, cached (Cost Management is rate-limited and updates a few times a day)."""
    now = now or time.time()
    with _lock:
        if not force and _cache['data'] and now - _cache['at'] < CACHE_SECONDS:
            return _cache['data']
    data = {'checked': now, 'costs': None, 'credit': None, 'errors': []}
    try:
        token = _token()
        sub = _subscription(token)
        for key, fetch in (('costs', _azure_costs), ('credit', _azure_credit)):
            try:
                data[key] = fetch(token, sub, now) if key == 'costs' else fetch(token, sub)
            except AzureError as exc:
                data['errors'].append(f'{key}: {exc}')
    except AzureError as exc:
        data['errors'].append(str(exc))
    with _lock:
        _cache.update(at=now, data=data)
    return data


def overview(force=False):
    ai = ai_summary()
    azure = azure_snapshot(force)
    month = ai['totals']['month_usd'] + ((azure.get('costs') or {}).get('month_usd') or 0)
    return {'ai': ai, 'azure': azure, 'month_total_usd': round(month, 4)}
