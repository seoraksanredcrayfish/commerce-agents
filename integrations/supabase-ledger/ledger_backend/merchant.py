# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""A MerchantBackend over one tenant of the ledger. Reads come from a
:class:`~ledger_backend.reader.LedgerReader`. The one write is a restock, applied through
the workbench's stock command (:class:`~ledger_backend.workbench.WorkbenchCli`) after the
host approves it; without a workbench, and for every other write, the ``stage_*`` methods
raise ``ChangeNotApplicable`` and the executor relays it.

The backend holds no stock: a restock is staged with the stock read then, for the preview,
and applied as the stock read at apply time plus the approved quantity. The workbench
refuses a fix when the stock moves under it, and the change stays staged. One restock is
one change, so a retried apply never adds twice.

What the ledger cannot supply is None with a note, never a zero: traffic and conversion
(no analytics source), campaign performance, and order amounts for a channel whose mirror
carries none. Order issues are not derived yet and come back empty."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from merchant_agent import (
    ActorKind,
    AlertCounts,
    BusinessSnapshot,
    Campaign,
    CampaignDraft,
    ChangeItem,
    ChangeKind,
    ChangeLedger,
    ChangeNotApplicable,
    ChangeStatus,
    InventoryActionItem,
    InventoryAlert,
    Listing,
    ListingDetails,
    ListingFilters,
    MerchantAgentConfig,
    MerchantBackend,
    MerchantSessionContext,
    MetricPoint,
    MetricSeries,
    OrderIssue,
    PriceUpdateItem,
    PricingContext,
    PromotionDraft,
    StagedChange,
)
from merchant_agent.types import DataLimitation

from .catalog import as_listing, find_listing, listings_of, option_values
from .periods import Window, resolve_period
from .reader import LedgerReader
from .rows import DailyOrders, ProductRow
from .tenant import TenantConfig
from .workbench import WorkbenchCli, WorkbenchError

_BROWSE_QUERIES = {"", "*", "all", "everything", "전체"}
_FILTERED_PAGE = 50
_LISTING_SORTS = {
    "stock_asc": lambda listing: listing.stock,
    "price_desc": lambda listing: -listing.price,
    "price_asc": lambda listing: listing.price,
}
_NO_WRITES = "Writes to the store are not wired for this ledger yet; nothing was staged."
_NO_COMMAND = "The workbench has no command for this change; nothing was staged."
_READ_LIMITATIONS = (
    DataLimitation(
        source="traffic", note="No analytics source: traffic and conversion are unknown."
    ),
    DataLimitation(source="campaigns", note="Campaign spend and results are not in the ledger."),
)
_NO_WRITES_LIMITATION = DataLimitation(
    source="writes", note="Listing, price, stock, and campaign writes are not wired."
)
_RESTOCK_ONLY_LIMITATION = DataLimitation(
    source="writes", note="Only restocks apply, one listing per change; prices and content do not."
)


def _today(session: MerchantSessionContext) -> date:
    now = session.local_now() or datetime.now(UTC)
    return now.date()


def _filtered(listings: list[Listing], filters: ListingFilters | None) -> list[Listing]:
    if filters is None:
        return listings
    if filters.status:
        listings = [listing for listing in listings if listing.status == filters.status]
    if filters.category:
        wanted = filters.category.casefold()
        listings = [
            listing for listing in listings if wanted in (listing.category or "").casefold()
        ]
    if filters.max_stock is not None:
        listings = [listing for listing in listings if listing.stock <= filters.max_stock]
    if filters.sort in _LISTING_SORTS:
        listings = sorted(listings, key=_LISTING_SORTS[filters.sort])
    return listings


def _margin_pct(price: float, cost: float) -> float | None:
    if price <= 0 or cost <= 0:
        return None
    return round((price - cost) / price * 100, 1)


def _change_pct(current: float, prior: float) -> float | None:
    return round((current - prior) / prior * 100, 1) if prior > 0 else None


