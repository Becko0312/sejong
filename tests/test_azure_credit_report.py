"""Unit tests for the daily Azure spend report."""
from datetime import date

from scripts import azure_credit_report as report


def _response(rows):
    return {
        'properties': {
            'columns': [
                {'name': 'Cost', 'type': 'Number'},
                {'name': 'UsageDate', 'type': 'Number'},
                {'name': 'Currency', 'type': 'String'},
            ],
            'rows': rows,
        },
    }


def test_parse_daily_rows_orders_by_date_and_coerces_yyyymmdd():
    resp = _response([
        [1.5, 20260923, 'USD'],
        [2.0, 20260921, 'USD'],
        [0.25, 20260922, 'USD'],
    ])

    parsed = report.parse_daily_rows(resp)

    assert parsed == [
        (date(2026, 9, 21), 2.0, 'USD'),
        (date(2026, 9, 22), 0.25, 'USD'),
        (date(2026, 9, 23), 1.5, 'USD'),
    ]


def test_parse_daily_rows_accepts_iso_dates_and_alternate_cost_column():
    resp = {
        'properties': {
            'columns': [
                {'name': 'PreTaxCost', 'type': 'Number'},
                {'name': 'Date', 'type': 'String'},
                {'name': 'Currency', 'type': 'String'},
            ],
            'rows': [
                [3.25, '2026-09-24', 'KRW'],
                [1.0, '2026-09-23T00:00:00Z', 'KRW'],
            ],
        },
    }

    parsed = report.parse_daily_rows(resp)

    assert parsed[0] == (date(2026, 9, 23), 1.0, 'KRW')
    assert parsed[1] == (date(2026, 9, 24), 3.25, 'KRW')


def test_summarize_reports_mtd_yesterday_and_budget_percentage():
    rows = [
        (date(2026, 9, 22), 2.0, 'USD'),
        (date(2026, 9, 23), 3.0, 'USD'),
    ]

    summary = report.summarize(
        rows,
        today=date(2026, 9, 24),
        budget=25.0,
    )

    assert summary['currency'] == 'USD'
    assert summary['mtd_total'] == 5.0
    assert summary['yesterday'] == {'date': '2026-09-23', 'amount': 3.0}
    assert summary['budget'] == 25.0
    assert summary['budget_percent'] == 20.0
    assert summary['recent'] == [('2026-09-22', 2.0), ('2026-09-23', 3.0)]


def test_summarize_zero_when_no_yesterday_row_and_no_budget():
    rows = [(date(2026, 9, 20), 1.0, 'USD')]

    summary = report.summarize(rows, today=date(2026, 9, 24))

    assert summary['yesterday'] == {'date': '2026-09-23', 'amount': 0.0}
    assert summary['budget'] is None
    assert summary['budget_percent'] is None


def test_render_body_includes_totals_and_budget():
    summary = report.summarize(
        [
            (date(2026, 9, 22), 2.0, 'USD'),
            (date(2026, 9, 23), 3.0, 'USD'),
        ],
        today=date(2026, 9, 24),
        budget=25.0,
    )

    body = report.render_body(summary)

    assert 'Month-to-date: 5.00 USD' in body
    assert 'Yesterday (2026-09-23): 3.00 USD' in body
    assert 'Budget: 25.00 USD — 20.0% consumed' in body
    assert '2026-09-22  2.00 USD' in body
