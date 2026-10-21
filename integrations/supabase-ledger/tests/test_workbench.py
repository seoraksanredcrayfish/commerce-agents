# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The workbench CLI port: the one way this package changes the store, through the same
command the workbench screens call."""

from __future__ import annotations

import json

import pytest
from ledger_backend.workbench import WorkbenchCli, WorkbenchError


class FakeRunner:
    def __init__(self, code: int = 0, stdout: str = ""):
        self.code = code
        self.stdout = stdout
        self.calls: list[tuple[list[str], dict[str, str], str]] = []

    async def __call__(self, argv, env, cwd):
        self.calls.append((list(argv), dict(env), cwd))
        return self.code, self.stdout


def fixed(before: int, after: int) -> str:
    return json.dumps(
        {
            "status": "fixed",
            "idempotencyKey": "k",
            "delta": after - before,
            "before": before,
            "after": after,
        }
    )


async def test_set_stock_calls_the_workbench_command_for_the_tenant():
    runner = FakeRunner(stdout="building…\n" + fixed(2, 5))
    cli = WorkbenchCli("/srv/workbench", "acme-workbench", "acme", runner=runner)

    result = await cli.set_stock("1-001", 5)

    argv, env, cwd = runner.calls[0]
    assert argv == [
        "pnpm", "--filter", "acme-workbench", "cli", "--",
        "stock.set", "--barcode", "1-001", "--count", "5", "--yes", "--json",
    ]  # fmt: skip
    assert env["SUPABASE_TENANT_ID"] == "acme"
    assert cwd == "/srv/workbench"
    assert (result.before, result.after) == (2, 5)


async def test_an_unchanged_count_is_not_an_error():
    runner = FakeRunner(stdout=json.dumps({"status": "noop", "before": 5}))

    result = await WorkbenchCli("/w", "acme-workbench", "acme", runner=runner).set_stock("1-001", 5)

    assert (result.before, result.after) == (5, 5)


async def test_a_refused_fix_raises_with_the_workbench_reason():
    runner = FakeRunner(code=1, stdout=json.dumps({"status": "failed", "reason": "재고 CAS 실패"}))

    with pytest.raises(WorkbenchError, match="재고 CAS 실패"):
        await WorkbenchCli("/w", "acme-workbench", "acme", runner=runner).set_stock("1-001", 5)


async def test_output_without_a_result_raises():
    runner = FakeRunner(code=1, stdout="Error: SUPABASE_URL is not set")

    with pytest.raises(WorkbenchError, match="SUPABASE_URL"):
        await WorkbenchCli("/w", "acme-workbench", "acme", runner=runner).set_stock("1-001", 5)


async def test_a_negative_or_fractional_count_never_reaches_the_workbench():
    runner = FakeRunner(stdout=fixed(0, 0))
    cli = WorkbenchCli("/w", "acme-workbench", "acme", runner=runner)

    with pytest.raises(WorkbenchError):
        await cli.set_stock("1-001", -1)
    assert runner.calls == []
