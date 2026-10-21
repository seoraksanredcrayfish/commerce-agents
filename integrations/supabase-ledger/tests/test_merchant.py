# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""LedgerMerchantBackend over an in-memory reader: read-only figures from the ledger, and
writes refused until a platform write is wired."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from ledger_backend.merchant import LedgerMerchantBackend
from ledger_backend.reader import InMemoryLedgerReader
from ledger_backend.rows import DailyOrders, ProductRow
from ledger_backend.tenant import TenantConfig

from merchant_agent import (
    ChangeNotApplicable,
    InventoryActionItem,
    ListingFilters,
    MerchantAgentConfig,
    MerchantSessionContext,
    PriceUpdateItem,
)

TENANT = TenantConfig(tenant_id="acme", option_labels=("color", "size"), low_stock_threshold=3)
OTHER = "other-tenant"
SESSION = MerchantSessionContext(
    session_id="s1",
    merchant_id="acme",
    operator="op-1",
    now=datetime(2026, 10, 21, 12, tzinfo=UTC),
)


def product(barcode, name, option="", *, family=None, price=39000, cost=9000, stock=0):
    return ProductRow(
        barcode=barcode,
        product_name=name,
        option_name=option,
        selling_price=price,
        cost_price=cost,
        stock_sellable=stock,
        family_no=family,
    )


def orders(day: str, source: str, count: int, amount: float | None) -> DailyOrders:
    return DailyOrders(day=date.fromisoformat(day), source=source, orders=count, amount=amount)


@pytest.fixture
def reader() -> InMemoryLedgerReader:
    return InMemoryLedgerReader(
        products={
            "acme": [
                product("1-001", "ACME Curved Pants", "IVORY,OS", family=14, stock=2),
                product("1-002", "ACME Curved Pants", "BLACK,OS", family=14, stock=9),
                product("2-001", "ACME Wool Knit", stock=0),
                product("3-001", "ACME Shirring Tee", stock=1, price=0),
            ],
            OTHER: [product("9-001", "Other Tenant Coat", stock=1)],
        },
        daily_orders={
            "acme": [
                orders("2026-10-20", "cafe24", 3, 120000),
                orders("2026-10-21", "cafe24", 1, 40000),
                orders("2026-10-21", "zigzag", 2, None),
                orders("2026-10-10", "cafe24", 2, 80000),  # the prior window
            ],
        },
    )


@pytest.fixture
def backend(reader) -> LedgerMerchantBackend:
    return LedgerMerchantBackend(reader, TENANT, MerchantAgentConfig())


async def test_search_matches_titles_within_the_tenant_only(backend):
    found = await backend.search_listings(SESSION, "pants")

    assert [listing.listing_id for listing in found] == ["c24:14"]
    assert await backend.search_listings(SESSION, "Other Tenant Coat") == []


async def test_a_browse_query_lists_everything_and_filters_apply(backend):
    out_of_stock = await backend.search_listings(SESSION, "", ListingFilters(status="out_of_stock"))

    assert [listing.listing_id for listing in out_of_stock] == ["2-001"]


async def test_get_listing_returns_a_family_with_variants_and_a_variant_by_id(backend):
    family = await backend.get_listing(SESSION, "c24:14")
    variant = await backend.get_listing(SESSION, "1-002")

    assert [v.listing_id for v in family.variants] == ["1-001", "1-002"]
    assert variant.variant_of == "c24:14"
    assert await backend.get_listing(SESSION, "9-001") is None  # another tenant's


async def test_low_stock_alerts_cover_positive_stock_at_or_under_the_threshold(backend):
    alerts = await backend.get_inventory_alerts(SESSION)

    assert [(a.listing_id, a.stock, a.variant_of) for a in alerts] == [
        ("3-001", 1, None),
        ("1-001", 2, "c24:14"),
    ]
    assert {a.kind for a in alerts} == {"low_stock"}
    assert alerts[0].threshold == 3


async def test_alerts_are_capped_lowest_stock_first_while_the_snapshot_counts_them_all(reader):
    capped = LedgerMerchantBackend(
        reader, TENANT.model_copy(update={"max_alerts": 1}), MerchantAgentConfig()
    )

    alerts = await capped.get_inventory_alerts(SESSION)
    snapshot = await capped.get_business_snapshot(SESSION)

    assert [a.listing_id for a in alerts] == ["3-001"]
    assert snapshot.alerts.low_stock == 2


async def test_pricing_context_carries_cost_margin_and_the_configured_caps(backend):
    context = await backend.get_pricing_context(SESSION, "c24:14")

    assert context.current_price == 39000
    assert context.currency == "KRW"
    assert context.max_price_delta_pct == 20.0
    first = context.variants[0]
    assert (first.unit_cost, first.margin_pct) == (9000, 76.9)


async def test_pricing_context_has_no_margin_when_the_price_is_unset(backend):
    context = await backend.get_pricing_context(SESSION, "3-001")

    assert context.margin_pct is None


async def test_snapshot_totals_amounts_where_known_and_says_which_channels_lack_them(backend):
    snapshot = await backend.get_business_snapshot(SESSION, "last_7_days")

    assert snapshot.period == "2026-10-15/2026-10-21"
    assert snapshot.sales == 160000
    assert snapshot.orders == 6
    assert snapshot.average_order_value == 40000  # over the four orders with amounts
    assert snapshot.sales_change_pct == 100.0
    assert snapshot.traffic is None and snapshot.conversion_rate is None
    assert "zigzag" in snapshot.note
    assert snapshot.alerts.low_stock == 2


async def test_sales_series_by_day_and_traffic_unavailable(backend):
    sales = await backend.query_metrics(SESSION, "sales", "2026-10-20/2026-10-21")
    traffic = await backend.query_metrics(SESSION, "traffic")

    assert [(p.date, p.value) for p in sales.points] == [
        ("2026-10-20", 120000),
        ("2026-10-21", 40000),
    ]
    assert sales.unit == "KRW"
    assert traffic.points == [] and traffic.note


async def test_writes_are_refused_until_a_platform_write_is_wired(backend):
    with pytest.raises(ChangeNotApplicable):
        await backend.stage_price_update(
            SESSION, [PriceUpdateItem(listing_id="1-001", new_price=1)]
        )
    with pytest.raises(ChangeNotApplicable):
        await backend.stage_inventory_action(
            SESSION, [InventoryActionItem(listing_id="1-001", action="restock", quantity=1)]
        )
    with pytest.raises(ChangeNotApplicable):
        await backend.apply_change(SESSION, "chg-0001")
    assert await backend.get_pending_changes(SESSION) == []


async def test_context_lists_what_the_ledger_cannot_supply(backend):
    context = await backend.get_merchant_context(SESSION)

    sources = {limitation.source for limitation in context["limitations"]}
    assert {"traffic", "campaigns", "writes"} <= sources
    assert context["currency"] == "KRW"


async def test_a_zero_threshold_turns_low_stock_alerts_off(reader):
    quiet = LedgerMerchantBackend(
        reader, TENANT.model_copy(update={"low_stock_threshold": 0}), MerchantAgentConfig()
    )

    assert await quiet.get_inventory_alerts(SESSION) == []
    assert (await quiet.get_business_snapshot(SESSION)).alerts.low_stock == 0


async def test_search_returns_at_most_the_limit_and_keeps_families_whole(backend):
    found = await backend.search_listings(SESSION, "", limit=1)

    assert [listing.listing_id for listing in found] == ["c24:14"]
    assert found[0].stock == 11  # both variants counted
