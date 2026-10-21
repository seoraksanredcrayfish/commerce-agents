# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The ledger's product rows grouped into the merchant agent's listing shapes."""

from __future__ import annotations

from ledger_backend.catalog import find_listing, listings_of
from ledger_backend.rows import ProductRow
from ledger_backend.tenant import TenantConfig

TENANT = TenantConfig(tenant_id="acme", option_labels=("color", "size"), low_stock_threshold=3)


def row(
    barcode: str, name: str, option: str, *, family: int | None, price=39000, stock=0, cost=9000
):
    return ProductRow(
        barcode=barcode,
        product_name=name,
        option_name=option,
        category="",
        cost_price=cost,
        selling_price=price,
        stock_sellable=stock,
        family_no=family,
    )


ROWS = [
    row("1-001", "ACME Curved Pants", "IVORY,OS", family=14, stock=2),
    row("1-002", "ACME Curved Pants", "BLACK,OS", family=14, stock=5, price=35000),
    row("2-001", "ACME Knit", "", family=None, stock=0),
]


def test_rows_sharing_a_family_number_become_one_family_with_variants():
    listings = listings_of(ROWS, TENANT)

    family = listings[0]
    assert family.listing_id == "p14"
    assert family.title == "ACME Curved Pants"
    assert family.options == {"color": ["IVORY", "BLACK"], "size": ["OS"]}
    assert family.price == 35000  # the lowest variant's
    assert family.stock == 7  # the sum
    assert family.currency == "KRW"
    assert [v.listing_id for v in family.variants] == ["1-001", "1-002"]
    assert family.variants[0].option_values == {"color": "IVORY", "size": "OS"}
    assert family.variants[0].variant_of == "p14"


def test_a_row_without_a_family_number_is_a_plain_listing_keyed_by_barcode():
    plain = listings_of(ROWS, TENANT)[1]

    assert plain.listing_id == "2-001"
    assert plain.options == {}
    assert plain.variants == []
    assert plain.status == "out_of_stock"


def test_an_option_string_with_more_parts_than_labels_keeps_the_rest_under_numbered_keys():
    listings = listings_of([row("9-1", "ACME Tee", "RED,M,LONG", family=9)], TENANT)

    assert listings[0].variants[0].option_values == {"color": "RED", "size": "M", "option3": "LONG"}


def test_a_zero_selling_price_is_marked_unset_rather_than_quoted_as_free():
    listings = listings_of([row("9-1", "ACME Tee", "", family=None, price=0)], TENANT)

    assert listings[0].attributes["price_status"] == "unset"


def test_find_listing_resolves_a_family_and_a_variant_id():
    listings = listings_of(ROWS, TENANT)

    assert find_listing(listings, "p14").listing_id == "p14"
    assert find_listing(listings, "1-002").variant_of == "p14"
    assert find_listing(listings, "nope") is None


def test_a_store_that_sells_without_stock_keeps_zero_stock_rows_active():
    made_to_order = TENANT.model_copy(update={"zero_stock_sellable": True})

    plain = listings_of(ROWS, made_to_order)[1]

    assert plain.stock == 0
    assert plain.status == "active"
