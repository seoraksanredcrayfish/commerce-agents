# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The merchant deployment the console loads, built from the host's environment."""

from __future__ import annotations

import pytest
from ledger_backend.deployment import build_merchant
from ledger_backend.merchant import LedgerMerchantBackend

ENV = {
    "LEDGER_TENANT_ID": "acme",
    "LEDGER_DSN": "postgresql://reader@db.example/postgres",
    "MERCHANT_BRAND_NAME": "ACME Outlet",
    "MERCHANT_MODEL": "claude-opus-5-5",
}


def test_the_backend_reads_the_tenant_and_writes_are_off_without_a_workbench():
    backend, config = build_merchant(ENV)

    assert isinstance(backend, LedgerMerchantBackend)
    assert backend._workbench is None
    assert (config.brand_name, config.model) == ("ACME Outlet", "claude-opus-5-5")
    assert config.require_host_approval is True
    assert not (config.enable_listing_edits or config.enable_pricing or config.enable_campaigns)


def test_a_workbench_root_and_package_turn_restocks_on():
    backend, _ = build_merchant(
        {**ENV, "WORKBENCH_ROOT": "/srv/workbench", "WORKBENCH_PACKAGE": "acme-workbench"}
    )

    assert backend._workbench is not None


def test_a_workbench_root_without_its_package_refuses_to_start():
    with pytest.raises(KeyError, match="WORKBENCH_PACKAGE"):
        build_merchant({**ENV, "WORKBENCH_ROOT": "/srv/workbench"})


def test_a_missing_database_refuses_to_start():
    env = {key: value for key, value in ENV.items() if key != "LEDGER_DSN"}

    with pytest.raises(KeyError, match="LEDGER_DSN"):
        build_merchant(env)


def test_the_ledger_connection_is_scoped_to_the_tenant(monkeypatch):
    import ledger_backend.deployment as deployment

    seen = {}

    def fake_readonly_fetch(dsn, *, settings=None, connect=None):
        seen.update(dsn=dsn, settings=settings)
        return lambda *_: None

    monkeypatch.setattr(deployment, "readonly_fetch", fake_readonly_fetch)
    build_merchant(ENV)

    assert seen == {"dsn": ENV["LEDGER_DSN"], "settings": {"app.tenant_id": "acme"}}
