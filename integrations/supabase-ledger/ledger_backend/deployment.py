# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The merchant deployment a host loads (``--backend ledger_backend.deployment:build_merchant``
on the Agent SDK console), built from the host's environment:

``LEDGER_DSN`` (required) and the ``LEDGER_*`` tenant variables of :mod:`ledger_backend.tenant`;
``LEDGER_TIMEZONE`` for day boundaries (default ``UTC``); ``MERCHANT_BRAND_NAME`` and
``MERCHANT_MODEL``; and ``WORKBENCH_ROOT`` with ``WORKBENCH_PACKAGE`` (and ``WORKBENCH_ENV_FILE``, the workbench
app's dotenv file) to turn restocks on.
Listing, price, and campaign tools stay off, and changes apply only after host approval."""

from __future__ import annotations

import os
from collections.abc import Mapping

from merchant_agent import MerchantAgentConfig

from .merchant import LedgerMerchantBackend
from .postgres import PostgresLedgerReader, readonly_fetch
from .tenant import tenant_from_env
from .workbench import workbench_from_env


def build_merchant(
    env: Mapping[str, str] = os.environ,
) -> tuple[LedgerMerchantBackend, MerchantAgentConfig]:
    tenant = tenant_from_env(env)
    reader = PostgresLedgerReader(
        readonly_fetch(env["LEDGER_DSN"], settings={"app.tenant_id": tenant.tenant_id}),
        timezone=env.get("LEDGER_TIMEZONE", "UTC"),
    )
    workbench = None
    if env.get("WORKBENCH_ROOT"):
        workbench = workbench_from_env(env, tenant.tenant_id)
    defaults = MerchantAgentConfig()
    config = MerchantAgentConfig(
        brand_name=env.get("MERCHANT_BRAND_NAME", defaults.brand_name),
        model=env.get("MERCHANT_MODEL", defaults.model),
        enable_listing_edits=False,
        enable_pricing=False,
        enable_campaigns=False,
        require_host_approval=True,
    )
    backend = LedgerMerchantBackend(reader, tenant, config, workbench=workbench)
    return backend, config
