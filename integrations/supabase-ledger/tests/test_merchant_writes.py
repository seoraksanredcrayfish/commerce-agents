# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Restocks staged by the agent and applied, after the host's approval, through the
workbench's stock command. The backend keeps no stock of its own."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ledger_backend.merchant import LedgerMerchantBackend
from ledger_backend.reader import InMemoryLedgerReader
from ledger_backend.rows import ProductRow
from ledger_backend.tenant import TenantConfig
from ledger_backend.workbench import StockFix, WorkbenchError

from merchant_agent import (
    ChangeNotApplicable,
    ChangeStatus,
    InventoryActionItem,
    MerchantAgentConfig,
    MerchantSessionContext,
)

TENANT = TenantConfig(tenant_id="acme", option_labels=("color", "size"))
SESSION = MerchantSessionContext(
    session_id="s1", merchant_id="acme", operator="op-1", now=datetime(2026, 10, 21, tzinfo=UTC)
)


def row(barcode: str, stock: int, family: int | None = 14) -> ProductRow:
    return ProductRow(
        barcode=barcode,
        product_name="ACME Pants",
        option_name="BLACK,OS",
        selling_price=39000,
        cost_price=9000,
        stock_sellable=stock,
        family_no=family,
    )


class FakeWorkbench:
    def __init__(self, fail: str | None = None):
        self.fail = fail
        self.calls: list[tuple[str, int]] = []

    async def set_stock(self, barcode: str, count: int) -> StockFix:
        self.calls.append((barcode, count))
        if self.fail:
            raise WorkbenchError(self.fail)
        return StockFix(before=0, after=count)


@pytest.fixture
def products() -> dict[str, list[ProductRow]]:
    return {"acme": [row("1-001", 2), row("1-002", 0)]}


@pytest.fixture
def workbench() -> FakeWorkbench:
    return FakeWorkbench()


@pytest.fixture
def backend(products, workbench) -> LedgerMerchantBackend:
    return LedgerMerchantBackend(
        InMemoryLedgerReader(products), TENANT, MerchantAgentConfig(), workbench=workbench
    )


def restock(listing_id: str, quantity: int) -> InventoryActionItem:
    return InventoryActionItem(listing_id=listing_id, action="restock", quantity=quantity)


async def test_a_restock_is_staged_with_the_stock_it_was_read_at(backend, workbench):
    change = await backend.stage_inventory_action(SESSION, [restock("1-001", 3)])

    assert [(i.target, i.field, i.before, i.after) for i in change.items] == [
        ("1-001", "stock", 2, 5)
    ]
    assert change.status is ChangeStatus.STAGED
    assert await backend.get_pending_changes(SESSION) == [change]
    assert workbench.calls == []  # staging writes nothing


async def test_apply_adds_the_approved_quantity_to_the_stock_at_apply_time(
    backend, products, workbench
):
    change = await backend.stage_inventory_action(SESSION, [restock("1-001", 3)])
    products["acme"][0] = row("1-001", 1)  # a sale between staging and applying

    applied = await backend.apply_change(SESSION, change.change_id)

    assert workbench.calls == [("1-001", 4)]
    assert applied.status is ChangeStatus.APPLIED
    assert applied.applied_by == "op-1"
    assert await backend.get_pending_changes(SESSION) == []


async def test_a_refused_workbench_write_leaves_the_change_staged(products):
    failing = FakeWorkbench(fail="재고 CAS 실패")
    backend = LedgerMerchantBackend(
        InMemoryLedgerReader(products), TENANT, MerchantAgentConfig(), workbench=failing
    )
    change = await backend.stage_inventory_action(SESSION, [restock("1-001", 3)])

    with pytest.raises(ChangeNotApplicable, match="재고 CAS 실패"):
        await backend.apply_change(SESSION, change.change_id)

    assert [c.change_id for c in await backend.get_pending_changes(SESSION)] == [change.change_id]


async def test_a_change_names_one_item_so_a_retry_never_adds_twice(backend):
    with pytest.raises(ChangeNotApplicable, match="one"):
        await backend.stage_inventory_action(SESSION, [restock("1-001", 1), restock("1-002", 1)])


async def test_a_family_is_restocked_per_variant(backend):
    with pytest.raises(ChangeNotApplicable, match="variant"):
        await backend.stage_inventory_action(SESSION, [restock("c24:14", 1)])


async def test_pause_and_activate_have_no_workbench_command(backend):
    with pytest.raises(ChangeNotApplicable):
        await backend.stage_inventory_action(
            SESSION, [InventoryActionItem(listing_id="1-001", action="pause")]
        )


async def test_a_discarded_change_is_never_applied(backend, workbench):
    change = await backend.stage_inventory_action(SESSION, [restock("1-001", 3)])

    await backend.discard_change(SESSION, change.change_id)

    with pytest.raises(ChangeNotApplicable):
        await backend.apply_change(SESSION, change.change_id)
    assert workbench.calls == []


async def test_without_a_workbench_nothing_is_staged(products):
    read_only = LedgerMerchantBackend(InMemoryLedgerReader(products), TENANT, MerchantAgentConfig())

    with pytest.raises(ChangeNotApplicable):
        await read_only.stage_inventory_action(SESSION, [restock("1-001", 3)])
