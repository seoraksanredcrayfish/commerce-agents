# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""PostgresLedgerReader over a recording fetch: every statement is a SELECT bound to the
tenant, and rows map onto the ledger records."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ledger_backend.postgres import PostgresLedgerReader


class RecordingFetch:
    """Answers every call with ``rows``, or each call in turn from ``responses``."""

    def __init__(self, rows=(), responses=None):
        self.responses = list(responses) if responses is not None else None
        self.rows = list(rows)
        self.calls: list[tuple[str, tuple]] = []

    async def __call__(self, sql: str, *args):
        self.calls.append((sql, args))
        return self.responses.pop(0) if self.responses is not None else self.rows


PRODUCT = {
    "barcode": "1-001",
    "product_name": "ACME Pants",
    "option_name": "BLACK,OS",
    "category": "",
    "cost_price": Decimal("9000"),
    "selling_price": Decimal("39000.0"),
    "stock_sellable": Decimal("2"),
    "family_no": 14,
}


async def test_search_reads_whole_families_for_the_tenant_up_to_a_limit():
    fetch = RecordingFetch([PRODUCT])

    rows = await PostgresLedgerReader(fetch).search_products("acme", "pants", 5)

    sql, args = fetch.calls[0]
    assert sql.lstrip().lower().startswith("with")
    assert "limit $3" in sql
    assert args == ("acme", "pants", 5)
    assert (rows[0].family_no, rows[0].selling_price, rows[0].stock_sellable) == (14, 39000, 2)


async def test_listing_rows_read_one_family_or_one_barcode():
    fetch = RecordingFetch([PRODUCT])
    reader = PostgresLedgerReader(fetch)

    await reader.listing_rows("acme", family_no=14)
    await reader.listing_rows("acme", barcode="1-001")

    assert [args for _, args in fetch.calls] == [("acme", 14, None), ("acme", None, "1-001")]


async def test_low_stock_reads_are_bounded_and_counted_separately():
    fetch = RecordingFetch(responses=[[{"count": 412}], [PRODUCT]])
    reader = PostgresLedgerReader(fetch)

    count = await reader.low_stock_count("acme", 3)
    await reader.low_stock_rows("acme", 3, 50)

    (count_sql, count_args), (rows_sql, rows_args) = fetch.calls
    assert count == 412 and count_args == ("acme", 3)
    assert "limit $3" in rows_sql and rows_args == ("acme", 3, 50)


async def test_daily_orders_bind_the_tenant_window_and_time_zone():
    fetch = RecordingFetch(
        [
            {
                "day": date(2026, 10, 21),
                "source": "cafe24",
                "orders": 3,
                "amount": Decimal("120000"),
            },
            {"day": date(2026, 10, 21), "source": "zigzag", "orders": 2, "amount": None},
        ]
    )
    reader = PostgresLedgerReader(fetch, timezone="Asia/Seoul")

    rows = await reader.daily_orders("acme", date(2026, 10, 15), date(2026, 10, 21))

    sql, args = fetch.calls[0]
    assert sql.lstrip().lower().startswith("select")
    assert args == ("acme", date(2026, 10, 15), date(2026, 10, 21), "Asia/Seoul")
    assert [(r.source, r.orders, r.amount) for r in rows] == [
        ("cafe24", 3, 120000),
        ("zigzag", 2, None),
    ]
