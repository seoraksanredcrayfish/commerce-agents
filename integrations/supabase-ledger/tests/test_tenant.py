# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The tenant binding read from the deployment's environment."""

from __future__ import annotations

import pytest
from ledger_backend.tenant import tenant_from_env


def test_the_environment_names_the_tenant_and_its_option_labels():
    tenant = tenant_from_env(
        {
            "LEDGER_TENANT_ID": "acme",
            "LEDGER_OPTION_LABELS": "color, size",
            "LEDGER_MAX_ALERTS": "10",
        }
    )

    assert tenant.tenant_id == "acme"
    assert tenant.option_labels == ("color", "size")
    assert (tenant.currency, tenant.low_stock_threshold, tenant.max_alerts) == ("KRW", 3, 10)


def test_a_missing_tenant_id_refuses_to_start():
    with pytest.raises(KeyError):
        tenant_from_env({})


def test_selling_without_stock_is_read_from_the_environment():
    tenant = tenant_from_env({"LEDGER_TENANT_ID": "acme", "LEDGER_ZERO_STOCK_SELLABLE": "true"})

    assert tenant.zero_stock_sellable is True
