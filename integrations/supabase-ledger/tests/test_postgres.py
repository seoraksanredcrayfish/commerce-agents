# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""PostgresLedgerReader over a recording fetch: every statement is a SELECT bound to the
tenant, and rows map onto the ledger records."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ledger_backend.postgres import PostgresLedgerReader


class RecordingFetch:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, tuple]] = []

    async def __call__(self, sql: str, *args):
        self.calls.append((sql, args))
        return self.rows


async def test_products_are_read_for_the_tenant_and_mapped():
    fetch = RecordingFetch(
        [
            {
                "barcode": "1-001",
                "product_name": "ACME Pants",
                "option_name": "BLACK,OS",
                "category": "",
                "cost_price": Decimal("9000"),
                "selling_price": Decimal("39000.0"),
                "stock_sellable": Decimal("2"),
                "family_no": 14,
            }
        ]
    )

    rows = await PostgresLedgerReader(fetch).products("acme")

    sql, args = fetch.calls[0]
    assert sql.lstrip().lower().startswith("select")
    assert args == ("acme",)
    assert (rows[0].family_no, rows[0].selling_price, rows[0].stock_sellable) == (14, 39000, 2)


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
