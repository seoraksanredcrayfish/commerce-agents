# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Product rows grouped into the merchant agent's listing shapes (``docs/backends.md``):
rows sharing a family number become one family, ``p<number>``, whose variants are the
rows keyed by barcode; a row with no family number is a plain listing keyed by barcode.
Pure functions over rows."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from merchant_agent import Listing, ListingDetails

from .rows import ProductRow
from .tenant import TenantConfig


def option_values(option_name: str, labels: Sequence[str]) -> dict[str, str]:
    parts = [part.strip() for part in option_name.split(",") if part.strip()]
    return {
        (labels[i] if i < len(labels) else f"option{i + 1}"): part for i, part in enumerate(parts)
    }


def _status(stock: float, tenant: TenantConfig) -> str:
    return "active" if stock > 0 or tenant.zero_stock_sellable else "out_of_stock"


def _attributes(price: float) -> dict[str, str]:
    # A zero price in the ledger means none was synced, not a free item.
    return {"price_status": "unset"} if price <= 0 else {}


def _variant(row: ProductRow, family_id: str | None, tenant: TenantConfig) -> ListingDetails:
    return ListingDetails(
        listing_id=row.barcode,
        title=row.product_name,
        status=_status(row.stock_sellable, tenant),
        price=row.selling_price,
        currency=tenant.currency,
        stock=int(row.stock_sellable),
        category=row.category or None,
        attributes=_attributes(row.selling_price),
        option_values=option_values(row.option_name, tenant.option_labels),
        variant_of=family_id,
    )


def _family(number: int, rows: list[ProductRow], tenant: TenantConfig) -> ListingDetails:
    family_id = f"p{number}"
    variants = [_variant(row, family_id, tenant) for row in rows]
    options: dict[str, list[str]] = {}
    for variant in variants:
        for name, value in variant.option_values.items():
            if value not in options.setdefault(name, []):
                options[name].append(value)
    price = min(v.price for v in variants)
    stock = sum(v.stock for v in variants)
    return ListingDetails(
        listing_id=family_id,
        title=rows[0].product_name,
        status=_status(stock, tenant),
        price=price,
        currency=tenant.currency,
        stock=stock,
        category=rows[0].category or None,
        attributes=_attributes(price),
        options=options,
        variants=variants,
    )


def listings_of(rows: Iterable[ProductRow], tenant: TenantConfig) -> list[ListingDetails]:
    """Families and plain listings in the order their first row appears."""
    groups: dict[int | str, list[ProductRow]] = {}
    for row in rows:
        groups.setdefault(row.family_no if row.family_no is not None else row.barcode, []).append(
            row
        )
    return [
        _family(key, group, tenant) if isinstance(key, int) else _variant(group[0], None, tenant)
        for key, group in groups.items()
    ]


def find_listing(listings: Iterable[ListingDetails], listing_id: str) -> ListingDetails | None:
    """A family, a plain listing, or a variant by id."""
    for listing in listings:
        if listing.listing_id == listing_id:
            return listing
        for variant in listing.variants:
            if variant.listing_id == listing_id:
                return variant  # type: ignore[return-value]
    return None


def as_listing(details: ListingDetails) -> Listing:
    """The search row for a record: the details less its variants and long fields."""
    return Listing(**details.model_dump(include=set(Listing.model_fields)))
