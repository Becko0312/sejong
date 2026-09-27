"""Prepaid Live-voice minutes, sold as PayLink (paylink.mn) invoices.

Every student starts with LIVE_FREE_MINUTES of Gemini Live talk time. After that they
buy a minute package: we create a hosted PayLink invoice, the student pays on PayLink's
page, and a poller (PayLink has no webhooks) credits the minutes exactly once.

Auth (PayLink developer portal): every request carries `pc` (process code), X-USERNAME
and X-SIGNATURE = base64(HMAC-SHA512(key=SIGNATURE_KEY, msg=password)) — constant per
password. One endpoint, POST {base}/external/process, always HTTP 200; success is
response_code == 'RC000000'. cu0900 creates an invoice, cu0904 reads its status.
"""
import base64
import hashlib
import hmac
import logging
import os
import time
import uuid
from app import store

log = logging.getLogger(__name__)

FREE_SECONDS = store.LIVE_FREE_SECONDS
# "minutes:price_mnt" pairs, e.g. 30 minutes for ₮5,000.
PACKAGES = [{'id': f'{m}min', 'minutes': int(m), 'price': int(p)}
            for m, p in (item.split(':') for item in os.getenv('LIVE_PACKAGES', '30:5000,60:9000,120:16000').split(','))]

SIGNATURE_KEY = b'TWVDb3JlRmliYUlLPQ=='
SUCCESS = 'RC000000'


class BillingError(Exception):
    def __init__(self, message, code=502):
        super().__init__(message)
        self.message, self.code = message, code


def _paylink():
    username, password = os.getenv('PAYLINK_USERNAME', ''), os.getenv('PAYLINK_PASSWORD', '')
    base = 'https://paylink.mn/api/v1' if os.getenv('PAYLINK_ENV') == 'production' else 'https://pay-link.fiba.mn/api/v1'
    return username, password, base


def paylink_configured():
    username, password, _ = _paylink()
    return bool(username and password)


def signature(password):
    return base64.b64encode(hmac.new(SIGNATURE_KEY, password.encode(), hashlib.sha512).digest()).decode()


def _call(pc, payload):
    import requests
    username, password, base = _paylink()
    try:
        response = requests.post(f'{base}/external/process', json=payload, timeout=20, headers={
            'pc': pc, 'X-USERNAME': username, 'X-SIGNATURE': signature(password),
            'Content-Type': 'application/json', 'Accept': 'application/json'})
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BillingError('The payment service is not reachable right now.') from exc
    if data.get('response_code') != SUCCESS:
        log.warning('PayLink %s failed: %s %s', pc, data.get('response_code'), str(data.get('response'))[:200])
        raise BillingError('The payment service rejected the request. Please try again later.')
    return data.get('response') or {}


# ---- Minute balance ----

def balance(username):
    with store.connect() as db:
        row = db.execute('SELECT live_seconds FROM users WHERE username=?', (username,)).fetchone()
    return max(0, int(row['live_seconds'] or 0)) if row else 0


def reserve(username, cap):
    """Take up to `cap` seconds off the balance for one session (refund the unused part later).

    Reserving up front means two tabs cannot both spend the same minutes."""
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT live_seconds FROM users WHERE username=?', (username,)).fetchone()
        granted = min(cap, max(0, int(row['live_seconds'] or 0))) if row else 0
        if granted:
            db.execute('UPDATE users SET live_seconds=live_seconds-? WHERE username=?', (granted, username))
    return granted


def grant(username, seconds):
    with store.connect() as db:
        changed = db.execute('UPDATE users SET live_seconds=MAX(0, live_seconds+?) WHERE username=?',
                             (int(seconds), username)).rowcount
    return bool(changed)


def record_session(username, seconds, usage):
    """Keep real per-session token usage so the minute price can be checked against Google's bill."""
    with store.connect() as db:
        db.execute('INSERT INTO live_sessions(username, seconds, prompt_tokens, response_tokens, created) VALUES(?,?,?,?,?)',
                   (username, seconds, usage.get('prompt', 0), usage.get('response', 0), time.time()))


def overview(user):
    unlimited = user['role'] == 'admin'
    return {'seconds': None if unlimited else balance(user['username']), 'unlimited': unlimited,
            'free_minutes': FREE_SECONDS // 60, 'packages': PACKAGES, 'paylink': paylink_configured()}


# ---- PayLink checkout ----

def create_checkout(username, package_id):
    package = next((p for p in PACKAGES if p['id'] == package_id), None)
    if not package:
        raise BillingError('Unknown package.', 400)
    if not paylink_configured():
        raise BillingError('Online payment is not available yet. Please contact the administrator.', 503)
    invoice = _call('cu0900', {
        'amount_total': package['price'], 'count_total': 1, 'amount': package['price'],
        'txndesc': f"Book2Course Live {package['minutes']} мин",
        'merchant_ref': f"{username[:24]}-{package['id']}-{uuid.uuid4().hex[:10]}"})
    if not invoice.get('invid') or not invoice.get('payment_link'):
        raise BillingError('The payment service returned an incomplete invoice.')
    with store.connect() as db:
        db.execute("INSERT INTO paylink_invoices(invid, username, amount, seconds, status, created) VALUES(?,?,?,?,'pending',?)",
                   (str(invoice['invid']), username, package['price'], package['minutes'] * 60, time.time()))
    return {'invid': str(invoice['invid']), 'payment_link': invoice['payment_link'], 'package': package}


def _credit(invid):
    # The guarded pending→paid flip is the idempotency gate: minutes are added exactly once.
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT username, seconds FROM paylink_invoices WHERE invid=? AND status='pending'", (invid,)).fetchone()
        if not row:
            return False
        db.execute("UPDATE paylink_invoices SET status='paid', paid=? WHERE invid=?", (time.time(), invid))
        db.execute('UPDATE users SET live_seconds=live_seconds+? WHERE username=?', (row['seconds'], row['username']))
    log.info('PayLink: credited %ss to %s (%s)', row['seconds'], row['username'], invid)
    return True


def reconcile(invid):
    """Ask PayLink for one invoice's status and apply it. Returns the stored status."""
    status = (_call('cu0904', {'invid': invid}).get('status') or '').lower()
    if status == 'paid':
        _credit(invid)
    elif status in ('canceled', 'cancelled', 'expired'):
        with store.connect() as db:
            db.execute("UPDATE paylink_invoices SET status=? WHERE invid=? AND status='pending'", (status, invid))
    return invoice_status(invid)


def invoice_status(invid):
    with store.connect() as db:
        row = db.execute('SELECT status FROM paylink_invoices WHERE invid=?', (invid,)).fetchone()
    return row['status'] if row else None


def invoice_owner(invid):
    with store.connect() as db:
        row = db.execute('SELECT username FROM paylink_invoices WHERE invid=?', (invid,)).fetchone()
    return row['username'] if row else None


def poll_pending(max_age=86400):
    """Reconcile every recent pending invoice (called by the background poller)."""
    if not paylink_configured():
        return 0
    with store.connect() as db:
        rows = db.execute("SELECT invid FROM paylink_invoices WHERE status='pending' AND created>?",
                          (time.time() - max_age,)).fetchall()
    paid = 0
    for row in rows:
        try:
            paid += reconcile(row['invid']) == 'paid'
        except BillingError as exc:
            log.warning('PayLink reconcile %s failed: %s', row['invid'], exc.message)
    return paid
