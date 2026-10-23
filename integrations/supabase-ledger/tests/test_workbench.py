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


async def test_a_reply_posts_through_the_workbench_reply_command():
    runner = FakeRunner(stdout=json.dumps({"ok": True, "error": None}))
    cli = WorkbenchCli("/w", "acme-workbench", "acme", runner=runner)

    await cli.reply_to_inquiry("thread-1", "Hello:) It ships next week. Thank you")

    argv, env, _ = runner.calls[0]
    assert argv[5:] == [
        "cs.reply", "--thread", "thread-1", "--body", "Hello:) It ships next week. Thank you",
        "--yes", "--json",
    ]  # fmt: skip
    assert env["SUPABASE_TENANT_ID"] == "acme"


async def test_a_refused_reply_raises_with_the_channel_reason():
    runner = FakeRunner(code=1, stdout=json.dumps({"ok": False, "error": "channel key missing"}))

    with pytest.raises(WorkbenchError, match="channel key missing"):
        await WorkbenchCli("/w", "acme-workbench", "acme", runner=runner).reply_to_inquiry(
            "thread-1", "Hello"
        )


async def test_an_order_text_sends_through_the_workbench_sms_command_with_its_approver():
    runner = FakeRunner(stdout=json.dumps({"ok": True}))
    cli = WorkbenchCli("/w", "acme-workbench", "acme", runner=runner)

    await cli.send_order_sms("20260101-0000001", "02-000-0000", "Hello", sent_by="owner")

    argv, _, _ = runner.calls[0]
    assert argv[5:7] == ["sms.send", "--input"]
    assert json.loads(argv[7]) == {
        "orderId": "20260101-0000001",
        "senderNo": "02-000-0000",
        "content": "Hello",
        "sentBy": "owner",
    }
    assert argv[8:] == ["--yes", "--json"]


async def test_a_blank_message_never_reaches_the_workbench():
    runner = FakeRunner(stdout=json.dumps({"ok": True}))
    cli = WorkbenchCli("/w", "acme-workbench", "acme", runner=runner)

    with pytest.raises(WorkbenchError):
        await cli.reply_to_inquiry("thread-1", "  ")
    with pytest.raises(WorkbenchError):
        await cli.send_order_sms("o-1", "02-000-0000", "", sent_by="owner")
    assert runner.calls == []


async def test_the_workbench_env_file_reaches_the_cli_and_the_tenant_still_wins(tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        '# workbench\nSUPABASE_URL="https://ledger.example"\n'
        "SUPABASE_SERVICE_ROLE_KEY='k-123'\nSUPABASE_TENANT_ID=other\n\nnot a line\n"
    )
    runner = FakeRunner(stdout=json.dumps({"ok": True}))
    cli = WorkbenchCli("/w", "acme-workbench", "acme", env_file=str(env_file), runner=runner)

    await cli.reply_to_inquiry("thread-1", "Hello")

    _, env, _ = runner.calls[0]
    assert env["SUPABASE_URL"] == "https://ledger.example"
    assert env["SUPABASE_SERVICE_ROLE_KEY"] == "k-123"
    assert env["SUPABASE_TENANT_ID"] == "acme"


async def test_a_missing_env_file_stops_before_the_cli_runs(tmp_path):
    runner = FakeRunner(stdout=json.dumps({"ok": True}))
    cli = WorkbenchCli(
        "/w", "acme-workbench", "acme", env_file=str(tmp_path / "absent"), runner=runner
    )

    with pytest.raises(WorkbenchError, match="absent"):
        await cli.reply_to_inquiry("thread-1", "Hello")
    assert runner.calls == []


async def test_the_deployment_variables_build_the_workbench_with_its_env_file(tmp_path):
    from ledger_backend.workbench import workbench_from_env

    env_file = tmp_path / ".env.local"
    env_file.write_text("SUPABASE_URL=https://ledger.example\n")
    runner = FakeRunner(stdout=json.dumps({"ok": True}))
    cli = workbench_from_env(
        {
            "WORKBENCH_ROOT": "/srv/workbench",
            "WORKBENCH_PACKAGE": "acme-workbench",
            "WORKBENCH_ENV_FILE": str(env_file),
        },
        "acme",
        runner=runner,
    )

    await cli.reply_to_inquiry("thread-1", "Hello")

    argv, env, cwd = runner.calls[0]
    assert (argv[2], cwd, env["SUPABASE_URL"]) == (
        "acme-workbench",
        "/srv/workbench",
        "https://ledger.example",
    )


async def test_a_pretty_printed_result_after_node_warnings_is_read():
    stdout = (
        "(node:1) [MODULE_TYPELESS_PACKAGE_JSON] Warning: reparsing as ES module\n"
        "{\n"
        '  "note": "warning text with { braces"\n'
        "}x\n"
        "{\n"
        '  "ok": true,\n'
        '  "error": null\n'
        "}\n"
    )
    runner = FakeRunner(stdout=stdout)

    await WorkbenchCli("/w", "acme-workbench", "acme", runner=runner).reply_to_inquiry(
        "thread-1", "Hello"
    )


async def test_a_pretty_printed_refusal_raises_with_its_reason():
    runner = FakeRunner(code=1, stdout='{\n  "ok": false,\n  "error": "channel key missing"\n}\n')

    with pytest.raises(WorkbenchError, match="channel key missing"):
        await WorkbenchCli("/w", "acme-workbench", "acme", runner=runner).reply_to_inquiry(
            "thread-1", "Hello"
        )
