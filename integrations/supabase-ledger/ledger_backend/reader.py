# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The read port between the backend and the ledger. Every method takes the tenant id
and returns only that tenant's rows; :mod:`ledger_backend.postgres` implements it over a
read-only connection, :class:`InMemoryLedgerReader` over fixtures."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Protocol

from .rows import DailyOrders, ProductRow


class LedgerReader(Protocol):
    async def products(self, tenant_id: str) -> list[ProductRow]:
        """Every product row of the tenant, in the ledger's listing order."""
        ...

    async def daily_orders(self, tenant_id: str, start: date, end: date) -> list[DailyOrders]:
        """Order counts and amounts per day and channel, ``start`` to ``end`` inclusive."""
        ...


class InMemoryLedgerReader:
    def __init__(
        self,
        products: Mapping[str, Sequence[ProductRow]],
        daily_orders: Mapping[str, Sequence[DailyOrders]] | None = None,
    ):
        self._products = products
        self._daily_orders = daily_orders or {}

    async def products(self, tenant_id: str) -> list[ProductRow]:
        return list(self._products.get(tenant_id, ()))

    async def daily_orders(self, tenant_id: str, start: date, end: date) -> list[DailyOrders]:
        return [row for row in self._daily_orders.get(tenant_id, ()) if start <= row.day <= end]
