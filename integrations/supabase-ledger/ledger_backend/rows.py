# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The ledger records the backend reads, as a :class:`~ledger_backend.reader.LedgerReader`
returns them. Field names follow the ledger's columns where they are plain."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class ProductRow(BaseModel):
    """One sellable option of one product (``public.products``). ``family_no`` is the
    Cafe24 product number the options share, None for a row the sync did not link."""

    barcode: str
    product_name: str
    option_name: str = ""
    category: str = ""
    cost_price: float = 0
    selling_price: float = 0
    stock_sellable: float = 0
    family_no: int | None = None


class DailyOrders(BaseModel):
    """Orders placed on one day through one channel (``ops.channel_order_mirror``).
    ``amount`` is None when that channel's mirror carries no order amounts."""

    day: date
    source: str
    orders: int
    amount: float | None = None
