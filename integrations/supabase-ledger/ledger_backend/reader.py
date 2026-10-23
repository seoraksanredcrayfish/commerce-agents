# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The read port between the backend and the ledger. Every method takes the tenant id
and returns only that tenant's rows, and every product read is bounded: the ledger is
never read whole. :mod:`ledger_backend.postgres` implements it over a read-only
connection, :class:`InMemoryLedgerReader` over fixtures."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Protocol

from .rows import DailyOrders, ProductRow


class LedgerReader(Protocol):
    async def search_products(self, tenant_id: str, query: str, limit: int) -> list[ProductRow]:
        """Every row of up to ``limit`` families (or unlinked rows) whose name contains
        ``query`` or whose barcode equals it; an empty query lists from the start."""
        ...

    async def listing_rows(
        self, tenant_id: str, *, family_no: int | None = None, barcode: str | None = None
    ) -> list[ProductRow]:
        """The rows of one family, or the one row with ``barcode``."""
        ...

    async def low_stock_rows(self, tenant_id: str, threshold: int, limit: int) -> list[ProductRow]:
        """Up to ``limit`` rows with stock above zero and at most ``threshold``, lowest
        first."""
        ...

    async def low_stock_count(self, tenant_id: str, threshold: int) -> int:
        """How many rows :meth:`low_stock_rows` would return without a limit."""
        ...

    async def daily_orders(self, tenant_id: str, start: date, end: date) -> list[DailyOrders]:
        """Order counts and amounts per day and channel, ``start`` to ``end`` inclusive."""
        ...


def _group_key(row: ProductRow) -> str:
    return f"c24:{row.family_no}" if row.family_no is not None else f"b:{row.barcode}"


def _is_low(row: ProductRow, threshold: int) -> bool:
    return 0 < row.stock_sellable <= threshold


class InMemoryLedgerReader:
    def __init__(
        self,
        products: Mapping[str, Sequence[ProductRow]],
        daily_orders: Mapping[str, Sequence[DailyOrders]] | None = None,
    ):
        self._products = products
        self._daily_orders = daily_orders or {}

    def _rows(self, tenant_id: str) -> list[ProductRow]:
        return list(self._products.get(tenant_id, ()))

    async def search_products(self, tenant_id: str, query: str, limit: int) -> list[ProductRow]:
        needle = query.casefold()
        hits = [
            _group_key(row)
            for row in self._rows(tenant_id)
            if not needle or needle in row.product_name.casefold() or row.barcode == query
        ]
        keys = list(dict.fromkeys(hits))[:limit]
        return [row for row in self._rows(tenant_id) if _group_key(row) in keys]

    async def listing_rows(
        self, tenant_id: str, *, family_no: int | None = None, barcode: str | None = None
    ) -> list[ProductRow]:
        return [
            row
            for row in self._rows(tenant_id)
            if (family_no is not None and row.family_no == family_no)
            or (barcode is not None and row.barcode == barcode)
        ]

    async def low_stock_rows(self, tenant_id: str, threshold: int, limit: int) -> list[ProductRow]:
        low = [row for row in self._rows(tenant_id) if _is_low(row, threshold)]
        return sorted(low, key=lambda row: row.stock_sellable)[:limit]

    async def low_stock_count(self, tenant_id: str, threshold: int) -> int:
        return sum(1 for row in self._rows(tenant_id) if _is_low(row, threshold))

    async def daily_orders(self, tenant_id: str, start: date, end: date) -> list[DailyOrders]:
        return [row for row in self._daily_orders.get(tenant_id, ()) if start <= row.day <= end]