def _known_amounts(rows: Sequence[DailyOrders]) -> tuple[float, int]:
    """Total amount and the order count it covers, over rows that carry amounts."""
    known = [row for row in rows if row.amount is not None]
    return sum(row.amount for row in known), sum(row.orders for row in known)


def _missing_amount_note(rows: Sequence[DailyOrders]) -> str | None:
    missing = sorted({row.source for row in rows if row.amount is None})
    if not missing:
        return None
    return f"Sales exclude {', '.join(missing)} orders, whose amounts the ledger lacks."[:140]


class LedgerMerchantBackend(MerchantBackend):
    def __init__(
        self,
        reader: LedgerReader,
        tenant: TenantConfig,
        config: MerchantAgentConfig,
        *,
        workbench: WorkbenchCli | None = None,
    ):
        self._reader = reader
        self._tenant = tenant
        self._config = config
        self._workbench = workbench
        self._changes = ChangeLedger(config)

    async def _record(self, listing_id: str) -> tuple[ListingDetails | None, dict[str, ProductRow]]:
        """The listing or variant ``listing_id`` names, read with its family, and the
        family's rows by barcode."""
        tenant_id = self._tenant.tenant_id
        if listing_id.startswith("c24:") and listing_id[4:].isdigit():
            rows = await self._reader.listing_rows(tenant_id, family_no=int(listing_id[4:]))
        else:
            rows = await self._reader.listing_rows(tenant_id, barcode=listing_id)
            if rows and rows[0].family_no is not None:
                rows = await self._reader.listing_rows(tenant_id, family_no=rows[0].family_no)
        listing = find_listing(listings_of(rows, self._tenant), listing_id)
        return listing, {row.barcode: row for row in rows}

    async def _orders(self, window: Window) -> tuple[list[DailyOrders], list[DailyOrders]]:
        rows = await self._reader.daily_orders(
            self._tenant.tenant_id, window.prior_start, window.end
        )
        current = [row for row in rows if row.day >= window.start]
        prior = [row for row in rows if row.day <= window.prior_end]
        return current, prior

    # -- Performance -------------------------------------------------------------

    async def get_business_snapshot(
        self, session: MerchantSessionContext, period: str | None = None
    ) -> BusinessSnapshot:
        window = resolve_period(period, _today(session))
        current, prior = await self._orders(window)
        sales, costed_orders = _known_amounts(current)
        prior_sales, _ = _known_amounts(prior)
        orders = sum(row.orders for row in current)
        prior_orders = sum(row.orders for row in prior)
        low_stock = await self._low_stock_count()
        return BusinessSnapshot(
            period=window.label,
            compare_to=window.prior_label,
            sales=round(sales, 2),
            orders=orders,
            average_order_value=round(sales / costed_orders, 2) if costed_orders else None,
            sales_change_pct=_change_pct(sales, prior_sales),
            orders_change_pct=_change_pct(orders, prior_orders),
            currency=self._tenant.currency,
            alerts=AlertCounts(low_stock=low_stock),
            note=_missing_amount_note(current),
        )

    async def query_metrics(
        self,
        session: MerchantSessionContext,
        metric: str,
        period: str | None = None,
        granularity: str = "day",
        segment: str | None = None,
    ) -> MetricSeries:
        cleaned = metric.strip().lower().replace(" ", "_")
        window = resolve_period(period or "last_30_days", _today(session))
        bucket = "week" if granularity == "week" else "day"
        if cleaned not in {"sales", "revenue", "orders", "average_order_value", "aov"}:
            return MetricSeries(
                metric=cleaned,
                granularity=bucket,
                period=window.label,
                note=f"The ledger has no {cleaned} figures.",
            )
        current, _ = await self._orders(window)
        points = [
            MetricPoint(date=start.isoformat(), value=_metric_value(cleaned, rows))
            for start, rows in _buckets(current, window, bucket)
        ]
        money = cleaned != "orders"
        return MetricSeries(
            metric=cleaned,
            unit=self._tenant.currency if money else None,
            granularity=bucket,
            period=window.label,
            segment=None,
            points=points,
            note=_missing_amount_note(current) if money else None,
        )

    async def get_campaign_performance(
        self, session: MerchantSessionContext, campaign_id: str | None = None
    ) -> list[Campaign]:
        return []

    # -- Catalog -------------------------------------------------------------------

    async def search_listings(
        self,
        session: MerchantSessionContext,
        query: str,
        filters: ListingFilters | None = None,
        limit: int = 8,
    ) -> list[Listing]:
        cleaned = query.strip()
        if cleaned.casefold() in _BROWSE_QUERIES:
            cleaned = ""
        # Filters apply to the page read, so a filtered read takes a wider page.
        page = limit if filters is None else max(limit, _FILTERED_PAGE)
        rows = await self._reader.search_products(self._tenant.tenant_id, cleaned, page)
        listings = [as_listing(listing) for listing in listings_of(rows, self._tenant)]
        return _filtered(listings, filters)[:limit]

    async def get_listing(
        self, session: MerchantSessionContext, listing_id: str
    ) -> ListingDetails | None:
        listing, _ = await self._record(listing_id)
        return listing

    # -- Inventory and order health --------------------------------------------------

    async def _low_stock_count(self) -> int:
        threshold = self._tenant.low_stock_threshold
        if threshold <= 0:
            return 0
        return await self._reader.low_stock_count(self._tenant.tenant_id, threshold)

    async def get_inventory_alerts(self, session: MerchantSessionContext) -> list[InventoryAlert]:
        threshold = self._tenant.low_stock_threshold
        if threshold <= 0:
            return []
        rows = await self._reader.low_stock_rows(
            self._tenant.tenant_id, threshold, self._tenant.max_alerts
        )
        return [
            InventoryAlert(
                listing_id=row.barcode,
                title=row.product_name,
                kind="low_stock",
                option_values=option_values(row.option_name, self._tenant.option_labels),
                variant_of=f"c24:{row.family_no}" if row.family_no is not None else None,
                stock=int(row.stock_sellable),
                threshold=threshold,
                storefront_visible=True,
            )
            for row in rows
        ]

    async def get_order_issues(self, session: MerchantSessionContext) -> list[OrderIssue]:
        return []

    # -- Pricing -----------------------------------------------------------------------

    async def get_pricing_context(
        self, session: MerchantSessionContext, listing_id: str
    ) -> PricingContext | None:
        listing, rows = await self._record(listing_id)
        if listing is None:
            return None

        def context(record: Listing) -> PricingContext:
            cost = rows[record.listing_id].cost_price
            return PricingContext(
                listing_id=record.listing_id,
                current_price=record.price,
                currency=self._tenant.currency,
                unit_cost=cost or None,
                margin_pct=_margin_pct(record.price, cost),
                max_price_delta_pct=self._config.max_price_delta_pct,
                max_promotion_discount_pct=self._config.max_promotion_discount_pct,
                option_values=record.option_values,
            )

        if not listing.variants:
            return context(listing)
        return PricingContext(
            listing_id=listing.listing_id,
            current_price=listing.price,
            currency=self._tenant.currency,
            max_price_delta_pct=self._config.max_price_delta_pct,
            max_promotion_discount_pct=self._config.max_promotion_discount_pct,
            variants=[context(variant) for variant in listing.variants],
        )

    # -- Staged writes: restocks through the workbench ---------------------------------

    async def stage_listing_update(
        self,
        session: MerchantSessionContext,
        listing_id: str,
        fields: dict[str, Any],
        note: str | None = None,
    ) -> StagedChange:
        raise ChangeNotApplicable(_NO_WRITES)

    async def stage_price_update(
        self,
        session: MerchantSessionContext,
        items: list[PriceUpdateItem],
        note: str | None = None,
    ) -> StagedChange:
        raise ChangeNotApplicable(_NO_WRITES)

    async def stage_inventory_action(
        self,
        session: MerchantSessionContext,
        items: list[InventoryActionItem],
        note: str | None = None,
    ) -> StagedChange:
        if self._workbench is None:
            raise ChangeNotApplicable(_NO_WRITES)
        if len(items) != 1:
            raise ChangeNotApplicable("Stage one listing per change; stage each restock alone.")
        item = items[0]
        if item.action != "restock":
            raise ChangeNotApplicable(_NO_COMMAND)
        listing, _ = await self._record(item.listing_id)
        if listing is None:
            raise ChangeNotApplicable(f"no listing {item.listing_id}")
        if listing.variants:
            raise ChangeNotApplicable(f"{listing.listing_id} is restocked per variant.")
        quantity = item.quantity or 0
        if quantity <= 0:
            raise ChangeNotApplicable("A restock adds one unit or more.")
        return self._changes.stage(
            kind=ChangeKind.INVENTORY_ACTION,
            summary=note or f"Restock {listing.listing_id} by {quantity}",
            items=[
                ChangeItem(
                    target=listing.listing_id,
                    field="stock",
                    before=listing.stock,
                    after=listing.stock + quantity,
                )
            ],
            actor=session.operator,
            actor_kind=ActorKind.AGENT,
        )

    async def stage_promotion(
        self, session: MerchantSessionContext, promotion: PromotionDraft
    ) -> StagedChange:
        raise ChangeNotApplicable(_NO_WRITES)

    async def stage_campaign(
        self, session: MerchantSessionContext, campaign: CampaignDraft
    ) -> StagedChange:
        raise ChangeNotApplicable(_NO_WRITES)

    async def get_pending_changes(self, session: MerchantSessionContext) -> list[StagedChange]:
        return self._changes.pending()

    async def apply_change(self, session: MerchantSessionContext, change_id: str) -> StagedChange:
        change = self._changes.get(change_id)
        if self._workbench is None or change is None:
            raise ChangeNotApplicable(f"no change with id {change_id!r} to apply")
        if change.status is not ChangeStatus.STAGED:
            raise ChangeNotApplicable(f"change {change_id} is {change.status.value}, not staged")
        (item,) = change.items
        listing, _ = await self._record(item.target)
        if listing is None:
            raise ChangeNotApplicable(f"no listing {item.target}")
        try:
            await self._workbench.set_stock(item.target, listing.stock + item.after - item.before)
        except WorkbenchError as error:
            raise ChangeNotApplicable(f"The workbench refused the restock: {error}") from error
        return self._changes.apply(change_id, actor=session.operator)

    async def discard_change(
        self,
        session: MerchantSessionContext,
        change_id: str,
        actor_kind: ActorKind = ActorKind.OPERATOR,
    ) -> StagedChange:
        return self._changes.discard(change_id, actor=session.operator, actor_kind=actor_kind)

    # -- Merchant context ----------------------------------------------------------------

    async def get_merchant_context(self, session: MerchantSessionContext) -> dict[str, Any] | None:
        writes = _NO_WRITES_LIMITATION if self._workbench is None else _RESTOCK_ONLY_LIMITATION
        return {"currency": self._tenant.currency, "limitations": [*_READ_LIMITATIONS, writes]}


def _metric_value(metric: str, rows: Sequence[DailyOrders]) -> float:
    if metric == "orders":
        return float(sum(row.orders for row in rows))
    amount, costed_orders = _known_amounts(rows)
    if metric in {"average_order_value", "aov"}:
        return round(amount / costed_orders, 2) if costed_orders else 0.0
    return round(amount, 2)


def _buckets(
    rows: Sequence[DailyOrders], window: Window, bucket: str
) -> list[tuple[date, list[DailyOrders]]]:
    """Every day (or every seven days from the window's start) with its rows, empty
    buckets included so a quiet day reads as zero orders."""
    step = 7 if bucket == "week" else 1
    starts = []
    day = window.start
    while day <= window.end:
        starts.append(day)
        day += timedelta(days=step)
    return [
        (start, [row for row in rows if start <= row.day < start + timedelta(days=step)])
        for start in starts
    ]
