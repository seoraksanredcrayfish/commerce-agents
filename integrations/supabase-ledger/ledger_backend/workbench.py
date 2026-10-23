# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The write port: the store's operations workbench CLI, which calls the same function as
the workbench screen and so keeps its stock checks, idempotency, and audit log. This
package never writes a ledger table itself.

A call here is the platform write. It passes ``--yes`` because the approval has already
happened: :meth:`LedgerMerchantBackend.apply_change` runs only for a change the host
marked approved (``MerchantAgentConfig.require_host_approval``)."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel

Runner = Callable[[Sequence[str], Mapping[str, str], str], Awaitable[tuple[int, str]]]


class WorkbenchError(Exception):
    """The workbench refused or failed a command; the message is its reason."""


class StockFix(BaseModel):
    before: int
    after: int


async def run_subprocess(argv: Sequence[str], env: Mapping[str, str], cwd: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=dict(env),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
    return process.returncode or 0, stdout.decode("utf-8", "replace")


def _last_json(stdout: str) -> dict | None:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def _read_env_file(path: str) -> dict[str, str]:
    """``KEY=value`` lines of a dotenv file; comments, blanks, and other lines are skipped
    and one pair of surrounding quotes is removed."""
    try:
        text = Path(path).read_text()
    except OSError as error:
        raise WorkbenchError(f"workbench env file {path} is unreadable: {error.strerror}") from None
    values: dict[str, str] = {}
    for line in text.splitlines():
        name, sep, value = line.strip().partition("=")
        if not sep or line.lstrip().startswith("#") or not name.isidentifier():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


class WorkbenchCli:
    """``root`` is the workbench checkout and ``package`` its app's pnpm package, whose
    ``cli`` script takes the commands. The CLI reads its credentials from the process
    environment, so ``env_file`` (the app's dotenv file) is loaded into it on each call;
    the tenant reaches the CLI as ``SUPABASE_TENANT_ID`` whatever that file says."""

    def __init__(
        self,
        root: str,
        package: str,
        tenant_id: str,
        *,
        env_file: str | None = None,
        runner: Runner = run_subprocess,
    ):
        self._root = root
        self._package = package
        self._tenant_id = tenant_id
        self._env_file = env_file
        self._runner = runner

    async def _run(self, command: str, *args: str) -> dict:
        argv = ["pnpm", "--filter", self._package, "cli", "--", command, *args, "--yes", "--json"]
        loaded = _read_env_file(self._env_file) if self._env_file else {}
        env = {**os.environ, **loaded, "SUPABASE_TENANT_ID": self._tenant_id}
        code, stdout = await self._runner(argv, env, self._root)
        result = _last_json(stdout)
        if result is None:
            raise WorkbenchError(f"{command} returned no result: {stdout.strip()[-300:]}")
        if code != 0 or result.get("status") == "failed" or result.get("ok") is False:
            raise WorkbenchError(str(result.get("reason") or result.get("error") or result))
        return result

    async def set_stock(self, barcode: str, count: int) -> StockFix:
        """Set one barcode's sellable stock to ``count`` (``stock.set``)."""
        if count < 0 or int(count) != count:
            raise WorkbenchError(f"stock must be a whole number of zero or more, got {count}")
        result = await self._run("stock.set", "--barcode", barcode, "--count", str(int(count)))
        before = int(result["before"])
        return StockFix(before=before, after=int(result.get("after", before)))

    async def reply_to_inquiry(self, thread_id: str, body: str) -> None:
        """Post ``body`` as the answer to one inquiry thread (``cs.reply``); the customer
        sees it on the thread's channel."""
        await self._run("cs.reply", "--thread", thread_id, "--body", _message(body))

    async def send_order_sms(
        self, order_id: str, sender_no: str, content: str, *, sent_by: str
    ) -> None:
        """Text the customer of one order from a registered sender number (``sms.send``);
        ``sent_by`` is the person who approved it."""
        payload = {
            "orderId": order_id,
            "senderNo": sender_no,
            "content": _message(content),
            "sentBy": sent_by,
        }
        await self._run("sms.send", "--input", json.dumps(payload, ensure_ascii=False))


def workbench_from_env(
    env: Mapping[str, str], tenant_id: str, *, runner: Runner = run_subprocess
) -> WorkbenchCli:
    """The workbench a deployment names: ``WORKBENCH_ROOT``, ``WORKBENCH_PACKAGE``, and the
    optional ``WORKBENCH_ENV_FILE``."""
    return WorkbenchCli(
        env["WORKBENCH_ROOT"],
        env["WORKBENCH_PACKAGE"],
        tenant_id,
        env_file=env.get("WORKBENCH_ENV_FILE") or None,
        runner=runner,
    )


def _message(text: str) -> str:
    if not text.strip():
        raise WorkbenchError("a customer message needs a body")
    return text
