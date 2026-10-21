# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""One tenant's binding to the shared ledger. Every query filters on ``tenant_id``; the
rest describes how that tenant's rows read. Values come from the deployment
(:func:`tenant_from_env`), never from this repository."""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, Field


class TenantConfig(BaseModel):
    tenant_id: str = Field(min_length=1)
    currency: str = "KRW"
    # Names for the comma-separated parts of a row's option string, in order; parts past
    # the last label are keyed option3, option4, ...
    option_labels: tuple[str, ...] = ()
    # Positive stock at or under this is a low-stock alert; 0 turns the alerts off.
    low_stock_threshold: int = Field(default=3, ge=0)
    # The most low-stock alerts one read returns, lowest stock first; counts cover all.
    max_alerts: int = Field(default=50, ge=1)
    # True for a store that sells items it orders in after the sale, so zero stock does
    # not take a listing off sale.
    zero_stock_sellable: bool = False


def tenant_from_env(env: Mapping[str, str] = os.environ) -> TenantConfig:
    """``LEDGER_TENANT_ID`` (required), ``LEDGER_CURRENCY``, ``LEDGER_OPTION_LABELS``
    (comma-separated), ``LEDGER_LOW_STOCK_THRESHOLD``, ``LEDGER_MAX_ALERTS``,
    ``LEDGER_ZERO_STOCK_SELLABLE`` (``true`` or ``false``)."""
    labels = env.get("LEDGER_OPTION_LABELS", "")
    return TenantConfig(
        tenant_id=env["LEDGER_TENANT_ID"],
        currency=env.get("LEDGER_CURRENCY", "KRW"),
        option_labels=tuple(label.strip() for label in labels.split(",") if label.strip()),
        low_stock_threshold=int(env.get("LEDGER_LOW_STOCK_THRESHOLD", "3")),
        max_alerts=int(env.get("LEDGER_MAX_ALERTS", "50")),
        zero_stock_sellable=env.get("LEDGER_ZERO_STOCK_SELLABLE", "false").lower() == "true",
    )
