# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The ledger reader over Postgres. Statements are fixed SELECTs bound to the tenant id;
``fetch`` runs one statement with positional ``$n`` arguments and returns mappings.
:func:`readonly_fetch` builds one on asyncpg that runs each statement in a read-only
transaction, so a write fails in the database whatever the statement says. Use a role
that can read only these tables."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import date
from typing import Any

from .rows import DailyOrders, ProductRow

Fetch = Callable[..., Awaitable[list[Mapping[str, Any]]]]

PRODUCTS_SQL = """
select barcode,
       product_name,
       coalesce(option_name, '') as option_name,
       coalesce(category, '') as category,
       coalesce(cost_price, 0) as cost_price,
       coalesce(selling_price, 0) as selling_price,
       coalesce(stock_sellable, 0) as stock_sellable,
       cafe24_product_no as family_no
from public.products
where tenant_id = $1
order by cafe24_product_no nulls last, barcode
"""

DAILY_ORDERS_SQL = """
select (ordered_at at time zone $4)::date as day,
       source,
       count(*)::int as orders,
       sum(order_amount) as amount
from ops.channel_order_mirror
where tenant_id = $1
  and ordered_at >= ($2::date)::timestamp at time zone $4
  and ordered_at < ($3::date + 1)::timestamp at time zone $4
group by 1, 2
order by 1, 2
"""


class PostgresLedgerReader:
    def __init__(self, fetch: Fetch, *, timezone: str = "UTC"):
        self._fetch = fetch
        self._timezone = timezone

    async def products(self, tenant_id: str) -> list[ProductRow]:
        return [ProductRow(**row) for row in await self._fetch(PRODUCTS_SQL, tenant_id)]

    async def daily_orders(self, tenant_id: str, start: date, end: date) -> list[DailyOrders]:
        rows = await self._fetch(DAILY_ORDERS_SQL, tenant_id, start, end, self._timezone)
        return [DailyOrders(**row) for row in rows]


def readonly_fetch(dsn: str) -> Fetch:
    """A fetch on asyncpg (the ``postgres`` extra) that opens a connection per statement
    and runs it in a read-only transaction."""
    import asyncpg

    async def fetch(sql: str, *args: Any) -> list[Mapping[str, Any]]:
        connection = await asyncpg.connect(dsn)
        try:
            async with connection.transaction(readonly=True):
                return [dict(record) for record in await connection.fetch(sql, *args)]
        finally:
            await connection.close()

    return fetch
