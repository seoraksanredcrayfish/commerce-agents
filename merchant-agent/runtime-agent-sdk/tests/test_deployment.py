# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""A deployment's backend and config, named as ``module:function`` for the console."""

from __future__ import annotations

import sys
import types

import pytest

from merchant_agent import MerchantAgentConfig
from merchant_agent_sdk import load_deployment


@pytest.fixture
def deployment_module(monkeypatch):
    module = types.ModuleType("acme_deployment")
    backend = object()
    config = MerchantAgentConfig(brand_name="ACME Outlet")
    module.build = lambda: (backend, config)
    module.not_a_pair = lambda: backend
    monkeypatch.setitem(sys.modules, "acme_deployment", module)
    return backend, config


def test_a_named_function_supplies_the_backend_and_config(deployment_module):
    backend, config = deployment_module

    assert load_deployment("acme_deployment:build") == (backend, config)


def test_a_spec_without_a_function_name_is_refused():
    with pytest.raises(ValueError, match="module:function"):
        load_deployment("acme_deployment")


def test_a_function_that_returns_no_config_is_refused(deployment_module):
    with pytest.raises(TypeError, match="MerchantAgentConfig"):
        load_deployment("acme_deployment:not_a_pair")
