#!/usr/bin/env python3
"""Email a daily Azure spend summary.

Queries Azure Cost Management for month-to-date (MTD) cost with a daily
breakdown, then sends a short plain-text report to a private address over
SMTP. Intended to be run once a day from GitHub Actions (or any cron).

Environment:
    AZURE_SUBSCRIPTION_ID   Subscription to report on.
    AZURE_TENANT_ID         Entra tenant of the service principal.
    AZURE_CLIENT_ID         Service-principal application id.
    AZURE_CLIENT_SECRET     Service-principal secret.
                            (Needs Cost Management Reader on the scope.)
    REPORT_SCOPE            Optional Cost Management scope override, e.g.
                            "/subscriptions/<id>/resourceGroups/<rg>".
                            Defaults to the subscription scope.
    REPORT_EMAIL_TO         Recipient address.
    REPORT_EMAIL_FROM       Sender address (usually the Gmail account).
    REPORT_SMTP_HOST        SMTP host; defaults to smtp.gmail.com.
    REPORT_SMTP_PORT        SMTP port; defaults to 587 (STARTTLS).
    REPORT_SMTP_USERNAME    SMTP login; defaults to REPORT_EMAIL_FROM.
    REPORT_SMTP_PASSWORD    SMTP password / Gmail App Password.
    REPORT_BUDGET_AMOUNT    Optional monthly budget; enables % consumed line.
    REPORT_BUDGET_CURRENCY  Optional; overrides the currency label.
    REPORT_DRY_RUN          If set to "1", print the report and skip SMTP.
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from email.message import EmailMessage
from typing import Iterable, Sequence

AUTHORITY = 'https://login.microsoftonline.com'
MANAGEMENT = 'https://management.azure.com'
COST_API_VERSION = '2023-11-01'
HTTP_TIMEOUT = 30


def get_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    body = urllib.parse.urlencode({
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
        'scope': f'{MANAGEMENT}/.default',
    }).encode('utf-8')
    req = urllib.request.Request(
        f'{AUTHORITY}/{tenant_id}/oauth2/v2.0/token',
        data=body,
        method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read())['access_token']


def query_cost(scope: str, token: str) -> dict:
    url = (
        f'{MANAGEMENT}{scope}/providers/Microsoft.CostManagement/query'
        f'?api-version={COST_API_VERSION}'
    )
    payload = json.dumps({
        'type': 'ActualCost',
        'timeframe': 'MonthToDate',
        'dataset': {
            'granularity': 'Daily',
            'aggregation': {
                'totalCost': {'name': 'Cost', 'function': 'Sum'},
            },
        },
    }).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=payload,
        method='POST',
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read())


def parse_daily_rows(response: dict) -> list[tuple[date, float, str]]:
    """Return sorted (date, amount, currency) tuples from a query response."""
    props = response.get('properties') or {}
    columns = [c.get('name', '') for c in props.get('columns') or []]
    lookup = {name.lower(): i for i, name in enumerate(columns)}
    cost_idx = _first_index(lookup, ('cost', 'pretaxcost', 'costusd'))
    date_idx = _first_index(lookup, ('usagedate', 'date'))
    curr_idx = lookup.get('currency')
    if cost_idx is None or date_idx is None:
        raise ValueError(f'unexpected cost query columns: {columns!r}')
    rows: list[tuple[date, float, str]] = []
    for raw in props.get('rows') or []:
        amount = float(raw[cost_idx])
        day = _coerce_date(raw[date_idx])
        currency = str(raw[curr_idx]) if curr_idx is not None else ''
        rows.append((day, amount, currency))
    rows.sort(key=lambda r: r[0])
    return rows


def _first_index(lookup: dict[str, int], names: Sequence[str]) -> int | None:
    for name in names:
        if name in lookup:
            return lookup[name]
    return None


def _coerce_date(value: object) -> date:
    if isinstance(value, (int, float)):
        text = f'{int(value):08d}'
    else:
        text = str(value)
    # API dates come as YYYYMMDD or ISO 8601 (YYYY-MM-DD[Thh:mm:ssZ]).
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    return datetime.fromisoformat(text.replace('Z', '+00:00')).date()


def summarize(
    rows: Sequence[tuple[date, float, str]],
    *,
    today: date,
    budget: float | None = None,
    budget_currency: str | None = None,
) -> dict:
    """Reduce daily rows into the numbers rendered in the email body."""
    mtd = sum(amount for _, amount, _ in rows)
    currency = budget_currency or _first_currency(rows) or ''
    by_day = {day: amount for day, amount, _ in rows}
    yesterday = today.fromordinal(today.toordinal() - 1)
    latest = by_day.get(yesterday, 0.0)
    seven = sorted(by_day.items())[-7:]
    percent = (mtd / budget * 100.0) if budget else None
    return {
        'as_of': today.isoformat(),
        'currency': currency,
        'mtd_total': mtd,
        'yesterday': {'date': yesterday.isoformat(), 'amount': latest},
        'recent': [(d.isoformat(), amt) for d, amt in seven],
        'budget': budget,
        'budget_percent': percent,
        'days_reported': len(by_day),
    }


def _first_currency(rows: Iterable[tuple[date, float, str]]) -> str | None:
    for _, _, currency in rows:
        if currency:
            return currency
    return None


def render_body(summary: dict) -> str:
    """Plain-text body of the daily email."""
    currency = summary['currency']
    lines = [
        f"Azure spend as of {summary['as_of']} (UTC)",
        '',
        f"Month-to-date: {_fmt_amount(summary['mtd_total'], currency)}"
        f" over {summary['days_reported']} day(s)",
        f"Yesterday ({summary['yesterday']['date']}):"
        f" {_fmt_amount(summary['yesterday']['amount'], currency)}",
    ]
    if summary['budget']:
        lines.append(
            f"Budget: {_fmt_amount(summary['budget'], currency)}"
            f" — {summary['budget_percent']:.1f}% consumed"
        )
    if summary['recent']:
        lines += ['', 'Recent daily cost:']
        lines += [
            f"  {day}  {_fmt_amount(amount, currency)}"
            for day, amount in summary['recent']
        ]
    lines += ['', '(Actual cost from Microsoft.CostManagement, MonthToDate.)']
    return '\n'.join(lines) + '\n'


def _fmt_amount(amount: float, currency: str) -> str:
    label = f' {currency}' if currency else ''
    return f'{amount:,.2f}{label}'


def send_email(
    *,
    body: str,
    subject: str,
    sender: str,
    recipient: str,
    smtp_host: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
) -> None:
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = recipient
    msg.set_content(body)
    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port, timeout=HTTP_TIMEOUT) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(smtp_username, smtp_password)
        smtp.send_message(msg)


def _env_or(name: str, default: str) -> str:
    """Return the env var's trimmed value, or ``default`` when missing/blank.

    ``os.environ.get(name, default)`` returns ``''`` (not the default) when
    the variable is set to an empty string — as GitHub Actions does for an
    unset ``vars.*`` reference. Fall back to ``default`` in that case too.
    """
    return os.environ.get(name, '').strip() or default


def _required(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if not value:
        raise SystemExit(f'missing required env var: {name}')
    return value


def _optional_float(name: str) -> float | None:
    raw = os.environ.get(name, '').strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise SystemExit(f'{name} must be a number: {exc}') from exc


def main(argv: Sequence[str] | None = None) -> int:
    subscription_id = _required('AZURE_SUBSCRIPTION_ID')
    scope = os.environ.get('REPORT_SCOPE', '').strip() or f'/subscriptions/{subscription_id}'
    tenant_id = _required('AZURE_TENANT_ID')
    client_id = _required('AZURE_CLIENT_ID')
    client_secret = _required('AZURE_CLIENT_SECRET')
    recipient = _required('REPORT_EMAIL_TO')
    sender = _required('REPORT_EMAIL_FROM')
    smtp_host = _env_or('REPORT_SMTP_HOST', 'smtp.gmail.com')
    smtp_port = int(_env_or('REPORT_SMTP_PORT', '587'))
    smtp_username = _env_or('REPORT_SMTP_USERNAME', sender)
    budget = _optional_float('REPORT_BUDGET_AMOUNT')
    budget_currency = os.environ.get('REPORT_BUDGET_CURRENCY', '').strip() or None
    dry_run = os.environ.get('REPORT_DRY_RUN', '').strip() == '1'

    token = get_token(tenant_id, client_id, client_secret)
    response = query_cost(scope, token)
    rows = parse_daily_rows(response)
    summary = summarize(
        rows,
        today=datetime.now(timezone.utc).date(),
        budget=budget,
        budget_currency=budget_currency,
    )
    body = render_body(summary)
    subject = (
        f"Azure spend {summary['as_of']}: "
        f"{_fmt_amount(summary['mtd_total'], summary['currency'])} MTD"
    )

    if dry_run:
        sys.stdout.write(f'Subject: {subject}\n\n{body}')
        return 0

    smtp_password = _required('REPORT_SMTP_PASSWORD')
    send_email(
        body=body,
        subject=subject,
        sender=sender,
        recipient=recipient,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
    )
    return 0


if __name__ == '__main__':  # pragma: no cover
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace') if exc.fp else ''
        raise SystemExit(f'{exc.code} {exc.reason}: {detail}') from exc
