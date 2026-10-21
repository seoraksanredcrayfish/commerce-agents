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

_PRODUCT_COLUMNS = """
select barcode,
       product_name,
       coalesce(option_name, '') as option_name,
       coalesce(category, '') as category,
       coalesce(cost_price, 0) as cost_price,
       coalesce(selling_price, 0) as selling_price,
       coalesce(stock_sellable, 0) as stock_sellable,
       cafe24_product_no as family_no
from public.products
"""

# The family key matches the workbench's product key: c24:<number>, else the barcode.
SEARCH_SQL = (
    """
with hits as (
  select distinct on (key) key, first_seen
  from (
    select coalesce('c24:' || cafe24_product_no, 'b:' || barcode) as key,
           coalesce(cafe24_product_no, 2147483647) as first_seen
    from public.products
    where tenant_id = $1
      and ($2 = '' or product_name ilike '%' || $2 || '%' or barcode = $2)
  ) matched
  order by key, first_seen
  limit $3
)
"""
    + _PRODUCT_COLUMNS
    + """
where tenant_id = $1
  and coalesce('c24:' || cafe24_product_no, 'b:' || barcode) in (select key from hits)
order by cafe24_product_no nulls last, barcode
"""
)

LISTING_ROWS_SQL = (
    _PRODUCT_COLUMNS
    + """
where tenant_id = $1
  and (cafe24_product_no = $2::int or barcode = $3::text)
order by barcode
"""
)

LOW_STOCK_ROWS_SQL = (
    _PRODUCT_COLUMNS
    + """
where tenant_id = $1 and stock_sellable > 0 and stock_sellable <= $2
order by stock_sellable, barcode
limit $3
"""
)

LOW_STOCK_COUNT_SQL = """
select count(*)::int as count
from public.products
where tenant_id = $1 and stock_sellable > 0 and stock_sellable <= $2
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

    async def search_products(self, tenant_id: str, query: str, limit: int) -> list[ProductRow]:
        rows = await self._fetch(SEARCH_SQL, tenant_id, query, limit)
        return [ProductRow(**row) for row in rows]

    async def listing_rows(
        self, tenant_id: str, *, family_no: int | None = None, barcode: str | None = None
    ) -> list[ProductRow]:
        rows = await self._fetch(LISTING_ROWS_SQL, tenant_id, family_no, barcode)
        return [ProductRow(**row) for row in rows]

    async def low_stock_rows(self, tenant_id: str, threshold: int, limit: int) -> list[ProductRow]:
        rows = await self._fetch(LOW_STOCK_ROWS_SQL, tenant_id, threshold, limit)
        return [ProductRow(**row) for row in rows]

    async def low_stock_count(self, tenant_id: str, threshold: int) -> int:
        rows = await self._fetch(LOW_STOCK_COUNT_SQL, tenant_id, threshold)
        return int(rows[0]["count"]) if rows else 0

    async def daily_orders(self, tenant_id: str, start: date, end: date) -> list[DailyOrders]:
        rows = await self._fetch(DAILY_ORDERS_SQL, tenant_id, start, end, self._timezone)
        return [DailyOrders(**row) for row in rows]


def readonly_fetch(dsn: str) -> Fetch:
    """A fetch on asyncpg (the ``postgres`` extra) that opens a connection per statement
    and runs it in a read-only transaction. The driver loads on the first statement."""

    async def fetch(sql: str, *args: Any) -> list[Mapping[str, Any]]:
        import asyncpg

        connection = await asyncpg.connect(dsn)
        try:
            async with connection.transaction(readonly=True):
                return [dict(record) for record in await connection.fetch(sql, *args)]
        finally:
            await connection.close()

    return fetch
